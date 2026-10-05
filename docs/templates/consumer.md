# `jm object NAME --preset consumer` — consumer (input → ())

A **consumer** takes input but produces no output — `step()` accepts a
sample and returns nothing; state carries whatever the algorithm
accumulates. The user reads the result by inspecting state via
getters or a dedicated method.

Concrete examples: a running mean / variance accumulator, an integrator,
a checksum, a histogram bin counter, a log-line writer that flushes to
disk, a metric reporter that ships samples to a stats system, or any
"fold" over an incoming stream.

`--preset consumer` expands to `--return-type void`, which strips the
output side of `step()`. The scaffold builds and tests green straight
away.

## Command

```sh
jm object NAME --preset consumer \
    --arg-type "float _Complex" \
    --state count:uint64_t:0 \
    --state sum:double:0.0
```

## What you get

### `native/inc/<pkg>/NAME/NAME_core.h`

Every C symbol carries the component's C stem, `<pkg>_NAME`
(`[project] c_prefix`, which `jm new` defaults to the package name).

```c
typedef struct {
    uint64_t count;
    double   sum;
} <pkg>_NAME_state_t;

<pkg>_NAME_state_t *<pkg>_NAME_create(uint64_t count, double sum);
void <pkg>_NAME_destroy(<pkg>_NAME_state_t *state);
void <pkg>_NAME_reset(<pkg>_NAME_state_t *state);

/* Accessors to read (and set) accumulated state. */
uint64_t <pkg>_NAME_get_count(const <pkg>_NAME_state_t *state);
void     <pkg>_NAME_set_count(<pkg>_NAME_state_t *state, uint64_t val);
double   <pkg>_NAME_get_sum(const <pkg>_NAME_state_t *state);
void     <pkg>_NAME_set_sum(<pkg>_NAME_state_t *state, double val);

/* Per-sample consumer. */
static inline void
<pkg>_NAME_step(<pkg>_NAME_state_t *state, float _Complex x)
{
    (void)state; (void)x; /* TODO: implement */
}

/* Block consumer. */
void <pkg>_NAME_steps(<pkg>_NAME_state_t *state,
                      const float _Complex *input, size_t n);
```

### `native/src/NAME/NAME_core.c`

```c
void
<pkg>_NAME_steps(<pkg>_NAME_state_t *state,
                 const float _Complex *input, size_t n)
{
    for (size_t i = 0; i < n; i++)
        <pkg>_NAME_step(state, input[i]);
}
```

## What you fill in

The reducer in `step()`, in `NAME_core.h`. A running-power accumulator is
typical:

```c
static inline void
<pkg>_NAME_step(<pkg>_NAME_state_t *state, float _Complex x)
{
    state->sum += (double)(crealf(x) * crealf(x) + cimagf(x) * cimagf(x));
    state->count++;
}
```

Other common shapes:

- Running sum / mean / RMS (as above).
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

| Slot                | Accepts                                                                                                                                     | Rejects                                                                                                                             | Default                            |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| `--arg-type`        | Any [scalar](../types.md#step-input-output-types).                                                                                          | `const char *`, `void` (use [generator](generator.md)).                                                                             | `float _Complex`                   |
| `--return-type`     | Implicit `void`; sinks produce no output.                                                                                                   | Nothing is refused: an explicit `--return-type` after `--preset` overrides the preset, and the object is then no longer a consumer. | `void`                             |
| `--state field:T:D` | Any [scalar](../types.md#state-variable-types). State carries the running aggregate, so `uint64_t`, `double`, and complex types are common. | `const char *`.                                                                                                                     | `count:uint64_t:0, sum:double:0.0` |

Generated accessors (`get_sum`, `get_count`, etc.) follow the standard
[State variable types](../types.md#state-variable-types) NumPy mapping.
