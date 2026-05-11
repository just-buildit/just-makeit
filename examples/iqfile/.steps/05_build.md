## 5. Build and test

```{05_build.sh}
```

`make` configures CMake and builds the `conv` extension module.
`make test` runs CTest (C lifecycle tests) and pytest (Python API tests)
for both `Cf32ToQ15` and `Q15ToCf32`.
