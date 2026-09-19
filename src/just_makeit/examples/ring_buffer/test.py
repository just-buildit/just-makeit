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
import tempfile
from pathlib import Path

CAP_CF32 = 64  # complex samples
CAP_IQ16 = 128  # int16 storage slots == 64 samples


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
        ],
        arg_type="void",
        return_type="float _Complex",
        no_step=True,
        header_only=True,
    )
    _method(
        jm_method,
        proj,
        "cf32_ring",
        "write",
        arg_type="float _Complex[]",
        return_type="size_t",
    )
    _method(
        jm_method,
        proj,
        "cf32_ring",
        "wait",
        borrow=True,
        params=[("n", "size_t")],
    )
    _method(
        jm_method,
        proj,
        "cf32_ring",
        "consume",
        params=[("n", "size_t")],
        return_type="void",
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
    hdr = proj / "native/inc/iq16_ring/iq16_ring_core.h"
    hdr.write_text(
        hdr.read_text(encoding="utf-8").replace(
            '#include "clib_common.h"',
            '#include "clib_common.h"\n#include <stdint.h>\n\n'
            "/** One complex q15 sample: the record a borrowed view hands"
            " back. */\ntypedef struct {\n    int16_t i;\n    int16_t q;\n"
            "} iq16_t;",
            1,
        ),
        encoding="utf-8",
    )
    _method(
        jm_method,
        proj,
        "iq16_ring",
        "write",
        arg_type="int16_t[]",
        return_type="size_t",
    )
    _method(
        jm_method,
        proj,
        "iq16_ring",
        "wait",
        borrow=True,
        params=[("n", "size_t")],
        record_dtype="iq16_t",
        result_fields=[
            {"name": "i", "type": "int16_t"},
            {"name": "q", "type": "int16_t"},
        ],
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
    # "Done!  Implement cf32_ring_wait() in cf32_ring_core.h".
    h = proj / "native/inc/cf32_ring/cf32_ring_core.h"
    _patch_body(
        h,
        "cf32_ring_write(cf32_ring_state_t *state",
        f"""\
    size_t free_ = {CAP_CF32} - (state->head - state->tail);
    size_t k = x_len < free_ ? x_len : free_;
    for (size_t i = 0; i < k; i++)
        state->data[(state->head + i) & {CAP_CF32 - 1}] = x[i];
    state->head += k;
    return k;""",
    )
    _patch_body(
        h,
        "cf32_ring_wait(cf32_ring_state_t *state",
        f"""\
    size_t have = state->head - state->tail;
    size_t off = state->tail & {CAP_CF32 - 1};
    /* Contiguous spans only. doppler double-maps so a wrapping span is still
       contiguous; refusing is enough to show the borrow. */
    if (n > have || off + n > {CAP_CF32})
        return NULL;
    return &state->data[off];""",
    )
    _patch_body(
        h,
        "cf32_ring_consume(cf32_ring_state_t *state",
        """\
    size_t have = state->head - state->tail;
    state->tail += n < have ? n : have;""",
    )

    h = proj / "native/inc/iq16_ring/iq16_ring_core.h"
    _patch_body(
        h,
        "iq16_ring_write(iq16_ring_state_t *state",
        f"""\
    /* x is INTERLEAVED int16: [i0,q0,i1,q1,...]. */
    size_t free_ = {CAP_IQ16} - (state->head - state->tail);
    size_t k = x_len < free_ ? x_len : free_;
    for (size_t i = 0; i < k; i++)
        state->data[(state->head + i) % {CAP_IQ16}] = x[i];
    state->head += k;
    return k;""",
    )
    _patch_body(
        h,
        "iq16_ring_wait(iq16_ring_state_t *state",
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
        "iq16_ring_consume(iq16_ring_state_t *state",
        """\
    /* n is in SAMPLES; the storage is int16, so two slots per sample. */
    size_t have = state->head - state->tail;
    size_t k = 2 * n < have ? 2 * n : have;
    state->tail += k;""",
    )

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


def _cmd(args, cwd):
    r = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=900
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

# ── integer IQ: a borrowed RECORD (gh-1310 decision B) ──────────────────
q = Iq16Ring()
q.write(np.array([1, 100, 2, 101, 3, 102, 4, 103], dtype=np.int16))
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
    with tempfile.TemporaryDirectory() as tmp:
        run(Path(tmp))
    print("ring_buffer: PASSED")
