# `jm object NAME --source` — source (no input; produces samples)

**Status: proposed.** Tracked in
[`developers/wizard-design.md`](../developers/wizard-design.md). The
`--source` flag would bundle `--arg-type void` with a `_core.c`
skeleton sized for generators — NCOs, LFSRs, decoded streams.

## Command

```sh
jm object NAME --source \
    --return-type "float _Complex" \
    --state phase:float:0.0f \
    --state freq:float:0.0f
```

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
