# Stateful vs pure components

By default, `just-makeit` generates **stateful** components: the library owns an
opaque `<component>_state_t *` that accumulates history across calls, and the
Python binding manages its lifetime automatically.

The `--pure` flag generates a **pure** component: the algorithm is identical,
but the caller owns the memory that holds the working state — or there is no
working state at all.

`--pure` auto-selects one of two styles based on the declared state variables:

| Declared state | Style | Caller manages |
|---|---|---|
| Scalar types only (or none) | **scalar** | nothing — params are function arguments |
| Any array type | **struct** | `<comp>_params_t *` — an explicit non-opaque struct |

______________________________________________________________________

## Stateful (default)

```sh
just-makeit new my_dsp --component agc \
    --state attack:double:0.01 \
    --state release:double:0.1 \
    --state envelope:double:0.0
```

**What you get:**

```c
// Opaque heap pointer — library owns it
agc_state_t *agc_create(double attack, double release, double envelope);
void         agc_destroy(agc_state_t *state);
void         agc_reset(agc_state_t *state);

float complex agc_step(agc_state_t *state, float complex x);
void          agc_steps(agc_state_t *state, const float complex *in,
                        float complex *out, size_t n);
```

```python
from my_dsp import AGC
agc = AGC(attack=0.01, release=0.1)
y   = agc.step(x)
arr = agc.steps(block)
```

**Cost:**

- One heap allocation per instance (`malloc` inside `_create()`).
- Python object holds the pointer; `__del__` frees it.
- Getters and setters cross the Python/C boundary to inspect or change params.

**Benefit:**

- Simplest Python API — construct once, call many times.
- Python's garbage collector handles lifetime; no explicit `destroy()` needed.
- Works well when you have one logical "channel" per Python object.

**Reach for stateful when:**

- The algorithm accumulates history that *is* the point: phase accumulators,
  IIR feedback, envelope followers, running statistics.
- The Python caller doesn't need to control allocation strategy.
- You want the simplest possible API for single-channel use.

**Examples:** FIR/IIR filters, oscillators, PLLs, AGC, running mean/variance,
delay lines where the history is private implementation detail.

______________________________________________________________________

## Pure scalar

```sh
just-makeit new my_dsp --component normalize \
    --pure \
    --state scale:double:1.0 \
    --state offset:double:0.0
```

**What you get:**

```c
// Inline in the header — zero call overhead
static inline float complex
normalize_fn(float complex x, double scale, double offset);

void normalize_steps(const float complex *in, float complex *out,
                     size_t n, double scale, double offset);
```

```python
from my_dsp import normalize        # module-level function
normalize(x, scale=1.0, offset=0.0)
normalize.steps(arr, scale=1.0)     # .steps attached in __init__.py
```

**Cost:**

- Each call must pass all params — no saved state between calls.
- If params change rarely, passing them every call is slightly redundant.
  (In practice the compiler inlines the values; overhead is immeasurable.)

**Benefit:**

- **No allocation at all.** No struct, no pointer, no heap.
- Each call is fully self-contained — safe to call from multiple threads
  with different params simultaneously.
- The `fn.steps = fn_steps` pattern keeps the Python API clean:
  `normalize.steps(arr)` works without creating any object.
- Inline in the header means the compiler sees the body at every call site
  and can constant-fold, vectorize, or eliminate the call entirely.

**Reach for pure scalar when:**

- The transform is genuinely memoryless: each output depends only on the
  current input and fixed parameters, never on previous inputs.
- Parameters are conceptually *configuration* rather than *state*: you set
  them up front and rarely change them, or they vary legitimately per call.
- You want to compose transforms without creating objects:
  `normalize_fn(saturate_fn(x, 1.0), 0.707, 0.0)`.

**Examples:** normalize, saturate/clip, dB-to-linear, TPDF dither, requantize,
window function evaluation, polynomial approximations, lookup-table wrappers.

______________________________________________________________________

## Pure struct

```sh
just-makeit new my_dsp --component fir_pure \
    --pure \
    --state coeffs:"float[16]" \
    --state delay:"float _Complex[16]"
```

**What you get:**

```c
// Non-opaque — all fields directly accessible
typedef struct {
    float         coeffs[16];
    float complex delay[16];
} fir_pure_params_t;

// Heap alloc helper (calloc; comment shows aligned_alloc for SIMD)
fir_pure_params_t *fir_pure_params_create(void);
void               fir_pure_params_free(fir_pure_params_t *p);
void               fir_pure_params_init(fir_pure_params_t *p);  // stack/pool init

float complex fir_pure_fn(float complex x, fir_pure_params_t *params);
void          fir_pure_steps(const float complex *in, float complex *out,
                             size_t n, fir_pure_params_t *params);
```

```python
from my_dsp import FirPure
fir = FirPure()           # _params_create() inside
y   = fir(x)              # callable via tp_call — no named step()
arr = fir.steps(block)

with FirPure() as fir2:   # context manager → _params_free on exit
    ...
```

**Cost:**

- Still one heap allocation per instance by default (via `_params_create()`).
  This is the same cost as stateful.
- The struct is non-opaque, so C callers can read/write fields directly —
  which is power, but also means you can corrupt state if you're careless.
- `destroy()` must be called explicitly in C (Python binding handles this
  in `__del__` and as a context manager).

**Benefit:**

- **Caller controls allocation.** The library provides `_params_create()` as
  a convenience, but the caller can just as well use:
  - Stack allocation + `_params_init()` (zero overhead, no heap)
  - `aligned_alloc(64, sizeof(*p))` for AVX-512 SIMD alignment
  - A pool allocator for N simultaneous channels
  - `mmap` for persistent state across process restarts

- **Non-opaque struct.** In C you can inspect, copy, serialize, or checkpoint
  the entire state with a single `memcpy`. No getter/setter indirection.

- **Reentrant by construction.** N threads each holding their own
  `fir_pure_params_t *` can call `fir_pure_fn` simultaneously — no shared
  state, no mutex needed.

- **Multi-channel without N separate Python objects.** In a C hot path you can
  hold `params[N_CHANNELS]` on the stack and process all channels in a tight
  loop with a single function pointer.

**Reach for pure struct when:**

- The algorithm has array-shaped working memory (delay lines, coefficient
  tables, history buffers) *and* you want the caller to control where that
  memory lives.
- You are building a multi-channel processor and want N independent channels
  to share a single algorithm implementation without N heap allocations in
  Python.
- You need SIMD-aligned structs and can't accept whatever alignment `malloc`
  gives you.
- You want to checkpoint or restore algorithm state: `memcpy(&saved, p, sizeof(*p))`.
- You are writing a C/C++ host that will pool-allocate many instances.

**Examples:** FIR filters where the caller provides the delay line, matched
filters where the caller owns the reference signal array, delay-line
reverb where room size determines buffer length at construction, any algorithm
whose "state" is semantically data the caller should own.

______________________________________________________________________

## Decision guide

```
Does your algorithm remember anything between calls?
│
├─ No (each output depends only on current input + fixed params)
│  └─ Use  --pure  (scalar style auto-selected when no arrays)
│     e.g. normalize, clip, dB conversion, window evaluation
│
└─ Yes (delay line, accumulator, envelope, phase, history buffer)
   │
   ├─ Is the state conceptually private implementation detail?
   │  The caller shouldn't need to see or own it.
   │  └─ Use stateful (default)
   │     e.g. IIR filter, oscillator, PLL, running stats
   │
   └─ Does the caller need to control allocation, alignment, or
      inspect/copy the state directly?
      │
      ├─ Yes
      │  └─ Use  --pure  with array state (struct style auto-selected)
      │     e.g. FIR with caller-managed delay line, convolution engine
      │
      └─ No — but you want explicit lifecycle + multiple channels
         sharing one algorithm in C
         └─ Also  --pure  struct; or stateful with N instances
            (both work; struct gives caller allocation control)
```

______________________________________________________________________

## Performance notes

All three styles generate the same inner-loop C: a single `_fn()` call per
sample and a `_steps()` loop over an array.  The performance difference is
in **allocation and indirection**, not in computation:

| | Stateful | Pure scalar | Pure struct |
|---|---|---|---|
| Allocation | `malloc` in `_create()` | none | `calloc` in `_params_create()` or caller-controlled |
| Pointer indirection in hot loop | `state->field` | none (direct args or inlined constants) | `params->field` |
| Thread safety | one instance per thread | fully reentrant | one `params_t *` per thread |
| SIMD alignment | library decides | n/a | caller can use `aligned_alloc(64, ...)` |
| Checkpoint / copy | via getters only | n/a | `memcpy` |

For most DSP workloads the bottleneck is arithmetic, not pointer indirection,
and all three styles will compile to equivalent assembly in the hot path.

The meaningful difference is the allocation path (called once, at construction)
and the ownership model (which code manages lifetime and where memory lives).
Choose the model that matches your architecture, not the one you expect to be
"faster" — profile first.

______________________________________________________________________

## Relationship to `--perf`

`--pure` and `--perf` are orthogonal and composable:

```sh
just-makeit init norm --pure --state scale:double:1.0
just-makeit perf   # adds JM_FORCEINLINE JM_HOT to norm_fn
```

`JM_FORCEINLINE` on a scalar pure `_fn()` is especially effective: the
function is already inline in the header, and the hint encourages the compiler
to inline it at every call site in the `_steps()` loop, enabling
auto-vectorization across the entire block.

For struct pure, `--perf` + `JM_DEFINE_STEPS` lets you swap in a
`_step_batch()` that processes a SIMD-width strip per call — the same
three-concern separation (algorithm length, SIMD width, scratch-buffer
chunk size) described in the [performance annotations](perf.md) guide.
