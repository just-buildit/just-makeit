## 5. Build and test

```{05_build.sh}
```

`make` runs cmake configure + compile.  `make test` runs CTest (the C smoke
tests) and then the auto-generated Python integration tests.

The generated C tests in `native/tests/test_acc_f32_core.c` and
`native/tests/test_acc_cf64_core.c` exercise `create`, `reset`, and the
`step`/`steps` round-trip using the `CHECK` macro.

Expected output:

```
[100%] Built target accumulator
Test project /tmp/.../my_acc/build
    Start 1: test_acc_f32_core
1/2 Test #1: test_acc_f32_core ............   Passed
    Start 2: test_acc_cf64_core
2/2 Test #2: test_acc_cf64_core ............   Passed

100% tests passed, 0 tests failed out of 2
```
