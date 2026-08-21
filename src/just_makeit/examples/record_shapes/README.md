# record_shapes example

One object, three methods, three different *shapes* of result — and the only
thing that changes between them is which key the method declares.

| declares | returns | the C kernel writes |
| ---------------------- | -------------------------------------- | ------------------------------- |
| `single` | ONE record, a named `PyStructSequence` | the struct **by value** |
| `record_dtype` | an ARRAY of records, a structured `ndarray` | `<struct> *out` |
| neither (just `result_fields`) | a `list[tuple]` | `<row> *result, size_t max_results` |

`result_fields` appears in all three, which is exactly why they are worth
seeing side by side: on its own it does not tell you what you get back.

**All three name a struct of yours as the return type** — that part does not
vary, which is what makes the key the only variable. jm never sees a field of
any of them: it writes the prototypes that mention them and, for
`record_dtype`, emits C that builds the numpy dtype at runtime from
`offsetof` and `sizeof`.

## TL;DR — see it work first

```sh
. <(curl -fsSL https://just-buildit.github.io/just-makeit/install.sh)
just-makeit example record_shapes
# record_shapes: PASSED
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

## 1. Scaffold a collector

```sh
just-makeit new evlog \
    --object collector \
    --state "count:uint64_t:0" \
    --state "t:uint64_t[64]" \
    --state "v:double[64]" \
    --arg-type double \
    --return-type void
```

`step()` takes a `double` and returns nothing — it records. The two fixed
length array state fields are the ring the records live in, and `count` is how
many have ever arrived. Everything the three methods report is read back out
of that ring.

---

## 2. Declare the record structs — they are yours, not jm's

```python
"""Declare the two record structs in the sacred header.

They are the author's, not jm's: jm writes prototypes that mention them and
never reads a field. Adding them BEFORE declaring the methods is what lets
`--return-type evlog_summary_t` and `--record-dtype evlog_rec_t` resolve.
"""

from pathlib import Path

HEADER = Path("native/inc/collector/collector_core.h")

STRUCTS = """\
/* One row of the log — the element type of what read() hands back. */
typedef struct
{
    uint64_t t;
    double   v;
} evlog_rec_t;

/* The whole-log summary — ONE of these, returned by value. */
typedef struct
{
    uint64_t n;
    double   mean;
} evlog_summary_t;

/* One peak. Every shape names a struct of yours; only the KEY differs. */
typedef struct
{
    size_t index;
    double value;
} evlog_peak_t;

"""


def main() -> None:
    text = HEADER.read_text(encoding="utf-8")
    if "evlog_rec_t" in text:
        return
    # Ahead of the generated state struct, which is the first `typedef
    # struct {` in the file.
    anchor = text.index("typedef struct {")
    HEADER.write_text(
        text[:anchor] + STRUCTS + text[anchor:], encoding="utf-8"
    )


if __name__ == "__main__":
    main()
```

This has to happen first. `--return-type evlog_summary_t` and
`--record-dtype evlog_rec_t` name types that must already exist in the sacred
header; jm puts them in prototypes and never looks inside them.

---

## 3. Declare the same data three ways

```sh
cd evlog

# ── shape 1: ONE record, by value ───────────────────────────────────────────
just-makeit method collector summary \
    --arg-type void \
    --return-type evlog_summary_t \
    --single \
    --result-field "n:uint64_t" \
    --result-field "mean:double" \
    --record-name Summary \
    --record-doc "Count and mean of everything recorded so far."

# ── shape 2: an ARRAY of records, as a structured ndarray ────────────────────
just-makeit method collector read \
    --arg-type void \
    --return-type double \
    --variable-output \
    --record-dtype evlog_rec_t \
    --result-field "t:uint64_t" \
    --result-field "v:double"

# ── shape 3: a list of tuples ───────────────────────────────────────────────
just-makeit method collector peaks \
    --arg-type void \
    --return-type evlog_peak_t \
    --result-field "index:size_t" \
    --result-field "value:double"
```

Three things to notice, because each one is a trap if you meet it later:

- **Every shape takes YOUR struct as the return type**, not a scalar. A
    scalar there is the mistake worth naming: `--single` with
    `--return-type double` emits a binding that reads `_r.n` off a `double`,
    and the plain shape emits `results[i].index` off one. Both are accepted
    by the CLI and neither compiles (gh-1064).
- **`--record-name` / `--record-doc` only apply to `--single`.** They name and
    document the record *type*, and the other two shapes have no type to name:
    one hands back an `ndarray`, the other a plain `list`.
- **`peaks` does NOT pass `--variable-output`.** Its count comes back as the
    return value of the kernel, which is what `size_t` + `max_results` is for.
    The flag belongs to the `record_dtype` shape, whose kernel fills a caller
    sized `out` buffer and needs a `read_max_out()` companion to size it.
    Passing it here also emits code that does not compile (gh-1064).

`--single` and `--record-dtype` are mutually exclusive; the CLI rejects the
pair.

---

## 4. Implement the kernels

```python
"""Implement step() and the three record kernels.

Every body here is the author's. jm generated the prototypes, the argument
parsing, the `PyStructSequence`, and the runtime dtype; what it cannot know is
what a record MEANS, so each `<<IMPLEMENT>>` placeholder is a no-op until this
runs.
"""

from pathlib import Path

HEADER = Path("native/inc/collector/collector_core.h")
CORE = Path("native/src/collector/collector_core.c")

STEP_BODY = """\
collector_step(collector_state_t *state, double x)
{
    size_t slot = (size_t)(state->count % EVLOG_CAPACITY);

    state->t[slot] = state->count;
    state->v[slot] = x;
    state->count++;
}"""

HELPERS = """\
/* Ring capacity. Must match the [64] on the t/v state fields: jm sizes the
 * struct members from the manifest and this constant is how the kernels
 * agree with it.
 */
#define EVLOG_CAPACITY 64

/* A value has to beat this to count as a peak. */
#define EVLOG_PEAK_THRESHOLD 1.0
"""

HELD = """\
/* How many slots actually hold data: the ring is not full until `count`
 * reaches capacity, and reading past that returns whatever create() zeroed.
 */
static size_t
collector_held(const collector_state_t *state)
{
    return state->count < EVLOG_CAPACITY ? (size_t)state->count
                                         : (size_t)EVLOG_CAPACITY;
}

"""

SUMMARY = """\
    size_t          held = collector_held(state);
    evlog_summary_t _r   = {0};
    double          sum  = 0.0;

    /* `n` is every record ever seen; the mean is over what the ring still
     * holds. Returned BY VALUE -- jm's binding converts it to a Summary.
     */
    _r.n = state->count;
    for (size_t i = 0; i < held; i++)
        sum += state->v[i];
    _r.mean = held ? sum / (double)held : 0.0;
    return _r;"""

READ_MAX_OUT = """\
    (void)n;
    /* Upper bound on what read() can produce, so the binding can size the
     * `out` buffer before calling it.
     */
    return collector_held(state);"""

READ = """\
    size_t held = collector_held(state);

    (void)n;
    /* Fill the caller's buffer with whole records; the return value is how
     * many are valid. jm turns those into a structured ndarray.
     */
    for (size_t i = 0; i < held; i++) {
        out[i].t = state->t[i];
        out[i].v = state->v[i];
    }
    return held;"""

PEAKS = """\
    size_t held  = collector_held(state);
    size_t n_out = 0;

    /* Whole evlog_peak_t rows into result[], capped at max_results; the
     * return value is how many were written. jm turns each row into a
     * tuple, reading the members named by result_fields.
     */
    for (size_t i = 0; i < held && n_out < max_results; i++) {
        if (state->v[i] <= EVLOG_PEAK_THRESHOLD)
            continue;
        result[n_out].index = i;
        result[n_out].value = state->v[i];
        n_out++;
    }
    return n_out;"""


def _replace(text: str, old: str, new: str, what: str) -> str:
    assert old in text, (
        f"anchor for {what} not found -- did the scaffold change?"
    )
    return text.replace(old, new, 1)


def main() -> None:
    h = HEADER.read_text(encoding="utf-8")
    h = _replace(
        h,
        """collector_step(collector_state_t *state, double x)
{
    (void)state; (void)x; /* TODO: implement */
}""",
        STEP_BODY,
        "step()",
    )
    h = _replace(
        h,
        '#include "clib_common.h"',
        '#include "clib_common.h"\n\n' + HELPERS,
        "helper defines",
    )
    HEADER.write_text(h, encoding="utf-8")

    c = CORE.read_text(encoding="utf-8")
    c = _replace(
        c,
        "/* <<IMPLEMENT: compute and return the record >> */",
        HELD + "/* <<IMPLEMENT: compute and return the record >> */",
        "held() helper",
    )
    c = _replace(
        c,
        "    (void)state;\n\n    evlog_summary_t _r = {0};\n    return _r; /* placeholder */",
        SUMMARY,
        "summary()",
    )
    c = _replace(
        c,
        "    (void)state; (void)n;\n    return 0; /* placeholder */",
        READ_MAX_OUT,
        "read_max_out()",
    )
    c = _replace(
        c,
        "    (void)state;\n    (void)n;\n    (void)out;\n    return 0; /* placeholder */",
        READ,
        "read()",
    )
    c = _replace(
        c,
        "    (void)state;\n\n    (void)result; (void)max_results;\n    return 0; /* placeholder */",
        PEAKS,
        "peaks()",
    )
    CORE.write_text(c, encoding="utf-8")


if __name__ == "__main__":
    main()
```

Three bodies, three different contracts, and none of them is guessable from
`result_fields` alone:

- `summary()` builds an `evlog_summary_t` on the stack and **returns it**.
- `read()` writes whole `evlog_rec_t` values into the `out` buffer the
    binding sized for it, and returns how many are valid. Its
    `read_max_out()` companion is what the binding calls first to do that
    sizing.
- `peaks()` writes whole `evlog_peak_t` rows into `result[]`, capped at
    `max_results`, and returns how many. jm reads the members named by
    `result_fields` off each row and builds a tuple from them.

---

## 5. Build

```sh
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 4
ctest --test-dir build --output-on-failure
```

---

## 6. Read the same ring back three ways

```python
"""Read one ring back three ways."""

import sys

sys.path.insert(0, "src")

import numpy as np  # noqa: E402

from evlog import Collector  # noqa: E402

c = Collector()
for x in (0.5, 2.5, 0.25, 1.75):
    c.step(x)

# ── shape 1: ONE record ──────────────────────────────────────────────────
s = c.summary()
print(f"summary()  -> {s}")
print(f"             type={type(s).__name__}  n={s.n}  mean={s.mean:.4f}")
assert s.n == 4
assert abs(s.mean - 1.25) < 1e-12
# A PyStructSequence: named fields AND tuple indexing.
assert s[0] == s.n and s[1] == s.mean

# ── shape 2: an ARRAY of records ─────────────────────────────────────────
rows = c.read()
print(f"read()     -> {rows!r}")
print(f"             dtype={rows.dtype}")
assert isinstance(rows, np.ndarray)
# The dtype was built by the generated C at runtime, from offsetof/sizeof on
# the author's struct -- jm never saw these names in a type context.
assert rows.dtype.names == ("t", "v")
assert rows.shape == (4,)
assert rows["t"].tolist() == [0, 1, 2, 3]
assert np.allclose(rows["v"], [0.5, 2.5, 0.25, 1.75])

# ── shape 3: a list of tuples ────────────────────────────────────────────
pk = c.peaks()
print(f"peaks()    -> {pk!r}")
assert isinstance(pk, list) and all(isinstance(p, tuple) for p in pk)
# Only values above the kernel's threshold, as (index, value) pairs.
assert pk == [(1, 2.5), (3, 1.75)]

print("record_shapes demo: PASSED")
```

```
summary()  -> Summary(n=4, mean=1.25)
             type=Summary  n=4  mean=1.2500
read()     -> array([(0, 0.5 ), (1, 2.5 ), (2, 0.25), (3, 1.75)],
                    dtype=[('t', '<u8'), ('v', '<f8')])
             dtype=[('t', '<u8'), ('v', '<f8')]
peaks()    -> [(1, 2.5), (3, 1.75)]
record_shapes demo: PASSED
```

The `read()` dtype is the part worth pausing on. `('t', '<u8'), ('v', '<f8')`
was not written down anywhere — jm has never seen inside `evlog_rec_t`. The
generated binding builds that dtype the first time `read()` is called, from
`offsetof(evlog_rec_t, t)` and `sizeof`, so it stays correct if you reorder
the struct or the compiler pads it differently.
