"""An output size past ``NPY_MAX_INTP`` raises ``OverflowError`` (gh-1710).

Every binding that sizes an output from a C value -- a function's
``out_size``, a caller-sized ``[M]`` length, a function's list-of-records
capacity, a method's ``max_out()``, a method's integer-param length, a
borrowed view's count, a handle's ``out_len_fn`` -- cast that value straight
to ``npy_intp``. ``(npy_intp)SIZE_MAX`` is ``-1``, so the caller saw numpy's
``ValueError: negative dimensions are not allowed``, which names neither the
call nor the size. Two were worse than a confusing message: the ``str``
output's ``malloc(_cap + 1)`` wrapped to ``malloc(0)``, and the
list-of-records ``malloc(_max * sizeof(T))`` wrapped to a SHORT buffer the
callee then filled ``_max`` records into.

Every site now goes through ``_coerce.output_size_c``, the one emitter. Each
is proven here on a real generated project, compiled and imported: an
oversized request raises ``OverflowError`` naming the member and the size,
and an ordinary size still allocates.
"""

from __future__ import annotations

from _jminc import INC_ROOT  # noqa: E402

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _coerce  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

SIZE_MAX = 2**64 - 1
TOO_LARGE = f"output of {SIZE_MAX} elements is too large"
# Under PY_SSIZE_T_MAX, but `* sizeof(rec_t)` (4) is 2**64: malloc(0).
WRAPS_ON_MULTIPLY = 2**62


def _skip_reason() -> str | None:
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()


def test_the_emitter_checks_before_it_converts():
    """The bound is tested on the size_t, before the narrowing cast."""
    src = _coerce.output_size_c("_dim", "f(x)", "fb", "Py_DECREF(a);")
    i_eval = src.index("size_t _dim_need = (size_t)(f(x));")
    i_check = src.index("if (_dim_need > (size_t)NPY_MAX_INTP)")
    i_release = src.index("Py_DECREF(a);")
    i_raise = src.index("PyExc_OverflowError")
    i_cast = src.index("npy_intp _dim = (npy_intp)_dim_need;")
    assert i_eval < i_check < i_release < i_raise < i_cast


# -- a real project: module functions + object methods -----------------------

REC_DECL = "/* Declare module-level functions here. */\n"
REC_T = "typedef struct { uint32_t a; } rec_t;\n"
PEEK = re.compile(
    r"(gen_peek\(gen_state_t \*state, size_t n\)\n\{\n"
    r"    \(void\)state; \(void\)n;\n)((?:.*\n)*?)    return NULL;\n"
)

MAX_OUT = re.compile(
    r"(gen_burst_max_out\(gen_state_t \*state, size_t n\)\n\{\n)"
    r"    \(void\)state; \(void\)n;\n    return 0; /\* placeholder \*/\n"
)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if _SKIP:
        pytest.skip(_SKIP)
    dest = tmp_path_factory.mktemp("gh1710") / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", dest, modules=["wfm"], c_prefix=None)
        object_run(
            dest, "gen", module=None, state_vars=[("phase", "uint32_t", "0")]
        )
        # site 1: a self-sizing ndarray output, sized by `out_size`.
        function_run(
            dest,
            "fb",
            "wfm",
            params=[("n", "size_t")],
            return_type="size_t",
            out_type="uint8_t",
            variable_output=True,
            out_size="n",
        )
        # site 2: the self-sizing `str` output (`malloc(_cap + 1)`).
        function_run(
            dest,
            "fs",
            "wfm",
            params=[("n", "size_t")],
            return_type="size_t",
            out_type="str",
            variable_output=True,
            out_size="n",
        )
        # site 3: a caller-sized output whose length is the `[M]` param.
        function_run(
            dest,
            "fc",
            "wfm",
            params=[("M", "uint64_t")],
            return_type="void",
            out_type="float64[M]",
        )
        # site 4: a list-of-records function whose capacity is a param.
        function_run(
            dest,
            "fr",
            "wfm",
            params=[("m", "size_t")],
            return_type="rec_t",
            result_fields=[{"name": "a", "type": "uint32_t"}],
            max_results_param="m",
        )
        # site 5: a variable_output method, allocated from `max_out()`.
        method_run(dest, "gen", "burst", None, "void", "uint32_t", True, [])
        # site 6: an `out_type` method sized by its integer param.
        method_run(
            dest,
            "gen",
            "fill",
            None,
            arg_type="void",
            return_type="void",
            variable_output=False,
            multi_output=[],
            params=[("n", "uint64_t")],
            out_type="float",
        )
        # site 7: a borrowed view, whose count is the caller's param.
        method_run(
            dest,
            "gen",
            "peek",
            None,
            "void",
            "float",
            False,
            [],
            params=[("n", "size_t")],
            borrow=True,
        )
    core = dest / "native/src/gen/gen_core.c"
    text, n = MAX_OUT.subn(
        r"\1    (void)state; (void)n;\n    return (size_t)-1;\n",
        core.read_text("utf-8"),
    )
    assert n == 1, "max_out stub shape changed; update this test"
    # A non-NULL lend, so the view is built and its count is what refuses.
    text, n = PEEK.subn(
        r"\1    static float _buf[4];\n    return _buf;\n", text
    )
    assert n == 1, "peek stub shape changed; update this test"
    core.write_text(text, encoding="utf-8")
    hdr = dest / INC_ROOT / "wfm/wfm_core.h"
    h = hdr.read_text("utf-8")
    assert h.count(REC_DECL) == 1, "wfm_core.h shape changed"
    hdr.write_text(h.replace(REC_DECL, REC_DECL + REC_T), encoding="utf-8")

    build = dest / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(dest),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
    return dest


def _run(dest: Path, body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", body],
        cwd=dest,
        env={**os.environ, "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )


def _raises_overflow(
    dest: Path, call: str, who: str, imp: str, size: int = SIZE_MAX
) -> None:
    r = _run(
        dest,
        f"{imp}\n"
        "try:\n"
        f"    {call}\n"
        "except OverflowError as e:\n"
        "    print('OVERFLOW', e)\n"
        "else:\n"
        "    print('NO ERROR')\n",
    )
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    want = f"OVERFLOW {who}: output of {size} elements is too large"
    assert r.stdout.strip() == want, r.stdout


_WFM = "from p.wfm import fb, fs, fc, fr"
_GEN = "from p.gen import Gen"


def test_function_ndarray_out_size(built):
    _raises_overflow(built, f"fb({SIZE_MAX})", "fb", _WFM)


def test_function_str_out_size(built):
    _raises_overflow(built, f"fs({SIZE_MAX})", "fs", _WFM)


def test_function_caller_sized_length(built):
    _raises_overflow(built, f"fc({SIZE_MAX})", "fc", _WFM)


def test_function_record_capacity(built):
    _raises_overflow(built, f"fr({SIZE_MAX})", "fr", _WFM)


def test_function_record_capacity_wrapping_on_the_multiply(built):
    """Below ``PY_SSIZE_T_MAX``, but ``_max * sizeof(rec_t)`` wraps to 0."""
    _raises_overflow(
        built,
        f"fr({WRAPS_ON_MULTIPLY})",
        "fr",
        _WFM,
        size=WRAPS_ON_MULTIPLY,
    )


def test_method_max_out(built):
    _raises_overflow(built, "Gen().burst(4)", "Gen.burst", _GEN)


def test_method_integer_param_length(built):
    _raises_overflow(built, f"Gen().fill({SIZE_MAX})", "Gen.fill", _GEN)


def test_borrowed_view_count(built):
    _raises_overflow(built, f"Gen().peek({SIZE_MAX})", "Gen.peek", _GEN)


# A narrowing to `npy_intp` that is NOT a size the emitter bounded. Each is a
# COUNT the kernel returned after writing into a buffer of known capacity --
# a different contract (a count past the capacity is the kernel's bug, not a
# request too large), tracked by gh-1716. Ratchet: this set may only shrink.
_RETURNED_COUNT = {
    "npy_intp _odim = (npy_intp)n_out;",
    "PyArray_DIMS((PyArrayObject *)_out)[0] = (npy_intp)_n;",
}
_NARROWING = re.compile(r"\(npy_intp\)\s*\(?\s*([A-Za-z_]\w*)")


def test_every_narrowing_reads_a_bounded_size(built):
    """Registration-free: every `(npy_intp)` the tree emits is checked.

    A site added later that casts a raw size is found here without being
    listed, because the scan walks what the generator wrote, not a list of
    the sites this fix knew about.
    """
    seen, offenders = 0, []
    for src in sorted((built / "native/src").rglob("*_ext.c")):
        for line in src.read_text("utf-8").splitlines():
            code = line.split("//", 1)[0].split("/*", 1)[0].strip()
            m = _NARROWING.search(code)
            if not m:
                continue
            seen += 1
            if m.group(1).endswith("_need") or code in _RETURNED_COUNT:
                continue
            offenders.append(f"{src.name}: {code}")
    assert seen >= 7, f"only {seen} narrowings found -- the scan is inert"
    assert not offenders, "\n".join(offenders)


def test_an_ordinary_size_still_allocates(built):
    """The guard refuses only what cannot be a dimension."""
    r = _run(
        built,
        f"{_WFM}\n{_GEN}\n"
        "print(len(fb(3)), repr(fs(3)), len(fc(3)), fr(3),"
        " len(Gen().fill(5)), len(Gen().peek(4)))\n",
    )
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    # fb/fs/fr are unimplemented stubs returning a count of 0.
    assert r.stdout.strip() == "0 '' 3 [] 5 4", r.stdout


# -- a handle module: `out_len_fn` -------------------------------------------

_BIG_H = """\
#ifndef BIG_H
#define BIG_H
#include <stddef.h>
typedef struct big big_t;
big_t *big_open(size_t need);
void big_close(big_t *b);
size_t big_need(const big_t *b);
size_t big_fill(big_t *b, float *out);
#endif
"""

_BIG_C = """\
#include "big/big.h"
#include <stdlib.h>
struct big { size_t need; };
big_t *big_open(size_t need) {
    big_t *b = malloc(sizeof *b);
    if (b) b->need = need;
    return b;
}
void big_close(big_t *b) { free(b); }
size_t big_need(const big_t *b) { return b->need; }
size_t big_fill(big_t *b, float *out) {
    for (size_t i = 0; i < b->need; i++) out[i] = (float)i;
    return b->need;
}
"""


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    if _SKIP:
        pytest.skip(_SKIP)
    from test_handle_build import _compile_import, _placed

    tmp = tmp_path_factory.mktemp("gh1710h")
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", tmp, ["widget"], [("gain", "float", "0.0f")])
        cfg = C.load(tmp)
        cfg.setdefault("module", {})["big"] = _placed(
            {
                "kind": "handle",
                "backing": "big",
                "header": "big/big.h",
                "type_name": "Big",
                "create_fn": "big_open",
                "close_fn": "big_close",
                "create_args": [{"name": "need", "type": "size_t"}],
                "methods": [
                    {
                        "name": "fill",
                        "fn": "big_fill",
                        "returns": "float[]",
                        "out_len_fn": "big_need",
                    }
                ],
            },
            tmp,
        )
        C.save(tmp, cfg)
        apply_run(tmp)
    assert (tmp / INC_ROOT).is_dir()
    return _compile_import(tmp, "big", _BIG_H, _BIG_C)


def test_handle_out_len_fn(big):
    assert big.Big(3).fill().tolist() == [0.0, 1.0, 2.0]
    with pytest.raises(OverflowError) as e:
        big.Big(SIZE_MAX).fill()
    assert str(e.value) == f"Big.fill: {TOO_LARGE}"
