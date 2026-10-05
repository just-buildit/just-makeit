# <<project>>

TODO: describe your project.

## Requirements

- Python 3.9+
- CMake ≥ 3.16
- A C99 compiler — GCC or Clang on Linux and macOS. On Windows,
  **clang-cl** (Visual Studio Build Tools' C++ workload plus LLVM), run from a
  Developer PowerShell; the `Makefile` selects it with Ninja. Not MSVC's own
  `cl.exe`: it has no C99 `float _Complex`. To use the `Makefile`, install
  GNU make (`winget install ezwinports.make`); or open the folder in Visual
  Studio, which configures through the generated `CMakePresets.json`
  ([Building on Windows](https://just-buildit.github.io/just-makeit/windows/)).
- NumPy (installed automatically by `make` if missing)

Install system build dependencies (detects OS/distro automatically):

```bash
jbx install-deps -g dev
```

## Quickstart

Build the extension in place, then install the package in editable mode:

```bash
make                     # compiles the extension into src/<<package>>/
pip install -e .         # points Python at src/; compiles nothing
```

Re-run `make` after any C change; Python edits take effect immediately.

## Development build

```bash
make                     # cmake configure + build
make test                # CTest + <<py_test_label>>
```

## Package

```bash
pip wheel . --no-deps -w dist     # wheel -> dist/
```

`pyproject.toml` names just-buildit as the PEP 517 build backend, so any
frontend builds the wheel. `just-makeit build` does the same when just-buildit
is installed in the environment just-makeit runs from
(`pip install just-makeit just-buildit`).

The wheel is then repaired through `uvx`, so `uv` must be on `PATH`: with
`auditwheel` on Linux, which also needs `patchelf` (`sudo apt install
patchelf`), `delocate` on macOS and `delvewheel` on Windows. To skip the
repair, set `repair = false` under `[tool.just-buildit]` in `pyproject.toml`.
