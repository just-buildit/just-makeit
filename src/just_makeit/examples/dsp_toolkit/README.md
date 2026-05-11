# dsp_toolkit example

A two-component DSP library built with `just-makeit`: a `Gain` component and
an `Ema` (exponential moving average) component.

Follow along to scaffold, implement, and combine them — and see the one place
the generator currently needs a manual touch when you add a second component.

## TL;DR — see it work first

```sh
uvx git+https://github.com/just-buildit/just-makeit install-deps
source /tmp/jm-venv/bin/activate
just-makeit example dsp_toolkit
# dsp_toolkit: PASSED
```

## Prerequisites

```sh
pip install just-makeit
just-makeit install-deps --check   # report what is installed vs. what will be installed
just-makeit install-deps           # install cmake, C compiler, numpy, and create a venv
source /tmp/jm-venv/bin/activate
```

Pass a custom path to keep the venv somewhere persistent:

```sh
just-makeit install-deps ~/my-venv && source ~/my-venv/bin/activate
```

---

## 1. Scaffold

```sh
just-makeit new dsp_toolkit \
    --object gain \
    --arg-type float \
    --return-type float \
    --state "gain:float:1.0"
cd dsp_toolkit && make
```

Scaffolds a single `gain` component with a real-valued `step()`:

| Name   | Type    | Default | Role        |
| ------ | ------- | ------- | ----------- |
| `gain` | `float` | `1.0`   | Scalar gain |

`make` configures CMake and builds the extension. The C test already passes
before you write a single line of logic.

---

## 2. Implement gain

Open `native/inc/gain/gain_core.h` and replace the `gain_step` stub — it's a one-liner:

```c
static inline float
gain_step(const gain_state_t *state, float x)
{
    return state->gain * x;
}
```

`gain_steps()` in `gain_core.c` loops over this automatically — no changes needed there.

---

## 3. Add a second component

```sh
just-makeit object ema \
    --arg-type float \
    --return-type float \
    --state "alpha:double:0.1" \
    --state "prev:float:0.0"
```

`just-makeit object` adds `ema` alongside `gain` in the same project:

- new C header, source, test, and benchmark under `native/`
- new Python stub, test, and benchmark under `src/dsp_toolkit/`
- umbrella header `native/inc/dsp_toolkit.h` updated with `#include "ema/ema_core.h"`
- root `CMakeLists.txt` updated with `add_subdirectory` and `target_link_libraries`

State:

| Name    | Type     | Default | Role                         |
| ------- | -------- | ------- | ---------------------------- |
| `alpha` | `double` | `0.1`   | Smoothing factor (0 < α < 1) |
| `prev`  | `float`  | `0.0`   | Previous output sample       |

---

## 4. Implement ema

`ema_step` must write back to `state->prev`, so the signature drops `const`:

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

The patch script handles both the body replacement and the `const` removal:

```python
"""Patch ema_step stub with the implementation.

EMA writes back to state->prev, so the signature drops `const`:
  const ema_state_t *  ->  ema_state_t *

Run from the project root: python3 .steps/04_patch.py
"""

import pathlib
import re
import sys

header = pathlib.Path("native/inc/ema/ema_core.h")
impl = pathlib.Path(__file__).with_name("04_step_after.c")

stub_re = re.compile(
    r"(static inline|JM_FORCEINLINE JM_HOT) float\s*\n"
    r"ema_step\((?:const )?ema_state_t \*state.*?\n\}",
    re.DOTALL,
)

text = header.read_text()
m = stub_re.search(text)
if not m:
    print("ERROR: stub not found — already patched or file changed", file=sys.stderr)
    sys.exit(1)

qualifier = m.group(1)
# The impl already has the non-const signature; sub replaces the whole match.
replacement = impl.read_text().strip().replace("static inline", qualifier, 1)
patched = stub_re.sub(replacement, text)
header.write_text(patched)
print(f"patched {header}")
```

---

## 5. Both components are exported automatically

After `just-makeit object`, `__init__.py` is updated in-place — the new import
and `__all__` entry are spliced in without touching anything else:

```python
"""dsp_toolkit — Gain component."""

from .gain import Gain
from .ema import Ema

__all__ = ["Gain", "Ema"]
```

Existing imports and any user additions are preserved.

---

## 6. Build and test

```sh
make && make test
```

CTest runs `test_gain_core` and `test_ema_core`.
pytest runs the 14 generated tests across both components.

---

## 7. Use from Python

```python
"""Demo: use Gain and Ema together from Python."""

import sys
sys.path.insert(0, "src")

import numpy as np
from dsp_toolkit import Gain, Ema

# A short burst followed by silence
signal = np.ones(20, dtype=np.float32)
signal[10:] = 0.0

gain = Gain(gain=2.0)
ema  = Ema(alpha=0.3)

print(f"{'n':>3}  {'input':>7}  {'gained':>7}  {'smoothed':>10}")
print("-" * 36)
for i, x in enumerate(signal):
    y_gain = gain.step(x)
    y_ema  = ema.step(float(y_gain))
    print(f"{i:>3}  {x:>7.3f}  {y_gain:>7.3f}  {y_ema:>10.4f}")
```

`Gain` and `Ema` are independent stateful objects — chain them however you need.
`steps()` is also available for block processing:

```python
y = Gain(gain=2.0).steps(signal)   # returns float32 ndarray
```

---

## 8. Use from Python

```python
"""Demo: use Gain and Ema together from Python."""

import sys
sys.path.insert(0, "src")

import numpy as np
from dsp_toolkit import Gain, Ema

# A short burst followed by silence
signal = np.ones(20, dtype=np.float32)
signal[10:] = 0.0

gain = Gain(gain=2.0)
ema  = Ema(alpha=0.3)

print(f"{'n':>3}  {'input':>7}  {'gained':>7}  {'smoothed':>10}")
print("-" * 36)
for i, x in enumerate(signal):
    y_gain = gain.step(x)
    y_ema  = ema.step(float(y_gain))
    print(f"{i:>3}  {x:>7.3f}  {y_gain:>7.3f}  {y_ema:>10.4f}")
```

`Gain` and `Ema` are independent stateful objects — chain them however you need.
`steps()` is also available for block processing:

```python
y = Gain(gain=2.0).steps(signal)   # returns float32 ndarray
```
