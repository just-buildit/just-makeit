# Generated artifacts

Five scaffold patterns in order of increasing complexity. Each section shows
the exact commands that produce the layout, then the complete file tree with
one-line annotations.

Files omitted from all trees for brevity: `README.md`, `.gitignore`,
`.gitattributes`, `benchmarks/history/.gitkeep`, `Doxyfile`,
`zensical.toml`, `docs/index.md`, `docs/api.md`, `bootstrap.toml`,
`cmake/<project>-config.cmake.in`. Every project also defaults to the
split-fragment layout (see
[Declarative scaffolding](declarative-scaffolding.md)): each object/module
gets its own `objects/<name>.toml` / `modules/<name>.toml` fragment file
alongside `just-makeit.toml`, also omitted below.

______________________________________________________________________

## 1. Module function

A module-level C function with no type object. Smallest possible extension:
one `.so`, one callable, no lifecycle.

```sh
just-makeit new mylib --module dsp
just-makeit function compute --module dsp \
    --param x:float --param n:int32_t --return-type float
```

```
mylib/
├── native/
│   ├── benchmarks/
│   │   ├── bench_dsp_core.c        # C benchmark for the module's functions
│   │   └── jm_bench.h
│   ├── inc/
│   │   └── mylib/
│   │       ├── clib_common.h       # shared C99 type aliases
│   │       ├── pyex_common.h       # Python/NumPy includes
│   │       ├── mylib.h             # umbrella header
│   │       └── dsp/
│   │           └── dsp_core.h      # jm declares mylib_compute() here
│   ├── src/
│   │   ├── mylib_lib.c             # version symbol
│   │   └── dsp/
│   │       ├── CMakeLists.txt
│   │       ├── dsp_core.c          # module boilerplate; #includes dsp_core.h
│   │       ├── compute.c           # C implementation ← write compute() here
│   │       └── dsp_ext.c           # Python binding (auto-generated)
│   └── tests/
│       ├── jm_test.h
│       └── test_dsp_core.c         # CTest
├── cmake/
│   └── mylib.pc.in                 # pkg-config template
├── src/
│   └── mylib/
│       ├── __init__.py
│       └── dsp/
│           ├── __init__.py         # from .dsp import compute
│           └── dsp.pyi             # type stub for dsp.so
├── CMakeLists.txt
├── .clang-tidy                 # clang-tidy config; `make tidy` runs it
├── CMakePresets.json           # IDE configure presets (see Building on Windows)
├── Makefile
├── pyproject.toml
└── just-makeit.toml
```

Every module function gets its own `.c` file by default, so multiple
functions in the same module compile as separate translation units. Pass
`--functions-in-core` on `just-makeit module`/`new --module` to keep them all
in `dsp_core.c` instead (one TU, shared static helpers).

______________________________________________________________________

## 2. Standalone object

A single type with its own `.so`. Full lifecycle: create/destroy/reset,
step/steps, getters/setters, C test, Python test, benchmark.

```sh
just-makeit new mylib --object engine --state gain:double:1.0
```

```
mylib/
├── native/
│   ├── benchmarks/
│   │   ├── bench_engine_core.c     # C-level microbenchmark
│   │   └── jm_bench.h              # shared per-round timing/stats helpers
│   ├── inc/
│   │   └── mylib/
│   │       ├── clib_common.h
│   │       ├── pyex_common.h
│   │       ├── mylib.h             # umbrella header
│   │       └── engine/
│   │           └── engine_core.h   # public C API + inline step()
│   ├── src/
│   │   ├── mylib_lib.c
│   │   └── engine/
│   │       ├── CMakeLists.txt
│   │       ├── engine_core.c       # lifecycle + algorithm ← implement here
│   │       └── engine_ext.c        # thin Python binding (auto-regenerated)
│   └── tests/
│       ├── jm_test.h               # shared CHECK/REQUIRE + epilogue
│       ├── test_engine_core.c      # CTest — exercises C API directly
│       └── test_engine_symbols.c   # derived link check
├── cmake/
│   └── mylib.pc.in
├── src/
│   └── mylib/
│       ├── __init__.py
│       ├── engine.pyi              # type stub for engine.so
│       ├── benchmarks/
│       │   ├── __init__.py
│       │   └── bench_engine.py     # Python benchmark
│       └── tests/
│           ├── __init__.py
│           └── test_engine.py      # unittest (runs under pytest too)
├── CMakeLists.txt
├── .clang-tidy                 # clang-tidy config; `make tidy` runs it
├── CMakePresets.json           # IDE configure presets (see Building on Windows)
├── Makefile
├── pyproject.toml
└── just-makeit.toml
```

Two C sources per object: `engine_core.c` (algorithm) and `engine_ext.c`
(Python glue); a `--header-only` object has only the `_ext.c`. Adding
methods with `just-makeit method` appends stubs to `engine_core.c` — no third
file is ever created.

______________________________________________________________________

## 3. Module with one object

One object inside a shared module `.so`. The module's `_ext.c` aggregator is
generated; the object's binding fragment and `_core.c` are yours to edit.

```sh
just-makeit new mylib --module dsp
just-makeit object filt --module dsp \
    --state coeff:float:0.5 --arg-type float --return-type float
```

```
mylib/
├── native/
│   ├── benchmarks/
│   │   ├── bench_filt_core.c
│   │   └── jm_bench.h
│   ├── inc/
│   │   └── mylib/
│   │       ├── clib_common.h
│   │       ├── pyex_common.h
│   │       ├── mylib.h
│   │       ├── dsp/
│   │       │   └── dsp_core.h      # module-level declarations
│   │       └── filt/
│   │           └── filt_core.h     # public C API + inline step()
│   ├── src/
│   │   ├── mylib_lib.c
│   │   ├── dsp/
│   │   │   ├── CMakeLists.txt
│   │   │   ├── dsp_core.c          # module boilerplate
│   │   │   ├── dsp_ext.c           # aggregator: includes each fragment
│   │   │   └── dsp_ext_filt.c      # Filt's binding fragment
│   │   └── filt/
│   │       ├── CMakeLists.txt
│   │       └── filt_core.c         # algorithm ← implement here
│   └── tests/
│       ├── jm_test.h               # shared CHECK/REQUIRE + epilogue
│       ├── test_filt_core.c        # CTest
│       └── test_filt_symbols.c     # derived link check
├── cmake/
│   └── mylib.pc.in
├── src/
│   └── mylib/
│       ├── __init__.py
│       └── dsp/
│           ├── __init__.py         # from .dsp import Filt
│           ├── dsp.pyi             # type stub for dsp.so
│           ├── benchmarks/
│           │   ├── __init__.py
│           │   └── bench_filt.py
│           └── tests/
│               ├── __init__.py
│               └── test_filt.py
├── CMakeLists.txt
├── .clang-tidy                 # clang-tidy config; `make tidy` runs it
├── CMakePresets.json           # IDE configure presets (see Building on Windows)
├── Makefile
├── pyproject.toml
└── just-makeit.toml
```

The object directory (`filt/`) holds its `_core.c` — there is no
`filt_ext.c`. Its Python binding is `native/src/dsp/dsp_ext_filt.c`, a
per-object fragment that `dsp_ext.c` (jm's aggregator) `#include`s. The
fragment is shared: `apply` adds missing members but never re-renders one
that exists
([Who owns a module's binding fragment](configuration.md#who-owns-a-modules-binding-fragment)).

______________________________________________________________________

## 4. Module with two objects

Two types in one `.so`. Each object gets its own `_core.c`/`_core.h` subtree
and binding fragment; `dsp_ext.c` and `dsp.pyi` cover both.

```sh
just-makeit new mylib --module dsp
just-makeit object fir    --module dsp --state gain:float:1.0
just-makeit object biquad --module dsp \
    --state b0:double:1.0 --arg-type float --return-type float
```

```
mylib/
├── native/
│   ├── benchmarks/
│   │   ├── bench_fir_core.c
│   │   ├── bench_biquad_core.c
│   │   └── jm_bench.h
│   ├── inc/
│   │   └── mylib/
│   │       ├── clib_common.h
│   │       ├── pyex_common.h
│   │       ├── mylib.h
│   │       ├── dsp/
│   │       │   └── dsp_core.h      # module-level declarations
│   │       ├── fir/
│   │       │   └── fir_core.h      # public C API + inline step()
│   │       └── biquad/
│   │           └── biquad_core.h   # public C API + inline step()
│   ├── src/
│   │   ├── mylib_lib.c
│   │   ├── dsp/
│   │   │   ├── CMakeLists.txt
│   │   │   ├── dsp_core.c          # module boilerplate
│   │   │   ├── dsp_ext.c           # aggregator — one .so for Fir and Biquad
│   │   │   ├── dsp_ext_fir.c       # Fir's binding fragment
│   │   │   └── dsp_ext_biquad.c    # Biquad's binding fragment
│   │   ├── fir/
│   │   │   ├── CMakeLists.txt
│   │   │   └── fir_core.c
│   │   └── biquad/
│   │       ├── CMakeLists.txt
│   │       └── biquad_core.c
│   └── tests/
│       ├── jm_test.h               # shared CHECK/REQUIRE + epilogue
│       ├── test_fir_core.c
│       ├── test_fir_symbols.c
│       ├── test_biquad_core.c
│       └── test_biquad_symbols.c
├── cmake/
│   └── mylib.pc.in
├── src/
│   └── mylib/
│       ├── __init__.py
│       └── dsp/
│           ├── __init__.py         # from .dsp import Fir, Biquad
│           ├── dsp.pyi             # one stub for both types
│           ├── benchmarks/
│           │   ├── __init__.py
│           │   ├── bench_fir.py
│           │   └── bench_biquad.py
│           └── tests/
│               ├── __init__.py
│               ├── test_fir.py
│               └── test_biquad.py
├── CMakeLists.txt
├── .clang-tidy                 # clang-tidy config; `make tidy` runs it
├── CMakePresets.json           # IDE configure presets (see Building on Windows)
├── Makefile
├── pyproject.toml
└── just-makeit.toml
```

One `.so`, one `.pyi`, one `__init__.py` — regardless of how many objects the
module contains. `dsp_ext.c` is re-rendered on every change; each object's
`dsp_ext_<obj>.c` fragment only gains missing members.

______________________________________________________________________

## 5. Multiple modules

Two independent `.so` files in one project. Each module has its own `_ext.c`,
`.pyi`, and `__init__.py`; they share the project's C library and CMake
scaffolding.

```sh
just-makeit new mylib --module dsp --module io
just-makeit object fir    --module dsp --state gain:float:1.0
just-makeit object reader --module io  --state pos:int64_t:0
```

```
mylib/
├── native/
│   ├── benchmarks/
│   │   ├── bench_fir_core.c
│   │   ├── bench_reader_core.c
│   │   └── jm_bench.h
│   ├── inc/
│   │   └── mylib/
│   │       ├── clib_common.h
│   │       ├── pyex_common.h
│   │       ├── mylib.h
│   │       ├── dsp/
│   │       │   └── dsp_core.h
│   │       ├── io/
│   │       │   └── io_core.h
│   │       ├── fir/
│   │       │   └── fir_core.h      # public C API + inline step()
│   │       └── reader/
│   │           └── reader_core.h   # public C API + inline step()
│   ├── src/
│   │   ├── mylib_lib.c
│   │   ├── dsp/
│   │   │   ├── CMakeLists.txt
│   │   │   ├── dsp_core.c
│   │   │   ├── dsp_ext.c           # Python binding for dsp.so (aggregator)
│   │   │   └── dsp_ext_fir.c       # Fir's binding fragment
│   │   ├── fir/
│   │   │   ├── CMakeLists.txt
│   │   │   └── fir_core.c
│   │   ├── io/
│   │   │   ├── CMakeLists.txt
│   │   │   ├── io_core.c
│   │   │   ├── io_ext.c            # Python binding for io.so (aggregator)
│   │   │   └── io_ext_reader.c     # Reader's binding fragment
│   │   └── reader/
│   │       ├── CMakeLists.txt
│   │       └── reader_core.c
│   └── tests/
│       ├── jm_test.h               # shared CHECK/REQUIRE + epilogue
│       ├── test_fir_core.c
│       ├── test_fir_symbols.c
│       ├── test_reader_core.c
│       └── test_reader_symbols.c
├── cmake/
│   └── mylib.pc.in
├── src/
│   └── mylib/
│       ├── __init__.py
│       ├── dsp/
│       │   ├── __init__.py         # from .dsp import Fir
│       │   ├── dsp.pyi
│       │   ├── benchmarks/         # __init__.py, bench_fir.py
│       │   └── tests/              # __init__.py, test_fir.py
│       └── io/
│           ├── __init__.py         # from .io import Reader
│           ├── io.pyi
│           ├── benchmarks/         # __init__.py, bench_reader.py
│           └── tests/              # __init__.py, test_reader.py
├── CMakeLists.txt
├── .clang-tidy                 # clang-tidy config; `make tidy` runs it
├── CMakePresets.json           # IDE configure presets (see Building on Windows)
├── Makefile
├── pyproject.toml
└── just-makeit.toml
```

Each module is independent: separate `.so`, separate stub, separate
`__init__.py`. The C object libraries (`fir_core`, `reader_core`) are linked
into their respective module `.so` files.

______________________________________________________________________

## Summary

| Pattern                   | Command                            | `.so` files  | `_ext.c` files                                       | `_core.c` files                              |
| ------------------------- | ---------------------------------- | ------------ | ---------------------------------------------------- | -------------------------------------------- |
| Module function           | `new --module` + `function`        | 1            | 1 (`<mod>_ext.c`)                                    | 1 (`<mod>_core.c`) + 1 `<fn>.c` per function |
| Standalone object         | `new --object`                     | 1 per object | 1 per object                                         | 1 per object                                 |
| Module, 1 object          | `new --module` + `object --module` | 1            | 1 (`<mod>_ext.c`) + 1 `<mod>_ext_<obj>.c`            | 1 (`<mod>_core.c`) + 1 per object            |
| Module, N objects         | same, repeated                     | 1            | 1 (`<mod>_ext.c`) + N `<mod>_ext_<obj>.c`            | 1 (`<mod>_core.c`) + N objects               |
| M modules, N objects each | same                               | M            | M (`<mod>_ext.c`) + 1 `<mod>_ext_<obj>.c` per object | M (`<mod>_core.c`) + N objects               |

**Invariant**: every standalone object has one `_core.c` (user code) and one
`_ext.c` (auto-generated Python binding); a `--header-only` object has no
`_core.c`. A module has one `<mod>_core.c`, one `<mod>_ext.c` aggregator and
one `<mod>_ext_<obj>.c` binding fragment per object, and each module function
its own `<fn>.c` (unless `--functions-in-core`).
