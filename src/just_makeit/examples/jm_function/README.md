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

Or with `pip`, which installs just-makeit and then builds the toolchain
venv at `/tmp/jm-venv`:

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
- Scalar parameters and a scalar return, exposed as a bare Python callable
- **Document once, in C** — a Doxygen comment on the declaration becomes the
    generated `.pyi` docstring, and a `@code` block becomes a runnable doctest

______________________________________________________________________

## 1. Scaffold

```sh
just-makeit new my_utils --module utils
cd my_utils

# Add a stateful gain object so the module has something else in it
just-makeit object gain --module utils \
    --arg-type float --return-type float \
    --state "gain:float:1.0"

# Regular function: own .c file, linker can see it
just-makeit function linear_to_db --module utils \
    --param "x:float" \
    --return-type float \
    --doc "Convert linear amplitude to dB (20*log10(x))."

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
native/inc/my_utils/utils/utils_core.h      ← declaration injected automatically
```

```c
/* native/src/utils/linear_to_db.c */
#include "my_utils/utils/utils_core.h"

/* <<IMPLEMENT: my_utils_linear_to_db>> */
float
my_utils_linear_to_db(float x)
{
    (void)x;
    return (float)0.0f; /* placeholder */
}
```

**`clamp`** (inline):

```c
/* native/inc/my_utils/utils/utils_core.h — injected inline */
/* <<IMPLEMENT: my_utils_clamp>> */
static inline float
my_utils_clamp(float x, float lo, float hi)
{
    (void)x; (void)lo; (void)hi;
    return (float)0.0f; /* placeholder */
}
```

______________________________________________________________________

## 3. Implement

**`linear_to_db`** (add `#include <math.h>` for `log10f`):

```c
float
my_utils_linear_to_db(float x)
{
    return 20.0f * log10f(x > 0.0f ? x : 1e-10f);
}
```

**`clamp`:**

```c
static inline float
my_utils_clamp(float x, float lo, float hi)
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
from my_utils.utils import linear_to_db, clamp

# linear_to_db: scalar in, scalar out
print(linear_to_db(10.0))   # 20.0
print(linear_to_db(1.0))    # 0.0

# clamp: scalar in, scalar out
print(clamp(1.5, 0.0, 1.0))    # 1.0
print(clamp(-0.5, 0.0, 1.0))   # 0.0
```

______________________________________________________________________

## 5. Document once, in C — rich stubs and runnable doctests

The sacred header is also the single source of truth for **documentation**. A
Doxygen `/** ... */` comment on a function's declaration flows straight into
the generated `.pyi` docstring, and a `@code` block becomes a **runnable
doctest**. Free functions are an ideal home for doctests — they take plain
scalars and return plain scalars, so the `>>>` lines read like ordinary
Python. Add a comment above the `my_utils_linear_to_db` declaration in
`native/inc/my_utils/utils/utils_core.h`:

```c
/**
 * @brief Convert linear amplitude to dB (20*log10(x)).
 * @param x  Linear amplitude (must be > 0).
 * @return The amplitude expressed in decibels.
 * @code
 * >>> from my_utils.utils import linear_to_db
 * >>> linear_to_db(1.0)
 * 0.0
 * >>> linear_to_db(10.0)
 * 20.0
 * @endcode
 */
float my_utils_linear_to_db(float x);
```

`jm apply` re-derives the stub, and `src/my_utils/utils/utils.pyi` now carries
the full numpy-style docstring — including the `@code` block as an `Examples`
doctest:

```python
def linear_to_db(x: float) -> float:
    """Convert linear amplitude to dB (20*log10(x)).

    Parameters
    ----------
    x : float
        Linear amplitude (must be > 0).

    Returns
    -------
    float
        The amplitude expressed in decibels.

    Examples
    --------
    >>> from my_utils.utils import linear_to_db
    >>> linear_to_db(1.0)
    0.0
    >>> linear_to_db(10.0)
    20.0

    """
```

That doctest is not decoration: run against the *built* extension, it fails
the moment the kernel drifts from its documented example. A generated
project's `make test` does not run `.pyi` doctests (this example's own test
does), so to make them a gate in your project add
`PYTHONPATH=src python -m pytest --doctest-glob='*.pyi' src/` to your test
step. Pass `-v` to watch every `>>>` line execute (the first six are the
scaffold's own examples on `Gain`):

```termynal
$ PYTHONPATH=src python -m doctest -v src/my_utils/utils/utils.pyi
{d}...{/d}
{d}Trying:{/d}
    linear_to_db(1.0)
{d}Expecting:{/d}
    0.0
{g}ok{/g}
{d}Trying:{/d}
    linear_to_db(10.0)
{d}Expecting:{/d}
    20.0
{g}ok{/g}
{d}Trying:{/d}
    clamp(5.0, 0.0, 3.0)
{d}Expecting:{/d}
    3.0
{g}ok{/g}
{d}...{/d}
{g}13 passed and 0 failed.{/g}
{g}Test passed.{/g}
```

The same treatment applies to the inline `clamp` — the Doxygen sits above its
`static inline` definition. This example's test drives the whole stub at once
with `pytest --doctest-glob='*.pyi'`.

______________________________________________________________________

## Key concepts

**Regular vs inline.** Without `--inline`, each function lives in its own
sacred `.c` file under `native/src/<module>/`. The file is never regenerated
once created — your implementation is safe across any number of `jm apply`
runs. With `--inline`, the body lives in the module header as a `static inline`
— no `.c` file, no link-time symbol, inlined at every call site.

**Document once, in C.** A Doxygen comment on the function declaration is the
single source of truth for its Python docstring: `@brief`/`@param`/`@return`
render as numpy-style prose and a `@code` block becomes a runnable doctest.
`jm apply` re-derives the `.pyi` from the header, and
`pytest --doctest-glob='*.pyi'` executes every `>>>` against the built
extension. This example's test runs it; a generated project does not by
default.

**Functions are module-level, not class methods.** They appear as bare callables
(`utils.clamp(...)`, not `obj.clamp(...)`). For per-instance behaviour, use
`jm method` instead.

## See also

- [`jm function` reference](../commands/extend.md#just-makeit-function)
- [Feature tour — Step 4: module function](../feature-tour.md)
- [Template gallery — function](../templates/function.md)
