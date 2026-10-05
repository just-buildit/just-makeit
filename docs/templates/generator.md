# `jm object NAME --preset generator` — generator (() → output)

A **generator** produces output without taking input — `step()` takes
no argument and returns the next value, `steps(n)` produces `n`
values. State carries whatever the algorithm needs to advance from one
call to the next.

Concrete examples: a numerically-controlled oscillator (NCO), a
pseudo-random sequence (LFSR), a counter, a UUID generator, a queue
drainer that yields the next item, or a parser tokenizer that emits
tokens one at a time from a pre-loaded buffer.

`--preset generator` expands to `--arg-type void`, which strips the
input side of `step()`. The scaffold builds and tests green straight
away; with no input, `void` arg-type defaults to a complex return.

A generator's whole job is to advance its own state on every call, so it
almost always needs `--mutable` (the default `step()` takes a `const`
state pointer; see [`--mutable`](../commands/scaffold.md)).

## Command

```sh
jm object NAME --preset generator \
    --return-type "float _Complex" \
    --state phase:float:0.0f \
    --state freq:float:0.0f \
    --mutable
```

## What you get

### `native/inc/<pkg>/NAME/NAME_core.h`

Every C symbol carries the component's C stem, `<pkg>_NAME`
(`[project] c_prefix`, which `jm new` defaults to the package name).

```c
typedef struct {
    float phase;
    float freq;
} <pkg>_NAME_state_t;

<pkg>_NAME_state_t *<pkg>_NAME_create(float phase, float freq);
void <pkg>_NAME_destroy(<pkg>_NAME_state_t *state);
void <pkg>_NAME_reset(<pkg>_NAME_state_t *state);
/* plus <pkg>_NAME_get_/set_ for each state field */

/* Per-call generator: emit one sample. */
static inline float _Complex
<pkg>_NAME_step(<pkg>_NAME_state_t *state)
{
    (void)state; /* TODO: implement */
    return (float _Complex)0;
}

/* Block generator: fill n samples into output[]. */
void <pkg>_NAME_steps(<pkg>_NAME_state_t *state, float _Complex *output,
                      size_t n);
```

### `native/src/NAME/NAME_core.c`

```c
void
<pkg>_NAME_steps(<pkg>_NAME_state_t *state, float _Complex *output,
                 size_t n)
{
    for (size_t i = 0; i < n; i++)
        output[i] = <pkg>_NAME_step(state);
}
```

## What you fill in

The `step()` body in `NAME_core.h` — advance state, emit one sample. A
cosine oscillator is typical (add `#include <math.h>` to `NAME_core.h`,
above `step()`, for `cosf`/`sinf`):

```c
static inline float _Complex
<pkg>_NAME_step(<pkg>_NAME_state_t *state)
{
    float _Complex y = cosf(state->phase) + sinf(state->phase) * I;
    state->phase += state->freq;
    return y;
}
```

Other shapes: an LFSR, a Costas loop, a file-decoded sample stream —
whatever produces one sample per call.

## Python usage

```python
import numpy as np
from <pkg> import NAME

src = NAME(phase=0.0, freq=0.01)
y = src.step()                           # → one complex sample
ys = src.steps(1024)                     # → (1024,) complex64
```

## Concrete types

| Slot                | Accepts                                            | Rejects                                                                                                                           | Default                             |
| ------------------- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| `--arg-type`        | Implicit `void`; sources take no input.            | Nothing is refused: an explicit `--arg-type` after `--preset` overrides the preset, and the object is then no longer a generator. | `void`                              |
| `--return-type`     | Any [scalar](../types.md#step-input-output-types). | `const char *`, `void` (use [consumer](consumer.md)); array return unsupported.                                                   | `float _Complex`                    |
| `--state field:T:D` | Any [scalar](../types.md#state-variable-types).    | `const char *`.                                                                                                                   | `phase:float:0.0f, freq:float:0.0f` |

The generator preset always emits a `steps(n)` that fills an `n`-sized
ndarray; the element type matches `--return-type`.
