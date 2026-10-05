# Array processing example

Every object just-makeit generates can process a block of samples in one call.
This example walks through the main ways the CLI exposes that capability, from
the free `steps()` that comes with every object to `--variable-output` batch
methods with multiple output streams.

Along the way, each section explains **who owns the memory**, **when it is
allocated**, and **what the Python caller can safely do with the returned array**.

Five patterns, five sections, then a sixth on documenting them. `--out-type`
and `--borrow` are covered in [Array memory ownership](../memory-ownership.md).

| #   | Pattern                                   | Output allocation                                                | Who owns it              |
| --- | ----------------------------------------- | ---------------------------------------------------------------- | ------------------------ |
| 1   | Auto-generated `steps()`                  | Per call (or zero if `out=` supplied)                            | Caller (numpy)           |
| 2   | `method` scalar stub + `method --batch`   | Per call (or zero if `out=` supplied)                            | Caller (numpy)           |
| 3   | `method --variable-output`                | Per call, sized by `_max_out(n_in)` (or zero if `out=` supplied) | Caller (numpy)           |
| 4   | `method --variable-output --multi-output` | Per call, one array per stream                                   | Caller (tuple of arrays) |
| 5   | `--arg-type type[]` (buffer primary arg)  | Caller supplies input buffer                                     | Caller (input)           |

All five patterns share a common rule: **inline `float[N]` state arrays in the
C struct require no heap allocation** — they are part of the struct itself.
Heap allocation only appears when the output size is not fixed at compile time.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example array_processing
# array_processing: PASSED
```

## Prerequisites

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
```

Pass a custom path to keep the venv somewhere persistent:

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh) -- ~/my-venv
```

Or with `pip`, which also works on Python 3.9 and 3.10 (the installer
needs 3.11+). It installs just-makeit, then builds the toolchain venv at
`/tmp/jm-venv`:

```sh
pip install just-makeit && just-makeit install-deps
source /tmp/jm-venv/bin/activate
```
