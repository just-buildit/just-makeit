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

### `native/inc/NAME/NAME_core.h`

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

### `native/src/NAME/NAME_core.c`

```c
static inline float complex
NAME_step(NAME_state_t *state)
{
    (void)state; /* TODO: implement */
    return (float complex)0;
}

void
NAME_steps(NAME_state_t *state, float complex *out, size_t n)
{
    for (size_t i = 0; i < n; i++) out[i] = NAME_step(state);
}
```

## What you fill in

The `step()` body — advance state, emit one sample. A cosine oscillator
is typical (add `#include <math.h>` to `NAME_core.c` for `cosf`/`sinf`):

```c
static inline float complex
NAME_step(NAME_state_t *state)
{
    float complex y = cosf(state->phase) + sinf(state->phase) * I;
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

| Slot                | Accepts                                            | Rejects                                                                         | Default                             |
| ------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------- | ----------------------------------- |
| `--arg-type`        | Implicit `void`; sources take no input.            | All explicit values — passing one is an error.                                  | `void`                              |
| `--return-type`     | Any [scalar](../types.md#step-input-output-types). | `const char *`, `void` (use [consumer](consumer.md)); array return unsupported. | `float _Complex`                    |
| `--state field:T:D` | Any [scalar](../types.md#state-variable-types).    | `const char *`.                                                                 | `phase:float:0.0f, freq:float:0.0f` |

The generator preset always emits a `steps(n)` that fills an `n`-sized
ndarray; the element type matches `--return-type`.
