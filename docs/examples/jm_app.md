# jm_app example

Scaffold three kinds of application entry point from a single DSP component:
a compiled C executable, a Python console script with `argparse`, and a
self-contained PEP 723 inline script.

## TL;DR — see it work first

```sh
just-makeit example jm_app
# jm_app: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```

______________________________________________________________________

## What it demonstrates

- `just-makeit app --target c` — a C `main()` that constructs the object from
    command-line arguments, reads stdin, and writes stdout
- `just-makeit app --target console` — a Python console script (`cli.py`) with
    `argparse` flags wired to each constructor parameter
- `just-makeit app --target pep723` — a PEP 723 inline-script block
    (`app.py`) that declares its own dependencies and runs without a virtualenv
- How each target wires its constructor parameters to command-line flags

______________________________________________________________________

## 1. Scaffold the component

```sh
just-makeit new my_ema --object ema \
    --state "alpha:double:0.1" \
    --state "prev:double:0.0" \
    --arg-type double --return-type double
cd my_ema
```

______________________________________________________________________

## 2. Generate the three app targets

```sh
just-makeit app --target c
just-makeit app --target console
just-makeit app --target pep723
```

Each command writes a scaffold to a different file and registers it in
`pyproject.toml` (console and pep723) or `CMakeLists.txt` (C).

______________________________________________________________________

## 3. What was created

**C executable** (`native/src/ema/ema_app.c`):

```c
/* Reads doubles from stdin, one per line.
   Usage: ema_app [--alpha ALPHA] [--prev PREV] */
int main(int argc, char **argv) { /* TODO: implement */ }
```

CMake wires the new `ema_app` target to link the same `ema_core` OBJECT
library the Python extension uses — one implementation, two consumers.

**Python console script** (`src/my_ema/cli.py`):

```python
#!/usr/bin/env python3
"""EMA command-line interface."""
import argparse
from my_ema import Ema

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--alpha", type=float, default=0.1)
    p.add_argument("--prev", type=float, default=0.0)
    args = p.parse_args()
    ema = Ema(alpha=args.alpha, prev=args.prev)
    # TODO: read input, run, write output
```

Registered in `pyproject.toml` under `[project.scripts]`:

```toml
[project.scripts]
ema-cli = "my_ema.cli:main"
```

**PEP 723 inline script** (`src/my_ema/app.py`):

```python
# /// script
# requires-python = ">=3.9"
# dependencies = ["my-ema"]
# ///
"""EMA standalone script — run with: uv run app.py"""
from my_ema import Ema

ema = Ema(alpha=0.1)
# TODO: read input, run, write output
```

Run without a virtualenv:

```sh
uv run src/my_ema/app.py
```

______________________________________________________________________

## 4. Implement and run

Fill in the `main()` body of whichever target you need, then:

```sh
# C executable
make && ./build/ema_app --alpha 0.2

# Console script (after pip install)
pip install -e .
ema-cli --alpha 0.2

# PEP 723 script
uv run src/my_ema/app.py
```

______________________________________________________________________

## Key concepts

**One component, three surfaces.** The DSP logic lives once in `ema_core.c`.
All three app targets call into it — the C executable via direct linkage, the
Python targets via the Python extension. No duplication.

**Constructor params become CLI flags.** Each `[[state]]` entry (or
`[[init_params]]` entry) with a default value becomes a typed `argparse`
argument. States without defaults become required positional arguments.

**PEP 723 enables zero-install scripting.** The inline metadata block
(`# ///`) lets `uv run` install the package into a throwaway venv on first
run — no manual `pip install` needed. Useful for distributing one-file tools
that wrap a compiled extension.

## See also

- [`jm app` reference](../commands/app.md)
- [C library distribution](../c-library.md) — how the generated `.so` and CMake
    config let C consumers link the same algorithm
