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

## TL;DR — see it work first

```sh
uvx git+https://github.com/just-buildit/just-makeit install-deps
source /tmp/jm-venv/bin/activate
just-makeit example filter_module
# filter_module: PASSED
```

## Prerequisites

```sh
pip install just-makeit
just-makeit install-deps --check   # report what is installed vs. what will be installed
just-makeit install-deps           # install cmake, C compiler, numpy, and create a venv
source /tmp/jm-venv/bin/activate
```

Pass a custom path to keep the venv somewhere persistent:

```sh
just-makeit install-deps ~/my-venv && source ~/my-venv/bin/activate
```
