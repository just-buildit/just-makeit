# <<project>>

TODO: describe your project.

## Requirements

- Python 3.9+
<<readme_requirements>>
- NumPy (installed automatically by `make` if missing)

Install the system build dependencies `bootstrap.toml` lists with
[jbx](https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit)
(one `curl` installs it), which picks your OS's package manager:

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
make                     # <<readme_make_builds>>
make test                # <<readme_c_tests>> + <<py_test_label>>
```

## Package

```bash
pip wheel . --no-deps -w dist     # wheel -> dist/
```

`pyproject.toml` names just-buildit as the PEP 517 build backend, which runs
`make just-build`, so any frontend builds the wheel.<<readme_jm_build>>

The wheel is then repaired through `uvx`, so `uv` must be on `PATH`. It runs
`delocate` on macOS<<readme_repair_windows>> and `auditwheel` on Linux, which
also needs `patchelf` (`sudo apt install patchelf`). To skip the repair, set
`repair = false` under `[tool.just-buildit]` in `pyproject.toml`.
