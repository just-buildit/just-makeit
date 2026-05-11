# test123

TODO: describe your project.

## Requirements

- Python 3.11+
- CMake ≥ 3.16
- A C99 compiler (GCC, Clang, or MSVC)
- NumPy (installed automatically by `make` if missing)

**Linux:** `sudo apt-get install cmake gcc pkg-config`
**macOS:** `brew install cmake pkg-config` (compiler via `xcode-select --install`)

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
