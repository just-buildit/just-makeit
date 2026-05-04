# just-makeit

Python C extensions the easy way.

`just-makeit init` scaffolds a complete, working C99 extension project — core C
library, thin Python binding, CMake build, and full test coverage — in one
command.

---

## Quickstart

```sh
pip install just-makeit
just-makeit init my_filter
cd my_filter
make
make test
```

That's it. You have a working Python C extension.

---

## What you get

```
my_filter/
    native/
        inc/
            clib_common.h           # common C99 types
            pyex_common.h           # common Python extension includes
            my_filter/
                my_filter_core.h    # component API — edit this first
        src/
            my_filter/
                my_filter_core.c    # C implementation — your business logic lives here
                my_filter_ext.c     # thin Python binding — no business logic
        tests/
            test_my_filter_core.c   # CTest
    src/
        my_filter/
            __init__.py             # Python package
            my_filter.pyi           # type stub
            tests/
                test_my_filter.py   # pytest
    CMakeLists.txt
    Makefile
    pyproject.toml
```

The `.so` extension is built directly into `src/my_filter/` alongside the
Python files — so `import my_filter` works from the source tree after a single
`make`.

---

## Commands

| Command | Description |
|---|---|
| `just-makeit init <name>` | Create a new C extension project |
| `just-makeit build` | Configure + build C, then package a wheel |
| `just-makeit test` | Build and run CTest + pytest |
| `just-makeit dry-run` | Preview what would be compiled |

See [Commands](commands.md) for the full reference.

---

## Design principles

**Separation of concerns.** Core C logic goes in `*_core.c` / `*_core.h`.
The Python extension in `*_ext.c` is a thin adapter — argument parsing, array
wrapping, and nothing more.  This keeps the C library independently testable
and usable from Rust, C++, or any other language.

**Full test coverage by default.** Every generated project has C tests (CTest)
and Python tests (pytest) from day one.

**just-buildit for packaging.** The generated `pyproject.toml` uses
[just-buildit](https://github.com/just-buildit/just-buildit) as the PEP 517
build backend, so `pip install .` just works.

---

## Requirements

- Python 3.12+
- CMake ≥ 3.16
- A C99 compiler (GCC, Clang, MSVC)
- NumPy (runtime dependency of generated projects)
