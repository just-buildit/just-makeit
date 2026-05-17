# accumulator example

Two running accumulators in a shared Python extension module — `AccF32`
(single-precision float) and `AccCf64` (double-precision complex) — built with
just-makeit from scratch.

An accumulator is the simplest stateful DSP primitive: push samples in, read
the running total out, reset when done.  This example is intentionally
straightforward so you can focus on the jm workflow rather than on the
algorithm.

| Type     | C type          | Python dtype        | Precision  |
| -------- | --------------- | ------------------- | ---------- |
| `AccF32` | `float`         | `np.float32`        | 32-bit     |
| `AccCf64`| `double complex`| `np.complex128`     | 128-bit    |

Both live in a shared `accumulator` subpackage:

```python
from my_acc.accumulator import AccF32, AccCf64
```

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example accumulator
# accumulator: all checks passed
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```
