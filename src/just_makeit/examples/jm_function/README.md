# jm_function example

Add stateless C functions to a module — no struct, no lifecycle, no state.
Demonstrates both a regular function (its own `.c` file) and a `--inline`
variant (lives entirely in the module header).

## TL;DR — see it work first

```sh
just-makeit example jm_function
# jm_function: PASSED
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

- `just-makeit function` — adding a module-level C function exposed as a
    Python callable with no class, no `self`
- The difference between a **regular function** (sacred `name.c` file) and
    an **`--inline` function** (static inline body in the module header)
- Array parameters (`--param "x:float _Complex[]"`) and scalar parameters
    in the same function
- `--out-type` — allocating a typed output array per call and returning it
    without a pre-allocated buffer

______________________________________________________________________

## 1. Scaffold

```sh
just-makeit new my_utils --module utils
cd my_utils

# Add a stateful gain object so the module has something else in it
just-makeit object gain --module utils \
    --arg-type float --return-type float \
    --state "scale:float:1.0"

# Regular function: own .c file, linker can see it
just-makeit function linear_to_db --module utils \
    --param "x:float _Complex[]" \
    --param "floor:float" \
    --return-type void \
    --out-type float \
    --doc "Convert complex magnitudes to dB, clipped at floor dB."

# Inline function: static inline in utils_core.h, no .c file
just-makeit function clamp --module utils \
    --param "x:float" \
    --param "lo:float" \
    --param "hi:float" \
    --return-type float \
    --inline \
    --doc "Clamp x to [lo, hi]."
```

______________________________________________________________________

## 2. What was created

**`linear_to_db`** (regular function):

```
native/src/utils/linear_to_db.c    ← sacred; implement here
native/inc/utils/utils_core.h      ← declaration injected automatically
```

```c
/* native/src/utils/linear_to_db.c */
#include "utils/utils_core.h"

/* <<IMPLEMENT: linear_to_db>> */
void
linear_to_db(const float complex *x, size_t x_len,
             float floor, float *out)
{
    (void)x; (void)x_len; (void)floor; (void)out;
}
```

**`clamp`** (inline):

```c
/* native/inc/utils/utils_core.h — injected inline */
static inline float
clamp(float x, float lo, float hi)
{
    (void)x; (void)lo; (void)hi;
    return (float)0.0f; /* placeholder */
}
```

______________________________________________________________________

## 3. Implement

**`linear_to_db`:**

```c
void
linear_to_db(const float complex *x, size_t x_len,
             float floor, float *out)
{
    for (size_t i = 0; i < x_len; i++) {
        float mag2 = crealf(x[i]) * crealf(x[i]) + cimagf(x[i]) * cimagf(x[i]);
        float db = (mag2 > 0.0f) ? 10.0f * log10f(mag2) : floor;
        out[i] = (db > floor) ? db : floor;
    }
}
```

**`clamp`:**

```c
static inline float
clamp(float x, float lo, float hi)
{
    if (x < lo) return lo;
    if (x > hi) return hi;
    return x;
}
```

______________________________________________________________________

## 4. Build and use

```sh
make && make test
pip install -e .
```

```python
import numpy as np
from my_utils import utils

# linear_to_db: allocates and returns a float32 output array
signal = (np.random.randn(64) + 1j * np.random.randn(64)).astype(np.complex64)
db = utils.linear_to_db(signal, floor=-80.0)
print(db.dtype, db.shape)   # float32 (64,)

# clamp: scalar in, scalar out
print(utils.clamp(1.5, 0.0, 1.0))   # 1.0
print(utils.clamp(-0.5, 0.0, 1.0))  # 0.0
```

______________________________________________________________________

## Key concepts

**Regular vs inline.** Without `--inline`, each function lives in its own
sacred `.c` file under `native/src/<module>/`. The file is never regenerated
once created — your implementation is safe across any number of `jm apply`
runs. With `--inline`, the body lives in the module header as a `static inline`
— no `.c` file, no link-time symbol, inlined at every call site.

**`--out-type` allocates the output.** The Python wrapper allocates a
NumPy array of the given element type on each call, passes `*out` to C, and
returns the array. The C function writes directly into the buffer — no copy.
The output length equals `len(x) / out_divisor` (default divisor: 1).

**Functions are module-level, not class methods.** They appear as bare callables
(`utils.clamp(...)`, not `obj.clamp(...)`). For per-instance behaviour, use
`jm method` instead.

## See also

- [`jm function` reference](../commands/extend.md#just-makeit-function-name-module-mod-param-nametype-return-type-type-doc-text)
- [Feature tour — Step 4: module function](../feature-tour.md)
- [Template gallery — function](../templates/function.md)
