# iqfile example

A block-wise converter between **cf32** (complex float-32, 8 bytes/sample)
and **q15** (complex signed 16-bit fixed-point, 4 bytes/sample) — the two
most common raw IQ file formats in software-defined radio.

```
cf32: [f32_i, f32_q, f32_i, f32_q, ...]   8 bytes per complex sample
q15:  [i16_i, i16_q, i16_i, i16_q, ...]   4 bytes per complex sample
```

This example builds a complete, installable Python package that demonstrates
every major just-makeit feature in one project:

| Feature                              | Where                                    |
| ------------------------------------ | ---------------------------------------- |
| Module subpackage (single `.so`)     | `conv` module                            |
| Two objects sharing one extension    | `Cf32ToQ15`, `Q15ToCf32`                 |
| Generator object (`--arg-type void`) | `Q15ToCf32` reads from a file descriptor |
| Field-backed property (`--field`)    | `samples_read`, `samples_written`        |
| Computed read-only property          | `eof` on `Q15ToCf32`                     |
| `pip install -e .` dev workflow      | step 6                                   |
| Wheel build (`just-makeit build`)    | step 8                                   |

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example iqfile
# iqfile: PASSED
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

---

## 1. Scaffold

```sh
just-makeit new iqfile --module conv
cd iqfile
```

This creates the project shell and an empty `conv` module subpackage.
No objects yet — just the plumbing: `CMakeLists.txt`, `Makefile`,
`pyproject.toml`, `just-makeit.toml`, and `src/iqfile/conv/__init__.py`.

---

## 2. Add the converter types

```sh
just-makeit object cf32_to_q15 \
    --module conv \
    --arg-type "float _Complex" \
    --return-type int32_t \
    --state "scale:float:32767.0f"

just-makeit object q15_to_cf32 \
    --module conv \
    --arg-type void \
    --return-type "float _Complex" \
    --state "fd:int32_t:-1" \
    --state "scale:float:32767.0f"
```

Two types, one `.so`.

### Cf32ToQ15 — writer

Takes a `float _Complex` sample, scales it, clamps it, and packs the
I and Q parts as two `int16_t` values.  Returns the number of bytes written
(`int32_t`, 4 on success, −1 on error) so the caller can detect short writes.

`--arg-type float _Complex` and `--return-type int32_t` generate:

```c
static inline int32_t
cf32_to_q15_step(const cf32_to_q15_state_t *state, float complex x);

void cf32_to_q15_steps(cf32_to_q15_state_t *state,
                       const float complex *input,
                       int32_t             *output,
                       size_t               n);
```

### Q15ToCf32 — reader

`--arg-type void` makes this a **generator**: no input parameter.  Each
`step()` call reads one complex q15 sample (two `int16_t`) from the file
descriptor stored in `fd` and returns it as a normalised `float complex`.

```c
static inline float complex
q15_to_cf32_step(const q15_to_cf32_state_t *state);

void q15_to_cf32_steps(q15_to_cf32_state_t *state,
                       float complex       *output,
                       size_t               n);
```

`fd` is passed at construction — the caller opens the file with `os.open()`:

```python
import os
from iqfile.conv import Q15ToCf32

fd = os.open("samples.q15", os.O_RDONLY)
reader = Q15ToCf32(fd=fd)
block  = reader.steps(1024)   # returns complex64 ndarray
os.close(fd)
```

---

## 3. Add properties

```sh
just-makeit property cf32_to_q15 samples_written \
    --module conv --type uint32_t --field

just-makeit property q15_to_cf32 samples_read \
    --module conv --type uint32_t --field

just-makeit property q15_to_cf32 eof \
    --module conv --type int32_t
```

Three properties across the two types:

| Object      | Property          | Kind      | Type       | Notes                               |
| ----------- | ----------------- | --------- | ---------- | ----------------------------------- |
| `Cf32ToQ15` | `samples_written` | `--field` | `uint32_t` | incremented by `step()`             |
| `Q15ToCf32` | `samples_read`    | `--field` | `uint32_t` | incremented by `step()`             |
| `Q15ToCf32` | `eof`             | computed  | `int32_t`  | implement via `read()` return value |

**Field-backed** (`--field`): adds `uint32_t samples_written;` to the state
struct and auto-implements the getter as `return state->samples_written` — no
`<<IMPLEMENT>>` stub needed.

**Computed** (`eof`, no `--field`): getter stub calls `q15_to_cf32_get_eof()`
which you implement — returning 1 when the last `read()` returned 0 bytes.

---

## 4. Implement the C kernels

```python
"""Implement cf32_to_q15_step() and add the samples_written counter."""

from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()

# ── step() in _core.h ──────────────────────────────────────────────────────
core_h = root / "native/inc/cf32_to_q15/cf32_to_q15_core.h"
text = core_h.read_text(encoding="utf-8")

if "<math.h>" not in text:
    text = text.replace(
        '#include "clib_common.h"',
        '#include "clib_common.h"\n#include <math.h>',
        1,
    )

OLD = """\
    (void)state; /* TODO: implement using state variables */
    return (int32_t)x;"""

NEW = """\
    float s = state->scale;
    int16_t i = (int16_t)fmaxf(-s, fminf(s, crealf(x) * s));
    int16_t q = (int16_t)fmaxf(-s, fminf(s, cimagf(x) * s));
    /* Pack I in low 16 bits, Q in high 16 bits.
     * Python: packed.view(np.int16) gives interleaved [i0, q0, i1, q1, ...] */
    return (int32_t)((uint32_t)(uint16_t)i | ((uint32_t)(uint16_t)q << 16));"""

assert OLD in text, "step stub not found — was it already patched?"
core_h.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
print(f"patched  {core_h.relative_to(root)}")

# ── samples_written counter in _core.c ────────────────────────────────────
core_c = root / "native/src/cf32_to_q15/cf32_to_q15_core.c"
text = core_c.read_text(encoding="utf-8")

OLD_LOOP = """\
    for (size_t i = 0; i < n; i++)
        output[i] = cf32_to_q15_step(state, input[i]);
}"""

NEW_LOOP = """\
    for (size_t i = 0; i < n; i++)
        output[i] = cf32_to_q15_step(state, input[i]);
    state->samples_written += (uint32_t)n;
}"""

assert OLD_LOOP in text, "steps() loop not found"
core_c.write_text(text.replace(OLD_LOOP, NEW_LOOP, 1), encoding="utf-8")
print(f"patched  {core_c.relative_to(root)}")
```

```python
"""Implement q15_to_cf32_step(), samples_read counter, and eof getter."""

from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()

# ── add <unistd.h> to _core.h ─────────────────────────────────────────────
core_h = root / "native/inc/q15_to_cf32/q15_to_cf32_core.h"
text = core_h.read_text(encoding="utf-8")

if "<unistd.h>" not in text:
    text = text.replace(
        '#include "clib_common.h"',
        '#include "clib_common.h"\n#include <unistd.h>',
        1,
    )

# ── step() in _core.h ──────────────────────────────────────────────────────
OLD = """\
    (void)state; /* TODO: implement */
    return (float complex)0;"""

NEW = """\
    int16_t pair[2] = {0, 0};
    if (state->fd >= 0)
        read((int)state->fd, pair, sizeof(pair));
    return ((float)pair[0] + (float)pair[1] * I) / state->scale;"""

assert OLD in text, "step stub not found — was it already patched?"
text = text.replace(OLD, NEW, 1)

# Add eof getter declaration before the closing header guard #endif
guard = "#endif /* Q15_TO_CF32_CORE_H */"
assert guard in text, "header guard not found"
text = text.replace(
    guard,
    "int32_t q15_to_cf32_get_eof(const q15_to_cf32_state_t *state);\n\n"
    + guard,
    1,
)
core_h.write_text(text, encoding="utf-8")
print(f"patched  {core_h.relative_to(root)}")

# ── samples_read counter + eof getter in _core.c ──────────────────────────
core_c = root / "native/src/q15_to_cf32/q15_to_cf32_core.c"
text = core_c.read_text(encoding="utf-8")

# Add <unistd.h> if needed (steps() calls read/lseek)
if "<unistd.h>" not in text:
    text = text.replace(
        '#include "q15_to_cf32/q15_to_cf32_core.h"',
        '#include "q15_to_cf32/q15_to_cf32_core.h"\n#include <unistd.h>',
        1,
    )

# Counter in steps()
OLD_LOOP = """\
    for (size_t i = 0; i < n; i++)
        output[i] = q15_to_cf32_step(state);
}"""

NEW_LOOP = """\
    for (size_t i = 0; i < n; i++)
        output[i] = q15_to_cf32_step(state);
    state->samples_read += (uint32_t)n;
}"""

assert OLD_LOOP in text, "steps() loop not found"
text = text.replace(OLD_LOOP, NEW_LOOP, 1)

# eof getter stub (called by the Python property)
EOF_IMPL = """
int32_t
q15_to_cf32_get_eof(const q15_to_cf32_state_t *state)
{
    if (state->fd < 0)
        return 1;
    off_t cur = lseek((int)state->fd, 0, SEEK_CUR);
    off_t end = lseek((int)state->fd, 0, SEEK_END);
    lseek((int)state->fd, cur, SEEK_SET);
    return cur == end ? 1 : 0;
}
"""

text += EOF_IMPL
core_c.write_text(text, encoding="utf-8")
print(f"patched  {core_c.relative_to(root)}")
```

### Cf32ToQ15 — step()

The return type is `int32_t`.  Rather than adding a separate output buffer for
the two `int16_t` values, the step packs both into one `int32_t`
(I in the low 16 bits, Q in the high 16 bits):

```c
static inline int32_t
cf32_to_q15_step (const cf32_to_q15_state_t *state, float complex x)
{
  float   scale   = state->scale;
  int16_t i       = (int16_t)(crealf (x) * scale);
  int16_t q       = (int16_t)(cimagf (x) * scale);
  int16_t pair[2] = { i, q };
  return (int32_t)sizeof (pair);
}
```

Python unpacks the packed array with `ndarray.view(np.int16)`:

```python
packed = writer.steps(cf32_block)          # dtype int32, shape (N,)
q15    = packed.view(np.int16)             # dtype int16, shape (2N,) — [i0,q0,i1,q1,…]
q15.tofile("samples.q15")
```

Samples are clamped to `[-scale, +scale]` before casting so an overdriven
input never wraps around silently.

### Q15ToCf32 — step()

Reads four bytes (two `int16_t`) from `state->fd` on every call:

```c
static inline float complex
q15_to_cf32_step (const q15_to_cf32_state_t *state)
{
  int16_t pair[2] = { 0, 0 };
  ssize_t n       = read ((int)state->fd, pair, sizeof (pair));
  (void)n;
  return (crealf (0.0f) + cimagf (0.0f) * I)
         + ((float)pair[0] + (float)pair[1] * I) / state->scale;
}
```

`fd` is an `int32_t` state variable — pass a POSIX file descriptor at
construction time:

```python
import os
fd     = os.open("samples.q15", os.O_RDONLY)
reader = Q15ToCf32(fd=fd)
block  = reader.steps(1024)    # reads 4 KiB, returns complex64 ndarray
os.close(fd)
```

### Counters and eof

`samples_written` and `samples_read` are **field-backed** properties — the
struct already has `uint32_t samples_written;` — so the patch adds a single
line to each `_steps()` function:

```c
state->samples_written += (uint32_t)n;   /* in cf32_to_q15_steps() */
state->samples_read    += (uint32_t)n;   /* in q15_to_cf32_steps()  */
```

`eof` is a **computed** property.  The patch appends `q15_to_cf32_get_eof()`
to `_core.c`; it uses `lseek` to compare the current and end file positions:

```c
off_t cur = lseek(state->fd, 0, SEEK_CUR);
off_t end = lseek(state->fd, 0, SEEK_END);
lseek(state->fd, cur, SEEK_SET);
return cur == end ? 1 : 0;
```

### Document once, in C

The sacred header is also the single source of truth for **documentation**. A
Doxygen `/** ... */` comment on `<obj>_create()` flows straight into the
generated `conv.pyi` as the Python class summary — document the intent in C,
once, and every surface inherits it:

```c
/**
 * @brief Pack complex float samples into interleaved q15 (int16 I/Q).
 */
cf32_to_q15_state_t *cf32_to_q15_create(float scale);
```

Property docstrings come from **whichever source actually backs the getter**:

| Property                          | Kind         | Doc source                         |
| --------------------------------- | ------------ | ---------------------------------- |
| `samples_written`, `samples_read` | field-backed | manifest `jm property --doc` value |
| `eof`                             | computed     | header `@brief` on the getter      |

A `--field` property's getter is auto-implemented inline — there is *no header
declaration to annotate* — so its docstring lives in the manifest, authored at
`jm property` time (step 3). A **computed** property (`eof`) has a real getter
declaration, so its `@brief` in `q15_to_cf32_core.h` becomes the property
docstring:

```c
/**
 * @brief True (1) once the backing file descriptor is exhausted.
 */
int32_t q15_to_cf32_get_eof(const q15_to_cf32_state_t *state);
```

`jm apply` re-derives the stub, and `conv.pyi` now carries a real summary on
each class plus a documented `eof` property:

```python
@final
class Q15ToCf32:
    """Read interleaved q15 (int16 I/Q) samples as complex float.
    ...
    """
    @property
    def eof(self) -> int:
        """True (1) once the backing file descriptor is exhausted."""
```

This example has no named `jm method` — only `step()`/`steps()` and
properties — and a property getter renders as prose, not as a runnable
`@code` doctest. So iqfile ships rich class summaries and property docs; for a
**runnable** header-authored doctest (`pytest --doctest-glob='*.pyi'`), see the
`accumulator` and `views_module` examples, whose named methods carry `@code`
blocks that execute against the built extension.

The enrichment for both types is scripted — run it after the kernels are
implemented, then re-derive the glue:

```python
"""Enrich the sacred ``<obj>_core.h`` headers with Doxygen so the generated
``conv.pyi`` carries real class summaries and rich property docstrings.

The header is the single source of truth for documentation: ``jm`` parses these
``/** ... */`` comments and turns them into numpy-style Python docstrings. Run
this after the C kernels are implemented (``04_patch_writer.py`` /
``04_patch_reader.py``); a follow-up ``jm apply`` re-derives the glue (``.pyi``
included) from the edited headers.

Which source flows where — verified empirically (mirrors the views_module
finding):

  - **Class summary** comes from ``<obj>_create``'s ``@brief``. Both converter
    objects own a real ``<obj>_create`` (they are distinct module objects, not
    views), so a header ``@brief`` enriches each class summary.
  - **Field-backed** property docstrings (``samples_written``, ``samples_read``)
    come from the manifest ``jm property --doc`` value, NOT a header ``@brief``:
    a ``--field`` getter is auto-implemented inline, so there is no header
    declaration to annotate. Those docs are set in ``test.py`` / step 3.
  - A **computed** property's getter has a real declaration, so it CAN carry a
    header ``@brief``. ``eof`` is computed (``q15_to_cf32_get_eof``), so its
    docstring is enriched here.

iqfile exposes no named ``jm method`` (only ``step``/``steps`` plus
properties), and a property getter's docstring renders as prose only — a
``@code`` block on a property is not turned into a runnable doctest. So this
example ships rich class summaries and property docs, but no runnable method
doctest (like the other "light" examples).

Usage:  python3 .steps/04b_doxygen.py     # run from the project root
"""

from __future__ import annotations

import pathlib
import re
import sys

# Class summaries, injected over jm's scaffold brief on each <obj>_create.
CREATE_BRIEFS = {
    "cf32_to_q15": (
        "Pack complex float samples into interleaved q15 (int16 I/Q)."
    ),
    "q15_to_cf32": (
        "Read interleaved q15 (int16 I/Q) samples as complex float."
    ),
}

# Computed-property getters keyed by the C declaration they sit above. Their
# @brief becomes the property __doc__ (field-backed props are documented via
# the manifest instead — see the module docstring).
GETTER_BLOCKS = {
    "q15_to_cf32": [
        (
            "int32_t q15_to_cf32_get_eof(",
            "/**\n"
            " * @brief True (1) once the backing file descriptor is"
            " exhausted.\n"
            " */\n",
        ),
    ],
}


def _enrich(obj: str) -> None:
    header = pathlib.Path("native/inc") / obj / f"{obj}_core.h"
    text = header.read_text(encoding="utf-8")

    # Replace jm's trivial scaffold brief on <obj>_create with a real summary.
    scaffold_re = re.compile(
        rf"/\*\*\n \* @brief Create a {obj} instance\..*?"
        rf"(?={obj}_state_t \*{obj}_create)",
        re.DOTALL,
    )
    new_create = f"/**\n * @brief {CREATE_BRIEFS[obj]}\n */\n"
    text, n = scaffold_re.subn(new_create, text, count=1)
    if n != 1:
        print(f"ERROR: {obj}_create scaffold brief not found", file=sys.stderr)
        sys.exit(1)

    # Prepend each computed getter's Doxygen block above its declaration.
    for decl, block in GETTER_BLOCKS.get(obj, []):
        if decl not in text:
            print(f"ERROR: declaration not found: {decl!r}", file=sys.stderr)
            sys.exit(1)
        text = text.replace(decl, block + decl, 1)

    header.write_text(text, encoding="utf-8")
    print(f"enriched {header}")


def main() -> None:
    for obj in CREATE_BRIEFS:
        _enrich(obj)


if __name__ == "__main__":
    main()
```

```sh
python3 .steps/04b_doxygen.py
just-makeit apply
```

---

## 5. Build and test

```sh
make
make test
```

`make` configures CMake and builds the `conv` extension module.
`make test` runs CTest (C lifecycle tests) and pytest (Python API tests)
for both `Cf32ToQ15` and `Q15ToCf32`.

---

## 6. Development install

```sh
pip install -e .
```

`pip install -e .` installs the package in editable mode using the
[just-buildit](https://github.com/just-buildit/just-buildit) PEP 517 backend.
The `.so` built in step 5 is used directly — no rebuild needed when you only
edit Python files.

After this, `from iqfile.conv import Cf32ToQ15, Q15ToCf32` works from anywhere.

---

## 7. Round-trip demo

```python
"""Round-trip demo: cf32 -> q15 file -> cf32, verify fidelity."""

import os
import sys
import tempfile
import numpy as np

# cwd is the project root when called from test.py; cmake builds the .so into src/.
sys.path.insert(0, "src")

from iqfile.conv import Cf32ToQ15, Q15ToCf32

N = 4096
rng = np.random.default_rng(42)

# ── Generate test signal (normalised to [-0.9, 0.9] to avoid clipping) ────
signal = (rng.standard_normal(N) + 1j * rng.standard_normal(N)).astype(
    np.complex64
)
signal *= 0.9 / np.max(np.abs(signal))

# ── Write cf32 -> q15 ─────────────────────────────────────────────────────
writer = Cf32ToQ15()
packed = writer.steps(signal)  # int32 array, shape (N,)
q15 = packed.view(np.int16)  # int16 view, shape (2N,)

with tempfile.NamedTemporaryFile(suffix=".q15", delete=False) as f:
    q15_path = f.name
    q15.tofile(f)

print(
    f"wrote    {N} complex samples -> {q15_path}  ({os.path.getsize(q15_path)} bytes)"
)
print(f"written: {writer.samples_written} samples")

# ── Read q15 -> cf32 ──────────────────────────────────────────────────────
fd = os.open(q15_path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
reader = Q15ToCf32(fd=fd)
recovered = reader.steps(N)

print(f"read:    {reader.samples_read} samples,  eof={reader.eof}")
os.close(fd)

# ── Verify round-trip fidelity ────────────────────────────────────────────
scale = 32767.0
quantisation_noise_floor = 1.0 / scale  # ≈ -90 dB
err = np.max(np.abs(signal - recovered))

print(f"max err: {err:.6f}  (floor ~{quantisation_noise_floor:.6f})")
assert err < 2.0 / scale, f"round-trip error too large: {err}"
print("PASSED")

os.unlink(q15_path)
```

The demo generates 4096 complex samples, writes them to a temporary `.q15`
file, reads them back, and verifies the round-trip error stays within one
quantisation step (~1/32767 ≈ −90 dBFS):

```
wrote    4096 complex samples -> /tmp/tmpXXXXXX.q15  (16384 bytes)
written: 4096 samples
read:    4096 samples,  eof=1
max err: 0.000031  (floor ~0.000031)
PASSED
```

Note the file size: 4096 samples × 4 bytes (two `int16_t`) = 16 384 bytes —
half the 32 768 bytes a cf32 file would use for the same signal.

---

## 8. Build a wheel

```sh
just-makeit build
```

`just-makeit build` runs CMake in release mode, packages the `.so` and Python
sources into a PEP 427 wheel, and writes it to `dist/`:

```
dist/iqfile-0.1.0-cp312-cp312-linux_x86_64.whl
```

Install it anywhere:

```sh
pip install dist/iqfile-*.whl
```

Or publish to PyPI:

```sh
pip install twine
twine upload dist/*
```
