"""End-to-end test: a header-only ring buffer with borrowed views.

The shape doppler's `DECLARE_DP_BUFFER` has: a macro template with no `.c`
file, whose `wait(n)` lends a pointer into memory the ring already owns. Three
jm features have to compose for that to be declarable, and this is the first
place all three are exercised together:

  * `header_only` (gh-1311)  -- the C lives in the header; there is no
    `<comp>_core.c`, and the CMake core library is INTERFACE.
  * `borrow` (gh-1312)       -- `wait(n)` returns a pointer; the binding wraps
    it without copying and pins the object so the memory outlives the view.
  * a borrowed `record_dtype` (gh-1310 decision B, gh-1317) -- numpy has no
    complex-integer dtype, so integer IQ comes back as a structured array.

Two objects in ONE module, because that is what the element type decides:

  | object      | element               | view                              |
  | ----------- | --------------------- | --------------------------------- |
  | `Cf32Ring`  | `float _Complex`      | `complex64`, 1-D                  |
  | `Iq16Ring`  | `iq16_t {i, q}`       | `[('i','<i2'),('q','<i2')]`, 1-D  |

They are separate components rather than one template because their element
shapes genuinely differ -- see gh-1310.

**The ring here is deliberately simpler than doppler's.** It is a fixed-capacity
circular buffer and `wait()` refuses a span that would wrap; doppler
double-maps its memory so a wrapping span is still contiguous. That mapping is
an implementation detail of the ring, not of the jm features under test, and
leaving it out keeps the example about the declarations.

Called by tests/test_examples.py via run(root).
Also runnable directly: python3 examples/ring_buffer/test.py
"""

import subprocess
import sys
from pathlib import Path

from just_makeit._example import scratch_dir

CAP_CF32 = 64  # complex samples
CAP_IQ16 = 128  # int16 storage slots == 64 samples


#: The author's own status vocabulary. jm never sees this -- it is named in
#: the manifest and emitted as `case` labels, which is why a row for `0` is
#: refused: 0 is success, and a borrow only asks after a NULL.
#:
#: The enum goes ABOVE jm's state struct (the manifest names it before any
#: method does); the function that reads the struct has to come BELOW it,
#: so the two are injected separately.
_CF32_STATUS_ENUM = """\
#include <stddef.h>

/** Why a read gave up. ONE function owns the precedence, so nothing
    re-derives it -- the binding reads this and nothing else. */
typedef enum {
    CF32_OK = 0,          /* n elements are readable now             */
    CF32_PENDING = 1,     /* fewer than n so far; nothing is wrong   */
    CF32_TOO_LARGE = 2,   /* n exceeds capacity: never satisfiable   */
    CF32_CLOSED = 3,      /* closed with fewer than n left: the end  */
    CF32_WRAPS = 4        /* the span would wrap the buffer          */
} cf32_ring_status_t;
"""

#: Its definition, in the order the binding depends on: `TOO_LARGE` before
#: `CLOSED` before `PENDING`, because "never satisfiable" outranks "not
#: yet" and a caller told the wrong one debugs the wrong end.
_CF32_STATUS_FN = f"""\
static inline cf32_ring_status_t
cf32_ring_wait_status (const ringdemo_cf32_ring_state_t *state, size_t n)
{{
    size_t have = state->head - state->tail;
    if (n > {CAP_CF32})
        return CF32_TOO_LARGE;
    if (n > have)
        return state->closed ? CF32_CLOSED : CF32_PENDING;
    if ((state->tail & {CAP_CF32 - 1}) + n > {CAP_CF32})
        return CF32_WRAPS;
    return CF32_OK;
}}

"""


def _insert_after(path: Path, marker: str, text: str) -> None:
    """Put *text* immediately after the line holding *marker*.

    After, not before: a generated definition's return type sits on its own
    line above its name, so inserting above the NAME lands between the two
    and the function loses its return type.
    """
    s = path.read_text(encoding="utf-8")
    i = s.index("\n", s.index(marker)) + 1
    path.write_text(s[:i] + text + s[i:], encoding="utf-8")


def _prepend_to_header(path: Path, text: str) -> None:
    """Put the author's own declarations into the sacred header.

    Before the method that names them, because jm renders the binding
    against what the header declares -- the same ordering `iq16_t` needs
    for the record ring below.
    """
    s = path.read_text(encoding="utf-8")
    path.write_text(
        s.replace(
            '#include "ringdemo/clib_common.h"',
            '#include "ringdemo/clib_common.h"\n\n' + text,
            1,
        ),
        encoding="utf-8",
    )


def _patch_body(path: Path, marker: str, body: str) -> None:
    """Replace the stub body of the function whose signature holds *marker*.

    The scaffold's stub is a no-op that compiles and returns NULL/0, which is
    what lets the untouched tree build; this is the author writing the real
    kernel over it.
    """
    s = path.read_text(encoding="utf-8")
    i = s.index(marker)
    open_ = s.index("{", i)
    close = s.index("\n}", open_)
    path.write_text(s[:open_] + "{\n" + body + s[close:], encoding="utf-8")


def run(root: Path) -> None:
    from just_makeit._method import run as jm_method
    from just_makeit._module import run as jm_module
    from just_makeit._new import run as jm_new
    from just_makeit._object import run as jm_object
    from just_makeit._property import run as jm_property
    from just_makeit._recorddecl import run as jm_record

    # ── 1. A project with one module to hold both rings ──────────────────
    jm_new("ringdemo", root / "ringdemo")
    proj = root / "ringdemo"
    jm_module(proj, "rings")

    # ── 2. The complex64 ring ────────────────────────────────────────────
    # `header_only` is the whole point: no `_core.c` is scaffolded, and the
    # CMake core library is INTERFACE rather than OBJECT.
    jm_object(
        proj,
        "cf32_ring",
        "rings",
        state_vars=[
            ("data", f"float _Complex[{CAP_CF32}]", ""),
            ("head", "size_t", "0"),
            ("tail", "size_t", "0"),
            # gh-1418: a producer that can CLOSE is what makes end-of-stream
            # a distinct answer from "not yet", which is the whole point of
            # the status table below.
            ("closed", "int", "0"),
        ],
        arg_type="void",
        return_type="float _Complex",
        no_step=True,
        header_only=True,
    )
    # The status enum and the function that owns the precedence are the
    # AUTHOR's, in the sacred header, and must exist before the method that
    # names them -- exactly as `iq16_t` does for the record ring below.
    _prepend_to_header(
        proj / "native/inc/ringdemo/cf32_ring/cf32_ring_core.h",
        _CF32_STATUS_ENUM,
    )
    # gh-1426 C: a message may name a PROPERTY, and an `expr` property has
    # no C getter at all -- its getset inlines the expression. That is the
    # shape a header-only component over someone else's struct actually
    # has, and it is why this is declared with `expr` rather than a field.
    jm_property(
        proj,
        "cf32_ring",
        "capacity",
        "rings",
        "size_t",
        False,
        expr=("sizeof(self->handle->data) / sizeof(self->handle->data[0])"),
    )
    _method(
        jm_method,
        proj,
        "cf32_ring",
        "write",
        arg_type="float _Complex[]",
        return_type="size_t",
        # gh-1426 B: a ring is the shape that must not silently cast, copy
        # and flatten its input -- the one path whose purpose is to avoid
        # copies.
        strict=True,
    )
    # The blocking-shaped read: n elements or a typed refusal. `nogil`
    # (gh-1418) is correctness rather than speed for a kernel that can
    # wait -- with the GIL held a producer thread could never run.
    _method(
        jm_method,
        proj,
        "cf32_ring",
        "wait",
        borrow=True,
        params=[("n", "size_t")],
        nogil=True,
        status_fn="cf32_ring_wait_status",
        status_errors=[
            {
                "status": "CF32_TOO_LARGE",
                "error": "ValueError",
                # gh-1426 C: the two numbers that make the message useful.
                # `{n}` is this method's param, `{capacity}` the property
                # declared above.
                "message": (
                    "wait({n}) can never be satisfied: "
                    "the ring holds {capacity}"
                ),
            },
            {
                "status": "CF32_CLOSED",
                "error": "EOFError",
                "message": "end of stream: the producer closed the ring",
            },
            {
                "status": "CF32_WRAPS",
                "error": "ValueError",
                "message": "that span wraps; consume() first",
            },
        ],
    )
    # The non-blocking twin. It shares ONE table with `wait` and differs
    # only in the row it declines to write: `CF32_PENDING` has no row, so
    # it falls through to `none_on_empty` and answers None (gh-1418).
    _method(
        jm_method,
        proj,
        "cf32_ring",
        "peek",
        borrow=True,
        params=[("n", "size_t")],
        none_on_empty=True,
        status_fn="cf32_ring_wait_status",
        status_errors=[
            {
                "status": "CF32_TOO_LARGE",
                "error": "ValueError",
                "message": (
                    "peek({n}) can never be satisfied: "
                    "the ring holds {capacity}"
                ),
            },
            {
                "status": "CF32_CLOSED",
                "error": "EOFError",
                "message": "end of stream: the producer closed the ring",
            },
        ],
    )
    # gh-1426 A: the release. Its count DEFAULTS to whatever the last
    # borrow handed out, so the caller writes the number once.
    _method(
        jm_method,
        proj,
        "cf32_ring",
        "consume",
        params=[("n", "size_t")],
        return_type="void",
        releases=["wait", "peek"],
    )
    # The producer's end-of-stream signal, and a second release: it takes
    # no count and simply invalidates whatever is outstanding.
    _method(
        jm_method,
        proj,
        "cf32_ring",
        "close",
        return_type="void",
        releases=["wait", "peek"],
    )

    # ── 3. The integer-IQ ring ───────────────────────────────────────────
    jm_object(
        proj,
        "iq16_ring",
        "rings",
        state_vars=[
            ("data", f"int16_t[{CAP_IQ16}]", ""),
            ("head", "size_t", "0"),
            ("tail", "size_t", "0"),
        ],
        arg_type="void",
        return_type="float _Complex",
        no_step=True,
        header_only=True,
    )
    # The record is the AUTHOR's type and must exist before the method that
    # names it -- it goes into the component's own (sacred) header.
    hdr = proj / "native/inc/ringdemo/iq16_ring/iq16_ring_core.h"
    hdr.write_text(
        hdr.read_text(encoding="utf-8").replace(
            '#include "ringdemo/clib_common.h"',
            '#include "ringdemo/clib_common.h"\n#include <stdint.h>\n\n'
            "/** One complex q15 sample: the record a borrowed view hands"
            " back. */\ntypedef struct {\n    int16_t i;\n    int16_t q;\n"
            "} iq16_t;",
            1,
        ),
        encoding="utf-8",
    )
    # gh-1404/gh-1405: the element is declared ONCE and both directions
    # reference it -- which is also what lets jm generate the contract
    # between them (`test_iq16_ring_invariants.py`). Declared with
    # `record`, not restated on each method, because a width family's rule
    # is "what you read is exactly what you can write".
    jm_record(
        proj,
        "iq16_ring",
        "iq16_t",
        [
            {"name": "i", "type": "int16_t"},
            {"name": "q", "type": "int16_t"},
        ],
    )
    _method(
        jm_method,
        proj,
        "iq16_ring",
        "write",
        arg_type="iq16_t[]",
        return_type="size_t",
    )
    _method(
        jm_method,
        proj,
        "iq16_ring",
        "wait",
        borrow=True,
        params=[("n", "size_t")],
        # The columns are NOT restated here: `[[iq16_ring.records]]`
        # declares them once and gh-1407 refuses a second description of
        # the same bytes.
        record_dtype="iq16_t",
    )
    _method(
        jm_method,
        proj,
        "iq16_ring",
        "consume",
        params=[("n", "size_t")],
        return_type="void",
    )

    # ── 4. The author writes the kernels -- IN THE HEADER ────────────────
    # There is no `_core.c` to write them into. jm's own guidance says so:
    # "Done!  Implement ringdemo_cf32_ring_wait() in cf32_ring_core.h".
    h = proj / "native/inc/ringdemo/cf32_ring/cf32_ring_core.h"
    # The precedence function goes in first, above the kernels that use it
    # and below the struct it reads.
    _insert_after(h, "} ringdemo_cf32_ring_state_t;", "\n" + _CF32_STATUS_FN)
    _patch_body(
        h,
        "ringdemo_cf32_ring_write(ringdemo_cf32_ring_state_t *state",
        f"""\
    size_t free_ = {CAP_CF32} - (state->head - state->tail);
    size_t k = x_len < free_ ? x_len : free_;
    for (size_t i = 0; i < k; i++)
        state->data[(state->head + i) & {CAP_CF32 - 1}] = x[i];
    state->head += k;
    return k;""",
    )
    # Both reads go through the ONE status function, so the answer the
    # binding raises on and the answer the kernel acts on cannot disagree.
    _patch_body(
        h,
        "ringdemo_cf32_ring_wait(ringdemo_cf32_ring_state_t *state",
        """\
    if (cf32_ring_wait_status(state, n) != CF32_OK)
        return NULL;
    return &state->data[state->tail & """
        f"{CAP_CF32 - 1}];",
    )
    _patch_body(
        h,
        "ringdemo_cf32_ring_peek(ringdemo_cf32_ring_state_t *state",
        """\
    if (cf32_ring_wait_status(state, n) != CF32_OK)
        return NULL;
    return &state->data[state->tail & """
        f"{CAP_CF32 - 1}];",
    )
    _patch_body(
        h,
        "ringdemo_cf32_ring_consume(ringdemo_cf32_ring_state_t *state",
        """\
    size_t have = state->head - state->tail;
    state->tail += n < have ? n : have;""",
    )
    _patch_body(
        h,
        "ringdemo_cf32_ring_close(ringdemo_cf32_ring_state_t *state",
        """\
    state->closed = 1;""",
    )

    h = proj / "native/inc/ringdemo/iq16_ring/iq16_ring_core.h"
    _patch_body(
        h,
        "ringdemo_iq16_ring_write(ringdemo_iq16_ring_state_t *state",
        f"""\
    /* gh-1405: x is ROWS of the declared element, not interleaved int16 --
       the same struct the reader hands back, so the two faces cannot
       disagree about what one sample is. Storage stays int16: two slots
       per sample. */
    size_t free_ = ({CAP_IQ16} - (state->head - state->tail)) / 2;
    size_t k = x_len < free_ ? x_len : free_;
    for (size_t i = 0; i < k; i++) {{
        state->data[(state->head + 2 * i) % {CAP_IQ16}] = x[i].i;
        state->data[(state->head + 2 * i + 1) % {CAP_IQ16}] = x[i].q;
    }}
    state->head += 2 * k;
    return k;""",
    )
    _patch_body(
        h,
        "ringdemo_iq16_ring_wait(ringdemo_iq16_ring_state_t *state",
        f"""\
    /* `data` is int16 storage; one RECORD is two of them, so the count is in
       SAMPLES and the offset is in storage slots. */
    size_t have = (state->head - state->tail) / 2;
    size_t off = state->tail % {CAP_IQ16};
    if (n > have || off + 2 * n > {CAP_IQ16})
        return NULL;
    return (iq16_t *)&state->data[off];""",
    )
    _patch_body(
        h,
        "ringdemo_iq16_ring_consume(ringdemo_iq16_ring_state_t *state",
        """\
    /* n is in SAMPLES; the storage is int16, so two slots per sample. */
    size_t have = state->head - state->tail;
    size_t k = 2 * n < have ? 2 * n : have;
    state->tail += k;""",
    )

    # ── 4b. Re-apply, now that the kernels are real ──────────────────────
    # The element contract (`test_<obj>_invariants.py`) is generated only
    # once the reader is more than a stub -- a borrowing stub returns NULL,
    # so the assertions would be red on a project nobody has implemented
    # yet. Re-applying after writing the bodies is what a user does, and it
    # is the step that produces the file this example then RUNS.
    from just_makeit._apply import run as jm_apply

    jm_apply(proj)

    # ── 5. Build ─────────────────────────────────────────────────────────
    # A fresh build dir on purpose: the failure this example was written after
    # was a CONFIGURE error ($<TARGET_OBJECTS:> on an INTERFACE library), which
    # only a clean configure shows.
    _cmd(["cmake", "-S", str(proj), "-B", str(proj / "build")], proj)
    _cmd(["cmake", "--build", str(proj / "build")], proj)

    # ── 6. Both rings, from Python ───────────────────────────────────────
    # repr(): the path becomes a Python string LITERAL in the demo source, and
    # a Windows `C:\Users` pasted in raw is a `\U` escape (gh-1368).
    _cmd([sys.executable, "-c", _DEMO.format(proj=repr(str(proj)))], proj)

    # ── 7. Run the contract jm generated ─────────────────────────────────
    # gh-1434: `test_<obj>_invariants.py` is jm's own file -- generated,
    # marked DO NOT EDIT, and until now executed by nothing in this repo.
    # Six defects reached a downstream through that gap (gh-1432), every
    # one of them a file that READ fine and could not RUN. So the example
    # that produces it also runs it.
    inv = (
        proj
        / "src"
        / "ringdemo"
        / "rings"
        / "tests"
        / "test_iq16_ring_invariants.py"
    )
    if not inv.exists():
        raise AssertionError(
            f"jm generated no element contract at {inv}. The declaration "
            "pair (a writer taking `iq16_t[]`, a reader with "
            "`record_dtype = iq16_t`) is what produces it -- if that is "
            "still declared, the generator stopped emitting it."
        )
    _cmd(
        [sys.executable, "-m", "pytest", str(inv), "-q"],
        proj,
        extra_path=True,
    )


def _method(
    jm_method,
    proj,
    obj,
    name,
    *,
    arg_type="void",
    return_type="float _Complex",
    **kw,
):
    jm_method(proj, obj, name, "rings", arg_type, return_type, False, [], **kw)


def _cmd(args, cwd, extra_path: bool = False):
    env = None
    if extra_path:
        # The same two places `_DEMO` puts on `sys.path`: the built
        # extension next to its build dir, and the package source.
        import glob
        import os

        paths = glob.glob(str(Path(cwd) / "build*/**/"), recursive=True)
        paths.append(str(Path(cwd) / "src"))
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=900, env=env
    )
    if r.returncode != 0:
        raise AssertionError(
            f"Command failed: {' '.join(str(a) for a in args)}\n"
            f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        )
    return r


_DEMO = """
import sys, glob
import numpy as np
sys.path[:0] = glob.glob({proj} + "/build*/**/", recursive=True) + [{proj} + "/src"]
from ringdemo.rings import Cf32Ring, Iq16Ring

# ── complex64: a plain borrowed view ────────────────────────────────────
r = Cf32Ring()
assert r.write(np.arange(8, dtype=np.complex64)) == 8
v = r.wait(4)
assert v.dtype == np.complex64, v.dtype
assert v.shape == (4,), v.shape
assert list(v[:4]) == [0, 1, 2, 3], v
assert v.base is r, "the view must pin the object that owns the memory"
assert not v.flags.writeable, "a consumer must not write through a borrow"
r.consume(4)
assert list(r.wait(4)) == [4, 5, 6, 7]

# ── the count is written ONCE: consume() takes the outstanding borrow's ──
r2 = Cf32Ring()
r2.write(np.arange(8, dtype=np.complex64))
v = r2.wait(5)
r2.consume()                       # no argument: releases the 5 just lent
assert list(r2.wait(3)) == [5, 6, 7]
r2.consume(1)                      # ...and an explicit k < n stays legal
assert list(r2.wait(2)) == [6, 7]
try:
    Cf32Ring().consume()           # nothing outstanding is a caller bug
    raise AssertionError("a release with no borrow behind it must say so")
except RuntimeError as e:
    assert "no outstanding borrow" in str(e), e

# ── a NULL means one of several things, and says which ──────────────────
r3 = Cf32Ring()
assert r3.capacity == 64, r3.capacity       # an `expr` property, inlined
try:
    r3.wait(99999)
    raise AssertionError("an unsatisfiable count must be refused")
except ValueError as e:
    # The two numbers that make it actionable, from a param and a property.
    assert "wait(99999)" in str(e) and "holds 64" in str(e), e

# peek() shares ONE table with wait() and declines only the PENDING row,
# so "not yet" is None rather than an exception...
assert r3.peek(4) is None
r3.write(np.arange(4, dtype=np.complex64))
assert list(r3.peek(4)) == [0, 1, 2, 3]
# ...while a row they share raises identically on both.
try:
    r3.peek(99999)
    raise AssertionError("peek must refuse what it can never satisfy")
except ValueError:
    pass

# ── end of stream is a normal event a consumer loop CATCHES ─────────────
r3.close()                          # a second release: no count, just clears
try:
    r3.wait(8)
    raise AssertionError("a closed ring must report the end of the stream")
except EOFError as e:
    assert "end of stream" in str(e), e

# ── a strict input is refused, not quietly cast, copied and flattened ───
r4 = Cf32Ring()
try:
    r4.write(np.arange(4, dtype=np.float32))
    raise AssertionError("the wrong dtype must be refused")
except TypeError as e:
    assert "complex64" in str(e), e
try:
    r4.write(np.zeros((4, 2), dtype=np.complex64))
    raise AssertionError("a 2-D array must not be accepted as 8 samples")
except ValueError as e:
    assert "1-D" in str(e), e

# ── integer IQ: a borrowed RECORD (gh-1310 decision B) ──────────────────
q = Iq16Ring()
# gh-1405: rows of the DECLARED element go in, and the same element comes
# back out -- one declaration, both directions.
_IQ = np.dtype([("i", "<i2"), ("q", "<i2")])
q.write(np.array([(1, 100), (2, 101), (3, 102), (4, 103)], dtype=_IQ))
w = q.wait(3)
assert w.dtype == np.dtype([("i", "<i2"), ("q", "<i2")]), w.dtype
assert w.itemsize == 4, w.itemsize          # == sizeof(iq16_t)
assert w.shape == (3,), w.shape             # ONE element per sample
assert [tuple(map(int, x)) for x in w] == [(1, 100), (2, 101), (3, 102)]
assert list(w["i"]) == [1, 2, 3] and list(w["q"]) == [100, 101, 102]
assert w.base is q

# The measurement that chose this representation: the packed-int32
# alternative increments I only and says nothing. A structured array refuses.
try:
    w + 1
    raise AssertionError("structured IQ must refuse arithmetic, not guess")
except TypeError:
    pass

q.consume(2)
assert [tuple(map(int, x)) for x in q.wait(2)] == [(3, 102), (4, 103)]
print("ring_buffer: both rings OK")
"""


if __name__ == "__main__":
    with scratch_dir() as tmp:
        run(Path(tmp))
    print("ring_buffer: PASSED")
