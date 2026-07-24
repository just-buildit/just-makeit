# Scenario 2 — Python package with multiple extensions

*You're here if:* you have several related but independent algorithms —
`Gain`, `EMA`, `Biquad` — and you want them all in one package with separate
`.so` files and full test coverage for each.

Multiple C objects in one project, all accessible from a single Python
package.

## 1. Scaffold the first object

```sh
just-makeit new dsp_toolkit \
    --object gain \
    --arg-type float \
    --return-type float \
    --state gain:float:1.0
cd dsp_toolkit && make
```

## 2. Add a second object

```sh
just-makeit object ema \
    --arg-type float \
    --return-type float \
    --state alpha:double:0.1 \
    --state prev:float:0.0 \
    --mutable
```

`object` writes all C and Python files for the new standalone object and updates:

- root `CMakeLists.txt` — `add_subdirectory` + `target_sources($<TARGET_OBJECTS:…>)`
- umbrella header `native/inc/dsp_toolkit.h` — `#include "ema/ema_core.h"`
- `src/dsp_toolkit/__init__.py` — splices in `from .ema import Ema` and
    adds `"Ema"` to `__all__`, preserving any existing user edits

After adding `ema`, `__init__.py` looks like:

```python
"""dsp_toolkit — Gain."""

from .gain import Gain
from .ema import Ema

__all__ = ["Gain", "Ema"]
```

No manual edits required.

## 3. Implement both objects

`gain_step` (read-only state):

```c
static inline float
gain_step(const gain_state_t *state, float x)
{
    return state->gain * x;
}
```

`ema_step` (writes back to state — drop `const`):

```c
static inline float
ema_step(ema_state_t *state, float x)
{
    float y = (float)state->alpha * x
            + (float)(1.0 - state->alpha) * state->prev;
    state->prev = y;
    return y;
}
```

## 4. Build and test

```sh
make && make test
```

CTest runs `test_gain_core` and `test_ema_core`. pytest runs the full
generated suite for both objects.

## 5. Install

```sh
pip install .
```

The wheel bundles all compiled DSOs (`gain.cpython-*.so`, `ema.cpython-*.so`,
…) alongside the Python package.

## 6. Use from Python

```python
import numpy as np
from dsp_toolkit import Gain, Ema

signal = np.ones(20, dtype=np.float32)

gain = Gain(gain=2.0)
ema  = Ema(alpha=0.3)

for x in signal:
    y = ema.step(gain.step(x))
```

## 7. Add more objects

```sh
just-makeit object dc_block --state r:double:0.995
```

Each `object` repeats the same pattern: new C files, updated CMake, updated
`__init__.py`. `make` picks up the new object automatically.

## 8. Install

```sh
pip install .
```

The wheel bundles the new DSO alongside all existing ones.
