## 5. Build and test

```{05_build.sh}
```

CMake builds one Python extension module (`filter.cpython-*.so`) inside the
`src/my_filters/filter/` subpackage directory.  It links the `filter_core`,
`fir_core` and `biquad_core` OBJECT libraries — no separate `fir.so` or
`biquad.so` anywhere.

CTest runs the two C tests:

```
1/2 Test #1: test_biquad_core .................   Passed    0.00 sec
2/2 Test #2: test_fir_core ....................   Passed    0.00 sec

100% tests passed, 0 tests failed out of 2
```

Both use the `CHECK` macro counter — failures print file/line and exit nonzero
regardless of `-DNDEBUG`.

The installed package layout:

```
src/my_filters/
  __init__.py
  filter/
    __init__.py                             ← from .filter import Fir, Biquad
    filter.cpython-312-x86_64-linux-gnu.so  ← both types in one .so
    filter.pyi                              ← stubs for both types
    tests/                                  ← test_fir.py, test_biquad.py
    benchmarks/                             ← bench_fir.py, bench_biquad.py
```
