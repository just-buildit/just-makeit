# delay_line example

A circular delay buffer with runtime-configurable length — the canonical pattern
for heap-allocated state with `create_impl`, `reset_impl`, and `destroy_impl`.

## TL;DR — see it work first

```sh
just-makeit example delay_line
# delay_line: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

______________________________________________________________________

## What it demonstrates

- A **heap-allocated circular buffer** whose size is not a compile-time constant
- All three lifecycle hooks: `create_impl`, `reset_impl`, `destroy_impl`
- Mixing opaque (`float *`) and scalar (`uint32_t`) state fields in one struct
- **`reset_impl` preserving the buffer** — reset zeros samples but keeps the
    allocation, so `length` survives across calls to `obj.reset()`
- The footgun to avoid: calling `set_length()` after construction silently
    leaves the buffer at its original size (use a resize method instead)

______________________________________________________________________

## 1. Write the fragment

```toml
# delay_line.toml
[delay_line]
arg_type     = "float"
return_type  = "float"
mutable      = "true"
create_impl  = """
obj->taps = calloc(length, sizeof(float));
if (!obj->taps) { free(obj); return NULL; }
obj->length = length;
obj->idx    = 0;
"""
reset_impl   = """
memset(state->taps, 0, state->length * sizeof(float));
state->idx = 0;
"""
destroy_impl = "free(state->taps);"

[[delay_line.state]]
name   = "taps"
type   = "float *"
opaque = true

[[delay_line.state]]
name    = "length"
type    = "uint32_t"
default = "64"

[[delay_line.state]]
name    = "idx"
type    = "uint32_t"
default = "0"
```

Key points:

| Field    | Kind   | Scalar default? | Constructor param?           | `reset` restores?         |
| -------- | ------ | --------------- | ---------------------------- | ------------------------- |
| `taps`   | opaque | —               | No                           | Zeroed in place           |
| `length` | scalar | `64`            | Yes                          | Preserved by `reset_impl` |
| `idx`    | scalar | `0`             | No (generated ctor skips it) | Zeroed by `reset_impl`    |

`reset_impl` runs **instead of** the auto-generated field-assignment body, so
it is responsible for everything — zeroing `taps` and resetting `idx`, but
explicitly **not** freeing or reallocating `taps` (the length survives).

______________________________________________________________________

## 2. Apply and build

```sh
just-makeit new delay_demo
cd delay_demo
just-makeit apply ../delay_line.toml
cmake -B build && cmake --build build
ctest --test-dir build
```

______________________________________________________________________

## 3. Implement step()

```c
/* native/inc/delay_line/delay_line_core.h */
static inline float
delay_line_step(delay_line_state_t *state, float x)
{
    /* Write new sample into ring buffer */
    state->taps[state->idx] = x;

    /* Tap at full delay distance */
    uint32_t out_idx = (state->idx + 1) % state->length;
    float y = state->taps[out_idx];

    /* Advance write pointer */
    state->idx = out_idx;
    return y;
}
```

______________________________________________________________________

## 4. Use from Python

```sh
pip install -e .
```

```python
import numpy as np
from delay_demo import DelayLine

# 4-sample delay
dl = DelayLine(length=4)

# Feed 8 impulses, read delayed output
x = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
for s in x:
    print(dl.step(s), end=" ")
# 0.0 0.0 0.0 0.0 1.0 0.0 0.0 0.0
print()

# Reset clears the buffer but preserves length
dl.reset()
assert dl.get_length() == 4     # length survives reset
assert dl.step(1.0) == 0.0      # fresh delay line

# Batch processing
signal = np.zeros(8, dtype=np.float32)
signal[0] = 1.0
out = DelayLine(length=4).steps(signal)
print(out)  # [0. 0. 0. 0. 1. 0. 0. 0.]
```

______________________________________________________________________

## Key concepts

**`reset_impl` owns the reset logic entirely.** When `reset_impl` is present
the auto-generated field-assignment is skipped. That makes it possible to zero
`taps` in place without freeing it — the allocation persists across `reset()`,
keeping `length` valid.

**Opaque fields + scalar fields can coexist.** `taps` is opaque (no
auto-getter, no constructor param); `length` and `idx` are scalar and do get
auto-generated getters and constructor params as normal.

**Don't call `set_length()` to resize.** The auto-generated setter writes the
scalar field but does not touch the `taps` allocation. If you need runtime
resize, expose a custom `resize` method that `realloc`s the buffer and updates
`length` and `idx` atomically. See the pitfall examples in
[Declarative scaffolding — opaque state](../declarative-scaffolding.md#opaque-state-fields-pointers-and-handles).

## See also

- [Declarative scaffolding — opaque state fields](../declarative-scaffolding.md#opaque-state-fields-pointers-and-handles)
- [Declarative scaffolding — custom destroy() body](../declarative-scaffolding.md#custom-destroy-body-destroy_impl)
