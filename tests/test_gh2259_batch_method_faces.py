"""gh-2259 (part 1): a ``batch`` method's faces, and ``nogil`` on every shape.

``batch = true`` (``jm method --batch``) is jm's declared form of an
elementwise block method -- ``void f(state, const T *in, size_t n, R *out)``,
array in, array of the same length out, an optional ``out=``. doppler's
``prod`` is one. Three of its faces were wrong:

1. **The ``.pyi`` had no member for it.** The standalone writer reached the
   batch shape through a ``continue`` that skipped the stub at the end of
   the loop; the module-aggregated writer rendered it as a scalar method
   (``prod(self, x: complex) -> complex``). Both now say array in, array
   out, ``out=`` optional -- and agree with each other.
2. **``nogil = true`` was accepted and ignored.** And not only on a batch
   method: every kernel call ``make_methods_ctx`` spelled inline rather
   than through ``_nogil_call`` ignored it -- every fixed-size result (a
   scalar, ``void``, ``status_return``, ``error_negative``,
   ``multi_output``, ``out_type``), a codec-pack method's sink, and a
   capsule module's bare ``(state)`` method. Measured, not assumed: doppler
   carried nine such methods believing the GIL released. Every call jm
   writes now goes through the one emitter; the two shapes whose binding
   jm does not write (``varargs``, ``manual_stub``) refuse the key.
3. **The runtime ``__doc__`` typed the arrays as scalars** (``x : complex``,
   ``Returns complex``). It now reads the same annotations the stub does,
   through the shared numpy section builder (gh-642).

The gates, cheapest first: a source gate over ``make_methods_ctx`` that
finds every kernel call it emits and requires each to be built inside the
emitter (registration-free: a new shape's call is covered the moment it is
written); the rendered C of each measured shape; both stub writers; the
refusal; ``apply`` naming a module object's fragment rendered before the
fix, which it does not re-render; and a compiled check that another thread
needing the GIL runs while the kernel does.
"""

# gh-1591: this file's hand-written C spells jm's bare derived names, so its
# projects opt out of the prefix `jm new` now defaults to.

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _capsule  # noqa: E402
from just_makeit import _codec  # noqa: E402
from just_makeit import _config  # noqa: E402
from test_codec_pack import _CODEC, _METHOD  # noqa: E402
from test_gh1960_method_params_reach_the_kernel import (  # noqa: E402
    _is_declaration,
)
from test_gh642_runtime_doc_parity import _runtime_doc  # noqa: E402

METHODS_PY = SRC / "just_makeit" / "_context" / "_methods.py"

#: The functions a kernel call in `make_methods_ctx` may be built inside:
#: the emitter and the two wrappers over it.
EMITTERS = frozenset(
    {"_nogil_call", "_kernel_call_block", "_single_kernel_block"}
)

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _project(root: Path, *, module: bool, nogil: bool = True) -> Path:
    """A project whose ``synth`` declares one batch method of each shape."""
    _ok("new", "p", "--no-c-prefix", cwd=root)
    proj = root / "p"
    where = ["--module", "dsp"] if module else []
    if module:
        _ok("module", "dsp", cwd=proj)
    _ok("object", "synth", *where, "--state", "go:int:0", cwd=proj)
    gil = ["--nogil"] if nogil else []
    _ok(
        "method",
        "synth",
        "prod",
        "--arg-type",
        "float _Complex",
        "--return-type",
        "float _Complex",
        "--batch",
        *gil,
        cwd=proj,
    )
    _ok(
        "method",
        "synth",
        "fill",
        "--return-type",
        "float",
        "--batch",
        *gil,
        cwd=proj,
    )
    return proj


def _pyi(proj: Path, module: bool) -> str:
    rel = "src/p/dsp/dsp.pyi" if module else "src/p/synth.pyi"
    return (proj / rel).read_text(encoding="utf-8")


def _ext(proj: Path, module: bool) -> str:
    rel = (
        "native/src/dsp/dsp_ext_synth.c"
        if module
        else "native/src/synth/synth_ext.c"
    )
    return (proj / rel).read_text(encoding="utf-8")


def _wrapper(ext: str, cls: str, name: str) -> str:
    """The generated ``<cls>_<name>`` wrapper function, whole."""
    m = re.search(rf"\n{cls}_{re.escape(name)}\(.*?\n}}\n", ext, re.S)
    assert m, f"no wrapper {cls}_{name}"
    return m.group(0)


def _member(pyi: str, name: str) -> ast.FunctionDef | None:
    cls = next(
        n
        for n in ast.parse(pyi).body
        if isinstance(n, ast.ClassDef) and n.name == "Synth"
    )
    return next(
        (
            n
            for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name == name
        ),
        None,
    )


def _signature(fn: ast.FunctionDef) -> list[str]:
    """``name: annotation = default`` per argument, then ``-> return``."""
    args = fn.args.args[1:]
    defaults = [None] * (len(args) - len(fn.args.defaults)) + list(
        fn.args.defaults
    )
    out = [
        f"{a.arg}: {ast.unparse(a.annotation)}"
        + (f" = {ast.unparse(d)}" if d is not None else "")
        for a, d in zip(args, defaults)
    ]
    return out + [f"-> {ast.unparse(fn.returns)}"]


def _sections(lines: list[str]) -> list[str]:
    """From the ``Parameters`` heading to the end of ``Returns``."""
    lines = [ln.strip() for ln in lines]
    start = lines.index("Parameters")
    end = next(
        (i for i in range(start, len(lines)) if lines[i] == "Examples"),
        len(lines),
    )
    return [ln for ln in lines[start:end] if ln]


@pytest.fixture(scope="module", params=[False, True], ids=["object", "module"])
def batch_project(request, tmp_path_factory) -> tuple[Path, bool]:
    module = request.param
    root = tmp_path_factory.mktemp("module" if module else "object")
    return _project(root, module=module), module


# ── 1. the stub ────────────────────────────────────────────────────────────


class TestTheStub:
    """Each producer on its own. That the two agree member for member is
    `tests/test_face_parity_gate.py`'s, whose fixture now has a batch
    method of each shape and compares the member SETS too."""

    def test_the_member_is_there(self, batch_project):
        """The regression: no `prod` at all in the standalone `.pyi`."""
        proj, module = batch_project
        pyi = _pyi(proj, module)
        assert _member(pyi, "prod") is not None, pyi
        assert _member(pyi, "fill") is not None, pyi

    def test_array_in_array_out_and_an_optional_out(self, batch_project):
        proj, module = batch_project
        pyi = _pyi(proj, module)
        assert _signature(_member(pyi, "prod")) == [
            "x: npt.NDArray[np.complex64]",
            "out: npt.NDArray[np.complex64] | None = None",
            "-> NDArray[np.complex64]",
        ]
        # The count is the kwlist's name and the binding's default.
        assert _signature(_member(pyi, "fill")) == [
            "count: int = 1",
            "out: npt.NDArray[np.float32] | None = None",
            "-> NDArray[np.float32]",
        ]

    def test_no_max_out_sibling(self, batch_project):
        """A batch result is exactly as long as its input: no bound."""
        proj, module = batch_project
        pyi = _pyi(proj, module)
        assert "prod_max_out" not in pyi
        assert "fill_max_out" not in pyi


# ── 3. the runtime doc ─────────────────────────────────────────────────────


class TestTheRuntimeDoc:
    def test_the_arrays_are_typed_as_arrays(self, batch_project):
        proj, module = batch_project
        doc = _runtime_doc(_ext(proj, module), "prod")
        assert "x : npt.NDArray[np.complex64]" in doc, doc
        assert "out : npt.NDArray[np.complex64] | None" in doc, doc
        assert doc[doc.index("Returns") + 2] == "NDArray[np.complex64]", doc
        assert "x : complex" not in doc

    def test_its_sections_are_the_stubs(self, batch_project):
        """One section builder for both faces (gh-642)."""
        proj, module = batch_project
        pyi = _pyi(proj, module)
        ext = _ext(proj, module)
        for name in ("prod", "fill"):
            stub = ast.get_docstring(_member(pyi, name)).split("\n")
            assert _sections(_runtime_doc(ext, name)) == _sections(stub)

    def test_the_synopsis_names_the_binding_arguments(self, batch_project):
        proj, module = batch_project
        ext = _ext(proj, module)
        assert _runtime_doc(ext, "prod")[0] == "prod(x, out=None) -> ndarray"
        # It said `fill(n, out=None)` over a kwlist binding `count`.
        assert (
            _runtime_doc(ext, "fill")[0]
            == "fill(count=1, out=None) -> ndarray"
        )

    def test_the_count_says_exact_not_at_most(self, batch_project):
        proj, module = batch_project
        doc = "\n".join(_runtime_doc(_ext(proj, module), "fill"))
        assert "_max_out" not in doc
        assert "exactly" in doc


# ── 2. nogil: every kernel call goes through the emitter ───────────────────


def _make_methods_ctx() -> ast.FunctionDef:
    tree = ast.parse(METHODS_PY.read_text(encoding="utf-8"))
    return next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "make_methods_ctx"
    )


def _calls_the_kernel(node: ast.JoinedStr) -> bool:
    """Does this f-string spell ``{c_fn}(`` -- a call, or a prototype?"""
    vals = node.values
    return any(
        isinstance(a, ast.FormattedValue)
        and isinstance(a.value, ast.Name)
        and a.value.id == "c_fn"
        and isinstance(b, ast.Constant)
        and str(b.value).startswith("(")
        for a, b in zip(vals, vals[1:])
    )


def _emitter(node: ast.AST, parents: dict) -> str:
    """The emitter this f-string is built inside, or ``""``."""
    while node in parents:
        node = parents[node]
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in EMITTERS
        ):
            return node.func.id
    return ""


class TestEveryKernelCallHonoursNogil:
    def test_every_call_is_built_inside_the_emitter(self):
        """Registration-free over the renderer's own source.

        Every f-string in `make_methods_ctx` that spells ``{c_fn}(`` is
        either a declaration or a CALL, and a call must be built inside
        `_nogil_call` -- that is what makes ``nogil`` reach it. A shape that
        spells its call inline fails here naming the line, which is how
        batch and the fixed-size results ignored the key.
        """
        fn = _make_methods_ctx()
        parents = {
            child: node
            for node in ast.walk(fn)
            for child in ast.iter_child_nodes(node)
        }
        # A declaration is what gh-1960's site marker calls one: a
        # `decl_lines` prototype, or a varargs method's `extern`.
        sites = [
            (n.lineno, _emitter(n, parents))
            for n in ast.walk(fn)
            if isinstance(n, ast.JoinedStr)
            and _calls_the_kernel(n)
            and not _is_declaration(n, parents)
        ]
        stray = [line for line, where in sites if not where]
        assert not stray, (
            f"kernel call(s) at {METHODS_PY.name} line(s) {stray} are not "
            f"built inside {sorted(EMITTERS)}: `nogil` cannot reach them"
        )
        # Armed: the walk found the calls it is about.
        assert len(sites) >= 15, sites


#: Every shape `jm method` accepted `--nogil` on, measured on v0.104.0 for
#: gh-2259: the first thirteen and both batch shapes held the GIL.
SHAPES = {
    "scalar": ["--arg-type", "float", "--return-type", "float"],
    "void_ret": ["--arg-type", "float", "--return-type", "void"],
    "noarg_scalar": ["--return-type", "float"],
    "noarg_void": ["--return-type", "void"],
    "array_arg": ["--arg-type", "float[]", "--return-type", "float"],
    "params": ["--param", "g:double", "--return-type", "float"],
    "arg_params": [
        "--arg-type",
        "float",
        "--param",
        "g:double",
        "--return-type",
        "float",
    ],
    "array_param": ["--param", "v:float[]", "--return-type", "float"],
    "status_return": [
        "--arg-type",
        "float",
        "--return-type",
        "int",
        "--status-return",
    ],
    "error_negative": [
        "--arg-type",
        "float",
        "--return-type",
        "int",
        "--error-negative",
    ],
    "multi_output": [
        "--arg-type",
        "float",
        "--return-type",
        "float",
        "--multi-output",
        "double",
    ],
    "multi_output_void": [
        "--arg-type",
        "float",
        "--return-type",
        "void",
        "--multi-output",
        "double",
    ],
    "out_type": [
        "--param",
        "n:size_t",
        "--return-type",
        "void",
        "--out-type",
        "float",
    ],
    "batch_arg": [
        "--arg-type",
        "float _Complex",
        "--return-type",
        "float _Complex",
        "--batch",
    ],
    "batch_gen": ["--return-type", "float", "--batch"],
    "borrow": ["--param", "n:size_t", "--return-type", "float", "--borrow"],
    "vo_arg": [
        "--arg-type",
        "float",
        "--return-type",
        "float",
        "--variable-output",
    ],
    "vo_gen": ["--return-type", "float", "--variable-output"],
    "single": [
        "--arg-type",
        "float",
        "--return-type",
        "rec_t",
        "--single",
        "--result-field",
        "a:double",
    ],
    "records": [
        "--arg-type",
        "float",
        "--return-type",
        "rec2_t",
        "--result-field",
        "a:double",
    ],
}


@pytest.fixture(scope="module")
def every_shape(tmp_path_factory) -> str:
    root = tmp_path_factory.mktemp("shapes")
    _ok("new", "mp", "--no-c-prefix", cwd=root)
    proj = root / "mp"
    _ok("object", "o", "--state", "k:double:0.0", cwd=proj)
    for name, flags in SHAPES.items():
        _ok("method", "o", name, *flags, "--nogil", cwd=proj)
    return (proj / "native/src/o/o_ext.c").read_text(encoding="utf-8")


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_call_runs_with_the_gil_released(every_shape, shape):
    body = _wrapper(every_shape, "O", shape)
    calls = [m.start() for m in re.finditer(rf"\bo_{shape}\(", body)]
    assert calls, body
    for at in calls:
        before = body[:at]
        assert before.rfind("Py_BEGIN_ALLOW_THREADS") > before.rfind(
            "Py_END_ALLOW_THREADS"
        ), f"o_{shape}() runs holding the GIL:\n{body}"
    # Nothing of Python's runs inside a released block -- the arrays are
    # acquired and the result allocated above it.
    for block in re.findall(
        r"Py_BEGIN_ALLOW_THREADS\n(.*?)Py_END_ALLOW_THREADS", body, re.S
    ):
        assert "Py" not in block, (
            f"Python C-API with the GIL released:\n{block}"
        )


def test_without_nogil_nothing_is_released(tmp_path):
    """The other half: the key is what releases it, not the shape."""
    proj = _project(tmp_path, module=False, nogil=False)
    assert "ALLOW_THREADS" not in _ext(proj, False)


def test_a_codec_sink_is_released():
    m = dict(_METHOD, nogil=True)
    body, _, _ = _codec.render_pack(
        "gizmo", "Gizmo", "Gizmo", m, _CODEC["kw"], ""
    )
    sinks = [x.start() for x in re.finditer(r"\bgizmo_add_kw\(", body)]
    assert len(sinks) == 2, body
    for at in sinks:
        before = body[:at]
        assert before.rfind("Py_BEGIN_ALLOW_THREADS") > before.rfind(
            "Py_END_ALLOW_THREADS"
        ), body
    plain, _, _ = _codec.render_pack(
        "gizmo", "Gizmo", "Gizmo", dict(_METHOD), _CODEC["kw"], ""
    )
    assert "ALLOW_THREADS" not in plain


def test_a_capsule_void_method_is_released():
    body = _capsule._emit_void_method(
        "eng", "eng", {"name": "flush", "nogil": True}
    )
    before = body[: body.index("eng_flush(w->state)")]
    assert before.rfind("Py_BEGIN_ALLOW_THREADS") > before.rfind(
        "Py_END_ALLOW_THREADS"
    ), body
    plain = _capsule._emit_void_method("eng", "eng", {"name": "flush"})
    assert "ALLOW_THREADS" not in plain


def _module_fragment_without_nogil(root: Path) -> Path:
    """A module object's fragment rendered from a row with no `nogil`."""
    _ok("new", "p", "--no-c-prefix", cwd=root)
    proj = root / "p"
    _ok("module", "dsp", cwd=proj)
    _ok("object", "mx", "--module", "dsp", cwd=proj)
    _ok("method", "mx", "w", "--return-type", "int", cwd=proj)
    return proj


class TestAFragmentRenderedBeforeIt:
    """A sacred fragment only gains MISSING members, so `apply` does not
    re-render a body to add the release -- it says the body holds the GIL,
    on the feature axis gh-2144's range guard reports on. doppler's nine
    held-GIL methods are all module objects."""

    def test_apply_names_the_member(self, tmp_path):
        proj = _module_fragment_without_nogil(tmp_path)
        frag = proj / "objects" / "mx.toml"
        text = frag.read_text(encoding="utf-8")
        assert text.count('name = "w"\n') == 1, text
        frag.write_text(
            text.replace('name = "w"\n', 'name = "w"\nnogil = true\n', 1),
            encoding="utf-8",
        )
        r = run_cli("apply", cwd=proj)
        assert r.returncode == 0, r.stderr
        said = r.stdout + r.stderr
        assert "w: the manifest declares nogil" in said, said
        assert "holding the GIL" in said, said

    def test_a_fragment_that_releases_it_is_not_named(self, tmp_path):
        proj = _module_fragment_without_nogil(tmp_path)
        frag = proj / "objects" / "mx.toml"
        text = frag.read_text(encoding="utf-8")
        frag.write_text(
            text.replace('name = "w"\n', 'name = "w"\nnogil = true\n', 1),
            encoding="utf-8",
        )
        (proj / "native" / "src" / "dsp" / "dsp_ext_mx.c").unlink()
        _ok("apply", cwd=proj)
        r = run_cli("apply", cwd=proj)
        assert "declares nogil" not in r.stdout + r.stderr


# ── 2b. nogil where jm writes no call: refused, by one validator ───────────


@pytest.mark.parametrize("shape", _config.NOGIL_UNWRITTEN_SHAPES)
class TestNogilWithoutAGeneratedCall:
    def test_the_cli_refuses_it(self, tmp_path, shape):
        _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
        proj = tmp_path / "p"
        _ok("object", "o", cwd=proj)
        flag = "--" + shape.replace("_", "-")
        manifest = (proj / "objects" / "o.toml").read_text(encoding="utf-8")
        r = run_cli("method", "o", "m", flag, "--nogil", cwd=proj)
        assert r.returncode == 1, r.stdout
        assert f"a {shape}" in r.stderr and "nogil" in r.stderr, r.stderr
        # Refused before anything was written.
        assert (proj / "objects" / "o.toml").read_text(
            encoding="utf-8"
        ) == manifest
        # ...and the shape itself is still accepted.
        _ok("method", "o", "m", flag, cwd=proj)

    def test_apply_refuses_the_manifest_row(self, tmp_path, shape):
        _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
        proj = tmp_path / "p"
        _ok("object", "o", cwd=proj)
        _ok("method", "o", "m", "--" + shape.replace("_", "-"), cwd=proj)
        frag = proj / "objects" / "o.toml"
        text = frag.read_text(encoding="utf-8")
        line = f"{shape} = true"
        assert text.count(line) == 1, text
        frag.write_text(
            text.replace(line, f"{line}\nnogil = true", 1), encoding="utf-8"
        )
        r = run_cli("apply", cwd=proj)
        assert r.returncode == 1, r.stdout
        assert "'o' method 'm': nogil" in r.stderr, r.stderr


# ── the compiled check: does another thread run during the kernel? ─────────

# Each kernel spins until `go` is set -- by another Python thread, through
# the generated setter, which needs the GIL -- or until 3 s of CPU pass.
# Released, the setter runs and the result says 2; held, the kernel gives
# up first and says 1.
_SPIN = """
    int g = 1;
    clock_t t0 = clock();
    while (!*(volatile int *)&state->go
           && clock() - t0 < 3 * CLOCKS_PER_SEC)
        ;
    if (*(volatile int *)&state->go)
        g = 2;
"""

KERNELS = f"""
void prod(void)
{{{_SPIN}
    for (size_t i = 0; i < n; i++)
        out[i] = in[i] * (float)g;
}}

void fill(void)
{{{_SPIN}
    for (size_t i = 0; i < n; i++)
        out[i] = (float)g;
}}

int wait(void)
{{{_SPIN}
    return g;
}}
"""

PROBE = """
import sys
import threading
import time

sys.path.insert(0, "src")
import numpy as np
from p import Synth


def run(call):
    s = Synth(go=0)
    t = threading.Thread(target=lambda: (time.sleep(0.2), s.set_go(1)))
    t.start()
    try:
        return call(s)
    finally:
        t.join()


x = np.ones(4, dtype=np.complex64)
print("prod", int(run(lambda s: s.prod(x))[0].real))
print("prod_out", int(run(lambda s: s.prod(x, out=np.zeros_like(x)))[0].real))
print("fill", int(run(lambda s: s.fill(4))[0]))
f = np.zeros(4, dtype=np.float32)
print("fill_out", int(run(lambda s: s.fill(4, out=f))[0]))
print("wait", run(lambda s: s.wait()))
print("hold", run(lambda s: s.hold()))
"""


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_another_thread_runs_during_the_kernel(tmp_path):
    kfile = tmp_path / "kernels.c"
    kfile.write_text(KERNELS, encoding="utf-8")
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("object", "synth", "--state", "go:int:0", cwd=proj)
    for name, flags in (
        (
            "prod",
            [
                "--arg-type",
                "float _Complex",
                "--return-type",
                "float _Complex",
                "--batch",
                "--nogil",
            ],
        ),
        ("fill", ["--return-type", "float", "--batch", "--nogil"]),
        ("wait", ["--return-type", "int", "--nogil"]),
        # The control: the same kernel, GIL held. It proves the probe can
        # tell -- a probe that always printed 2 would pass the rest.
        ("hold", ["--return-type", "int"]),
    ):
        src = "wait" if name == "hold" else name
        _ok(
            "method",
            "synth",
            name,
            *flags,
            "--impl",
            f"{kfile}::{src}",
            cwd=proj,
        )
    core = proj / "native" / "src" / "synth" / "synth_core.c"
    core.write_text(
        "#include <time.h>\n" + core.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # Against THIS interpreter, which runs the probe: the Makefile's own
    # pick is whatever `python3` the PATH holds.
    build = subprocess.run(
        ["make", f"PYTHON={sys.executable}"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert build.returncode == 0, build.stderr[-3000:]
    script = proj / "probe.py"
    script.write_text(PROBE, encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(script)],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.returncode == 0, out.stderr[-3000:]
    got = dict(line.split() for line in out.stdout.split("\n") if line)
    assert got == {
        "prod": "2",
        "prod_out": "2",
        "fill": "2",
        "fill_out": "2",
        "wait": "2",
        "hold": "1",
    }, got
