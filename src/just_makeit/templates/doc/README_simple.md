# <<project>>

TODO: describe your project.

## Requirements

- Python 3.9+
- A C99 compiler (GCC or Clang) and GNU make, on Linux or macOS. This
  project uses the `make` build backend, which does not build on Windows;
  setting `build = "cmake"` in `just-makeit.toml` and running
  `just-makeit apply` adds the CMake build, which does.
- NumPy (installed automatically by `make` if missing)

## Quickstart

Build the extension in place, then install the package in editable mode:

```bash
make                     # compiles the extension into src/<<package>>/
pip install -e .         # points Python at src/; compiles nothing
```

Re-run `make` after any C change; Python edits take effect immediately.

## Development build

```bash
make                     # build the extension(s)
make test                # C tests + <<py_test_label>>
```

## Package

```bash
pip wheel . --no-deps -w dist     # wheel -> dist/
```

`pyproject.toml` names just-buildit as the PEP 517 build backend, which runs
`make just-build`, so any frontend builds the wheel.

The wheel is then repaired through `uvx`, so `uv` must be on `PATH`: with
`auditwheel` on Linux, which also needs `patchelf` (`sudo apt install
patchelf`), and `delocate` on macOS. To skip the repair, set
`repair = false` under `[tool.just-buildit]` in `pyproject.toml`.
