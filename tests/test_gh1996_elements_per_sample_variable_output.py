"""``elements_per_sample`` on a variable_output method's array (gh-1996).

A kernel taking interleaved I/Q in an ``int16_t[]`` counts **samples**: its
``x_len``, the ``max_out`` capacity it is handed and the ``n_out`` it returns
are all pairs of elements. ``elements_per_sample = 2`` on the array says so,
and gh-805 §C read it for exactly one thing -- the ``<p>_len`` local of the
ordinary method-param builder. The variable-output binding has its own
acquisition and never used that local, so the key was accepted and ignored:

* the kernel was handed ``PyArray_SIZE(x)``, an ELEMENT count, as ``x_len``;
* under ``pass_capacity`` it was handed the buffer's element count as its
  capacity in samples, and wrote twice as far as the buffer allows;
* the result was allocated and returned as ``n_out`` elements where the
  kernel produced ``n_out`` samples.

It compiled, so ``jm adopt`` offered it to doppler as an ordinary ``differs``
unit; accepting it shipped the overrun. Now every count the binding passes is
in samples (``_coerce.array_count_c``, the division the ``<p>_len`` local
takes too) and every count that becomes a numpy length again is multiplied
back: the allocation, the trimmed result, the ``out=`` capacity and view,
and ``<m>_max_out``'s answer. An output whose element differs from the
interleaved input's cannot be counted in that unit, so it is refused before
anything is written rather than guessed at.

This file proves it three ways:

* **the build**: one compiled object whose kernels echo the counts they are
  handed and write exactly ``n_out * E`` elements, driven through the
  allocating path and ``out=`` -- the latter into a view of a larger array,
  so a write past the caller's buffer lands on guard elements the test reads
  back;
* **the render**: registration-free over every variable-output method in a
  manifest of shapes, so a count site added later is held to the unit without
  being listed;
* **the refusals**: an output that cannot carry the interleave, and a value
  that is not an interleave at all.

GATE: a variable_output method's counts cross into C in the samples its
array's elements_per_sample declares, and come back as elements.
"""

from __future__ import annotations

import contextlib
import hashlib
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

from _jmrun import run_cli  # noqa: E402

from just_makeit import _config as C  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


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


def _quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            return fn(*args, **kwargs)


# -- the build ----------------------------------------------------------------

#: Every kernel ECHOES what it is handed -- I = `x_len`, Q = `max_out` (or
#: the scalar) -- and writes `min(k, capacity)` samples of two elements, so
#: what the binding passed is read back from the result, and a capacity
#: overstated by the interleave factor writes past the buffer by exactly
#: that much. None reads `x`: on a binding that passes an element count as
#: `x_len`, reading would be an over-read beside the bug under test.
_FRAGMENT = '''\
[iq]
arg_type = "int16_t"
return_type = "int16_t"
mutable = "false"
no_state = "false"
no_step = "true"

[[iq.state]]
name = "k"
type = "size_t"
default = "0"

[[iq.methods]]
name = "run"
arg_type = "void"
return_type = "int16_t"
variable_output = true
pass_capacity = true
max_out = 5
params = [{name = "x", type = "int16_t[]", elements_per_sample = 2}]
impl = """(void)x;
    size_t n = state->k < max_out ? state->k : max_out;
    for (size_t i = 0; i < n; i++) {
        out[2 * i] = (int16_t)x_len;
        out[2 * i + 1] = (int16_t)max_out;
    }
    return n;"""

[[iq.methods]]
name = "echo"
arg_type = "void"
return_type = "int16_t"
variable_output = true
params = [{name = "x", type = "int16_t[]", elements_per_sample = 2}]
impl = """(void)state; (void)x;
    for (size_t i = 0; i < x_len; i++) {
        out[2 * i] = (int16_t)x_len;
        out[2 * i + 1] = (int16_t)i;
    }
    return x_len;"""

[[iq.methods]]
name = "scaled"
arg_type = "void"
return_type = "int16_t"
variable_output = true
pass_capacity = true
max_out = 6
params = [
    {name = "x", type = "int16_t[]", elements_per_sample = 2},
    {name = "g", type = "int"},
]
impl = """(void)x;
    size_t n = state->k < max_out ? state->k : max_out;
    for (size_t i = 0; i < n; i++) {
        out[2 * i] = (int16_t)x_len;
        out[2 * i + 1] = (int16_t)g;
    }
    return n;"""
'''


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The `iq` object above, applied from its fragment and compiled.

    `run_max_out` is then made to depend on its count (`x_len + 1`), which
    is the author's half of the contract and the only way a count passed to
    it in the wrong unit shows in what it answers.
    """
    if _SKIP:
        pytest.skip(_SKIP)
    dest = tmp_path_factory.mktemp("gh1996") / "p"
    _quiet(new_run, "p", dest, c_prefix=None)
    fragment = dest.parent / "iq.toml"
    fragment.write_text(_FRAGMENT, encoding="utf-8")
    _quiet(apply_run, dest, fragment)
    core = dest / "native/src/iq/iq_core.c"
    text = core.read_text("utf-8")
    stub = re.search(
        r"iq_run_max_out\([^)]*\)\s*\{[^}]*?return 5;", text, re.S
    )
    assert stub, "run_max_out's declared-constant stub is not where expected"
    patched = text[: stub.start()] + stub.group(0).replace(
        "return 5;", "return x_len + 1;"
    )
    core.write_text(patched + text[stub.end() :], encoding="utf-8")

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


def _outcome(dest: Path, body: str) -> str:
    """What *body* prints, or ``CRASH <rc>`` when the interpreter died.

    A binding that writes past a buffer may take the process down instead of
    returning a wrong answer; either way the test fails, and it fails on its
    own assertion rather than inside a helper.
    """
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import numpy as np\nfrom p.iq import Iq\n" + body,
        ],
        cwd=dest,
        env={**os.environ, "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    if r.returncode != 0:
        return f"CRASH {r.returncode}: {r.stderr.strip()[-300:]}"
    return r.stdout.strip()


def test_the_allocated_result_is_n_out_samples_of_elements(built):
    """8 elements in are 4 samples; `max_out(4) + 1` is a capacity of 5.

    The kernel fills its capacity, so the result is 5 samples -- 10
    elements, each pair echoing `x_len = 4` and `max_out = 5`.
    """
    got = _outcome(built, "print(Iq(99).run(np.zeros(8, np.int16)).tolist())")
    assert got == str([4, 5] * 5), got


def test_a_short_result_is_trimmed_to_n_out_samples(built):
    """`n_out` below the capacity: the trim is in samples too."""
    got = _outcome(built, "print(Iq(2).run(np.zeros(8, np.int16)).tolist())")
    assert got == str([4, 5] * 2), got


def test_out_is_filled_to_its_capacity_and_not_past_it(built):
    """The caller's buffer is a 10-element view of a 20-element array.

    Its capacity is 5 SAMPLES. A binding that handed the kernel its element
    count would let it write 10 samples -- 20 elements -- and the second
    half of the array, which nothing should touch, would be overwritten.
    """
    got = _outcome(
        built,
        "buf = np.full(20, -7, np.int16)\n"
        "y = Iq(99).run(np.zeros(8, np.int16), out=buf[:10])\n"
        "print(y.tolist(), buf[10:].tolist())",
    )
    assert got == f"{[4, 5] * 5} {[-7] * 10}", got


def test_out_view_is_n_out_samples_of_elements(built):
    got = _outcome(
        built,
        "buf = np.full(20, -7, np.int16)\n"
        "y = Iq(2).run(np.zeros(8, np.int16), out=buf[:10])\n"
        "print(y.tolist(), buf[4:].tolist())",
    )
    assert got == f"{[4, 5] * 2} {[-7] * 16}", got


def test_max_out_answers_in_elements(built):
    """`run_max_out(len(x))` sizes `out=`: 8 elements -> (4 + 1) * 2."""
    assert _outcome(built, "print(Iq(0).run_max_out(8))") == "10"


def test_without_pass_capacity_the_fallback_is_in_samples(built):
    """No capacity is passed, so the binding allocates `max(max_out, n)`.

    `echo` declares no `max_out` (its stub answers 0), so the allocation is
    the call's own length -- 3 samples for 6 elements -- and the kernel
    writes exactly that many.
    """
    got = _outcome(built, "print(Iq(0).echo(np.zeros(6, np.int16)).tolist())")
    assert got == str([3, 0, 3, 1, 3, 2]), got


def test_an_array_beside_a_scalar_is_counted_in_samples(built):
    """The params path that is not `out=`-eligible honours the key too."""
    got = _outcome(
        built, "print(Iq(99).scaled(np.zeros(8, np.int16), 7).tolist())"
    )
    assert got == str([4, 7] * 6), got


# -- the render: every variable_output method, every count -------------------

#: One row per shape. Each is a variable_output method; what it is checked
#: against is derived from its own params by `_interleave_of`, not stated
#: here, so a row added later is held to the same rule.
_SHAPES = [
    # single array, pass_capacity: the issue's row, with `out=` and max_out
    {
        "name": "one",
        "pass_capacity": True,
        "params": [
            {"name": "x", "type": "int16_t[]", "elements_per_sample": 2}
        ],
    },
    # the same, but max_out is only a hint the call's length is clamped to
    {
        "name": "one_nc",
        "params": [
            {"name": "x", "type": "int16_t[]", "elements_per_sample": 2}
        ],
    },
    # a factor that is not 2, so a hard-coded pair is caught
    {
        "name": "three",
        "pass_capacity": True,
        "params": [
            {"name": "x", "type": "int16_t[]", "elements_per_sample": 3}
        ],
    },
    # an array beside a scalar (no `out=`)
    {
        "name": "beside",
        "pass_capacity": True,
        "params": [
            {"name": "x", "type": "int16_t[]", "elements_per_sample": 2},
            {"name": "g", "type": "double"},
        ],
    },
    # two arrays, the interleave on the one that sizes the output
    {
        "name": "two",
        "params": [
            {"name": "x", "type": "int16_t[]", "elements_per_sample": 2},
            {"name": "c", "type": "float[]"},
        ],
    },
    # two arrays, the interleave on the one that does NOT size the output
    {
        "name": "second",
        "return_type": "float",
        "params": [
            {"name": "c", "type": "float[]"},
            {"name": "x", "type": "int16_t[]", "elements_per_sample": 2},
        ],
    },
    # two outputs, both carrying the input's element
    {
        "name": "multi",
        "multi_output": ["int16_t"],
        "params": [
            {"name": "x", "type": "int16_t[]", "elements_per_sample": 2}
        ],
    },
    # nogil hoists every PyArray_SIZE out of the call
    {
        "name": "nog",
        "nogil": True,
        "pass_capacity": True,
        "params": [
            {"name": "x", "type": "int16_t[]", "elements_per_sample": 2}
        ],
    },
    # `rank` is the other gh-805 §C key this acquisition used to drop
    {
        "name": "ranked",
        "params": [
            {
                "name": "x",
                "type": "int16_t[]",
                "elements_per_sample": 2,
                "rank": 1,
            }
        ],
    },
]


def _method(row: dict) -> dict:
    return {
        "arg_type": "void",
        "return_type": "int16_t",
        "variable_output": True,
        **row,
    }


@pytest.fixture(scope="module")
def shapes(tmp_path_factory):
    """Every shape on a standalone object, and the issue's on a module one."""
    root = tmp_path_factory.mktemp("gh1996shapes") / "p"
    _quiet(new_run, "p", root, c_prefix=None)
    _quiet(
        object_run, root, "w", None, arg_type="int16_t", return_type="int16_t"
    )
    _quiet(module_run, root, "dsp")
    _quiet(
        object_run,
        root,
        "mq",
        "dsp",
        arg_type="int16_t",
        return_type="int16_t",
    )
    cfg = C.load(root)
    cfg["w"]["methods"] = [_method(r) for r in _SHAPES]
    cfg["mq"]["methods"] = [_method(_SHAPES[0])]
    C.save(root, cfg)
    _quiet(apply_run, root)
    return root


def _interleave_of(m: dict) -> "dict[str, int]":
    """Each array param's declared interleave, by name (1 when undeclared)."""
    return {
        p["name"]: int(p.get("elements_per_sample", 1))
        for p in m.get("params", [])
        if p["type"].endswith("[]")
    }


def _sizing(m: dict) -> int:
    """The interleave of the array the output is sized from: the first."""
    return next(iter(_interleave_of(m).values()), 1)


def _function(src: str, name: str) -> str:
    """The body of the generated C function *name*, hoists put back.

    With `nogil` every ``(T)PyArray_SIZE(a)`` in the kernel call is lifted
    into a ``_ngN`` local declared before the GIL is released; substituting
    each back means the checks below read one spelling of a count. Each
    kernel call (``out=`` and allocating) hoists its own ``_ng0``..., so a
    declaration is substituted only up to the next one of the same name.
    """
    m = re.search(rf"^{re.escape(name)}\(.*?^\}}", src, re.M | re.S)
    assert m, f"no generated function {name}"
    body = m.group(0)
    hoist = re.compile(r"^[^\n]*\b(_ng\d+) = ([^\n]+?);\n", re.M)
    while True:
        d = hoist.search(body)
        if not d:
            return body
        local, expr = d.group(1), d.group(2)
        rest = body[d.end() :]
        nxt = re.search(rf"\b{local} = ", rest)
        cut = nxt.start() if nxt else len(rest)
        used = re.sub(rf"\b{local}\b", lambda _m, e=expr: e, rest[:cut])
        body = body[: d.start()] + used + rest[cut:]


def _ext_sources(root: Path) -> str:
    return "\n".join(
        p.read_text("utf-8") for p in sorted(root.rglob("*_ext*.c"))
    )


def _vo_methods(root: Path):
    cfg = C.load(root)
    for comp in ("w", "mq"):
        for m in cfg[comp].get("methods", []):
            if m.get("variable_output"):
                yield comp, m


def test_every_count_is_in_samples(shapes):
    """No array of an interleaved param is measured in elements.

    Every ``PyArray_SIZE`` the binding takes of such an array -- the kernel's
    count, the length ``max_out`` is asked about, the fallback capacity --
    is divided by that param's own factor. Derived from each method's
    params, so a count site added later is held to it unlisted.
    """
    src = _ext_sources(shapes)
    seen = 0
    for comp, m in _vo_methods(shapes):
        body = _function(src, f"{comp.capitalize()}_{m['name']}")
        for pname, e in _interleave_of(m).items():
            sizes = re.findall(rf"PyArray_SIZE\({pname}_arr\)( / \d+)?", body)
            assert sizes, f"{comp}.{m['name']}: {pname} is never measured"
            want = f" / {e}" if e != 1 else ""
            assert all(s == want for s in sizes), (
                f"{comp}.{m['name']}: {pname} measured as {sizes}, "
                f"want every one{want or ' undivided'}:\n{body}"
            )
            seen += 1
    assert seen >= len(_SHAPES) + 1


def test_every_result_length_is_n_out_samples_of_elements(shapes):
    """What becomes a numpy length again is multiplied back -- once."""
    src = _ext_sources(shapes)
    for comp, m in _vo_methods(shapes):
        e = _sizing(m)
        body = _function(src, f"{comp.capitalize()}_{m['name']}")
        odims = re.findall(r"npy_intp _odim = \(npy_intp\)(.+?);", body)
        assert odims, f"{comp}.{m['name']}: no result length"
        want = "n_out" if e == 1 else f"(n_out * {e})"
        assert set(odims) == {want}, (comp, m["name"], odims)
        # The allocation: bounded as samples, then scaled to elements.
        scales = re.findall(r"_adim \*= (\d+);", body)
        assert scales == ([] if e == 1 else [str(e)]), (m["name"], scales)
        if "out_arr" in body:
            caps = re.findall(r"size_t _cap = (.+?);", body)
            assert caps[0] == (
                "(size_t)PyArray_SIZE(out_arr)" + ("" if e == 1 else f" / {e}")
            ), (m["name"], caps)


def test_max_out_takes_and_returns_elements(shapes):
    """``<m>_max_out(len(x))`` sizes ``out=`` in elements, both ways."""
    src = _ext_sources(shapes)
    checked = 0
    for comp, m in _vo_methods(shapes):
        e = _sizing(m)
        fn = f"{comp.capitalize()}_{m['name']}_max_out"
        if not re.search(rf"^{fn}\(", src, re.M):
            continue
        body = _function(src, fn)
        if e == 1:
            assert "_mo" not in body, body
            continue
        assert re.search(rf"\(size_t\)\w+_len / {e}\)", body), body
        assert f"PyLong_FromSize_t(_mo * {e})" in body, body
        checked += 1
    assert checked >= 3  # one, three, nog -- and the module object's


def test_rank_is_guarded_on_this_path_too(shapes):
    body = _function(_ext_sources(shapes), "W_ranked")
    assert body.index("PyArray_NDIM(x_arr) != 1") < body.index(
        "PyArray_SIZE(x_arr)"
    ), body


def test_every_doc_face_counts_max_out_in_elements(shapes):
    """The binding's docstring, the object's ``.pyi`` and the module's."""
    pyis = {
        "runtime": _ext_sources(shapes),
        "object stub": (shapes / "src/p/w.pyi").read_text("utf-8"),
        "module stub": next(
            p.read_text("utf-8")
            for p in sorted((shapes / "src/p").rglob("*.pyi"))
            if "class Mq" in p.read_text("utf-8")
        ),
    }
    for face, text in pyis.items():
        assert "Number of input elements one() will be given." in text, face


# -- the refusals ------------------------------------------------------------


def _tree(root: Path) -> "dict[str, str]":
    return {
        str(p.relative_to(root)): hashlib.sha1(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "build" not in p.relative_to(root).parts
    }


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "p"
    _quiet(new_run, "p", root, c_prefix=None)
    _quiet(
        object_run, root, "w", None, arg_type="int16_t", return_type="int16_t"
    )
    return root


def _refused(root: Path, method: dict) -> str:
    """Add *method* to `w`, apply, and require a refusal that wrote nothing."""
    cfg = C.load(root)
    cfg["w"]["methods"] = [method]
    C.save(root, cfg)
    before = _tree(root)
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1, f"{r.stdout}\n{r.stderr}"
    assert r.stderr.startswith("error: "), r.stderr
    assert _tree(root) == before, "a refused apply changed the tree"
    return r.stderr


_X2 = {"name": "x", "type": "int16_t[]", "elements_per_sample": 2}


@pytest.mark.parametrize(
    "extra, other",
    [
        ({"return_type": "float _Complex"}, "float _Complex"),
        ({"out_type": "float"}, "float"),
        ({"multi_output": ["float"]}, "float"),
        (
            {
                "record_dtype": "rec_t",
                "result_fields": [{"name": "a", "type": "uint32_t"}],
            },
            "rec_t",
        ),
    ],
    ids=["return-type", "out-type", "multi-output", "record-dtype"],
)
def test_an_output_of_another_element_is_refused(project, extra, other):
    err = _refused(project, _method({"name": "run", "params": [_X2], **extra}))
    assert "declares elements_per_sample = 2 on 'x' (int16_t)" in err, err
    assert f"its output element is '{other}'" in err, err


def test_the_interleave_on_a_non_sizing_array_is_not_refused(project):
    """It divides that array's count and says nothing about the output."""
    cfg = C.load(project)
    cfg["w"]["methods"] = [
        _method(
            {
                "name": "run",
                "return_type": "float",
                "params": [{"name": "c", "type": "float[]"}, _X2],
            }
        )
    ]
    C.save(project, cfg)
    r = run_cli("apply", cwd=project)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("bad", [0, -2, "2", True])
@pytest.mark.parametrize("vo", [True, False], ids=["vo", "fixed"])
def test_a_value_that_is_not_an_interleave_is_refused(project, bad, vo):
    """Both readers of the key share the one validated accessor."""
    row = {
        "name": "run",
        "arg_type": "void",
        "return_type": "int16_t" if vo else "int",
        "variable_output": vo,
        "params": [{**_X2, "elements_per_sample": bad}],
    }
    err = _refused(project, row)
    assert (
        f"param 'x' declares elements_per_sample = {bad!r}; it must be an "
        f"integer of at least 1." in err
    ), err
