# `jm object NAME --sink` — sink (consumes samples; no output)

**Status: proposed.** Tracked in
[`developers/wizard-design.md`](../developers/wizard-design.md). The
`--sink` flag would bundle `--return-type void` with a `_core.c`
skeleton sized for accumulators, integrators, and writers.

## Command

```sh
jm object NAME --sink \
    --arg-type "float _Complex" \
    --state count:uint64_t:0 \
    --state sum:double:0.0
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

## Concrete types

| Slot                | Accepts                                                                                                                                     | Rejects                                           | Default                            |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- | ---------------------------------- |
| `--arg-type`        | Any [scalar](../types.md#step-input--output-types).                                                                                         | `const char *`, `void` (use [source](source.md)). | `float _Complex`                   |
| `--return-type`     | Implicit `void`; sinks produce no output.                                                                                                   | All explicit values — passing one is an error.    | `void`                             |
| `--state field:T:D` | Any [scalar](../types.md#state-variable-types). State carries the running aggregate, so `uint64_t`, `double`, and complex types are common. | `const char *`.                                   | `count:uint64_t:0, sum:double:0.0` |

Generated accessors (`get_sum`, `get_count`, etc.) follow the standard
[State variable types](../types.md#state-variable-types) NumPy mapping.
