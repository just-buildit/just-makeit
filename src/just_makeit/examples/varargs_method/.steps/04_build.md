## 4. Build and test

```{04_build.sh}
```

`filter_configure_core.c` uses `<Python.h>` and compiles into the Python
extension target only, so the C-only CTest binary links without Python.
The two translation units stay cleanly separated: pure-C core in the OBJECT
library, Python-aware binding compiled directly into the DSO.
