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

- `just-makeit app --target c` — a complete C `main()` that constructs the
    object from command-line arguments, reads stdin, and writes stdout
- `just-makeit app --target console` — a Python console script (`cli.py`) with
    `argparse` flags wired to each constructor parameter
- `just-makeit app --target pep723` — a PEP 723 inline-script (`my_ema.py` at
    the project root) that declares its own dependencies and runs without a
    virtualenv
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

Because `ema`'s `step(x) -> y` is a scalar transform, all three targets are
generated as *complete, working* sample-stream tools — a real argument parser
(one `--flag` per constructor state var, plus `--input`/`--output`) and a
read → `step()` → write loop. No hand-editing is required.

**C executable** (`native/src/app/my_ema.c`):

```c
int
main(int argc, char *argv[])
{
    /* --- parse args --- */
    double alpha = 0.1;
    double prev = 0.0;
    const char *in_path = NULL;
    const char *out_path = NULL;
    /* ...strcmp loop over argv for --alpha/--prev/--input/--output... */

    FILE *in = in_path ? fopen(in_path, "rb") : stdin;
    FILE *out = out_path ? fopen(out_path, "wb") : stdout;

    /* --- create --- */
    ema_state_t *state = ema_create(alpha, prev);

    /* --- process --- */
    double x;
    while (fread(&x, sizeof x, 1, in) == 1) {
        double y = ema_step(state, x);
        fwrite(&y, sizeof y, 1, out);
    }

    /* --- cleanup --- */
    ema_destroy(state);
    return 0;
}
```

CMake wires the new `my_ema` target to link the same `ema_core` OBJECT
library the Python extension uses — one implementation, two consumers.

**Python console script** (`src/my_ema/cli.py`):

```python
import argparse
import sys

import numpy as np

from . import Ema


def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="my_ema")
    p.add_argument("--input", "-i", default=None)
    p.add_argument("--output", "-o", default=None)
    p.add_argument("--alpha", type=float, default=0.1)
    p.add_argument("--prev", type=float, default=0.0)
    return p


def main() -> None:
    args = _make_parser().parse_args()
    if args.input:
        data = np.fromfile(args.input, dtype=np.float64)
    else:
        data = np.frombuffer(sys.stdin.buffer.read(), dtype=np.float64)
    obj = Ema(alpha=args.alpha, prev=args.prev)
    out = np.array([obj.step(x) for x in data], dtype=np.float64)
    if args.output:
        out.tofile(args.output)
    else:
        sys.stdout.buffer.write(out.tobytes())
```

Registered in `pyproject.toml` under `[project.scripts]` (the script name
defaults to the project name, since no `--name` was passed):

```toml
[project.scripts]
my_ema = "my_ema.cli:main"
```

**PEP 723 inline script** (`my_ema.py`, at the project root):

```python
# /// script
# requires-python = ">=3.9"
# dependencies = ["my_ema==0.1.0", "numpy"]
# ///
"""my_ema standalone script (PEP 723)."""
import argparse
import sys

import numpy as np

from my_ema import Ema

# ...same _make_parser() and main() as the console script above...
```

Run without a virtualenv:

```sh
uv run my_ema.py
```

______________________________________________________________________

## 4. Build and run

Each target works as generated — pick whichever you need and run it:

```sh
# C executable
make && ./build/my_ema --alpha 0.2

# Console script (after pip install)
pip install -e .
my_ema --alpha 0.2

# PEP 723 script
uv run my_ema.py --alpha 0.2
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
