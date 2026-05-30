# `jm object NAME --generator` — generator (() → output)

## Customization

```sh
--arg-type void
```

The generator preset is the [object generalist](index.md) with the input
side of `step()` stripped — `step()` takes no argument and returns the
next value, `steps(n)` produces `n` values. Everything else (state,
lifecycle, getters/setters, binding, tests) is identical. State
carries whatever the algorithm needs to advance from one call to the
next.

Concrete examples: a numerically-controlled oscillator (NCO), a
pseudo-random sequence (LFSR), a counter, a UUID generator, a queue
drainer that yields the next item, or a parser tokenizer that emits
tokens one at a time from a pre-loaded buffer.

**Status: proposed `--generator` shorthand.** Tracked in
[`developers/wizard-design.md`](../developers/wizard-design.md). The
underlying flag (`--arg-type void`) works today; the named alias
hasn't shipped yet.

## Command

Works today (the `--generator` shorthand is Phase 3a; the underlying
flags do the same thing):

```sh
jm new my_dsp --object NAME \
    --arg-type void \
    --return-type "float _Complex" \
    --state phase:float:0.0f \
    --state freq:float:0.01f
```

## TOML written

The command above writes the component fragment to `objects/NAME.toml`.
You can equally hand-author the fragment and run `jm apply` — both
paths produce identical files.

```toml
# objects/NAME.toml
arg_type = "void"
return_type = "float _Complex"
mutable = "false"
no_state = "false"
no_step = "false"

[[state]]
name = "phase"
type = "float"
default = "0.0f"

[[state]]
name = "freq"
type = "float"
default = "0.01f"
```

Copying this component to another project = copying
`objects/NAME.toml` and the `native/inc/NAME/`, `native/src/NAME/`,
`src/<pkg>/NAME.pyi` paths together, then running `jm apply` in the
destination.

## What you get

### `native/inc/NAME/NAME_core.h` (proposed)

```c
typedef struct {
    float phase;
    float freq;
} NAME_state_t;

NAME_state_t *NAME_create(float phase, float freq);
void          NAME_destroy(NAME_state_t *state);
void          NAME_reset(NAME_state_t *state);

/* Per-call generator: emit one sample. */
static inline float complex
NAME_step(NAME_state_t *state);

/* Block generator: fill n samples into out[]. */
void NAME_steps(NAME_state_t *state, float complex *out, size_t n);
```

### `native/src/NAME/NAME_core.c` (proposed)

```c
static inline float complex
NAME_step(NAME_state_t *state)
{
    /* TODO: advance state, emit one sample. The default body is a
       trivial cosine oscillator — replace with your generator. */
    float complex y = cosf(state->phase) + sinf(state->phase) * I;
    state->phase += state->freq;
    return y;
}

void
NAME_steps(NAME_state_t *state, float complex *out, size_t n)
{
    for (size_t i = 0; i < n; i++) out[i] = NAME_step(state);
}
```

## What you fill in

The `step()` body. Replace the cosine placeholder with your generator —
LFSR, Costas loop, file-decoded sample stream, whatever produces one
sample per call.

## Python usage

```python
import numpy as np
from <pkg> import NAME

src = NAME(phase=0.0, freq=0.01)
y = src.step()                           # → one complex sample
ys = src.steps(1024)                     # → (1024,) complex64
```

## Extending the initial command

A component's whole spec lives in the `jm object` command that
creates it — flags compose. Here's the base above plus four more
customizations:

Base from the Command section above + three more customizations.
**NEW** lines are the additions; everything unmarked is the base
preset.

=== "Shell"

    ```sh
    jm object NAME \
        --arg-type void --return-type "float _Complex" \
        --state phase:float:0.0f \
        --state freq:float:0.01f \
        --state amplitude:float:1.0f \              # NEW: extra state field
        --init-param sample_rate:float:48000.0 \    # NEW: ctor param distinct from state
        --perf                                      # NEW: hot-path annotation
    ```

=== "TOML"

    ```toml
    # objects/NAME.toml
    arg_type = "void"
    return_type = "float _Complex"
    mutable = "false"
    no_state = "false"
    no_step = "false"
    perf = "true"                # NEW

    [[state]]
    name = "phase"
    type = "float"
    default = "0.0f"

    [[state]]
    name = "freq"
    type = "float"
    default = "0.01f"

    # NEW: extra state field
    [[state]]
    name = "amplitude"
    type = "float"
    default = "1.0f"

    # NEW: init_param distinct from state
    [[init_params]]
    name = "sample_rate"
    type = "float"
    default = "48000.0"
    ```

What each addition contributes:

- `--state amplitude:float:1.0f` — another state field; struct
    member, getter, setter, ctor kwarg, and reset assignment, all
    generated.
- `--init-param sample_rate:float:48000.0` — a ctor parameter
    distinct from state; you compute the per-call increment from it
    inside `NAME_create()` in `_core.c`.
- `--perf` — annotates this object's hot-path functions with
    `JM_HOT` / `JM_FORCEINLINE`. (`jm perf` retrofits the whole project
    at once.)

Bodies live in `_core.c`. Open the file, replace `/* TODO */` markers
with your logic. There is no flag for lifting bodies from elsewhere.

### Methods, properties, and variable-output

These are **repeatable structures**. CLI for one-offs; TOML for
multi-method components.

=== "One-off via CLI"

    ```sh
    jm property NAME freq --type float --writable
    jm method NAME peek --return-type float
    jm method NAME mark \
        --variable-output --max-out 64 \
        --result-field idx:size_t \
        --result-field magnitude:float
    ```

=== "Many at once via TOML"

    ```toml
    # objects/NAME.toml — append the blocks below
    [[properties]]
    name = "freq"
    type = "float"
    writable = true

    [[methods]]
    name = "peek"
    arg_type = "void"
    return_type = "float"

    [[methods]]
    name = "mark"
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

Either path updates the fragment and regenerates glue. Sacred
`_core.c` is not touched; add each new method's body yourself
following the declaration in `_core.h`.

### The resulting Python class

After the composed `jm object` command plus the three follow-ups, the
same generator `NAME` Python class exposes:

```python
src = NAME(phase=0.0, freq=0.01, amplitude=1.0, sample_rate=48000.0)
src.freq = 0.02                # property (writable)
phase = src.peek()             # custom scalar method
events = src.mark(buffer)      # variable-output method
y = src.step()                 # original step() still works
ys = src.steps(1024)           # original steps(n) still works
```

The C surface, the binding, the tests, the bench, and the `.pyi`
stub all stay in sync.

## Concrete types

| Slot                | Accepts                                             | Rejects                                                                                                             | Default                             |
| ------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| `--arg-type`        | Implicit `void`; sources take no input.             | All explicit values — passing one is an error.                                                                      | `void`                              |
| `--return-type`     | Any [scalar](../types.md#step-input--output-types). | `const char *`, `void` (use [consumer](consumer.md)), any `T[]` (blockwise-shaped — use [blockwise](blockwise.md)). | `float _Complex`                    |
| `--state field:T:D` | Any [scalar](../types.md#state-variable-types).     | `const char *`.                                                                                                     | `phase:float:0.0f, freq:float:0.0f` |

The source preset always emits a `steps(n)` that fills an `n`-sized
ndarray; the element type matches `--return-type`.
