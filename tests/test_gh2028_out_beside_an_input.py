"""``out=`` for a variable_output method with an input AND params (gh-2028).

A ``variable_output`` method with an ``arg_type`` input and params beside it
-- ``run(x, gain)`` -- was the one single-output shape offered no ``out=``
buffer. gh-1079 / gh-1998 kept it out because its parse dropped the params
(gh-1960). gh-1960 put it through `_build_params_parse`, so the binding half
was small: an optional trailing ``out`` in that parse, sized from the input
exactly as the same method without params is (``n_in``, which
``<m>_max_out()`` already takes).

What held it back was the doc. `_docstring.render_numpy_method_doc` matches
the header's ``@param`` entries to the Python arguments by name, then zips the
LEFTOVERS by position when the counts agree -- that is how ``@param in`` (the
C name) documents the Python ``x``. ``out`` joining the arguments added a
Python leftover with no Doxygen partner, the counts stopped agreeing, the zip
was skipped, and ``x`` fell back to ``Input.``. The same arithmetic had
always applied to every input-only ``variable_output`` method, which has
carried ``out=`` since gh-219: its ``@param in`` reached ``x`` only when the
header also spelled ``@param out``.

So the fix is two halves, and each is sabotaged separately:

* the doc -- the binding's own arguments (``count``, ``out``: the keys of
  `_gluedoc.binding_param_docs`) never take a positional slot of the
  author's ``@param`` list; they are documented by name or by jm's default;
* the binding -- `_outbuf.why_not` offers the shape ``out=``, and
  `_build_params_parse` parses it as the trailing optional argument.

GATE: the shape, built, takes ``out=`` positionally and by keyword, writes
into it and returns a view of it, and refuses an undersized one; both
``.pyi`` faces and the runtime ``__doc__`` list it; and a header's
``@param in`` reaches ``x`` with ``out`` present, on the input-only shape too.
"""

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import pytest
from _compilers import default_cc

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docstring import (  # noqa: E402
    DoxyBlock,
    render_numpy_method_doc,
    render_runtime_doc,
)
from just_makeit._gluedoc import OUT_PARAM_DOC, binding_param_docs  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            return fn(*args, **kwargs)


# -- the doc half: a binding argument takes no positional slot ---------------

#: jm's binding arguments, read off the map both faces document them from
#: and take the binding's names from, rather than listed here.
_BINDING = sorted(binding_param_docs(count=True, out=True))

#: The header documents the block input by its C name, as jm scaffolds it.
_BLOCK = DoxyBlock(
    brief="Scale a block.",
    params=[("in", "Samples to scale."), ("gain", "Linear scale.")],
)


@pytest.mark.parametrize(
    "binding",
    [
        c
        for n in range(1, len(_BINDING) + 1)
        for c in combinations(_BINDING, n)
    ],
    ids=lambda c: "+".join(c),
)
def test_a_binding_argument_takes_no_positional_slot(binding):
    """`in` reaches `x` whichever binding arguments sit beside it."""
    py = [("x", "ndarray"), ("gain", "float")] + [(b, "T") for b in binding]
    descs = render_numpy_method_doc(_BLOCK, py, binding=set(binding))[2]
    assert descs["x"] == "Samples to scale."
    assert descs["gain"] == "Linear scale."
    assert all(descs[b] == "" for b in binding), descs


def test_both_faces_read_the_binding_from_param_defaults():
    """The faces pass `binding_param_docs`, so its keys are the binding."""
    lines = render_runtime_doc(
        _BLOCK,
        "run",
        [("x", "ndarray"), ("gain", "float"), ("out", "ndarray | None")],
        "ndarray",
        param_defaults=binding_param_docs(count=False, out=True),
    )
    text = "\n".join(lines)
    assert "x : ndarray\n    Samples to scale." in text, text
    assert "out : ndarray | None\n    Optional pre-allocated" in text, text


def test_an_authored_out_still_documents_out():
    """By NAME, an author's `@param out` outranks jm's default (gh-1042)."""
    block = DoxyBlock(
        brief="Scale a block.",
        params=[("in", "Samples."), ("out", "Your buffer.")],
    )
    descs = render_numpy_method_doc(
        block, [("x", "ndarray"), ("out", "ndarray")], binding={"out"}
    )[2]
    assert descs == {"out": "Your buffer.", "x": "Samples."}


# -- the doc half, generated: both stubs and the runtime literal -------------

#: Per method: its params and the authored block, `@param out` deliberately
#: absent -- an author documents the kernel, not jm's buffer argument.
_AUTHORED = {
    # The latent half: input-only, which has had `out=` since gh-219.
    "copy": (
        [],
        " * @brief Copy one block through.\n"
        " *\n"
        " * @param in Samples to copy. Any length.\n"
        " * @return The copied block.",
    ),
    # The gh-2028 shape: an input beside a param.
    "scale": (
        [("gain", "double")],
        " * @brief Scale one block.\n"
        " *\n"
        " * @param in Samples to scale. Any length.\n"
        " * @param gain Linear scale.\n"
        " * @return The scaled block.",
    ),
}


def _authored_project(tmp_path: Path, module: "str | None") -> Path:
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root, c_prefix=None)
    if module:
        _quiet(module_run, root, module)
    _quiet(
        object_run,
        root,
        "amp",
        module,
        state_vars=[("g", "double", "1.0")],
        arg_type="void",
        return_type="float",
    )
    for name, (params, _) in _AUTHORED.items():
        _quiet(
            method_run,
            root,
            "amp",
            name,
            module,
            "float[]",
            "float",
            True,
            [],
            params=params,
        )
    header = root / INC_ROOT / "amp" / "amp_core.h"
    text = header.read_text(encoding="utf-8")
    for name, (_, block) in _AUTHORED.items():
        seed = f" * @brief {name}."
        assert text.count(seed) == 1, f"the scaffold no longer seeds {seed}"
        text = text.replace(seed, block)
    header.write_text(text, encoding="utf-8")
    _quiet(apply_run, root)
    return root


_C_DOC_LINE = re.compile(r'^\s*"(.*)"[,}\s]*$')


def _runtime_doc(ext_c: str, method: str) -> str:
    """The ``PyMethodDef`` doc literal for *method*, unescaped."""
    start = ext_c.index(f'{{"{method}",')
    out: list[str] = []
    for raw in ext_c[start:].splitlines()[1:]:
        m = _C_DOC_LINE.match(raw)
        if not m:
            break
        out.append(m.group(1))
    assert out, f"no doc literal found for {method}()"
    return "".join(out).encode().decode("unicode_escape")


def _stub_doc(pyi: str, method: str) -> str:
    """The ``.pyi`` docstring for *method*, dedented."""
    m = re.search(
        rf'    def {method}\(.*?:\n        """(.*?)\n        """', pyi, re.S
    )
    assert m, f"no stub docstring found for {method}()"
    return "\n".join(
        ln[8:] if ln.startswith("        ") else ln
        for ln in m.group(1).split("\n")
    )


def _param(doc: str, name: str) -> str:
    """The description under *name*'s ``Parameters`` entry, one line."""
    m = re.search(rf"^{name} : [^\n]*\n((?:    [^\n]*\n?)+)", doc, re.M)
    assert m, f"no Parameters entry for {name!r} in:\n{doc}"
    return " ".join(ln.strip() for ln in m.group(1).splitlines())


#: (face, module, the .pyi, the binding's C file) -- the standalone stub is
#: `_context/_methods`'s, the module-aggregated one is `_stubs`'.
_FACES = [
    ("standalone", None, "src/demo/amp.pyi", "native/src/amp/amp_ext.c"),
    ("module", "dsp", "src/demo/dsp/dsp.pyi", "native/src/dsp/dsp_ext_amp.c"),
]


@pytest.fixture(scope="module", params=_FACES, ids=[f[0] for f in _FACES])
def faces(request, tmp_path_factory):
    _, module, pyi, ext = request.param
    root = _authored_project(tmp_path_factory.mktemp("gh2028doc"), module)
    return {
        name: {
            "stub": _stub_doc((root / pyi).read_text("utf-8"), name),
            "runtime": _runtime_doc((root / ext).read_text("utf-8"), name),
        }
        for name in _AUTHORED
    }


@pytest.mark.parametrize("face", ["stub", "runtime"])
@pytest.mark.parametrize("method", sorted(_AUTHORED))
def test_param_in_reaches_x_beside_out(faces, method, face):
    """The header's `@param in` documents `x`, with `out` in the list."""
    doc = faces[method][face]
    assert _param(doc, "out").startswith(OUT_PARAM_DOC.split(".")[0]), doc
    assert _param(doc, "x").startswith(
        "Samples to copy." if method == "copy" else "Samples to scale."
    ), doc


@pytest.mark.parametrize("face", ["stub", "runtime"])
def test_the_shape_lists_out(faces, face):
    """`out` sits after the declared param, on every face."""
    doc = faces["scale"][face]
    assert re.search(r"^gain : float$", doc, re.M), doc
    assert doc.index("\ngain : ") < doc.index("\nout : "), doc
    assert _param(doc, "gain") == "Linear scale."


# -- the binding half, built and called --------------------------------------


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

#: Two kernels over one input and a scalar, each bounded by `max_out` --
#: `run` handed its capacity, `run_d` writing blind against `max(max_out,
#: n)` -- and `run_d`'s `gain` defaulted, so `out` joins the optional group
#: the default opened rather than opening one (gh-802).
_FRAGMENT = '''\
[amp]
arg_type = "float"
return_type = "float"
mutable = "false"
no_state = "true"
no_step = "true"

[[amp.methods]]
name = "run"
arg_type = "float"
return_type = "float"
variable_output = true
pass_capacity = true
max_out = 777
params = [{ name = "gain", type = "double" }]
impl = """(void)state;
    size_t n = n_in < max_out ? n_in : max_out;
    for (size_t i = 0; i < n; i++)
        out[i] = in[i] * (float)gain;
    return n;"""

[[amp.methods]]
name = "run_d"
arg_type = "float"
return_type = "float"
variable_output = true
max_out = 777
params = [{ name = "gain", type = "double", default = "1.0" }]
impl = """(void)state;
    for (size_t i = 0; i < n_in; i++)
        out[i] = in[i] * (float)gain;
    return n_in;"""
'''

#: (id, the call). Each prints ``[values, shares-memory, tail-untouched]``
#: when given ``buf[:4]`` of a 10-element guard buffer.
_CALLS = {
    "positional": "a.run(x, 2.0, buf[:4])",
    "keyword": "a.run(x, gain=2.0, out=buf[:4])",
    "all-keyword": "a.run(x=x, gain=2.0, out=buf[:4])",
    "defaulted-keyword": "a.run_d(x, out=buf[:4])",
    "defaulted-positional": "a.run_d(x, 3.0, buf[:4])",
}

_WANT = {
    "positional": [0.0, 2.0, 4.0, 6.0],
    "keyword": [0.0, 2.0, 4.0, 6.0],
    "all-keyword": [0.0, 2.0, 4.0, 6.0],
    "defaulted-keyword": [0.0, 1.0, 2.0, 3.0],
    "defaulted-positional": [0.0, 3.0, 6.0, 9.0],
}

_PROBE = """\
import json
import numpy as np
from p.amp import Amp

a = Amp()
x = np.arange(4, dtype=np.float32)
res = {}

def _try(fn):
    try:
        return fn()
    except Exception as e:
        return "ERR " + type(e).__name__ + ": " + str(e)

def _into(call):
    buf = np.full(10, 9, np.float32)
    y = call(buf)
    return [y.tolist(), bool(np.shares_memory(y, buf)),
            buf[4:].tolist() == [9.0] * 6]

calls = CALLS
for k, src in calls.items():
    res[k] = _try(lambda: _into(eval("lambda buf: " + src)))
res["undersized"] = _try(
    lambda: a.run(x, 2.0, out=np.zeros(3, np.float32)).tolist())
res["undersized-blind"] = _try(
    lambda: a.run_d(x, out=np.zeros(3, np.float32)).tolist())
res["allocating"] = _try(lambda: a.run(x, 2.0).tolist())
res["allocating-default"] = _try(lambda: a.run_d(x).tolist())
res["max_out"] = _try(lambda: a.run_max_out(4))
res["doc"] = Amp.run.__doc__.splitlines()[0]
res["doc_d"] = Amp.run_d.__doc__.splitlines()[0]
print(json.dumps(res))
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """The object applied from its fragment, `max_out` made per-call."""
    base = tmp_path_factory.mktemp("gh2028")
    dest = base / "p"
    _quiet(new_run, "p", dest, c_prefix=None)
    fragment = base / "amp.toml"
    fragment.write_text(_FRAGMENT, encoding="utf-8")
    _quiet(apply_run, dest, fragment)
    core = dest / "native/src/amp/amp_core.c"
    src = core.read_text("utf-8")
    # `max_out` answers the input's length, so an undersized buffer is one
    # shorter than the call needs.
    stubs = re.findall(r"_max_out\([^)]*size_t n_in\)\s*\{[^}]*?\}", src)
    assert len(stubs) == 2, f"max_out stubs not where expected:\n{src}"
    for s in stubs:
        assert s.count("return 777;") == 1, s
        src = src.replace(s, s.replace("return 777;", "return n_in;"))
    core.write_text(src, encoding="utf-8")
    return dest


def _wrapper(root: Path, method: str) -> str:
    """The C wrapper jm generated for *method*, up to its closing brace."""
    text = (root / "native/src/amp/amp_ext.c").read_text("utf-8")
    i = text.index(f"\nAmpObj_{method}(")
    return text[i : text.index("\n}\n", i)]


@pytest.mark.parametrize(
    "method, fmt", [("run", "Od|O"), ("run_d", "O|dO")], ids=["run", "run_d"]
)
def test_the_parse_takes_out_last_and_optional(project, method, fmt):
    """`out` is the parse's trailing optional argument.

    It opens the optional group after a required param, and joins the one a
    defaulted param already opened -- `O|dO`, not `O|d|O`, which CPython
    refuses at the first call.
    """
    body = _wrapper(project, method)
    assert 'static char *_kwlist[] = {"x", "gain", "out", NULL};' in body, body
    assert f'PyArg_ParseTupleAndKeywords(args, kwds, "{fmt}",' in body, body


@pytest.fixture(scope="module")
def outcome(project):
    """Build the object once and run every call in one interpreter."""
    if _SKIP:
        pytest.skip(_SKIP)
    dest = project
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
    r = subprocess.run(
        [sys.executable, "-c", _PROBE.replace("CALLS", repr(_CALLS))],
        cwd=dest,
        env={**os.environ, "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, f"probe crashed:\n{r.stdout}\n{r.stderr}"
    return {**json.loads(r.stdout), "_root": dest}


@pytest.mark.parametrize("call", sorted(_CALLS))
def test_out_is_written_in_place_and_not_past(outcome, call):
    """The result IS the caller's buffer, and its guard tail is untouched."""
    assert outcome[call] == [_WANT[call], True, True], outcome[call]


@pytest.mark.parametrize("key", ["undersized", "undersized-blind"])
def test_an_undersized_out_is_refused(outcome, key):
    """Sized from the input, as the same method without params is."""
    assert outcome[key] == "ERR ValueError: out has 3 elements, need >= 4"


def test_the_allocating_call_is_unchanged(outcome):
    assert outcome["allocating"] == [0.0, 2.0, 4.0, 6.0]
    assert outcome["allocating-default"] == [0.0, 1.0, 2.0, 3.0]


def test_max_out_sizes_the_buffer_from_the_input(outcome):
    assert outcome["max_out"] == 4


def test_the_runtime_doc_lists_out(outcome):
    assert outcome["doc"].startswith("run(x, gain, out)"), outcome["doc"]
    assert outcome["doc_d"].startswith("run_d(x, gain, out)"), outcome["doc"]


def test_the_stub_publishes_what_the_binding_takes(outcome):
    text = (outcome["_root"] / "src/p/amp.pyi").read_text("utf-8")
    for name, gain in (("run", "float"), ("run_d", r"float = 1\.0")):
        assert re.search(
            rf"def {name}\(\s*self,\s*x: [^,]+,\s*gain: {gain},\s*"
            r"out: [^=]+\| None = None,?\s*\)",
            text,
        ), text
    assert "def run_max_out(self, n_in: int) -> int:" in text
