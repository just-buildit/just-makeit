"""A count the kernel returns past its buffer raises ``RuntimeError`` (gh-1716).

A self-sizing output hands its kernel a buffer of known capacity and then
trusts the COUNT the kernel returns: the count became the array's dimension
(``PyArray_DIMS(arr)[0] = n``), the ``PyArray_Resize`` target, the length of a
view over the caller's ``out=``, the length of a ``list`` read out of a
records buffer, of a ``str`` or ``bytes`` copied out of a byte buffer, or the
stop of an ``out[:n]`` slice. Nothing compared it with the capacity, so a
kernel returning more than it was given -- a bug, or a ``(size_t)-1``
sentinel -- produced a result shaped past its own allocation: ``fb(3)``
returned 8 elements read from a 3-byte buffer, and ``PyArray_Resize`` GREW a
method's result into memory the kernel never wrote. The ``str`` output alone
clamped, silently.

Every site now goes through ``_coerce.returned_count_c``, the read-side twin
of gh-1710's ``output_size_c``. This file proves it three ways:

* the source: no generator spells the guard by hand, and the old silent
  clamp is gone;
* the build: one compiled project per module kind, every site class driven
  with a count past the capacity (refused, naming the member, the count and
  the capacity), exactly at it, and zero (both still returned);
* the generated code: registration-free, every place a generated binding
  consumes a count is preceded, in the same function, by the guard on that
  same variable -- so a site added later that trims by a count is found
  without being listed.
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
from _compilers import default_cc

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _coerce  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._function import run as function_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_SRC = Path(__file__).parent.parent / "src" / "just_makeit"


def _skip_reason() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()


def _msg(who: str, n: int, cap: int) -> str:
    return f"{who}: wrote {n} elements into a buffer of {cap}"


# -- the emitter --------------------------------------------------------------


def test_the_guard_runs_before_the_count_is_used():
    """Compare, release, raise, return -- and declare nothing."""
    src = _coerce.returned_count_c("_n", "_dim", "fb", "Py_DECREF(_out);")
    i_check = src.index("if ((size_t)(_n) > (size_t)(_dim))")
    i_release = src.index("Py_DECREF(_out);")
    i_raise = src.index("PyErr_Format(PyExc_RuntimeError,")
    i_return = src.index("return NULL;")
    assert i_check < i_release < i_raise < i_return
    # No new local: a site's C locals are what a manifest name may collide
    # with (`_builtins`), and the guard must not add one.
    assert not re.search(r"^\s*(size_t|npy_intp|int)\s+\w+\s*=", src, re.M)
    assert _coerce.RETURNED_COUNT_BLOCK_RE.fullmatch(src.strip())


# -- the source: one emitter, no hand-written copy ---------------------------


def test_no_generator_bounds_a_count_by_hand():
    """The message has one spelling, in the emitter; the old clamp is gone."""
    found: "dict[str, int]" = {}
    clamps: "list[str]" = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC)
        if rel.parts[0] == "examples":
            continue
        text = path.read_text("utf-8")
        # The unit is a parameter since gh-1998 (an interleaved kernel
        # counts samples), so the needle is the part every spelling shares.
        n = text.count("into a buffer of %zu")
        if n:
            found[str(rel)] = n
        clamps += [
            f"{rel}: {m.group(0)}"
            for m in re.finditer(r"if \(\w+ > \w+\) \w+ = \w+;", text)
        ]
    assert found == {"_coerce.py": 2}, found  # the code and its doctest
    assert not clamps, clamps


# -- an object + module-function project, compiled ---------------------------

REC_T = "typedef struct { uint32_t a; } rec_t;\n\n"
FIELDS = [{"name": "a", "type": "uint32_t"}]


def _declare_rec(header: Path) -> None:
    text = header.read_text("utf-8")
    cut = text.index("#ifdef __cplusplus")
    header.write_text(text[:cut] + REC_T + text[cut:], encoding="utf-8")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Every object / module-function site class, its kernel returning a
    count the caller controls: the function's `k`, or the object's state
    `k` (`Gen(k)`)."""
    if _SKIP:
        pytest.skip(_SKIP)
    dest = tmp_path_factory.mktemp("gh1716") / "p"
    ret_k = "(void)out; return state->k;"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", dest, modules=["wfm"], c_prefix=None)
        object_run(
            dest,
            "gen",
            module=None,
            state_vars=[("k", "size_t", "0")],
            no_step=True,
        )
        _declare_rec(dest / INC_ROOT / "gen/gen_core.h")
        _declare_rec(dest / INC_ROOT / "wfm/wfm_core.h")
        # function: a self-sizing ndarray, `out_size` elements.
        function_run(
            dest,
            "fb",
            "wfm",
            params=[("n", "size_t"), ("k", "size_t")],
            return_type="size_t",
            out_type="uint8_t",
            variable_output=True,
            out_size="n",
            impl_body="(void)n; (void)out; return k;",
        )
        # function: a self-sizing `str`, which used to clamp silently.
        function_run(
            dest,
            "fs",
            "wfm",
            params=[("n", "size_t"), ("k", "size_t")],
            return_type="size_t",
            out_type="str",
            variable_output=True,
            out_size="n",
            # Fills what fits, so a valid count decodes as text.
            impl_body=(
                "for (size_t i = 0; i < k && i < n; i++) out[i] = 'x';"
                " return k;"
            ),
        )
        # function: list-of-records into a `m`-record buffer.
        function_run(
            dest,
            "fr",
            "wfm",
            params=[("m", "size_t"), ("k", "size_t")],
            return_type="rec_t",
            result_fields=FIELDS,
            max_results_param="m",
            impl_body="(void)m; (void)result; return k;",
        )
        # method: variable_output -- the allocated path AND `out=`.
        method_run(
            dest, "gen", "burst", None, "void", "uint32_t", True, [],
            impl_body=ret_k,
        )  # fmt: skip
        # method: record_dtype, the same two paths over a structured dtype.
        method_run(
            dest, "gen", "rows", None, "void", "uint32_t", True, [],
            record_dtype="rec_t", result_fields=FIELDS, impl_body=ret_k,
        )  # fmt: skip
        # method: list-of-records into a `results[4]` on the stack.
        method_run(
            dest, "gen", "recs", None, "void", "rec_t", False, [],
            result_fields=FIELDS, max_results=4,
            impl_body="(void)result; (void)max_results; return state->k;",
        )  # fmt: skip

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


_IMP = (
    "import numpy as np\nfrom p.wfm import fb, fs, fr\nfrom p.gen import Gen\n"
)


def _outcome(dest: Path, call: str) -> str:
    """``LEN <n>`` for a result, ``ERR <type>: <msg>`` for a raise."""
    r = _run(
        dest,
        f"{_IMP}"
        "try:\n"
        f"    r = {call}\n"
        "except Exception as e:\n"
        "    print('ERR', type(e).__name__ + ':', e)\n"
        "else:\n"
        "    print('LEN', len(r))\n",
    )
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    return r.stdout.strip()


#: (id, call making the kernel return <count> into <cap>, who, cap).
#: `{k}` is the count the kernel returns.
_OBJ_SITES = [
    ("function-ndarray", "fb(3, {k})", "fb", 3),
    ("function-str", "fs(3, {k})", "fs", 3),
    ("function-records", "fr(3, {k})", "fr", 3),
    # max_out() is a stub returning 0, so the allocation is the request.
    ("method-allocated", "Gen({k}).burst(4)", "Gen.burst", 4),
    (
        "method-out",
        "Gen({k}).burst(2, out=np.zeros(6, np.uint32))",
        "Gen.burst",
        6,
    ),
    ("record-dtype-allocated", "Gen({k}).rows(4)", "Gen.rows", 4),
    (
        "record-dtype-out",
        "Gen({k}).rows(2, out=np.zeros(6, [('a', np.uint32)]))",
        "Gen.rows",
        6,
    ),
    ("method-records", "Gen({k}).recs()", "Gen.recs", 4),
]


@pytest.mark.parametrize(
    "call, who, cap",
    [s[1:] for s in _OBJ_SITES],
    ids=[s[0] for s in _OBJ_SITES],
)
def test_a_count_past_the_buffer_is_refused(built, call, who, cap):
    got = _outcome(built, call.format(k=cap + 1))
    assert got == f"ERR RuntimeError: {_msg(who, cap + 1, cap)}", got
    # A sentinel such as (size_t)-1 is the same refusal, not a wrap.
    huge = 2**64 - 1
    got = _outcome(built, call.format(k=huge))
    assert got == f"ERR RuntimeError: {_msg(who, huge, cap)}", got


@pytest.mark.parametrize(
    "call, cap",
    [(s[1], s[3]) for s in _OBJ_SITES],
    ids=[s[0] for s in _OBJ_SITES],
)
def test_a_count_within_the_buffer_is_returned(built, call, cap):
    """Exactly the capacity, and zero: the guard refuses only an overrun."""
    assert _outcome(built, call.format(k=cap)) == f"LEN {cap}"
    assert _outcome(built, call.format(k=0)) == "LEN 0"


# -- a handle module and a capsule module ------------------------------------

_CNT_H = """\
#ifndef CNT_H
#define CNT_H
#include <stddef.h>
typedef struct cnt cnt_t;
cnt_t *cnt_open(size_t k, size_t cap);
void cnt_close(cnt_t *c);
size_t cnt_cap(const cnt_t *c);
size_t cnt_fill(cnt_t *c, float *out);
size_t cnt_save(const cnt_t *c, void *out);
size_t cnt_pop(cnt_t *c, float *out, size_t n);
size_t cnt_scale(const cnt_t *c, const float *in, size_t n_in,
                 float *out, size_t max_out);
#endif
"""

#: Every kernel writes NOTHING and returns `k`: what is under test is what
#: the binding does with the count, not a real overrun.
_CNT_C = """\
#include "cnt/cnt.h"
#include <stdlib.h>
struct cnt { size_t k, cap; };
cnt_t *cnt_open(size_t k, size_t cap) {
    cnt_t *c = malloc(sizeof *c);
    if (c) { c->k = k; c->cap = cap; }
    return c;
}
void cnt_close(cnt_t *c) { free(c); }
size_t cnt_cap(const cnt_t *c) { return c->cap; }
size_t cnt_fill(cnt_t *c, float *out) { (void)out; return c->k; }
size_t cnt_save(const cnt_t *c, void *out) { (void)out; return c->k; }
size_t cnt_pop(cnt_t *c, float *out, size_t n) {
    (void)out; (void)n; return c->k;
}
size_t cnt_scale(const cnt_t *c, const float *in, size_t n_in,
                 float *out, size_t max_out) {
    (void)in; (void)n_in; (void)out; (void)max_out; return c->k;
}
"""

_CAPS_H = """\
#ifndef CAPS_H
#define CAPS_H
#include <stddef.h>
typedef struct caps caps_state_t;
caps_state_t *caps_create(size_t k);
void caps_destroy(caps_state_t *s);
size_t caps_execute(caps_state_t *s, const float *in, size_t n_in,
                    float *out, size_t max_out);
#endif
"""

_CAPS_C = """\
#include "caps/caps.h"
#include <stdlib.h>
struct caps { size_t k; };
caps_state_t *caps_create(size_t k) {
    caps_state_t *s = malloc(sizeof *s);
    if (s) s->k = k;
    return s;
}
void caps_destroy(caps_state_t *s) { free(s); }
size_t caps_execute(caps_state_t *s, const float *in, size_t n_in,
                    float *out, size_t max_out) {
    (void)in; (void)n_in; (void)out; (void)max_out; return s->k;
}
"""

_CNT_MODULE = {
    "kind": "handle",
    "backing": "cnt",
    "header": "cnt/cnt.h",
    "type_name": "Cnt",
    "create_fn": "cnt_open",
    "close_fn": "cnt_close",
    "create_args": [
        {"name": "k", "type": "size_t"},
        {"name": "cap", "type": "size_t"},
    ],
    "methods": [
        # (e) handle-length array, sized by `out_len_fn`.
        {
            "name": "fill",
            "fn": "cnt_fill",
            "returns": "float[]",
            "out_len_fn": "cnt_cap",
        },
        # (f) handle-length bytes, sized by `out_len_fn`.
        {
            "name": "save",
            "fn": "cnt_save",
            "returns": "bytes",
            "out_len_fn": "cnt_cap",
        },
        # (c) int-in -> array-out.
        {
            "name": "pop",
            "fn": "cnt_pop",
            "returns": "float[]",
            "args": [{"name": "n", "type": "size_t"}],
        },
        # (d) array-in + writable out -> `out[:n_out]`.
        {
            "name": "scale",
            "fn": "cnt_scale",
            "returns": "float[]",
            "args": [
                {"name": "x", "type": "float[]"},
                {"name": "out", "type": "float[]", "writable": True},
            ],
        },
    ],
}

_CAPS_MODULE = {
    "kind": "capsule",
    "backing": "caps",
    "header": "caps/caps.h",
    "capsule_name": "proj.caps",
    "init_params": [{"name": "k", "type": "size_t"}],
    "methods": [
        {
            "name": "execute",
            "arg_type": "float[]",
            "return_type": "float[]",
            "caller_out": True,
        }
    ],
}


@pytest.fixture(scope="module")
def kinds(tmp_path_factory):
    """The handle and capsule modules, compiled and imported."""
    if _SKIP:
        pytest.skip(_SKIP)
    from test_handle_build import _compile_import, _placed

    tmp = tmp_path_factory.mktemp("gh1716k")
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", tmp, ["widget"], [("gain", "float", "0.0f")])
        cfg = C.load(tmp)
        cfg.setdefault("module", {})["cnt"] = _placed(_CNT_MODULE, tmp)
        cfg["module"]["caps"] = _placed(_CAPS_MODULE, tmp)
        C.save(tmp, cfg)
        apply_run(tmp)
    return {
        "root": tmp,
        "cnt": _compile_import(tmp, "cnt", _CNT_H, _CNT_C),
        "caps": _compile_import(tmp, "caps", _CAPS_H, _CAPS_C),
    }


def _kind_sites(kinds):
    import numpy as np

    cnt, caps = kinds["cnt"], kinds["caps"]
    x = np.zeros(2, np.float32)
    return {
        # (id): (call(k) -> result, who, cap)
        "handle-out-len-fn": (lambda k: cnt.Cnt(k, 4).fill(), "Cnt.fill", 4),
        "handle-bytes": (lambda k: cnt.Cnt(k, 4).save(), "Cnt.save", 4),
        "handle-int-in": (lambda k: cnt.Cnt(k, 0).pop(4), "Cnt.pop", 4),
        "handle-out-view": (
            lambda k: cnt.Cnt(k, 0).scale(x, np.zeros(4, np.float32)),
            "Cnt.scale",
            4,
        ),
        "capsule-out-view": (
            lambda k: caps.caps_execute(
                caps.caps_create(k), x, np.zeros(4, np.float32)
            ),
            "caps_execute",
            4,
        ),
    }


_KIND_IDS = [
    "handle-out-len-fn",
    "handle-bytes",
    "handle-int-in",
    "handle-out-view",
    "capsule-out-view",
]


@pytest.mark.parametrize("site", _KIND_IDS)
def test_a_kind_module_refuses_a_count_past_the_buffer(kinds, site):
    call, who, cap = _kind_sites(kinds)[site]
    with pytest.raises(RuntimeError) as e:
        call(cap + 1)
    assert str(e.value) == _msg(who, cap + 1, cap)


@pytest.mark.parametrize("site", _KIND_IDS)
def test_a_kind_module_returns_a_count_within_the_buffer(kinds, site):
    call, _who, cap = _kind_sites(kinds)[site]
    assert len(call(cap)) == cap
    assert len(call(0)) == 0


# -- a composer module --------------------------------------------------------


@pytest.fixture(scope="module")
def composer(tmp_path_factory) -> Path:
    """gh-1711's `studio` project, its generator returning `max + 1` when
    the first source's gain is negative -- writing nothing."""
    if _SKIP:
        pytest.skip(_SKIP)
    from test_gh1711_composer_owned_pointer import PLAYLIST_C, build_project

    anchor = "  size_t n = 0;\n  while (n < max"
    assert PLAYLIST_C.count(anchor) == 1, "playlist_execute changed"
    lying = PLAYLIST_C.replace(
        anchor,
        "  if (st->n_tracks && st->tracks[0].n_sources\n"
        "      && st->tracks[0].sources[0].gain < 0)\n"
        "    return max + 1;\n" + anchor,
    )
    return build_project(
        tmp_path_factory.mktemp("gh1716c") / "studio", playlist_c=lying
    )


_MIX = (
    "import sys\n"
    "sys.path.insert(0, 'src')\n"
    "from studio.playlist.playlist import Clip, Mix, Track\n"
    "def mix(g):\n"
    "    return Mix(Track.sum(Clip(gain=g), dur=4))\n"
)


@pytest.mark.parametrize(
    "call, who, cap",
    [
        ("mix(-1.0).execute(3)", "Mix.execute", 3),
        ("mix(-1.0).compose(block=3)", "Mix.compose", 3),
    ],
    ids=["composer-execute", "composer-compose"],
)
def test_a_composer_refuses_a_count_past_the_buffer(composer, call, who, cap):
    r = _run(
        composer,
        f"{_MIX}"
        "try:\n"
        f"    {call}\n"
        "except RuntimeError as e:\n"
        "    print('ERR', e)\n",
    )
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    assert r.stdout.strip() == f"ERR {_msg(who, cap + 1, cap)}", r.stdout


def test_a_composer_returns_a_count_within_the_buffer(composer):
    """`dur=4`: execute(4) fills exactly; compose drains 4 in blocks of 3;
    execute(0) asks for nothing."""
    r = _run(
        composer,
        f"{_MIX}"
        "print(len(mix(1.0).execute(4)), len(mix(1.0).compose(block=3)),"
        " len(mix(1.0).execute(0)))\n",
    )
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    assert r.stdout.strip() == "4 4 0", r.stdout


# -- the generated code: every consumed count is guarded ---------------------

#: A generated statement that turns a count into a length the binding then
#: reads by. Group 1 is the count.
_CONSUMERS = (
    # a dimension: `PyArray_DIMS(arr)[0] = (npy_intp)n`, `_odim = ...`
    re.compile(r"\(npy_intp\)\s*\(?\s*([A-Za-z_][\w>.-]*)"),
    # a list built from a records buffer
    re.compile(r"\bPyList_New\s*\(\s*\(Py_ssize_t\)\s*([A-Za-z_][\w>.-]*)"),
    # the stop of an `out[:n]` view
    re.compile(
        r"\bPyLong_FromSsize_t\s*\(\s*\(Py_ssize_t\)\s*([A-Za-z_][\w>.-]*)"
    ),
    # a str / bytes copied out of a buffer (a NULL buffer is an allocation)
    re.compile(
        r"FromStringAndSize\s*\(\s*(?!NULL\b)[^,]+,\s*"
        r"\(Py_ssize_t\)\s*([A-Za-z_][\w>.-]*)"
    ),
)

#: Counts that are not a kernel's report about a buffer the binding sized,
#: by the statement that consumes them. Each carries its reason: a new one
#: must say why it is safe, not just be named.
_NOT_A_RETURNED_COUNT = (
    (
        re.compile(r"\w+_need\b"),
        "gh-1710's output_size_c: a size the binding is about to ALLOCATE",
    ),
    (
        re.compile(r"PyObject \*list = PyList_New\(\(Py_ssize_t\)n\)"),
        "a composer's `segments`: the length of an array the C state owns "
        "and returned with it -- there is no buffer jm sized",
    ),
    (
        re.compile(r"PyList_New\(\(Py_ssize_t\)src\[i\]\.\w+\)"),
        "a composer segment's own source count, read from the same array",
    ),
)


def _why_exempt(statement: str) -> "str | None":
    """Why *statement* consumes a count that needs no guard, or None."""
    return next(
        (why for pat, why in _NOT_A_RETURNED_COUNT if pat.search(statement)),
        None,
    )


def _functions(text: str):
    """Each top-level C function body (from its `{` at column 0)."""
    for m in re.finditer(r"^\{\n(.*?)^\}", text, re.M | re.S):
        yield m.group(1)


def _strip_comments(text: str) -> str:
    """Comments removed and string literals emptied, so neither prose nor a
    brace inside a message is read as code."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', text)


def _in_scope(body: str, start: int, end: int) -> bool:
    """Whether the block open at *start* is still open at *end*: a guard in
    one branch (the `out=` path) does not cover a count used after that
    branch has closed (the allocated path)."""
    depth = 0
    for ch in body[start:end]:
        depth += ch == "{"
        depth -= ch == "}"
        if depth < 0:
            return False
    return True


def _unguarded(text: str) -> "tuple[int, list[str]]":
    seen, bad = 0, []
    for body in _functions(_strip_comments(text)):
        for pat in _CONSUMERS:
            for m in pat.finditer(body):
                count = m.group(1)
                start = body.rfind("\n", 0, m.start()) + 1
                stop = body.find("\n", m.end())
                line = body[start : stop if stop >= 0 else None].strip()
                if _why_exempt(line):
                    continue
                seen += 1
                guarded = any(
                    g.group(1) == count
                    and _in_scope(body, g.start(), m.start())
                    for g in _coerce.RETURNED_COUNT_GUARD_RE.finditer(
                        body, 0, m.start()
                    )
                )
                if not guarded:
                    bad.append(f"{count}: {line}")
    return seen, bad


def _exts(root: Path) -> "list[Path]":
    return sorted((root / "native/src").rglob("*_ext*.c"))


def test_every_consumed_count_is_guarded(built, kinds, composer):
    """Registration-free over the generated code of every module kind.

    The scan reads what the generators WROTE, so a site added later that
    shapes a result by a count is found here without being listed -- the
    way a fix in four places misses the fifth.
    """
    seen, offenders = 0, []
    files = [*_exts(built), *_exts(kinds["root"]), *_exts(composer)]
    for src in files:
        n, bad = _unguarded(src.read_text("utf-8"))
        seen += n
        offenders += [f"{src.name}: {b}" for b in bad]
    # 3 functions + burst/rows x 2 paths + recs + 4 handle + 1 capsule
    # + 2 composer: an inert scan finds none.
    assert seen >= 15, f"only {seen} consumed counts found -- scan inert"
    assert not offenders, "\n".join(offenders)


def test_the_scan_sees_a_count_used_without_its_guard():
    """The scan's own negative: drop the guard and it names the site."""
    guard = _coerce.returned_count_c("_n", "_dim", "fb")
    body = (
        "static PyObject *\nf(void)\n{\n"
        "    size_t _n = fb(out);\n"
        f"{guard}"
        "    PyArray_DIMS((PyArrayObject *)_out)[0] = (npy_intp)_n;\n"
        "    return _out;\n}\n"
    )
    assert _unguarded(body) == (1, [])
    seen, bad = _unguarded(body.replace(guard, ""))
    assert seen == 1 and len(bad) == 1, bad
    # ...and a guard on a DIFFERENT count does not cover this one.
    other = _coerce.returned_count_c("_m", "_dim", "fb")
    assert _unguarded(body.replace(guard, other))[1], "wrong-variable guard"
    # ...nor does one in a branch that closed before the count is used.
    branch = "    if (out) {\n" + guard + "        return out;\n    }\n"
    assert _unguarded(body.replace(guard, branch))[1], "closed-branch guard"


# -- a sacred fragment predating the guard is told so ------------------------

FRAG = ("native", "src", "m", "m_ext_r.c")
WHY = "jm refuses a count the kernel returns past the buffer"


def _vo_project(tmp_path, *method_args) -> Path:
    """A module object whose one method `burst` is declared by
    *method_args* -- its binding lives in a sacred per-object fragment."""
    from _jmrun import run_cli

    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "q", cwd=root).returncode == 0
    proj = root / "q"
    assert run_cli("module", "m", cwd=proj).returncode == 0
    r = run_cli(
        "object", "r", "--module", "m", "--no-state", "--no-step", cwd=proj
    )
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(
        "method", "r", "burst", "--module", "m", *method_args, cwd=proj
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert run_cli("apply", cwd=proj).returncode == 0
    return proj


def _apply_out(proj: Path) -> str:
    from _jmrun import run_cli

    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


_VARIABLE_OUTPUT = ("--arg-type", "void", "--return-type", "uint32_t",
                    "--variable-output")  # fmt: skip
#: A list-of-records method: the guard is its ONLY raise, so this is the
#: shape on which reading the guard as a declared raise would show.
_RECORDS = ("--arg-type", "void", "--return-type", "rec_t",
            "--result-field", "a:uint32_t")  # fmt: skip


@pytest.mark.parametrize(
    "shape, guards",
    [(_VARIABLE_OUTPUT, 2), (_RECORDS, 1)],
    ids=["variable-output", "records"],
)
def test_a_fragment_without_the_guard_is_told_what_it_lacks(
    tmp_path, shape, guards
):
    """The advisory names jm's guard -- not a manifest that "needs raises":
    the guard raises, and no manifest changed."""
    proj = _vo_project(tmp_path, *shape)
    frag = proj.joinpath(*FRAG)
    # Rendered before gh-1716: every returned-count guard removed.
    old, n = _coerce.RETURNED_COUNT_BLOCK_RE.subn("", frag.read_text())
    assert n == guards, "fixture no longer renders its returned-count guards"
    frag.write_text(old)

    out = _apply_out(proj)
    assert "m_ext_r.c" in out, out
    assert f"burst: {WHY}" in out, out
    assert "needs raises" not in out, out
    assert "result shape" not in out, out


def test_a_current_fragment_is_silent(tmp_path):
    out = _apply_out(_vo_project(tmp_path, *_VARIABLE_OUTPUT))
    assert "no longer matches" not in out, out


def test_a_gnu_rewrapped_guard_is_still_the_guard(tmp_path):
    """GNU style spaces every cast and call; the marker is the comparison,
    not one layout of it."""
    proj = _vo_project(tmp_path, *_VARIABLE_OUTPUT)
    frag = proj.joinpath(*FRAG)
    src, n = re.subn(
        r"if \(\(size_t\)\((\w+)\) > \(size_t\)\(",
        r"if ((size_t) (\1)\n        > (size_t) (",
        frag.read_text(),
    )
    assert n == 2
    frag.write_text(src)
    out = _apply_out(proj)
    assert "no longer matches" not in out, out


def test_a_member_with_no_returned_count_is_silent(tmp_path):
    proj = _vo_project(
        tmp_path, "--arg-type", "float", "--return-type", "float"
    )
    assert not _coerce.RETURNED_COUNT_GUARD_RE.search(
        proj.joinpath(*FRAG).read_text()
    )
    out = _apply_out(proj)
    assert "no longer matches" not in out, out
