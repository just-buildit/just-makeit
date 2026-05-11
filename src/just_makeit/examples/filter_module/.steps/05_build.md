## 5. Build and test

```{05_build.sh}
```

CMake builds one Python extension module (`filter.cpython-*.so`) inside the
`src/my_filters/filter/` subpackage directory.  It links `fir_core` and
`biquad_core` OBJECT libraries — no separate `fir.so` or `biquad.so` anywhere.

CTest runs the two C tests:

```
test_fir_core    PASSED
test_biquad_core PASSED
```

Both use the `CHECK` macro counter — failures print file/line and exit nonzero
regardless of `-DNDEBUG`.

The installed package layout:

```
src/my_filters/
  __init__.py
  filter/
    __init__.py                              ← from .filter import Fir, Biquad
    filter.cpython-312-x86_64-linux-gnu.so  ← both types in one .so
```
