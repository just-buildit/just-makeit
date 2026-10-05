## 3. Add the types

```{03_objects.sh}
```

`just-makeit object` does two things for each type:

**Per-object C library** (same as `just-makeit object`, no Python module target):

| File                                   | Purpose                                                                                                       |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `native/inc/my_filters/fir/fir_core.h` | Header: struct, inline `my_filters_fir_step`, getters/setters                                                 |
| `native/src/fir/fir_core.c`            | Source: create/destroy/reset/steps                                                                            |
| `native/src/fir/CMakeLists.txt`        | OBJECT library + C test + bench (no `.so`)                                                                    |
| `native/tests/test_fir_core.c`         | C test with `CHECK` macro counter                                                                             |
| `native/benchmarks/bench_fir_core.c`   | C benchmark                                                                                                   |
| `native/tests/test_fir_symbols.c`      | Links the address of every C function the binding calls (fails at link time if one is declared but undefined) |

It also writes the object's Python test and benchmark
(`src/my_filters/filter/tests/test_fir.py`,
`src/my_filters/filter/benchmarks/bench_fir.py`) and its manifest fragment,
`objects/fir.toml`.

**Module wiring** — each `just-makeit object` then writes or updates:

| File                                                   | What changes                                                                    |
| ------------------------------------------------------ | ------------------------------------------------------------------------------- |
| `native/src/filter/filter_ext_fir.c`                   | Created: the `FirObject` type and its methods                                   |
| `native/src/filter/filter_ext.c`                       | `#include "filter_ext_fir.c"` added, and `PyInit_filter` registers `Fir`        |
| `native/src/filter/CMakeLists.txt`                     | `fir_core` added to the link list                                               |
| `src/my_filters/filter/__init__.py`                    | `from .filter import Fir` added                                                 |
| `src/my_filters/filter/filter.pyi`                     | `class Fir` added                                                               |
| `CMakeLists.txt`, `native/inc/my_filters/my_filters.h` | `native/src/fir` added as a subdirectory, and its header to the umbrella header |

After both objects, `modules/filter.toml`:

```toml
[module.filter]
objects = ["fir", "biquad"]
```

```python
# src/my_filters/filter/__init__.py — generated (Windows DLL-directory shim
# above this line omitted)
from .filter import Fir, Biquad  # noqa: E402

__all__ = ["Fir", "Biquad"]
```

`filter_ext.c` `#include`s `filter_ext_fir.c` and `filter_ext_biquad.c` (one
`FirObject` / `BiquadObject` each), then a single `PyInit_filter` registers
both.

### Fir state

| Name     | Type                 | Default | Role          |
| -------- | -------------------- | ------- | ------------- |
| `coeffs` | `float[16]`          | zeros   | Tap weights   |
| `delay`  | `float _Complex[16]` | zeros   | Input history |
| `gain`   | `float`              | `1.0`   | Output scalar |

### Biquad state (Direct Form II transposed, real-valued)

`Biquad` uses `--arg-type float --return-type float` — real signals, double-precision
arithmetic.  A module can host types with different I/O types; `Fir` is complex,
`Biquad` is real.

| Name | Type     | Default | Role                                        |
| ---- | -------- | ------- | ------------------------------------------- |
| `b0` | `double` | `1.0`   | Feed-forward coefficient                    |
| `b1` | `double` | `0.0`   | Feed-forward coefficient                    |
| `b2` | `double` | `0.0`   | Feed-forward coefficient                    |
| `a1` | `double` | `0.0`   | Feed-back coefficient                       |
| `a2` | `double` | `0.0`   | Feed-back coefficient                       |
| `w1` | `double` | `0.0`   | Delay state (double for numerical headroom) |
| `w2` | `double` | `0.0`   | Delay state                                 |
