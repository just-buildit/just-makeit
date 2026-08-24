# <<project>>

TODO: describe your project.

## Requirements

- Python 3.9+
- CMake ≥ 3.16
- A C99 compiler — GCC or Clang. **Not MSVC**: it does not support C99
  `float _Complex`, which is why the `Makefile` forces the MinGW generator on
  Windows.
- NumPy (installed automatically by `make` if missing)

Install system build dependencies (detects OS/distro automatically):

```bash
jbx install-deps -g dev
```

## Quickstart

Install and build in one step (recommended):

```bash
pip install -e .
```

## Development build

```bash
make                     # cmake configure + build
make test                # CTest + pytest
```

## Package

```bash
pip install just-buildit
just-makeit build        # wheel -> dist/
```
