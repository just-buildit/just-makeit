## 3. Add a second component

```{03_init.sh}
```

`just-makeit object` adds `ema` alongside `gain` in the same project:

- new C header, source, test, and benchmark under `native/`
- new Python stub, test, and benchmark under `src/dsp_toolkit/`
- umbrella header `native/inc/dsp_toolkit.h` updated with `#include "ema/ema_core.h"`
- root `CMakeLists.txt` updated with `add_subdirectory` and `target_link_libraries`

State:

| Name    | Type     | Default | Role                         |
| ------- | -------- | ------- | ---------------------------- |
| `alpha` | `double` | `0.1`   | Smoothing factor (0 < α < 1) |
| `prev`  | `float`  | `0.0`   | Previous output sample       |
