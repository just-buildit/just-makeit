# `jm object NAME --consumer` — consumer (input → ())

## Customization

```sh
--return-type void
```

The consumer preset is the [object generalist](index.md) with the output
side of `step()` stripped — `step()` accepts a sample and returns
nothing; state carries whatever the algorithm accumulates. Everything
else (state, lifecycle, getters/setters, binding, tests) is identical.
The user reads the result by inspecting state via getters or a
dedicated method.

Concrete examples: a running mean / variance accumulator, an
integrator, a checksum, a histogram bin counter, a log-line writer
that flushes to disk, a metric reporter that ships samples to a stats
system, or any "fold" over an incoming stream.

**Status: proposed `--consumer` shorthand.** Tracked in
[`developers/wizard-design.md`](../developers/wizard-design.md). The
underlying flag (`--return-type void`) works today; the named alias
hasn't shipped yet.

## Command

Works today (the `--consumer` shorthand is Phase 3a; the underlying
flag does the same thing):

```sh
jm new my_dsp --object NAME \
    --arg-type "float _Complex" \
    --return-type void \
    --state count:uint64_t:0 \
    --state sum:double:0.0
```

## TOML written

The command above writes the component fragment to `objects/NAME.toml`.
Hand-author the fragment and `jm apply` — both paths produce identical
files.

```toml
# objects/NAME.toml
arg_type = "float _Complex"
return_type = "void"
mutable = "false"
no_state = "false"
no_step = "false"

[[state]]
name = "count"
type = "uint64_t"
default = "0"

[[state]]
name = "sum"
type = "double"
default = "0.0"
```

## What you get

### `native/inc/NAME/NAME_core.h` (proposed)

```c
typedef struct {
    uint64_t count;
    double   sum;
} NAME_state_t;

NAME_state_t *NAME_create(uint64_t count, double sum);
void          NAME_destroy(NAME_state_t *state);
void          NAME_reset(NAME_state_t *state);

/* Per-sample consumer. */
static inline void
NAME_step(NAME_state_t *state, float complex x);

/* Block consumer. */
void NAME_steps(NAME_state_t *state, const float complex *in, size_t n);

/* Generic accessor to read accumulated state. */
double NAME_get_sum(const NAME_state_t *state);
uint64_t NAME_get_count(const NAME_state_t *state);
```

### `native/src/NAME/NAME_core.c` (proposed)

```c
static inline void
NAME_step(NAME_state_t *state, float complex x)
{
    /* TODO: update internal state. The default body accumulates
       |x|^2 and increments a counter — replace with your reducer. */
    state->sum += (double)(crealf(x) * crealf(x) + cimagf(x) * cimagf(x));
    state->count++;
}

void
NAME_steps(NAME_state_t *state, const float complex *in, size_t n)
{
    for (size_t i = 0; i < n; i++) NAME_step(state, in[i]);
}
```

## What you fill in

The reducer in `step()`. Common shapes:

- Running sum / mean / RMS.
- Histogram bin update.
- Threshold counter ("how many samples above X?").
- Direct write to a file descriptor stored in state.

## Python usage

```python
import numpy as np
from <pkg> import NAME

acc = NAME(count=0, sum=0.0)
acc.steps(np.ones(1024, dtype=np.complex64))
print(acc.get_sum(), acc.get_count())
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
        --arg-type "float _Complex" --return-type void \
        --state count:uint64_t:0 \
        --state sum:double:0.0 \
        --state sum_sq:double:0.0 \                 # NEW: extra state field
        --init-param window:size_t:1024 \           # NEW: ctor param distinct from state
        --perf                                      # NEW: hot-path annotation
    ```

=== "TOML"

    ```toml
    # objects/NAME.toml
    arg_type = "float _Complex"
    return_type = "void"
    mutable = "false"
    no_state = "false"
    no_step = "false"
    perf = "true"                # NEW

    [[state]]
    name = "count"
    type = "uint64_t"
    default = "0"

    [[state]]
    name = "sum"
    type = "double"
    default = "0.0"

    # NEW: extra state field
    [[state]]
    name = "sum_sq"
    type = "double"
    default = "0.0"

    # NEW: init_param distinct from state
    [[init_params]]
    name = "window"
    type = "size_t"
    default = "1024"
    ```

What each addition contributes:

- `--state sum_sq:double:0.0` — another state field; struct member,
    getter, setter, ctor kwarg, and reset assignment, all generated.
- `--init-param window:size_t:1024` — a ctor parameter distinct
    from state; useful for sizing a sliding-window accumulator inside
    `NAME_create()`.
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
    jm property NAME sum --type double --writable
    jm method NAME mean --return-type double
    jm method NAME outliers \
        --variable-output --max-out 64 \
        --result-field idx:size_t \
        --result-field magnitude:float
    ```

=== "Many at once via TOML"

    ```toml
    # objects/NAME.toml — append the blocks below
    [[properties]]
    name = "sum"
    type = "double"
    writable = true

    [[methods]]
    name = "mean"
    arg_type = "void"
    return_type = "double"

    [[methods]]
    name = "outliers"
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
same consumer `NAME` Python class exposes:

```python
acc = NAME(count=0, sum=0.0, sum_sq=0.0, window=1024)
acc.sum = 0.0                       # property (writable)
mu = acc.mean()                     # custom scalar method
events = acc.outliers(buffer)       # variable-output method
acc.steps(np.ones(1024, ...))       # original consumer behaviour
```

The C surface, the binding, the tests, the bench, and the `.pyi`
stub all stay in sync.

## Concrete types

| Slot                | Accepts                                                                                                                                     | Rejects                                                 | Default                            |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- | ---------------------------------- |
| `--arg-type`        | Any [scalar](../types.md#step-input--output-types).                                                                                         | `const char *`, `void` (use [generator](generator.md)). | `float _Complex`                   |
| `--return-type`     | Implicit `void`; sinks produce no output.                                                                                                   | All explicit values — passing one is an error.          | `void`                             |
| `--state field:T:D` | Any [scalar](../types.md#state-variable-types). State carries the running aggregate, so `uint64_t`, `double`, and complex types are common. | `const char *`.                                         | `count:uint64_t:0, sum:double:0.0` |

Generated accessors (`get_sum`, `get_count`, etc.) follow the standard
[State variable types](../types.md#state-variable-types) NumPy mapping.
