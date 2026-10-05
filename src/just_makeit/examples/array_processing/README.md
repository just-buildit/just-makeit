# Array processing example

Every object just-makeit generates can process a block of samples in one call.
This example walks through the main ways the CLI exposes that capability, from
the free `steps()` that comes with every object to `--variable-output` batch
methods with multiple output streams.

Along the way, each section explains **who owns the memory**, **when it is
allocated**, and **what the Python caller can safely do with the returned array**.

Five patterns, five sections, then a sixth on documenting them. `--out-type`
and `--borrow` are covered in [Array memory ownership](../memory-ownership.md).

| #   | Pattern                                   | Output allocation                                                | Who owns it              |
| --- | ----------------------------------------- | ---------------------------------------------------------------- | ------------------------ |
| 1   | Auto-generated `steps()`                  | Per call (or zero if `out=` supplied)                            | Caller (numpy)           |
| 2   | `method` scalar stub + `method --batch`   | Per call (or zero if `out=` supplied)                            | Caller (numpy)           |
| 3   | `method --variable-output`                | Per call, sized by `_max_out(n_in)` (or zero if `out=` supplied) | Caller (numpy)           |
| 4   | `method --variable-output --multi-output` | Per call, one array per stream                                   | Caller (tuple of arrays) |
| 5   | `--arg-type type[]` (buffer primary arg)  | Caller supplies input buffer                                     | Caller (input)           |

All five patterns share a common rule: **inline `float[N]` state arrays in the
C struct require no heap allocation** — they are part of the struct itself.
Heap allocation only appears when the output size is not fixed at compile time.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example array_processing
# array_processing: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip`, which also works on Python 3.9 and 3.10 (the installer
needs 3.11+). It installs just-makeit, then builds the toolchain venv at
`/tmp/jm-venv`:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
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

| C function            | Signature                                                                                   |
| --------------------- | ------------------------------------------------------------------------------------------- |
| `my_arrays_ema_step`  | `static inline float my_arrays_ema_step(const my_arrays_ema_state_t *s, float x)`           |
| `my_arrays_ema_steps` | `void my_arrays_ema_steps(my_arrays_ema_state_t *s, const float *in, float *out, size_t n)` |

`step()` is a `static inline` function in the sacred header
`native/inc/my_arrays/ema/ema_core.h`; `steps()` is a thin loop in
`native/src/ema/ema_core.c` that calls it once per sample. You implement
`step()`; `steps()` comes for free.

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
void my_arrays_ema_steps(my_arrays_ema_state_t       *state,
               const float       *input,
               float             *output,
               size_t             n);
```

This is true with or without `--perf`: `JM_DEFINE_STEPS` only replaces the
loop body (adding SIMD dispatch), not the signature or the allocation model.

### The Python ext — one malloc per call

The ext is the only place an allocation happens. It calls `PyArray_SimpleNew`
to create the output array, passes the raw pointer to `my_arrays_ema_steps`, then
returns the numpy array to the caller:

```
call f.steps(block)
│
├─ ext calls PyArray_SimpleNew(n)   ← one malloc, every call
│
├─ calls my_arrays_ema_steps(state, block.data, out.data, 1024)
│    └─ no allocation inside; fills out[] in place
│
└─ returns ndarray to caller
   ownership: caller
   lifetime:  indefinite — safe to hold, copy, or discard at will
```

Successive calls are independent: the previous result is never overwritten.
`--variable-output` (§3) behaves the same way: each call returns a new,
independently owned array.

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
writable, C-contiguous, the correct dtype, and exactly as long as the input.

```
call f.steps(block, buf)
│
├─ ext validates buf: dtype, C-contiguous, len == n
│
├─ calls my_arrays_ema_steps(state, block.data, buf.data, 1024)
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
} my_arrays_ema_state_t;
```

`my_arrays_ema_create()` does exactly one `malloc` for the whole struct. There is no
`malloc` per field, no pointer to chase, and no fragmentation.

Contrast this with a hypothetical `float *coeffs` pointer: that would require
a separate allocation, a separate free, and careful ownership accounting.
just-makeit avoids this entirely by embedding arrays inline whenever the length
is fixed at code-generation time.

---

## 2. `method` — scalar stub + `--batch` companion

Use `just-makeit method` when you need an execute path with **different
input or output types** than the primary `step()`.

```sh
# Add a second execute method with a different I/O type.
# This object produces uint32 phase words in addition to float output.
just-makeit method ema quantize \
    --arg-type float \
    --return-type uint32_t
```

The command declares it in `native/inc/my_arrays/ema/ema_core.h` and appends a
stub to `native/src/ema/ema_core.c`:

```c
uint32_t my_arrays_ema_quantize(my_arrays_ema_state_t *state, float x);
```

For **1:1-rate batch work** (output count equals input count), declare a batch
method and let jm generate its binding. `native/src/ema/ema_ext.c` is jm's
glue, rewritten by every `jm apply`, so never edit it by hand:

```sh
just-makeit method ema quantize_steps \
    --arg-type float \
    --return-type uint32_t \
    --batch
```

That declares
`void my_arrays_ema_quantize_steps(my_arrays_ema_state_t *state, const float *in, size_t n, uint32_t *out);`
and appends its stub to `native/src/ema/ema_core.c`. Implement it as a loop
over the scalar method:

```c
/* Batch companion for my_arrays_ema_quantize(): the body of the stub that
 * `just-makeit method ... --batch` appended to native/src/ema/ema_core.c.
 * The Python ext allocates out[] (or takes the caller's out= array) before
 * calling this; the Python caller only passes the input array.
 * This is the right pattern when output count == input count (1:1 rate).
 */
void
my_arrays_ema_quantize_steps (my_arrays_ema_state_t *state, const float *in,
                              size_t n, uint32_t *out)
{
  for (size_t i = 0; i < n; i++)
    out[i] = my_arrays_ema_quantize (state, in[i]);
}
```

### Array ownership for a `--batch` method

The Python caller's experience is identical to the auto-generated `steps()`:
pass one input array, get back a new numpy array.

```
call f.quantize_steps(block)
│
├─ ext calls PyArray_SimpleNew(n, uint32)   ← one malloc, every call
│
├─ calls my_arrays_ema_quantize_steps(state, block.data, n, out.data)
│    └─ loop: out[i] = my_arrays_ema_quantize(state, block[i])
│
└─ returns ndarray to caller
   ownership: caller
   lifetime:  indefinite — object holds no reference to it
```

The C function `my_arrays_ema_quantize_steps` takes both pointers, but the ext
owns that allocation. As with `steps()`, a caller that wants to reuse a buffer
passes it as `out=` (`f.quantize_steps(block, buf)`) and the ext writes into it
instead.

**When to use this pattern**

- You need a different input or output type than the primary `step()`.
- Output count equals input count (1:1 rate).
- jm generates the binding, `out=` included; you write only the loop.

**When not to use it**

If the output count differs from the input count (e.g. a decimator), use
`--variable-output`. See §3.

---

## 3. `method --variable-output` — self-sizing batch

Use this when the **output count differs from the input count but can be
bounded from the state and the call's input length**. The classic case is a
rate-changing block: a 2× decimator fed `n_in` samples produces at most
`ceil(n_in / 2)`.

```sh
# A half-band decimator: input block of N complex samples, output ≤ N/2 samples.
# The output count is bounded by the input length (ceil(n_in / 2)), so
# --variable-output sizes each call's output array from _max_out(n_in).
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

The command declares two C functions in
`native/inc/my_decim/hbdecim/hbdecim_core.h` and appends their stubs to
`native/src/hbdecim/hbdecim_core.c`:

| Stub                                             | When called                         | Your job                                  |
| ------------------------------------------------ | ----------------------------------- | ----------------------------------------- |
| `my_decim_hbdecim_execute_max_out(state, n_in)`  | Every Python call, before `execute` | Return the output bound for `n_in` inputs |
| `my_decim_hbdecim_execute(state, in, n_in, out)` | Every Python call                   | Fill `out`, return actual count           |

The bound is also callable from Python, as `d.execute_max_out(n_in)`.

Implement both:

```c
/* Implement in native/src/hbdecim/hbdecim_core.c.
 *
 * The Python ext calls this on every execute() call, with that call's input
 * length, to size the output array.  Return the largest n_out that execute()
 * can produce for n_in inputs.  Here: n_in / 2, rounded up.
 *
 * Without --exact-max-out the binding never allocates fewer than n_in
 * elements: a smaller bound, 0 included, falls back to n_in.
 */
size_t
my_decim_hbdecim_execute_max_out (my_decim_hbdecim_state_t *state, size_t n_in)
{
  (void)state;
  return (n_in + 1) / 2;
}

/* Process n_in samples into out[]; return n_out, the count written.
 * The caller (Python ext) supplies out[], sized from execute_max_out(n_in).
 */
size_t
my_decim_hbdecim_execute (my_decim_hbdecim_state_t *state,
                          const float _Complex *in, size_t n_in,
                          float _Complex *out)
{
  size_t n_out = 0;
  (void)state;
  for (size_t i = 0; i + 1 < n_in; i += 2)
    {
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

d = Hbdecim()

block = (np.random.randn(1024) + 1j * np.random.randn(1024)).astype(np.complex64)
out = d.execute(block)   # a new array, shape (≤512,)
```

`d.execute(block)` returns a **NumPy-owned array**, sized
`max(execute_max_out(n), n)` and trimmed to the count the kernel reported.

### Array ownership for `--variable-output`

```
out = d.execute(block)
│
├─ ext allocates a NumPy array of max(execute_max_out(1024), 1024)
│  └─ the kernel writes straight into it — no copy
│
├─ calls my_decim_hbdecim_execute(state, block.data, 1024, out.data)  → returns 512
│
└─ returns it trimmed to 512
   ownership: the returned array owns its memory
   lifetime:  independent of the object and of every other result
```

Every result is independent. Accumulating them is safe, and always was
intended to be:

```python
chunks = [d.execute(b) for b in blocks]   # each keeps its own data
whole = np.concatenate(chunks)
```

Nothing the object does later can disturb an array you already hold — not a
same-size call, not a larger one, not `destroy()`.

!!! note "This used to be a constraint, and no longer is"

    Earlier versions returned a view into a buffer the object reused, so a
    result went stale on the next call and had to be copied. Two mechanisms
    were built to make that safe (gh-219, gh-437) before the approach was
    abandoned in gh-604 — measurement showed it retained ~514 KiB per call and
    ran 6-8× slower than simply allocating. If you have code that defensively
    copies each result, you can drop the copy.

To write into your own buffer instead, pass `out=` — see
[Array memory ownership](../memory-ownership.md) for when that is worth it.

### When to use `--variable-output`

| Use case                         | `_max_out` returns | Appropriate?                                      |
| -------------------------------- | ------------------ | ------------------------------------------------- |
| Decimator, ratio R               | `ceil(n_in / R)`   | Yes                                               |
| FIFO with fixed capacity C       | `C`                | Yes                                               |
| FIR filter, 1:1 rate             | `n_in`             | No — output size = input size; use auto `steps()` |
| Integrator / accumulator         | 1 per sample       | No — use scalar `step()`                          |
| Overflow detector, 1:1 rate      | `n_in`             | No — use `jm method ... --batch` (§2)             |

---

## 4. `method --variable-output --multi-output` — parallel output streams

`--multi-output TYPE` adds a second output array alongside the primary one;
each call allocates both from NumPy and returns them independently owned.  The Python call returns a tuple.  The flag is repeatable for
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

The command declares two more C functions in `hbdecim_core.h` and appends
their stubs to `hbdecim_core.c`:

```c
size_t my_decim_hbdecim_execute_ovf_max_out(my_decim_hbdecim_state_t *state,
                                            size_t n_in);
size_t my_decim_hbdecim_execute_ovf(my_decim_hbdecim_state_t *state,
                                    const float _Complex *in, size_t n_in,
                                    float _Complex *out, uint8_t *out1);
```

Both `out` and the secondary array `out1` are allocated by the ext on every
call, NumPy-owned, `max(execute_ovf_max_out(n_in), n_in)` elements each. Your
implementation fills both and returns the count:

```c
/* Implement in native/src/hbdecim/hbdecim_core.c.
 *
 * Two output arrays: primary (filtered samples) and secondary (overflow
 * flags). Both are allocated per call by the ext, NumPy-owned, sized from
 * execute_ovf_max_out(n_in). Return the actual count written to both arrays.
 */
size_t
my_decim_hbdecim_execute_ovf_max_out (my_decim_hbdecim_state_t *state,
                                      size_t                    n_in)
{
  (void)state;
  return (n_in + 1) / 2;
}

size_t
my_decim_hbdecim_execute_ovf (my_decim_hbdecim_state_t *state,
                              const float _Complex *in, size_t n_in,
                              float _Complex *out,  /* primary */
                              uint8_t        *out1) /* secondary: overflow */
{
  size_t n_out = 0;
  (void)state;
  for (size_t i = 0; i + 1 < n_in; i += 2)
    {
      float _Complex y = (in[i] + in[i + 1]) * 0.5f;
      out[n_out]       = y;
      out1[n_out]      = (cabsf (y) > 1.0f) ? 1 : 0;
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
samples, flags = d.execute_ovf(block)   # tuple of two new, independently owned arrays
```

### Array ownership for multi-output

```
samples, flags = d.execute_ovf(block)
│
├─ ext allocates complex64[max(execute_ovf_max_out(1024), 1024)]
│  and uint8[same], both NumPy-owned
│
├─ calls my_decim_hbdecim_execute_ovf(state, block.data, 1024, out, out1)  → returns 512
│
└─ returns (out, out1), each trimmed to 512
   ownership: the caller owns both arrays
   lifetime:  independent of the object and of every other result
```

As in §3, every result is independent; nothing needs copying before the next
call. Unlike `execute()`, a multi-output method takes no `out=` buffer.

---

## 5. `--arg-type type[]` — array-buffer primary arg

Some objects are designed to consume an entire buffer in one call — a
decimator, a packet framer, a block codec.  Wrapping them with a scalar
`step()` + auto-generated `steps()` adds indirection that compilers cannot
always eliminate.  Pass `[]` on the arg type to express this directly.

```sh
just-makeit new my_buf \
    --object buf_proc \
    --arg-type "float _Complex[]" \
    --return-type int \
    --state "count:int32_t:0"
```

The generated `step()` takes a pointer and a length; Python passes it a numpy
array:

```c
/* native/inc/my_buf/buf_proc/buf_proc_core.h */
static inline int
my_buf_buf_proc_step(
    my_buf_buf_proc_state_t *state,
    const float _Complex *x, size_t x_len)
{
    (void)state; (void)x; (void)x_len; /* TODO: implement */
    return (int)0;
}
```

`steps()` is **not** generated — the primary operation already takes a buffer.

### What Python sees

```python
import numpy as np
from my_buf import BufProc

proc = BufProc()
block = (np.random.randn(1024) + 1j * np.random.randn(1024)).astype(np.complex64)
n = proc.step(block)   # passes the whole array; returns int
```

### Type stub (`my_buf/src/my_buf/buf_proc.pyi`)

```python
class BufProc:
    def __init__(self, count: int = 0) -> None: ...
    def step(self, x: npt.NDArray[np.complex64]) -> int:
        """Process one input sample."""
    # no steps() — the primary op already takes a buffer
```

### Choosing between the five patterns

```
Does output count equal input count?
├─ Yes, and input is one sample → use step() + auto steps()          (§1)
│
├─ Yes, but a method has a different return type → jm method --batch (§2)
│
├─ No → can the output count be bounded from the input length?
│       ├─ Yes, one stream  → --variable-output                       (§3)
│       └─ Yes, N streams   → --variable-output --multi-output        (§4)
│
└─ Primary op takes a whole buffer → --arg-type type[]                (§5)
   (no steps() generated; step() accepts NDArray directly)
```

---

## 6. Document once, in C — rich stubs and runnable doctests

The sacred header is also the single source of truth for **documentation**. A
Doxygen `/** ... */` comment on `create()` or a named method flows straight into
the generated `.pyi` docstring, and a `@code` block on a method becomes a
**runnable doctest**. Give `my_arrays_ema_quantize` a real body in
`native/src/ema/ema_core.c`:

```c
uint32_t
my_arrays_ema_quantize(my_arrays_ema_state_t *state, float x)
{
    (void)state;
    if (x <= 0.0f)
        return 0U;
    return (uint32_t)(x + 0.5f);
}
```

and a comment above its declaration in `native/inc/my_arrays/ema/ema_core.h`:

```c
/**
 * @brief Quantize one sample to an unsigned integer code.
 * @param x  Input sample; values <= 0 map to 0.
 * @return Nearest non-negative integer to x (round half up).
 * @code
 * >>> from my_arrays import Ema
 * >>> e = Ema()
 * >>> e.quantize(3.4)
 * 3
 * >>> e.quantize(3.6)
 * 4
 * @endcode
 */
uint32_t my_arrays_ema_quantize(my_arrays_ema_state_t *state, float x);
```

`jm apply` re-derives the stub, and `src/my_arrays/ema.pyi` now carries the full
numpy-style docstring — including the `@code` block as an `Examples` doctest:

```python
    def quantize(self, x: float) -> int:
        """Quantize one sample to an unsigned integer code.

        Parameters
        ----------
        x : float
            Input sample; values <= 0 map to 0.

        Returns
        -------
        int
            Nearest non-negative integer to x (round half up).

        Examples
        --------
        >>> from my_arrays import Ema
        >>> e = Ema()
        >>> e.quantize(3.4)
        3
        >>> e.quantize(3.6)
        4

        """
```

That doctest is not decoration: run against the *built* extension, it fails
the moment the kernel drifts from its documented example. A generated
project's `make test` does not run `.pyi` doctests (this example's own test
does), so to make it a gate in your project add
`PYTHONPATH=src python -m pytest --doctest-glob='*.pyi' src/` to your test
step. To watch every `>>>` line execute, run `doctest -v` after `make`:

```termynal
$ PYTHONPATH=src python -m doctest -v src/my_arrays/ema.pyi
{d}...{/d}
{d}Trying:{/d}
    e = Ema()
{d}Expecting nothing{/d}
{g}ok{/g}
{d}Trying:{/d}
    e.quantize(3.4)
{d}Expecting:{/d}
    3
{g}ok{/g}
{d}Trying:{/d}
    e.quantize(3.6)
{d}Expecting:{/d}
    4
{g}ok{/g}
{d}...{/d}
{g}10 passed and 0 failed.{/g}
{g}Test passed.{/g}
```

That summary is Python 3.12's; 3.13 and later print `10 passed.` instead.
jm's own CI runs this stub's doctests with `pytest --doctest-glob='*.pyi'`,
the same command as above.
