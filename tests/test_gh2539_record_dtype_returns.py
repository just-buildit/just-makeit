"""gh-2539: a ``record_dtype`` method's ``Returns`` names the record's columns.

A ``variable_output`` method with ``record_dtype`` and ``result_fields``
returns ONE structured ndarray (gh-788). Every stub annotates it
``NDArray[Any]`` -- numpy's typing cannot spell a structured dtype's
columns -- so the docstring is the only place they can be stated. Both
``.pyi`` faces said ``Output.``; the runtime ``__doc__`` alone listed the
columns, in a ``Fields`` section numpydoc does not know. A reader of the
stub learned the fields only if the method's ``doc`` repeated them, which
is what doppler's ``Correlator.push`` did meanwhile.

The fix is one answer, `_record.returns_fields`, laid out by the one entry
helper a ``single`` record's class already lists its ``Attributes`` with
(`_record.field_entries`), and handed to the renderer by every method face:
the standalone stub, the module-aggregated stub, and the runtime doc of
both a standalone object and a module object.

GATE: on every one of those four faces, the method's ``Returns`` entry
      lists each column ``name : type`` with its doc, and says nothing
      generic; the four ``Returns`` blocks are the same text; a header
      ``@return`` still leads them; a manifest ``doc`` still owns the
      summary.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from _jmrun import run_cli
from test_body_vs_doc_gate import _runtime_docs

_REC = (
    "--arg-type", "float _Complex", "--return-type", "size_t",
    "--variable-output", "--record-dtype", "hit_t",
    "--result-field", "index:uint64_t", "--result-field", "power:float",
)  # fmt: skip

#: The columns every face must list, in order: ``(entry, doc or "")``.
COLUMNS = [("index : int", "Sample index of the hit."), ("power : float", "")]

#: ``(label, .pyi path, ext.c path, class name)`` for each object.
FACES = [
    ("standalone", "src/p/corr.pyi", "native/src/corr/corr_ext.c", "Corr"),
    (
        "module",
        "src/p/dsp/dsp.pyi",
        "native/src/dsp/dsp_ext_mcorr.c",
        "Mcorr",
    ),
]


def _scaffold(tmp_path: Path, *, method_doc: str = "") -> Path:
    """One standalone object and one module object, each with ``push``.

    The first column carries a manifest ``doc`` and the second none, so a
    face that drops a field's doc and one that drops an undocumented field
    are both visible.
    """
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    for argv in (
        ("object", "corr"),
        ("method", "corr", "push", *_REC),
        ("module", "dsp"),
        ("object", "mcorr", "--module", "dsp"),
        ("method", "mcorr", "push", "--module", "dsp", *_REC),
    ):
        r = run_cli(*argv, cwd=root)
        assert r.returncode == 0, (argv, r.stdout + r.stderr)
    for frag in ("objects/corr.toml", "objects/mcorr.toml"):
        path = root / frag
        text = path.read_text("utf-8")
        anchor = 'name = "index"\ntype = "uint64_t"\n'
        assert text.count(anchor) == 1, text
        text = text.replace(anchor, anchor + f'doc = "{COLUMNS[0][1]}"\n')
        if method_doc:
            anchor = 'name = "push"\n'
            assert text.count(anchor) == 1, text
            text = text.replace(anchor, anchor + f'doc = "{method_doc}"\n')
        path.write_text(text, "utf-8")
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


def _stub_doc(pyi: str, cls: str) -> str:
    """``cls.push``'s docstring, as ``ast`` reads it out of the stub."""
    tree = ast.parse(pyi)
    node = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls
    )
    fn = next(
        f
        for f in node.body
        if isinstance(f, ast.FunctionDef) and f.name == "push"
    )
    return ast.get_docstring(fn) or ""


def _returns(doc: str) -> str:
    """The ``Returns`` section of a numpy docstring, heading to next one."""
    lines = textwrap.dedent(doc).splitlines()
    start = lines.index("Returns")
    end = len(lines)
    for i in range(start + 2, len(lines) - 1):
        if (
            lines[i]
            and not lines[i][0].isspace()
            and set(lines[i + 1]) == {"-"}
        ):
            end = i
            break
    return "\n".join(lines[start:end]).strip()


def _docs(root: Path) -> dict[str, str]:
    """``{face: push's docstring}`` for all four faces."""
    out: dict[str, str] = {}
    for label, pyi, ext, cls in FACES:
        out[f"{label} .pyi"] = _stub_doc((root / pyi).read_text("utf-8"), cls)
        out[f"{label} runtime"] = _runtime_docs(
            (root / ext).read_text("utf-8")
        )["push"]
    return out


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    return _scaffold(tmp_path_factory.mktemp("gh2539"))


@pytest.mark.parametrize(
    "face",
    [f"{label} {kind}" for label, *_ in FACES for kind in (".pyi", "runtime")],
)
def test_every_face_lists_the_columns_in_returns(project, face):
    """Each column, its type and its doc, inside the ``Returns`` entry."""
    ret = _returns(_docs(project)[face])
    lines = ret.splitlines()
    assert lines[2] == "NDArray[Any]", ret
    assert "One row per `hit_t` record, with fields:" in ret, ret
    assert "Output." not in ret, ret
    for entry, doc in COLUMNS:
        assert f"    {entry}" in lines, (face, ret)
        if doc:
            at = lines.index(f"    {entry}")
            assert lines[at + 1] == f"        {doc}", (face, ret)


def test_no_face_keeps_a_section_numpydoc_does_not_know(project):
    """The runtime-only ``Fields`` heading is gone: the columns are in
    ``Returns`` on that face too, not stated twice."""
    for face, doc in _docs(project).items():
        assert "\nFields\n" not in f"\n{doc}\n", (face, doc)


def test_the_four_returns_blocks_are_one_text(project):
    """A stub and ``help()`` say the same, and a module object says what a
    standalone one does."""
    blocks = {face: _returns(doc) for face, doc in _docs(project).items()}
    assert len(set(blocks.values())) == 1, blocks


def test_a_header_return_leads_the_columns(tmp_path):
    """The author's ``@return`` keeps its place; the columns follow it."""
    root = _scaffold(tmp_path)
    for header in (
        root / "native/inc/p/corr/corr_core.h",
        root / "native/inc/p/mcorr/mcorr_core.h",
    ):
        text = header.read_text("utf-8")
        anchor = " * @param out\n * @return\n"
        assert text.count(anchor) == 1, text
        header.write_text(
            text.replace(anchor, " * @param out\n * @return Detected hits.\n"),
            "utf-8",
        )
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    for face, doc in _docs(root).items():
        lines = _returns(doc).splitlines()
        assert lines[3:6] == [
            "    Detected hits.",
            "",
            "    One row per `hit_t` record, with fields:",
        ], (face, lines)


def test_a_manifest_doc_still_owns_the_summary(tmp_path):
    """The method's ``doc`` is the summary, as before; the columns are
    added to ``Returns`` beside it, not in its place."""
    root = _scaffold(tmp_path, method_doc="Detect the hits in a block.")
    for face, doc in _docs(root).items():
        if face.endswith(".pyi"):
            assert doc.splitlines()[0] == "Detect the hits in a block.", doc
        assert "One row per `hit_t` record" in _returns(doc), (face, doc)
