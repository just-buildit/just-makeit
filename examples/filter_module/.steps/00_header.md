# filter_module example

A two-type filter library where `Fir` (FIR filter) and `Biquad` (biquad IIR)
live together in a single `filter` Python extension module.

Before this workflow, every component produced its own `.so`:
```
my_filters/fir.cpython-312-x86_64-linux-gnu.so
my_filters/biquad.cpython-312-x86_64-linux-gnu.so
```

With `module` + `object`, related types share one `.so` as a proper subpackage:
```
my_filters/filter/filter.cpython-312-x86_64-linux-gnu.so
my_filters/filter/__init__.py   ← re-exports Fir, Biquad
```

Users import cleanly:
```python
from my_filters.filter import Fir, Biquad
```

## What you'll need

- `just-makeit` (`pip install just-makeit`)
- `cmake` ≥ 3.16
- A C compiler (`gcc` or `clang`)
- `numpy` (`pip install numpy` — needed before cmake runs)
- A text editor

`jm-install-deps` (installed with `just-makeit`) detects your OS, installs cmake and a C compiler, then creates a venv at `/tmp/jm-venv` with numpy and just-makeit:

```sh
# Set up deps + venv, then activate in your current shell:
source $(which jm-install-deps)

# Or with a custom venv path:
jm-install-deps ~/my-venv
```
