# Overview

**just-makeit** turns a CLI description of your algorithm into a complete,
buildable Python C extension — before you write a single line of implementation
code.

______________________________________________________________________

## One command gives you

- C99 core library (`*_core.c` / `*_core.h`) — independently testable,
  usable from any language
- Thin Python binding (`*_ext.c`) — argument parsing and array wrapping only
- CMake build system + GNU Make wrapper
- CTest (C lifecycle) + pytest (Python API) suites, all passing
- NumPy buffer protocol (`steps()`, zero-copy `out=` path)
- Type stubs (`.pyi`) for IDE completion

______________________________________________________________________

## You describe; it generates

| CLI declaration | What you get in C and Python |
|---|---|
| `--state name:type[:default]` | struct field · constructor arg · getter/setter · `reset()` target |
| `--arg-type T` / `--return-type T` | `step()` signature; `void` input = generator, `void` output = sink, `T[]` = buffer-primary (no `steps()`) |
| `method` / `property` / `function` | named execute methods, struct-backed or computed properties, module-level functions |
| `--variable-output` / `--multi-output` | pre-allocated zero-copy batch output; tuple return for parallel streams |
| `perf` | `JM_FORCEINLINE JM_HOT` + `JM_DEFINE_STEPS` SIMD dispatch, non-destructively applied |

______________________________________________________________________

## Two structural modes

**Standalone objects** — each type gets its own `.so`:

```sh
just-makeit new my_project --object engine --state gain:double:1.0
```

```python
from my_project import Engine
engine = Engine(gain=1.0)
y = engine.steps(x)
```

**Module subpackages** — multiple types share one `.so`, one import:

```sh
just-makeit new my_filters --module filter
just-makeit object fir    --module filter --state "coeffs:float[16]"
just-makeit object biquad --module filter --state "b0:double:1.0"
```

```python
from my_filters.filter import Fir, Biquad
```

______________________________________________________________________

## Packaging

The generated project uses
[just-buildit](https://github.com/just-buildit/just-buildit) as its PEP 517
backend — `pip install .`, `pip install -e .`, and `just-makeit build` (wheel)
all work out of the box.
