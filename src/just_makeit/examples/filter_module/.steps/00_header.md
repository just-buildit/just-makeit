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
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example filter_module
# filter_module: PASSED
```

## Prerequisites

With jbx, the recommended way on every platform
([getting jbx](https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit)):

```sh
jbx just-makeit:install
source /tmp/jm-venv/bin/activate   # Linux and macOS
```

Or with the curl installer, which also activates the venv:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip`, which installs just-makeit and then builds the toolchain
venv at `/tmp/jm-venv`:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

On Windows, from PowerShell without jbx (see
[Building on Windows](../windows.md)):

```powershell
irm https://just-buildit.github.io/just-makeit/install.ps1 | iex
```
