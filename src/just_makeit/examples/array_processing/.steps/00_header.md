# Array processing example

Every object just-makeit generates can process a block of samples in one call.
This example walks through every way the CLI exposes that capability, from the
free `steps()` that comes with every object to `--variable-output` batch methods
with multiple output streams.

Along the way, each section explains **who owns the memory**, **when it is
allocated**, and **what the Python caller can safely do with the returned array**.

Five patterns, five sections:

| # | Pattern | Output allocation | Who owns it |
|---|---------|-------------------|-------------|
| 1 | Auto-generated `steps()` | Per call (or zero if `out=` supplied) | Caller (numpy) |
| 2 | `method` scalar stub + hand-written `_steps()` | Per call (or zero if `out=` supplied) | Caller (numpy) |
| 3 | `method --variable-output` | Allocated at `__init__`, re-used | Object (zero-copy view) |
| 4 | `method --variable-output --multi-output` | Same — one buffer per stream | Object (tuple of views) |
| 5 | `--arg-type type[]` (buffer primary arg) | Caller supplies input buffer | Caller (input) |

All five patterns share a common rule: **inline `float[N]` state arrays in the
C struct require no heap allocation** — they are part of the struct itself.
Heap allocation only appears when the output size is not fixed at compile time.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh)
just-makeit example array_processing
# array_processing: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://raw.githubusercontent.com/just-buildit/just-makeit/main/install.sh) -- ~/my-venv
```

Or with `pip` if just-makeit is already installed:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```
