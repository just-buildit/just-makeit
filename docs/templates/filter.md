# `jm object NAME` — filter (sample → sample)

The default `jm object NAME` invocation produces a sample-by-sample
filter: one complex sample in, one complex sample out, with a single
`gain` state field as a starting point. Inline `step()` for the hot
path, `steps()` for batch processing, getters/setters on every state
field, a full CPython binding, a CTest smoke test, and a Python
benchmark.

This page shows the exact output of the current CLI on jm 0.13.22.

## Command

```sh
jm new my_dsp \
    --object my_filter \
    --arg-type "float _Complex" \
    --return-type "float _Complex" \
    --state gain:float:1.0f
```

## What you get

### `native/inc/my_filter/my_filter_core.h`

```c
#ifndef MY_FILTER_CORE_H
#define MY_FILTER_CORE_H

#include "clib_common.h"

/* state struct — one entry per --state flag */
typedef struct {
    float gain;
} my_filter_state_t;

my_filter_state_t *my_filter_create(float gain);
void               my_filter_destroy(my_filter_state_t *state);
void               my_filter_reset(my_filter_state_t *state);

/* inline step — declared in the header so callers can inline at -O2 */
static inline float complex
my_filter_step(const my_filter_state_t *state, float complex x)
{
    (void)state; /* TODO: implement using state variables */
    return (float complex)x;
}

void my_filter_steps(my_filter_state_t *state,
                     const float complex *input,
                     float complex       *output,
                     size_t               n);

float my_filter_get_gain(const my_filter_state_t *state);
void  my_filter_set_gain(my_filter_state_t *state, float val);

#endif /* MY_FILTER_CORE_H */
```

### `native/src/my_filter/my_filter_core.c`

```c
#include "my_filter/my_filter_core.h"

my_filter_state_t *
my_filter_create(float gain)
{
    my_filter_state_t *obj = calloc(1, sizeof(*obj));
    if (!obj) return NULL;
    obj->gain = gain;
    return obj;
}

void my_filter_destroy(my_filter_state_t *state) { free(state); }
void my_filter_reset(my_filter_state_t *state)   { state->gain = 1.0f; }

void
my_filter_steps(my_filter_state_t *state,
                const float complex *input,
                float complex       *output,
                size_t               n)
{
    for (size_t i = 0; i < n; i++)
        output[i] = my_filter_step(state, input[i]);
}

float my_filter_get_gain(const my_filter_state_t *state) { return state->gain; }
void  my_filter_set_gain(my_filter_state_t *state, float val) { state->gain = val; }
```

### `native/src/my_filter/my_filter_ext.c`

278-line CPython binding (omitted here; open the file in your project).
Covers: object lifecycle (`tp_new`/`tp_dealloc`), arg parsing for
`step(x)` and `steps(arr)` with numpy zero-copy contiguity checks,
`get_gain`/`set_gain` methods, a `reset()` method, a `__repr__`. None
of it is meant to be edited.

### `native/tests/test_my_filter_core.c`

```c
int main(void) {
    int _fails = 0;
    my_filter_state_t *obj = my_filter_create(1.0f);
    CHECK(obj != NULL);

    /* gain: getter / setter */
    CHECK(my_filter_get_gain(obj) == 1.0f);
    my_filter_set_gain(obj, 2.0f);
    CHECK(my_filter_get_gain(obj) == 2.0f);

    /* step: verify it runs without crashing */
    (void)my_filter_step(obj, 0.0f + 0.0f * I);

    /* reset restores defaults */
    my_filter_set_gain(obj, 2.0f);
    my_filter_reset(obj);
    CHECK(my_filter_get_gain(obj) == 1.0f);

    my_filter_destroy(obj);
    return _fails ? 1 : 0;
}
```

### `src/my_dsp/my_filter.pyi`

```python
class MyFilter:
    def __init__(self, gain: np.float32 = 1.0) -> None: ...
    def reset(self) -> None: ...
    def step(self, x: complex) -> complex: ...
    def steps(self, x: NDArray[np.complex64],
              out: NDArray[np.complex64] | None = None) -> NDArray[np.complex64]: ...
    def get_gain(self) -> np.float32: ...
    def set_gain(self, value: np.float32) -> None: ...
```

## What you fill in

One line in `my_filter_step()`. A first-order IIR is typical:

```c
static inline float complex
my_filter_step(const my_filter_state_t *state, float complex x)
{
    return state->gain * x;   /* ← your math here */
}
```

That's the only change you need to make. `steps()` drives `step()` over
an array; the Python binding wraps both. `jm build && jm test` confirms
everything links and runs.

## Python usage

```python
import numpy as np
from my_dsp import MyFilter

flt = MyFilter(gain=0.5)
y = flt.step(1.0 + 0j)              # → 0.5+0j  (after you fill in the body)
ys = flt.steps(np.ones(8, dtype=np.complex64))
flt.reset()
```

## When to use a different preset

- Output count differs from input count → `--block` or `--variable-output`.
- No input (oscillator, generator) → `--source`.
- No output (accumulator, sink) → `--sink`.
- Stateful resource (file, socket) → `--reader`.
- Detector / event emitter → `--detector`.
