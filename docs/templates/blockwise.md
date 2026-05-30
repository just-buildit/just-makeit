# `jm object NAME --blockwise` — blockwise processor (array → array)

## Customization

```sh
--arg-type "T[]" --return-type "T[]"
```

The blockwise preset is the [object generalist](index.md) with array IO
instead of scalar IO. Every other part of the generated component —
state struct, lifecycle, getters/setters, CPython binding, CTest,
bench — is identical to a processor. The only thing the specialization
changes is the per-sample arg/return types becoming array types, which
in turn drives `step()` into a block-loop shape.

Concrete examples: an FFT, an overlap-save filter, a CSV row
transformer that re-encodes a batch, an image kernel applied across a
row, or any algorithm where the per-element cost is dominated by a
shared setup that you'd rather do once per block.

**Status: proposed `--blockwise` shorthand.** Tracked in
[`developers/wizard-design.md`](../developers/wizard-design.md). The
flag bundle works today; the named alias hasn't shipped yet.

## Command

> **Status: not implemented today.** Array `arg_type` and
> `return_type` are not yet supported by the renderer (verified
> 0.13.23: `KeyError` from `make_sample_ctx` when array types are
> passed even via hand-authored TOML). The flag combination below
> is the **design intent**; Phase 3a ships both the `--blockwise`
> shorthand and the renderer support.

```sh
jm object NAME --blockwise \
    --elem-type "float _Complex" \
    --state gain:float:1.0f

# Equivalent flag form (also Phase 3a):
jm object NAME \
    --arg-type "float _Complex[]" \
    --return-type "float _Complex[]" \
    --state gain:float:1.0f
```

## TOML written (planned)

When the renderer support lands, the command will write this fragment:

```toml
# objects/NAME.toml — design intent, not yet renderable
arg_type = "float _Complex[]"
return_type = "float _Complex[]"
mutable = "false"
no_state = "false"
no_step = "false"

[[state]]
name = "gain"
type = "float"
default = "1.0f"
```

Until then, this page documents the planned output so the design can
be reviewed.

## What you get

### `native/inc/NAME/NAME_core.h` (proposed)

```c
typedef struct {
    float gain;
} NAME_state_t;

NAME_state_t *NAME_create(float gain);
void          NAME_destroy(NAME_state_t *state);
void          NAME_reset(NAME_state_t *state);

void NAME_steps(NAME_state_t       *state,
                const float complex *in, size_t n,
                float complex      *out);
```

There is no inline `step()` — block transforms operate on whole
buffers, so the public surface is `steps()` alone.

### `native/src/NAME/NAME_core.c` (proposed)

```c
void
NAME_steps(NAME_state_t       *state,
           const float complex *in, size_t n,
           float complex      *out)
{
    /* TODO: process n samples from in[] into out[].
       The default body below is a unity-gain pass-through with
       a state->gain multiplier. Replace with your block algorithm. */
    for (size_t i = 0; i < n; i++) {
        out[i] = state->gain * in[i];
    }
}
```

## What you fill in

The body of the `for` loop. Common shapes:

- FIR filter — accumulate into a tap-delay buffer in state, multiply by
    coefficients.
- FFT — call `fftwf_execute` on a pre-planned plan stored in state.
- Block remap — pure data shuffling (deinterleave, complex conjugate,
    swap halves).

### The plan-once-execute-many pattern

Algorithms with heavy per-call setup — pre-built plans, lookup
tables, vendor handles whose definitions stay in C — fit the
blockwise preset cleanly. The heavy work lives in `NAME_create()`;
the hot path stays a `steps()` call. The state struct carries the
plan as an opaque field (see
[opaque state fields](../types.md#opaque-state-fields)).

```sh
jm object NAME \
    --arg-type "float _Complex[]" --return-type "float _Complex[]" \
    --init-param n:size_t
# Declare the opaque field (CLI flag pending — see Phase 3 roadmap):
#   --state plan:fftwf_plan:opaque   (planned, 0.14)
```

Once scaffolded, fill in `_core.c` directly:

```c
NAME_state_t *
NAME_create(size_t n)
{
    NAME_state_t *obj = calloc(1, sizeof(*obj));
    if (!obj) return NULL;
    obj->n       = n;
    obj->scratch = fftwf_alloc_complex(n);
    obj->plan    = fftwf_plan_dft_1d(n, obj->scratch, obj->scratch,
                                     FFTW_FORWARD, FFTW_MEASURE);
    return obj;
}

void
NAME_destroy(NAME_state_t *state)
{
    if (!state) return;
    fftwf_destroy_plan(state->plan);
    fftwf_free(state->scratch);
    free(state);
}

void
NAME_steps(NAME_state_t *state,
           const float _Complex *in, size_t n,
           float _Complex       *out)
{
    /* TODO: copy in→scratch, execute plan, copy scratch→out.
       Hot path — no allocations, no plan rebuilding. */
    memcpy(state->scratch, in, n * sizeof(*in));
    fftwf_execute(state->plan);
    memcpy(out, state->scratch, n * sizeof(*out));
}
```

Same shape works for any heavy-construction algorithm: opaque vendor
handles in state, block-shaped `steps()` calls in the hot path. No
new preset needed.

## Python usage

```python
import numpy as np
from <pkg> import NAME

xform = NAME(gain=1.0)
out = xform.steps(np.ones(1024, dtype=np.complex64))   # → (1024,) complex64
```

## Extending the initial command

A component's whole spec lives in the `jm object` command that
creates it — flags compose. Here's the base above plus four more
customizations:

Base from the Command section above + three more customizations.
**NEW** lines are the additions; everything unmarked is the base
preset. (Both forms remain design intent until Phase 3a — see
Status callout above.)

=== "Shell"

    ```sh
    jm object NAME \
        --arg-type "float _Complex[]" --return-type "float _Complex[]" \
        --state gain:float:1.0f \
        --state cutoff:float:0.5 \                  # NEW: extra state field
        --init-param block_size:size_t:1024 \       # NEW: ctor param distinct from state
        --perf                                      # NEW: hot-path annotation
    ```

=== "TOML"

    ```toml
    # objects/NAME.toml — design intent, not yet renderable
    arg_type = "float _Complex[]"
    return_type = "float _Complex[]"
    mutable = "false"
    no_state = "false"
    no_step = "false"
    perf = "true"                # NEW

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
    name = "block_size"
    type = "size_t"
    default = "1024"
    ```

What each addition contributes:

- `--state cutoff:float:0.5` — another state field; struct member,
    getter, setter, ctor kwarg, and reset assignment, all generated.
- `--init-param block_size:size_t:1024` — a ctor parameter distinct
    from state; useful for sizing scratch buffers inside
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
    jm property NAME cutoff --type float --writable
    jm method NAME analyse --return-type float
    jm method NAME detect \
        --variable-output --max-out 64 \
        --result-field idx:size_t \
        --result-field magnitude:float
    ```

=== "Many at once via TOML"

    ```toml
    # objects/NAME.toml — append the blocks below
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

Either path updates the fragment and regenerates glue. Sacred
`_core.c` is not touched; add each new method's body yourself
following the declaration in `_core.h`.

### The resulting Python class

After the composed `jm object` command plus the three follow-ups, the
same blockwise `NAME` Python class exposes:

```python
xform = NAME(gain=1.0, cutoff=0.5, block_size=1024)
xform.cutoff = 0.3                          # property (writable)
energy = xform.analyse()                    # custom scalar method
events = xform.detect(np.ones(1024, ...))   # variable-output method
out = xform.steps(np.ones(1024, ...))       # original blockwise still works
```

The C surface, the binding, the tests, the bench, and the `.pyi`
stub all stay in sync.

## Concrete types

| Slot                       | Accepts                                                                         | Rejects                                                                                                                    | Default           |
| -------------------------- | ------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| `--elem-type` (per-sample) | Any element type in the [array element table](../types.md#array-element-types). | `bool`, `int` (use `int32_t`), `const char *`, `long double _Complex` — no canonical numpy dtype.                          | `float _Complex`  |
| `--state field:T:D`        | Any [scalar](../types.md#state-variable-types).                                 | `const char *`.                                                                                                            | `gain:float:1.0f` |
| `--return-type`            | Not accepted — blockwise always emits the same element type as `--elem-type`.   | All values. For width-changing transforms (e.g. `float[]` → `int16_t[]`) use a [function](function.md) with `--out-param`. | —                 |
