# `jm object NAME --detector` — detector (variable-output events)

**Status: proposed.** Tracked in
[`developers/wizard-design.md`](../developers/wizard-design.md). The
`--detector` flag would bundle `--variable-output`, register a
`--max-out N` companion that fills in the `_max_out()` function, and
generate a `_core.c` skeleton sized for event emitters — peak
detectors, threshold crossings, packet boundaries.

This preset would replace the multi-step TOML dance that variable-output
methods require today.

## Command

```sh
jm object NAME --detector \
    --arg-type "float _Complex" \
    --max-out 1024 \
    --state threshold:float:0.5f
```

## What you get

### `native/inc/NAME/NAME_core.h` (proposed)

```c
typedef struct {
    float threshold;
} NAME_state_t;

/* A simple event record. The wizard would let you customise the
   result struct via repeatable --result-field flags. */
typedef struct {
    size_t  sample_index;
    float   magnitude;
} NAME_event_t;

NAME_state_t *NAME_create(float threshold);
void          NAME_destroy(NAME_state_t *state);
void          NAME_reset(NAME_state_t *state);

/* Worst-case event count for the next NAME_detect() call. */
size_t NAME_detect_max_out(NAME_state_t *state);

/* Scan in[0..n_in-1]; emit detected events into out[]; return count. */
size_t NAME_detect(NAME_state_t           *state,
                   const float complex    *in, size_t n_in,
                   NAME_event_t           *out);
```

### `native/src/NAME/NAME_core.c` (proposed)

```c
size_t
NAME_detect_max_out(NAME_state_t *state)
{
    (void)state;
    return 1024;   /* TODO: tune to your worst-case per-call event count. */
}

size_t
NAME_detect(NAME_state_t        *state,
            const float complex *in, size_t n_in,
            NAME_event_t        *out)
{
    /* TODO: scan in[0..n_in-1], emit events into out[].
       Return the number of events found.
       The default body finds samples whose magnitude exceeds
       state->threshold and records the index + magnitude. */
    size_t n_out = 0;
    size_t cap   = NAME_detect_max_out(state);
    for (size_t i = 0; i < n_in && n_out < cap; i++) {
        float m = crealf(in[i]) * crealf(in[i])
                + cimagf(in[i]) * cimagf(in[i]);
        if (m > state->threshold * state->threshold) {
            out[n_out].sample_index = i;
            out[n_out].magnitude    = sqrtf(m);
            n_out++;
        }
    }
    return n_out;
}
```

## What you fill in

- The `_max_out()` upper bound — pick a number that's safely larger
    than your worst case per call.
- The detection rule itself — replace the threshold-and-magnitude
    placeholder with your matched filter, peak finder, or
    packet-boundary logic.

The Python binding allocates a buffer sized to `_max_out()` once and
reuses it across calls (zero-copy view returned to Python on each
call), so this preset is malloc-free in the hot path.

## Python usage

```python
import numpy as np
from <pkg> import NAME

det = NAME(threshold=0.5)
events = det.detect(np.random.randn(8192).astype(np.complex64) * (1 + 1j))
# events is a structured ndarray with fields sample_index and magnitude
```

## Concrete types

| Slot                               | Accepts                                                                                                                                     | Rejects                                                                                                                     | Default                                |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| `--arg-type` (input stream)        | Any [array element type](../types.md#array-element-types).                                                                                  | `bool`, `int`, `const char *`, `long double _Complex`, `void` (use a [source](source.md) preset for self-driven detection). | `float _Complex`                       |
| `--max-out N`                      | Any positive integer literal — the worst-case events-per-call upper bound.                                                                  | Non-integer values, zero, runtime expressions.                                                                              | `1024`                                 |
| `--state field:T:D`                | Any [scalar](../types.md#state-variable-types). Threshold/parameter state lives here.                                                       | `const char *`.                                                                                                             | `threshold:float:0.5f`                 |
| `--result-field name:T` (proposed) | Each field of the event struct is one [scalar](../types.md#state-variable-types). Common shape: `sample_index:size_t` + one numeric metric. | `T[]`, `T[N]`, `const char *`, nested structs. The record is a flat C struct → numpy structured dtype.                      | `sample_index:size_t, magnitude:float` |

**Gap.** The event struct (`NAME_event_t`) is user-defined and has no
first-class registry support yet — today it lives only inside the
generated C and the Python binding turns it into a numpy structured
dtype. The `--result-field` flag is the proposed CLI surface; the
TOML equivalent is `result_fields = [{name, type}, ...]` (see
[method param types](../types.md#method-param-types)).
