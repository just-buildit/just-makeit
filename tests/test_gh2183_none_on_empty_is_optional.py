"""gh-2183: a method whose binding can return None says so on every face.

``none_on_empty = true`` makes an empty result a normal answer: the binding
returns ``None`` instead of an empty array. On a ``borrow`` (gh-1418) the
stub said so -- ``-> NDArray[np.complex64] | None`` -- because the borrow's
branch of each annotation chain asked the key. The ``variable_output``
branch never did, so doppler's nine readouts (``PSD``'s five, ``AccTrace
.value``, ``Corr.execute``, ``Corr2D.execute``, ``Specan.execute``) were
stubbed ``-> NDArray[np.float32]`` above a binding that returns ``None`` on
both its allocating route and its ``out=`` route. A type checker accepted
``x.value().shape`` and rejected ``if x.value() is None``: the opposite of
what the binding does.

The fix is one predicate, `_diagnostics.empty_is_none`: the binding's
empty-result ``Py_RETURN_NONE`` is emitted from it, and every annotation --
both ``.pyi`` producers and the runtime synopsis line -- adds ``| None``
through `optional_ann`, and both ``Returns`` sections say when through
`empty_is_none_doc`.

The gate does not ask that predicate, which would be asking the code under
test to grade itself. **The binding is the reference**, as in
``test_body_vs_doc_gate.py``: each wrapper's empty-result branch (``if
(!n_out)`` / a NULL borrow) either returns ``None`` or it does not, and that
is read out of the generated C. Then the built extension is asked: every
method the stub calls optional is CALLED on a scaffold kernel, which writes
nothing, and must return ``None`` on each route it offers -- so the
annotation is proven true, not merely present.

GATE: for every method of an object, standalone and inside a module, a
      wrapper that returns None for an empty result is annotated ``| None``
      on the ``.pyi``, the runtime synopsis and both ``Returns`` types, and
      both ``Returns`` descriptions say when; a wrapper that never does is
      annotated optional on no face. Built, each method its stub calls
      optional returns None from an empty kernel, through ``out=`` too.
"""

from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli
from test_body_vs_doc_gate import _runtime_docs

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: The methods hung off one object: ``(name, jm method flags, expected)``.
#: *expected* is whether the binding answers None for an empty result --
#: what the gate holds the binding to before it trusts it as the reference,
#: so a predicate that stopped emitting the None is red here rather than
#: silently agreed with by every face.
_VO = "--arg-type float --return-type float --variable-output"
_GEN = "--arg-type void --return-type float --variable-output"
_BORROW = "--borrow --param n:size_t --return-type float"
METHODS: "list[tuple[str, str, bool]]" = [
    # The issue's shape: a generator with a capacity and an `out=`.
    ("value", f"{_GEN} --pass-capacity --none-on-empty", True),
    # An input block, `out=` sized from it.
    ("run", f"{_VO} --none-on-empty", True),
    # Scalar params only: `out=` sized from `<m>_max_out(state)`.
    ("tune", f"{_GEN} --param gain:float --none-on-empty", True),
    # Two outputs: a tuple, allocated, with no `out=`.
    ("pair", f"{_VO} --multi-output float --none-on-empty", True),
    # A borrowed view: the route that was already right (gh-1418).
    ("peek", f"{_BORROW} --none-on-empty", True),
    # The same two routes without the key: an empty array, and a raise.
    ("plain", _VO, False),
    ("wait", _BORROW, False),
    # The key where no route reads it -- a 1:1 batch is dispatched ahead of
    # the variable-output route, and a scalar has no empty result -- so no
    # face may promise a None the binding never returns. Accepted today;
    # gh-2202 refuses it, and these two then move to a refusal test.
    ("block", f"{_VO} --batch --none-on-empty", False),
    ("level", "--arg-type float --return-type float --none-on-empty", False),
]

_EMPTY = re.compile(r"if\s*\(\s*!\s*(?:n_out|_p)\s*\)\s*")
# One PyMethodDef row's Python name and the C wrapper it points at: the
# identifier right before `, METH_`, past whatever cast precedes it.
_ROW = re.compile(r'\{"(\w+)",[^"{}]*?\b(\w+),\s*METH_')
_RETURNS = re.compile(r"^Returns\n-+\n(.*)\n((?:    .*(?:\n|$))*)", re.M)


#: gh-2200: `--batch --variable-output` declares a kernel its own scaffold
#: stub contradicts, so a project carrying it does not compile. The text
#: faces keep it -- it is the shape `empty_is_none`'s batch term exists for
#: -- and the project that is BUILT leaves it out.
_BUILDS = [case for case in METHODS if case[0] != "block"]


def _scaffold(
    base: Path,
    module: "str | None",
    methods: "list[tuple[str, str, bool]]" = METHODS,
) -> "tuple[str, str]":
    """One object carrying *methods*; returns ``(ext_c, pyi)`` text."""
    base.mkdir(parents=True)
    assert run_cli("new", "p", cwd=base).returncode == 0
    root = base / "p"
    where = ("--module", module) if module else ()
    if module:
        r = run_cli("module", module, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli(
        "object",
        "o",
        *where,
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    for name, flags, _ in methods:
        r = run_cli("method", "o", name, *flags.split(), *where, cwd=root)
        assert r.returncode == 0, (name, r.stdout + r.stderr)
    assert run_cli("apply", cwd=root).returncode == 0
    if module:
        ext = root / "native" / "src" / module / f"{module}_ext_o.c"
        pyi = root / "src" / "p" / module / f"{module}.pyi"
    else:
        ext = root / "native" / "src" / "o" / "o_ext.c"
        pyi = root / "src" / "p" / "o.pyi"
    return ext.read_text("utf-8"), pyi.read_text("utf-8")


def _block(text: str, at: int) -> str:
    """The statement or ``{...}`` block that starts at *at*."""
    if text[at] != "{":
        return text[at : text.index(";", at) + 1]
    depth = 0
    for i in range(at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[at : i + 1]
    raise AssertionError(f"unbalanced block at {at}")


def binding_returns_none(ext_c: str) -> "dict[str, bool]":
    """``{python name: does its wrapper return None for an empty result}``.

    Read from the generated C, the one artefact that cannot be wrong about
    what the binding does: each empty-result test -- a zero count, a NULL
    borrow -- is followed to its statement or block, and the method answers
    None when any of them reaches ``Py_RETURN_NONE``.
    """
    out: "dict[str, bool]" = {}
    for name, cfn in _ROW.findall(ext_c):
        body = re.search(rf"^{cfn}\(.*?^\}}", ext_c, re.S | re.M)
        assert body, f"no definition of {cfn} for {name}()"
        text = body.group(0)
        out[name] = any(
            "Py_RETURN_NONE" in _block(text, m.end())
            for m in _EMPTY.finditer(text)
        )
    return out


def _returns(doc: str) -> "tuple[str, str]":
    """``(type line, description)`` of *doc*'s numpy ``Returns``, or blanks.

    Anchored on the heading and its underline: prose mentioning a return is
    not the section.
    """
    m = _RETURNS.search(doc)
    if not m:
        return "", ""
    return m.group(1).strip(), " ".join(m.group(2).split())


def _stub_faces(pyi: str) -> "dict[str, tuple[str, str, str]]":
    """``{method: (annotation, Returns type, Returns description)}``."""
    out = {}
    for cls in (n for n in ast.parse(pyi).body if isinstance(n, ast.ClassDef)):
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and fn.returns is not None:
                out[fn.name] = (
                    ast.unparse(fn.returns),
                    *_returns(ast.get_docstring(fn) or ""),
                )
    return out


def _help_faces(ext_c: str) -> "dict[str, tuple[str, str, str]]":
    """``{method: (synopsis return, Returns type, Returns description)}``."""
    out = {}
    for name, doc in _runtime_docs(ext_c).items():
        synopsis = doc.split("\n", 1)[0]
        out[name] = (
            synopsis.split(" -> ", 1)[1] if " -> " in synopsis else "",
            *_returns(doc),
        )
    return out


def _optional(face: "tuple[str, str, str]") -> "tuple[bool, bool, bool]":
    """Whether each part of a face says the method may return None."""
    ann, rtype, desc = face
    return (
        ann.endswith("| None"),
        rtype.endswith("| None"),
        "None when" in desc,
    )


@pytest.fixture(scope="module", params=["standalone", "module"])
def tree(request, tmp_path_factory) -> "tuple[str, str, str]":
    base = tmp_path_factory.mktemp(f"gh2183_{request.param}")
    module = None if request.param == "standalone" else "m"
    ext, pyi = _scaffold(base / "t", module)
    return request.param, ext, pyi


class TestTheGateIsArmed:
    """The reference has to see both answers before agreement means much."""

    def test_the_binding_is_the_reference_it_is_held_to(self, tree):
        """Every declared method is a wrapper, and returns None exactly where
        the key is read. A predicate that stopped emitting the None would
        otherwise leave every face agreeing with a binding that never
        returns it -- green, and wrong about the product."""
        _, ext, _ = tree
        found = binding_returns_none(ext)
        assert {n: found.get(n) for n, _, _ in METHODS} == {
            n: want for n, _, want in METHODS
        }

    def test_each_face_parsed(self, tree):
        _, ext, pyi = tree
        assert {"value", "peek", "plain"} <= set(_stub_faces(pyi))
        assert {"value", "peek", "plain"} <= set(_help_faces(ext))


class TestEveryFaceAgreesWithTheBinding:
    @pytest.mark.parametrize("face", [".pyi", "help()"])
    def test_none_is_documented_exactly_where_it_is_returned(self, tree, face):
        kind, ext, pyi = tree
        faces = _stub_faces(pyi) if face == ".pyi" else _help_faces(ext)
        wrong = {}
        for name, returns_none in binding_returns_none(ext).items():
            if name not in {n for n, _, _ in METHODS}:
                continue
            if name not in faces:
                # gh-1905: a batch method has no standalone `.pyi` entry.
                # Absent is not a false promise; absent where the binding
                # DOES return None is one.
                if returns_none:
                    wrong[name] = "missing"
                continue
            said = _optional(faces[name])
            if said != (returns_none,) * 3:
                wrong[name] = (returns_none, faces[name])
        assert not wrong, f"{kind} {face}: {wrong}"

    def test_the_two_stub_producers_say_it_alike(self, tmp_path_factory):
        """The standalone `.pyi` and the module-aggregated one are separate
        producers; wiring one is how every peer pair here has diverged."""
        faces = []
        for module in (None, "m"):
            base = tmp_path_factory.mktemp("gh2183_pair")
            _, pyi = _scaffold(base / "t", module)
            faces.append(_stub_faces(pyi))
        shared = (set(faces[0]) & set(faces[1])) & {n for n, _, _ in METHODS}
        assert {"value", "run", "peek"} <= shared
        assert {n: faces[0][n] for n in shared} == {
            n: faces[1][n] for n in shared
        }


class TestTheWords:
    def test_returns_says_when_for_each_route(self, tree):
        _, ext, pyi = tree
        for faces in (_stub_faces(pyi), _help_faces(ext)):
            assert faces["value"][2].endswith(
                "None when the C call writes no output."
            ), faces["value"]
            assert faces["peek"][2].endswith(
                "None when the C call lends nothing (returns NULL)."
            ), faces["peek"]

    def test_a_tuple_result_is_optional_as_a_whole(self, tree):
        """The binding returns None, not a tuple of Nones."""
        _, _, pyi = tree
        assert _stub_faces(pyi)["pair"][0] == (
            "tuple[NDArray[np.float32], NDArray[np.float32]] | None"
        )


def _probe(pyi: str) -> str:
    """A pytest file calling every method the stub calls optional.

    Derived from the stub, not listed: each required argument from its
    annotation, and -- where the method offers ``out=`` -- a second call
    through a buffer sized by its own ``<m>_max_out``. Scaffold kernels
    write nothing, so every one of these calls has an empty result.
    """
    tree = ast.parse(pyi)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    fns = {f.name: f for f in cls.body if isinstance(f, ast.FunctionDef)}

    def value(ann: str) -> str:
        m = re.search(r"NDArray\[(np\.\w+)\]", ann)
        if m:
            return f"np.zeros(4, dtype={m.group(1)})"
        return {"int": "4", "float": "1.0"}[ann]

    lines = [
        "import numpy as np",
        f"from p import {cls.name}",
        "",
        "",
        "def test_gh2183_empty_results_are_none():",
        f"    o = {cls.name}()",
    ]
    for name, fn in fns.items():
        if not ast.unparse(fn.returns or ast.Constant(None)).endswith(
            "| None"
        ):
            continue
        args = fn.args.args[1:]
        required = args[: len(args) - len(fn.args.defaults)]
        call = ", ".join(value(ast.unparse(a.annotation)) for a in required)
        lines += [
            f"    assert o.{name}({call}) is None, {name!r}",
            f"    assert o.{name}.__doc__.split(chr(10))[0]"
            f".endswith('| None'), o.{name}.__doc__",
            f"    assert 'None when' in o.{name}.__doc__, {name!r}",
        ]
        out = next((a for a in args if a.arg == "out"), None)
        if out is None:
            continue
        mo = fns[f"{name}_max_out"]
        mo_arg = "4" if len(mo.args.args) > 1 else ""
        elem = re.search(r"NDArray\[(np\.\w+)\]", ast.unparse(out.annotation))
        # A scaffold `_max_out` answers 0, and an input-sized method then
        # needs the input's length (gh-421): room for both.
        buf = (
            f"np.empty(max(o.{name}_max_out({mo_arg}), 64),"
            f" dtype={elem.group(1)})"
        )
        sep = ", " if call else ""
        lines += [
            f"    assert o.{name}({call}{sep}out={buf}) is None, {name!r}",
        ]
    return "\n".join(lines) + "\n"


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
class TestTheAnnotationIsTrue:
    """Text cannot answer this: does the compiled binding return None?"""

    def test_every_optional_method_returns_none_when_empty(self, tmp_path):
        _scaffold(tmp_path / "t", None, _BUILDS)
        root = tmp_path / "t" / "p"
        # An all-scalar-params method sizes its output from `_max_out` alone
        # and refuses a bound of 0, so that one bound is implemented -- the
        # kernel itself stays the scaffold, which writes nothing.
        core = root / "native" / "src" / "o" / "o_core.c"
        text, n = re.subn(
            r"(p_o_tune_max_out\([^)]*\)\s*\{[^}]*?)return 0;",
            r"\1return 4;",
            core.read_text("utf-8"),
        )
        assert n == 1, "the scaffold's tune_max_out body moved"
        core.write_text(text, encoding="utf-8")
        pyi = (root / "src" / "p" / "o.pyi").read_text("utf-8")
        probe = _probe(pyi)
        # Five methods, and the three that offer `out=` called twice.
        assert probe.count(") is None, ") == 8, probe
        (root / "src" / "p" / "tests" / "test_gh2183_probe.py").write_text(
            probe, encoding="utf-8"
        )
        out = run_cli("test", cwd=root)
        assert out.returncode == 0, out.stdout + out.stderr
        # Absent output is not a pass: the probe has to have RUN.
        assert (
            "test_gh2183_probe.py::test_gh2183_empty_results_are_none PASSED"
            in out.stdout
        ), out.stdout
