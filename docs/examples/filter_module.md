# filter_module example

A two-type filter library where `Fir` (FIR filter) and `Biquad` (biquad IIR)
share a single Python extension module — one `.so`, one subpackage import.

**Source:** `examples/filter_module/`

---

## What it demonstrates

| Feature | Where |
|---------|-------|
| `just-makeit module` | Step 2 — scaffold an empty module slot |
| `just-makeit object` | Step 3 — add `Fir` and `Biquad` to the module |
| Module regeneration | Each `object` call fully rewrites `filter_ext.c`, CMakeLists, and `__init__.py` |
| Single-import subpackage | `from my_filters.filter import Fir, Biquad` |
| `CHECK` macro C tests | Both objects get failure-counting C tests (no silent assert) |
| `-lm` on test/bench | Auto-linked; math functions work without manual CMake edits |

---

## Key workflow

```sh
just-makeit new my_filters
cd my_filters

just-makeit module filter

just-makeit object fir    --module filter --state "coeffs:float[16]" --state "delay:float _Complex[16]" --state "gain:float:1.0"
just-makeit object biquad --module filter --state "b0:double:1.0" --state "b1:double:0.0" --state "b2:double:0.0" \
                                          --state "a1:double:0.0" --state "a2:double:0.0" \
                                          --state "w1:double:0.0" --state "w2:double:0.0"
```

After these three commands:

```
native/src/
  fir/
    fir_core.h / fir_core.c          ← C library (OBJECT lib, no .so)
    CMakeLists.txt                    ← OBJECT + test + bench targets
  biquad/
    biquad_core.h / biquad_core.c
    CMakeLists.txt
  filter/
    filter_ext.c                      ← FirObject + BiquadObject + PyInit_filter
    CMakeLists.txt                    ← links fir_core + biquad_core → filter.so

src/my_filters/
  filter/
    __init__.py                       ← from .filter import Fir, Biquad
    filter.cpython-*.so               ← built here by CMake
```

Adding a third type later is one command:

```sh
just-makeit object iir --module filter --state "sos:double[20]" --state "zi:double[10]"
```

`filter_ext.c`, `CMakeLists.txt`, and `__init__.py` are regenerated from
scratch.  `Fir` and `Biquad` are untouched.

---

## See also

- [Commands — module](../commands.md#just-makeit-module-name)
- [Commands — object](../commands.md#just-makeit-object-name---module-name---state-nametypedefault----arg-type-type---return-type-type)
- [dsp_toolkit example](dsp_toolkit.md) — standalone objects with `just-makeit object`
