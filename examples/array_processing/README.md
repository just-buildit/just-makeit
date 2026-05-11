# Array processing example

Every object just-makeit generates can process a block of samples in one call.
This example walks through every way the CLI exposes that capability, from the
free `steps()` that comes with every object to `--variable-output` batch methods
with multiple output streams.

Along the way, each section explains **who owns the memory**, **when it is
allocated**, and **what the Python caller can safely do with the returned array**.

Four patterns, four sections:

| # | Pattern | Output allocation | Who owns it |
|---|---------|-------------------|-------------|
| 1 | Auto-generated `steps()` | Per call (or zero if `out=` supplied) | Caller (numpy) |
| 2 | `method` scalar stub + hand-written `_steps()` | Per call (or zero if `out=` supplied) | Caller (numpy) |
| 3 | `method --variable-output` | Allocated at `__init__`, re-used | Object (zero-copy view) |
| 4 | `method --variable-output --multi-output` | Same — one buffer per stream | Object (tuple of views) |

All four patterns share a common rule: **inline `float[N]` state arrays in the
C struct require no heap allocation** — they are part of the struct itself.
Heap allocation only appears when the output size is not fixed at compile time.

## TL;DR — see it work first

```sh
git clone https://github.com/just-buildit/just-makeit
cd just-makeit
uvx git+https://github.com/just-buildit/just-makeit install-deps
source /tmp/jm-venv/bin/activate
python3 examples/array_processing/test.py
# array_processing: PASSED
```

## Prerequisites

```sh
pip install just-makeit
just-makeit install-deps --check   # report what is installed vs. what will be installed
just-makeit install-deps           # install cmake, C compiler, numpy, and create a venv
source /tmp/jm-venv/bin/activate
```

Pass a custom path to keep the venv somewhere persistent:

```sh
just-makeit install-deps ~/my-venv && source ~/my-venv/bin/activate
```

---

## 1. Auto-generated `steps()` — free with every object

```sh
just-makeit new my_arrays \
    --object ema \
    --arg-type float \
    --return-type float \
    --state alpha:double:0.1 \
    --state prev:float:0.0
cd my_arrays
```

Every `just-makeit object` generates both `step()` and `steps()`:

| C function | Signature |
|---|---|
| `ema_step` | `float ema_step(ema_state_t *s, float x)` |
| `ema_steps` | `void ema_steps(ema_state_t *s, const float *in, float *out, size_t n)` |

`steps()` is a thin loop in `native/src/ema/ema_core.c` — it calls `step()`
once per sample. You implement `step()`; `steps()` comes for free.

### What Python sees

```python
import numpy as np
from my_arrays import Ema

f = Ema(alpha=0.1)

block = np.random.randn(1024).astype(np.float32)
out   = f.steps(block)   # returns np.ndarray, shape (1024,), dtype float32
```

`steps()` allocates a fresh numpy array on every call (`PyArray_SimpleNew`) and
returns it. The caller owns that array outright — the object holds no reference
to it and never touches it again.

### The C API — caller-supplied pointers, no allocation

At the C level, `steps()` takes both pointers from the caller and allocates
nothing:

```c
/* Output buffer must be pre-allocated by caller. */
void ema_steps(ema_state_t       *state,
               const float       *input,
               float             *output,
               size_t             n);
```

This is true with or without `--perf`: `JM_DEFINE_STEPS` only replaces the
loop body (adding SIMD dispatch), not the signature or the allocation model.

### The Python ext — one malloc per call

The ext is the only place an allocation happens. It calls `PyArray_SimpleNew`
to create the output array, passes the raw pointer to `ema_steps`, then
returns the numpy array to the caller:

```
call f.steps(block)
│
├─ ext calls PyArray_SimpleNew(n)   ← one malloc, every call
│
├─ calls ema_steps(state, block.data, out.data, 1024)
│    └─ no allocation inside; fills out[] in place
│
└─ returns ndarray to caller
   ownership: caller
   lifetime:  indefinite — safe to hold, copy, or discard at will
```

Successive calls are independent: the previous result is never overwritten.
This is the opposite of `--variable-output` (§3), where the object owns a
fixed buffer and reuses it each call.

### Eliminating the per-call malloc with `out=`

Pass a pre-allocated numpy array as the second argument and the ext writes
directly into it — `PyArray_SimpleNew` is skipped entirely:

```python
buf = np.empty(1024, dtype=np.float32)   # allocate once

for block in stream:
    f.steps(block, buf)   # zero allocation on the hot path
```

The returned object is the same array you passed in (`ret is buf`), so you
can ignore the return value or use it for chaining. The buffer must be
C-contiguous, the correct dtype, and at least as long as the input.

```
call f.steps(block, buf)
│
├─ ext validates buf: dtype, C-contiguous, len == n
│
├─ calls ema_steps(state, block.data, buf.data, 1024)
│    └─ no allocation; fills buf in place
│
└─ returns buf (same object, new reference)
   ownership: caller retains
   lifetime:  safe to reuse immediately on next call
```

This is the right choice for any processing loop where throughput matters.
For one-shot calls or exploratory work the default (no `out=`) is simpler.

### Inline array state — no heap per field

If your object has fixed-length array state (e.g. `--state "coeffs:float[16]"`),
those arrays live **inside the C struct**, not on the heap:

```c
typedef struct {
    float  coeffs[16];   /* inline — no extra malloc */
    float  delay[16];    /* inline */
    float  gain;
} ema_state_t;
```

`ema_create()` does exactly one `malloc` for the whole struct. There is no
`malloc` per field, no pointer to chase, and no fragmentation.

Contrast this with a hypothetical `float *coeffs` pointer: that would require
a separate allocation, a separate free, and careful ownership accounting.
just-makeit avoids this entirely by embedding arrays inline whenever the length
is fixed at code-generation time.

---

## 2. `method` — scalar stub + hand-written `_steps()`

Use `just-makeit method` when you need an execute path with **different
input or output types** than the primary `step()`.

```sh
# Add a second execute method with a different I/O type.
# This object produces uint32 phase words in addition to float output.
just-makeit method ema quantize \
    --arg-type float \
    --return-type uint32_t
```

The command writes a scalar C stub into `native/src/ema/ema_methods.c`:

```c
uint32_t ema_quantize(ema_state_t *state, float x);
```

For **1:1-rate batch work** (output count equals input count), write the
`_steps()` companion by hand in the same file:

```c
/* Hand-written batch companion for ema_quantize().
 * Add this to native/src/ema/ema_methods.c after implementing the scalar stub.
 * The Python ext allocates out[] via PyArray_SimpleNew before calling this;
 * the Python caller only passes the input array.
 * This is the right pattern when output count == input count (1:1 rate).
 */
void
ema_quantize_steps(ema_state_t   *state,
                   const float   *in,
                   uint32_t      *out,
                   size_t         n)
{
    for (size_t i = 0; i < n; i++)
        out[i] = ema_quantize(state, in[i]);
}
```

Then wire it into `native/src/ema/ema_ext.c` following the `ema_steps`
pattern already there.

### Array ownership for hand-written `_steps()`

The Python caller's experience is identical to the auto-generated `steps()`:
pass one input array, get back a new numpy array.

```
call f.quantize_steps(block)
│
├─ ext calls PyArray_SimpleNew(n, uint32)   ← one malloc, every call
│
├─ calls ema_quantize_steps(state, block.data, out.data, n)
│    └─ loop: out[i] = ema_quantize(state, block[i])
│
└─ returns ndarray to caller
   ownership: caller
   lifetime:  indefinite — object holds no reference to it
```

The C function `ema_quantize_steps` takes both pointers, but the ext owns
that allocation — the Python caller never passes or manages an output buffer.

**When to use this pattern**

- You need a different input or output type than the primary `step()`.
- Output count equals input count (1:1 rate).
- Straightforward; no infrastructure beyond the loop.

**When not to use it**

If the maximum output count depends on object state and is knowable at init
time (e.g. a decimator), `--variable-output` is more ergonomic — it removes
the per-call allocation from the caller's responsibility. See §3.

---

## 3. `method --variable-output` — pre-allocated, zero-copy batch

Use this when the **maximum output count is bounded by state and knowable at
init time**.  The classic case is a rate-changing block: a 2× decimator with
block size `B` can produce at most `ceil(B / 2)` outputs per call.

```sh
# A half-band decimator: input block of N complex samples, output ≤ N/2 samples.
# Because the maximum output is known at init time (ceil(block_size / 2)),
# --variable-output pre-allocates the output buffer once and returns a view.
cd ..
just-makeit new my_decim \
    --object hbdecim \
    --arg-type "float _Complex" \
    --return-type "float _Complex" \
    --state "delay:float _Complex[12]"
cd my_decim

just-makeit method hbdecim execute \
    --arg-type "float _Complex" \
    --return-type "float _Complex" \
    --variable-output
```

The command generates two C stubs in `native/src/hbdecim/hbdecim_methods.c`:

| Stub | When called | Your job |
|------|-------------|----------|
| `hbdecim_execute_max_out(state)` | Once at Python `__init__` | Return the output bound |
| `hbdecim_execute(state, in, n_in, out)` | Every Python call | Fill `out`, return actual count |

Implement both:

```c
/* Implement in native/src/hbdecim/hbdecim_methods.c.
 *
 * The Python ext calls this once at __init__ to size the pre-allocated
 * output buffer.  Return the largest n_out that execute() can ever produce
 * for any valid call.  Here: block_size / 2, rounded up.
 *
 * Must be positive.  Returning 0 causes malloc(0), which is implementation-
 * defined and will likely produce a silent bug.
 */
size_t
hbdecim_execute_max_out(hbdecim_state_t *state)
{
    /* state->block_size is a constructor parameter (add with just-makeit add) */
    return (state->block_size + 1) / 2;
}

/* Process n_in samples; write actual output count to *out; return n_out.
 * The caller (Python ext) supplies the pre-allocated output buffer.
 */
size_t
hbdecim_execute(hbdecim_state_t    *state,
                const float complex *in,
                size_t              n_in,
                float complex       *out)
{
    size_t n_out = 0;
    for (size_t i = 0; i + 1 < n_in; i += 2) {
        /* TODO: polyphase half-band implementation */
        out[n_out++] = (in[i] + in[i + 1]) * 0.5f;
    }
    return n_out;
}
```

### What Python sees

```python
import numpy as np
from my_decim import Hbdecim

d = Hbdecim()           # __init__ calls execute_max_out(); mallocs output buffer once

block = (np.random.randn(1024) + 1j * np.random.randn(1024)).astype(np.complex64)
view  = d.execute(block)  # returns zero-copy view; shape (≤512,)
```

`d.execute(block)` returns a **numpy view** into the object's internal output
buffer.  No allocation happens on this call path at all.

### Array ownership for `--variable-output`

```
d = Hbdecim()
│
└─ ext calls hbdecim_execute_max_out()  → 512
   ext mallocs float complex[512]       ← one malloc, at __init__
   stored as d._out_buf (opaque)

view = d.execute(block)
│
├─ calls hbdecim_execute(state, block.data, 1024, d._out_buf)  → returns 512
│
└─ returns numpy view wrapping d._out_buf[:512]
   ownership: object retains the buffer
   lifetime:  view is valid until the NEXT call to d.execute()
              — do not hold the view across calls; copy if you need to keep it

# Safe: process, then copy if needed
view = d.execute(block)
keep = view.copy()       # independent array, survives next call
```

**Critical constraint**: the view becomes **stale on the next `execute()` call**
because the object overwrites the same buffer.  Copy before calling again if
you need to retain more than one block.

### When to use `--variable-output`

| Use case | `_max_out` returns | Appropriate? |
|---|---|---|
| Decimator, ratio R, block size B | `ceil(B / R)` | Yes |
| FIFO with fixed capacity C | `C` | Yes |
| FIR filter, 1:1 rate | unknown at init | No — output size = input size; use auto `steps()` |
| Integrator / accumulator | 1 per sample | No — use scalar `step()` |
| Overflow detector, 1:1 rate | unknown at init | No — use scalar method + hand-written `_steps()` |

---

## 4. `method --variable-output --multi-output` — parallel output streams

`--multi-output TYPE` adds a second pre-allocated output buffer alongside the
primary one.  The Python call returns a tuple.  The flag is repeatable for
three or more streams.

```sh
# Two parallel output streams from one call:
# primary: float _Complex (filtered samples)
# secondary: uint8_t (per-sample overflow flag)
just-makeit method hbdecim execute_ovf \
    --arg-type "float _Complex" \
    --return-type "float _Complex" \
    --variable-output \
    --multi-output uint8_t
```

Generated stubs in `hbdecim_methods.c`:

```c
size_t hbdecim_execute_ovf_max_out(hbdecim_state_t *state);
size_t hbdecim_execute_ovf(hbdecim_state_t    *state,
                           const float complex *in, size_t n_in,
                           float complex       *out,
                           uint8_t             *ovf);
```

Both `out` and `ovf` are pre-allocated to `_max_out()` elements and owned by
the object.  Your implementation fills both and returns the count:

```c
/* Implement in native/src/hbdecim/hbdecim_methods.c.
 *
 * Two output arrays: primary (filtered samples) and secondary (overflow flags).
 * Both are pre-allocated by the ext to execute_ovf_max_out() elements.
 * Return the actual count written to both arrays.
 */
size_t
hbdecim_execute_ovf_max_out(hbdecim_state_t *state)
{
    return (state->block_size + 1) / 2;
}

size_t
hbdecim_execute_ovf(hbdecim_state_t    *state,
                    const float complex *in,
                    size_t              n_in,
                    float complex       *out,    /* primary */
                    uint8_t             *ovf)    /* secondary */
{
    size_t n_out = 0;
    for (size_t i = 0; i + 1 < n_in; i += 2) {
        float complex y = (in[i] + in[i + 1]) * 0.5f;
        out[n_out] = y;
        ovf[n_out] = (cabsf(y) > 1.0f) ? 1 : 0;
        n_out++;
    }
    return n_out;
}
```

### What Python sees

```python
import numpy as np
from my_decim import Hbdecim

d = Hbdecim()

block    = (np.random.randn(1024) + 1j * np.random.randn(1024)).astype(np.complex64)
samples, flags = d.execute_ovf(block)   # tuple of two zero-copy views
```

### Array ownership for multi-output

```
d = Hbdecim()
│
└─ ext mallocs float complex[512]  → d._out_buf
   ext mallocs uint8_t[512]        → d._ovf_buf
   both stored in the object

samples, flags = d.execute_ovf(block)
│
├─ calls hbdecim_execute_ovf(..., d._out_buf, d._ovf_buf) → returns 512
│
├─ returns (view into d._out_buf[:512],
│           view into d._ovf_buf[:512])
│
│  ownership: object retains both buffers
│  lifetime:  both views stale after next call to execute_ovf()
│             — copy before calling again

n_ovf = int(flags.sum())           # safe — flags is still valid here
samples_copy = samples.copy()      # independent; survives next call
```

The same "stale after next call" rule applies to every buffer produced by
`--variable-output`.  The zero-copy design makes the steady-state path
allocation-free; the copy obligation is the trade-off.

### Choosing between the four patterns

```
Does output count equal input count?
├─ Yes → use auto steps() or hand-written _steps()  (§1, §2)
│         caller allocates output; object never holds a reference
│
└─ No → is the maximum output count knowable at init time?
        ├─ Yes → --variable-output [--multi-output ...]  (§3, §4)
        │         object owns the buffer; Python gets a zero-copy view
        │         copy the view before calling again
        │
        └─ No  → allocate per call in the ext, return a fresh ndarray
                  (hand-write the ext glue; just-makeit does not generate this)
```
