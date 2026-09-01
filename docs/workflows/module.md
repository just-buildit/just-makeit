# Scenario 3 — Grouped types in a single subpackage module

*You're here if:* you're building a collection of related filter types —
`Fir`, `Biquad`, `Equalizer` — and you want `from my_filters.filter import Fir, Biquad` rather than a separate top-level import for each.

Use this when multiple related Python types should share one `.so` and import
from a common subpackage path. Each type still has its own independent C
library; the module is the Python grouping unit only.

## 1. Scaffold the project and module together

```sh
just-makeit new my_filters --module filter
cd my_filters
```

`--module` is repeatable — `--module osc --module env` scaffolds two modules
in one command. Each module is an empty slot; types are added with
`just-makeit object`.

Alternatively, scaffold the project first and add the module separately:

```sh
just-makeit new my_filters
cd my_filters
just-makeit module filter
```

## 2. Add types

```sh
just-makeit object fir \
    --module filter \
    --state "coeffs:float[16]" \
    --state "delay:float _Complex[16]" \
    --state "gain:float:1.0"

just-makeit object biquad \
    --module filter \
    --arg-type float \
    --return-type float \
    --state "b0:double:1.0" \
    --state "b1:double:0.0" \
    --state "b2:double:0.0" \
    --state "a1:double:0.0" \
    --state "a2:double:0.0" \
    --state "w1:double:0.0" \
    --state "w2:double:0.0"
```

Each `just-makeit object` call:

- Creates the C library (`_core.h`, `_core.c`, C test, C benchmark)
- Fully regenerates the module's `filter_ext.c`, `CMakeLists.txt`, and `__init__.py`

After both objects:

```python
# src/my_filters/filter/__init__.py — generated
from .filter import Fir, Biquad

__all__ = ["Fir", "Biquad"]
```

Types within a module may have different `--arg-type`/`--return-type`. Here
`Fir` processes `float _Complex` and `Biquad` processes `float`.

## 3. Implement

Edit `native/inc/fir/fir_core.h` and `native/inc/biquad/biquad_core.h` to
fill in the `_step` stubs, exactly as in Scenarios 1 and 2.

## 4. Build and test

```sh
make && make test
```

CMake builds one `.so` (`filter.cpython-*.so`) inside
`src/my_filters/filter/`, linking both `fir_core` and `biquad_core` OBJECT
libraries. CTest runs `test_fir_core` and `test_biquad_core`.

## 5. Install

```sh
pip install .
```

The wheel contains one `.so` for the `filter` subpackage rather than one `.so`
per type.

## 6. Use from Python

```python
import numpy as np
from my_filters.filter import Fir, Biquad

fir = Fir(gain=1.0)
bq  = Biquad(b0=1.0)
```

Both types are fully independent — separate `create`/`destroy` lifecycles,
each with its own `step`, `steps`, `reset`, and context manager support.

## 7. Add a third type later

```sh
just-makeit object iir --module filter --state "gain:float:1.0"
```

`filter_ext.c`, `CMakeLists.txt`, and `__init__.py` are all regenerated from
the complete object list. `Fir` and `Biquad` are unaffected.

## 8. Install

```sh
pip install .
```

The wheel is rebuilt with `iir` included.

## Standalone object vs module object — when to use which

| `just-makeit object` (no `--module`)                 | `just-makeit module` + `just-makeit object --module` |
| ---------------------------------------------------- | ---------------------------------------------------- |
| Each type gets its own `.so`                         | All types share one `.so` subpackage                 |
| `from my_pkg import Gain, Ema`                       | `from my_pkg.filter import Fir, Biquad`              |
| Good for unrelated algorithms                        | Good for a cohesive type family                      |
| Simpler; each type is independent at the `.so` level | One import namespace for the group                   |

Both workflows produce a `lib<project>.so` C library that supports
`cmake --install`, pkg-config, and CMake `find_package`.
