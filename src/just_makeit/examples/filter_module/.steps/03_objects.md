## 3. Add the types

```{03_objects.sh}
```

`just-makeit object` does two things for each type:

**Per-object C library** (same as `just-makeit object`, no Python module target):

| File                                 | Purpose                                            |
| ------------------------------------ | -------------------------------------------------- |
| `native/inc/fir/fir_core.h`          | Header: struct, inline `fir_step`, getters/setters |
| `native/src/fir/fir_core.c`          | Source: create/destroy/reset/steps                 |
| `native/src/fir/CMakeLists.txt`      | OBJECT library + C test + bench (no `.so`)         |
| `native/tests/test_fir_core.c`       | C test with `CHECK` macro counter                  |
| `native/benchmarks/bench_fir_core.c` | C benchmark                                        |

**Module regeneration** — after each `just-makeit object`, these are fully rewritten:

| File                                | What changes                                          |
| ----------------------------------- | ----------------------------------------------------- |
| `native/src/filter/filter_ext.c`    | `FirObject` type added; `PyMODINIT_FUNC` registers it |
| `native/src/filter/CMakeLists.txt`  | `fir_core` added to link list                         |
| `src/my_filters/filter/__init__.py` | `from .filter import Fir` added                       |

After both objects:

```toml
[module.filter]
objects = ["fir", "biquad"]
```

```python
# src/my_filters/filter/__init__.py — generated
from .filter import Fir, Biquad

__all__ = ["Fir", "Biquad"]
```

`filter_ext.c` contains both `FirObject` and `BiquadObject` type definitions
followed by a single `PyInit_filter` that registers both.

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
