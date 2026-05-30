# `jm object NAME` — processor (input → output, 1:1)

## Customization

**None.** The processor is the unspecialized form of generalist 1 —
the C struct + Python class template (see [gallery overview](index.md)).
What `jm object NAME` produces with no shape flags. The other object
presets (blockwise / generator / consumer / reader) are flag-bundle
specializations of this same template.

A processor takes one input, returns one output, and carries whatever
state your algorithm needs. Inline `step()` for the hot path,
`steps()` for batch processing, getters/setters on every state field,
a full CPython binding, a CTest smoke test, and a Python benchmark —
all generated.

Concrete examples: a DSP filter (FIR/IIR/biquad), a Q15→float
converter, a running-average smoother, a byte-to-token transformer for
a parser, or any 1:1 transform where each output depends on the
current input plus accumulated state.

This page shows the exact output of the current CLI on jm 0.13.23,
using a single-pole low-pass filter as the worked example.

## Command

```sh
jm new my_dsp \
    --object my_filter \
    --arg-type "float _Complex" \
    --return-type "float _Complex" \
    --state gain:float:1.0f
```

## TOML written

The command above writes the component fragment to
`objects/my_filter.toml`. You can equally hand-author the fragment
and run `jm apply` — both paths produce identical files.

```toml
# objects/my_filter.toml
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "false"
no_state = "false"
no_step = "false"

[[state]]
name = "gain"
type = "float"
default = "1.0f"
```

Copying this component to another project = copying
`objects/my_filter.toml` and the `native/inc/my_filter/`,
`native/src/my_filter/`, `src/<pkg>/my_filter.pyi` paths together,
then running `jm apply` in the destination.

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

## Extending the initial command

A component's whole spec lives in the `jm object` command that
creates it — flags compose. Below: base from the Command section
above + three more customizations, in both shell and TOML form.
**NEW** lines are the additions; everything unmarked is the base
preset.

=== "Shell"

    ```sh
    jm object my_filter \
        --arg-type "float _Complex" \
        --return-type "float _Complex" \
        --state gain:float:1.0f \
        --state cutoff:float:0.5 \                  # NEW: extra state field
        --init-param sample_rate:float:48000.0 \    # NEW: ctor param distinct from state
        --perf                                      # NEW: hot-path annotation
    ```

=== "TOML"

    ```toml
    # objects/my_filter.toml
    arg_type = "float _Complex"
    return_type = "float _Complex"
    mutable = "false"
    no_state = "false"
    no_step = "false"
    perf = "true"                # NEW (driven by --perf above)

    [[state]]
    name = "gain"
    type = "float"
    default = "1.0f"

    # NEW: extra state field
    [[state]]
    name = "cutoff"
    type = "float"
    default = "0.5"

    # NEW: init_param distinct from state
    [[init_params]]
    name = "sample_rate"
    type = "float"
    default = "48000.0"
    ```

What each addition contributes:

- `--state cutoff:float:0.5` — another state field; struct member,
    getter, setter, ctor kwarg, and reset assignment, all generated.
- `--init-param sample_rate:float:48000.0` — a ctor parameter that
    isn't a state field. The ctor takes `sample_rate`; state stays
    internal and you initialise it from `sample_rate` directly inside
    `my_filter_create()` in `_core.c`.
- `--perf` — annotates this object's hot-path functions with
    `JM_HOT` / `JM_FORCEINLINE`. (`jm perf` retrofits the whole project
    in one shot.)

Bodies live in `_core.c`. Open the file, replace `/* TODO */` markers
with your logic. There is no flag for lifting bodies from elsewhere —
you edit your code in your file.

### Methods, properties, and variable-output

These are **repeatable structures** — the CLI offers one-off verbs
for adding a single thing; multi-method components are authored in
the TOML fragment directly. (Repeating CLI flags is awful UX; TOML
is the right tool for this.)

=== "One-off via CLI"

    ```sh
    # Add a Python property over a state field
    jm property my_filter cutoff --type float --writable

    # Add a named method
    jm method my_filter analyse --return-type float

    # Add a variable-output method (event emitter)
    jm method my_filter detect \
        --variable-output --max-out 64 \
        --result-field idx:size_t \
        --result-field magnitude:float
    ```

=== "Many at once via TOML"

    ```toml
    # objects/my_filter.toml — append the blocks below
    [[properties]]
    name = "cutoff"
    type = "float"
    writable = true

    [[methods]]
    name = "analyse"
    arg_type = "void"
    return_type = "float"

    [[methods]]
    name = "detect"
    arg_type = "float _Complex[]"
    return_type = "size_t"
    variable_output = true
    max_out = 64

    [[methods.result_fields]]
    name = "idx"
    type = "size_t"

    [[methods.result_fields]]
    name = "magnitude"
    type = "float"
    ```

Either path updates the fragment and regenerates the glue files
(`_core.h`, `_ext.c`, `.pyi`, tests). The sacred `_core.c` is **not**
touched — you add each new method's body to `_core.c` yourself,
following the declaration in `_core.h`. Until you do, the binding
declares the method but no implementation links (clean linker error,
not a silent runtime bug).

### The resulting Python class

After the composed `jm object` command plus the three follow-ups, the
same `MyFilter` Python class exposes:

```python
flt = MyFilter(gain=0.5, cutoff=0.5, sample_rate=48000.0)
flt.cutoff = 0.3                      # property (writable)
energy = flt.analyse()                # custom scalar method
events = flt.detect(buffer)           # variable-output method
y = flt.step(1.0 + 0j)                # original step still works
```

The C surface, the binding, the tests, the bench, and the `.pyi`
stub all stay in sync.

## Concrete types

| Slot                | Accepts                                                                                           | Rejects                                                                                                        | Default           |
| ------------------- | ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ----------------- |
| `--arg-type`        | Any [scalar](../types.md#step-input--output-types).                                               | `const char *`, `void` (routes to [generator](generator.md)), any `T[]` (routes to [blockwise](blockwise.md)). | `float _Complex`  |
| `--return-type`     | Same as `--arg-type`.                                                                             | Same as `--arg-type`; `void` routes to [consumer](consumer.md).                                                | `float _Complex`  |
| `--state field:T:D` | Any [scalar](../types.md#state-variable-types). Fixed arrays `T[N]` also legal but skip the ctor. | `const char *`, `T[]` (use a fixed `T[N]` instead).                                                            | `gain:float:1.0f` |

## When to use a different preset

- Array input → array output → `--blockwise`.
- No input → `--generator`.
- No output → `--consumer`.
- Stateful external resource (file, socket) → `--reader`.
- Variable-output (event emitter) → add `--variable-output --max-out N`
    plus repeatable `--result-field name:T` to any of the above.
