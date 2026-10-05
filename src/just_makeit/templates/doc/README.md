# <<project>>

TODO: describe your project.

## Requirements

- Python 3.9+
- CMake ≥ 3.16
- A C99 compiler — GCC or Clang on Linux and macOS. On Windows,
  **clang-cl** (Visual Studio Build Tools' C++ workload plus LLVM), run from a
  Developer PowerShell; the `Makefile` selects it with Ninja. Not MSVC's own
  `cl.exe`: it has no C99 `float _Complex`.
- NumPy (installed automatically by `make` if missing)

Install the system build dependencies `bootstrap.toml` lists with
[jbx](https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit)
(one `curl` installs it), which picks your OS's package manager:

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
