"""gh-1400: a view's field-backed property documents itself like its parent.

A view is rendered as a synthetic component, and `_stubs._view_doc_blocks`
re-keys the parent's header blocks under that name so inherited members
resolve (gh-685). It rewrites top-level keys carrying the `<obj>_` prefix,
and special-cases the `_max_out` arity set, which rides a reserved key
(gh-761). gh-1300's per-struct field map rides a reserved key too -- and its
keys are STRUCT names, which no prefix filter sees -- so it was dropped and
every field-backed property on a view fell to its name stub.

The binding never had the bug: `_make_view_ctx` renders a view under the
PARENT's name, where `<obj>_state_t` resolves. So the two faces of one
member disagreed, which is the shape asserted here: not "the view says
something" but "the view says what the parent says, and what the binding
emits".

Measured on doppler before the fix: `CellAsyncDsssReceiver.cn0_dbhz_est`
read `Cached from the winning acquisition hit.` at runtime and
`Cn0 dbhz est.` in the stub.
"""

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402

import ast
import re
from pathlib import Path

from _jmrun import run_cli

FIELD_DOC = "Estimated carrier-to-noise density, dB-Hz."


def _project(tmp_path: Path) -> Path:
    """A module object with a documented state field, and a view over it.

    A view is a module-object feature, so the parent lives in a module and
    both classes land in the module's one stub -- which is also where the
    bug showed, since a module stub renders every view.
    """
    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "p", cwd=root).returncode == 0
    proj = root / "p"
    assert run_cli("module", "dsp", cwd=proj).returncode == 0
    r = run_cli(
        "object",
        "rx",
        "--module",
        "dsp",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        "--state",
        "cn0_dbhz:double:0.0",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr

    header = proj / INC_ROOT / "rx" / "rx_core.h"
    text = header.read_text(encoding="utf-8")
    assert "    double cn0_dbhz;" in text
    header.write_text(
        text.replace(
            "    double cn0_dbhz;",
            f"    double cn0_dbhz;  /**< {FIELD_DOC} */",
            1,
        ),
        encoding="utf-8",
    )
    r = run_cli(
        "property", "rx", "cn0_dbhz", "--type", "double", "--field", cwd=proj
    )
    assert r.returncode == 0, r.stderr
    # A view binds its own constructor, so the header must declare one.
    text = header.read_text(encoding="utf-8")
    header.write_text(
        text.replace(
            "rx_state_t *rx_create(",
            "rx_state_t *rx_create_cell(double cn0_dbhz);\n"
            "rx_state_t *rx_create(",
            1,
        ),
        encoding="utf-8",
    )
    r = run_cli(
        "view",
        "rx",
        "CellRx",
        "--module",
        "dsp",
        "--create-fn",
        "rx_create_cell",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr
    return proj


def _stub_docs(proj: Path) -> dict:
    """``{class: {member: docstring}}`` parsed from the generated stub."""
    pyi = proj / "src" / "p" / "dsp" / "dsp.pyi"
    out: dict = {}
    for node in ast.parse(pyi.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    out.setdefault(node.name, {}).setdefault(
                        item.name, ast.get_docstring(item) or ""
                    )
    return out


def _binding_doc(proj: Path, cls: str, prop: str) -> str:
    """The text of *prop*'s slot in *cls*'s generated PyGetSetDef table."""
    src = "".join(
        p.read_text(encoding="utf-8")
        for p in proj.joinpath("native", "src", "dsp").glob("*.c")
    )
    m = re.search(
        rf'\{{\s*"{prop}",\s*\(getter\){cls}_getprop_{prop}\s*,'
        rf'\s*(?:\(setter\)\w+|NULL),\s*"((?:[^"\\]|\\.)*)"',
        src,
        re.S,
    )
    assert m, f"no getset slot for {cls}.{prop}"
    return m.group(1).replace("\\n", "\n").strip()


def test_the_view_derives_the_field_comment_like_its_parent(tmp_path):
    proj = _project(tmp_path)
    docs = _stub_docs(proj)
    assert docs["Rx"]["cn0_dbhz"] == FIELD_DOC, "the parent is the control"
    assert docs["CellRx"]["cn0_dbhz"] == FIELD_DOC


def test_the_two_faces_of_the_view_agree(tmp_path):
    """The stub and the binding document the view's property the same.

    This is the assertion that would have caught gh-1400: the binding was
    right all along, so a test of the stub alone against a hard-coded string
    could be satisfied while the faces still disagreed.
    """
    proj = _project(tmp_path)
    stub = _stub_docs(proj)["CellRx"]["cn0_dbhz"]
    assert stub == _binding_doc(proj, "CellRx", "cn0_dbhz")
    assert stub != "Cn0 dbhz.", "the name stub is the bug"


def test_the_report_does_not_call_it_a_gap(tmp_path):
    """`jm status --docs` must not list a member its own renderer documents.

    Paired with the test below, which adds an UNDOCUMENTED property to the
    same view: without it this would pass for a report that cannot see views
    at all, which is what it did before gh-1400 taught it to walk them.
    """
    proj = _project(tmp_path)
    out = run_cli("status", "--docs", cwd=proj)
    assert out.returncode == 0, out.stderr
    assert "cn0_dbhz" not in out.stdout


def test_an_undocumented_view_member_is_reported(tmp_path):
    """The view is genuinely walked, not merely absent from the report."""
    proj = _project(tmp_path)
    # A second field, deliberately left without a `/**<` comment.
    # `--field` adds the struct member itself, so nothing documents it.
    r = run_cli(
        "property", "rx", "snr_db", "--type", "double", "--field", cwd=proj
    )
    assert r.returncode == 0, r.stderr
    out = run_cli("status", "--docs", cwd=proj)
    assert out.returncode == 0, out.stderr
    assert "CellRx.snr_db (view property)" in out.stdout
    # ...and the documented one still is not listed, for either class.
    assert "cn0_dbhz" not in out.stdout


def test_an_undocumented_view_method_is_reported(tmp_path):
    """A view inherits its parent's methods, so it inherits the gap too."""
    proj = _project(tmp_path)
    r = run_cli(
        "method",
        "rx",
        "tick",
        "--arg-type",
        "void",
        "--return-type",
        "void",
        cwd=proj,
    )
    assert r.returncode == 0, r.stderr
    out = run_cli("status", "--docs", cwd=proj)
    assert out.returncode == 0, out.stderr
    assert "CellRx.tick (view method)" in out.stdout
    assert "rx.tick (method)" in out.stdout, "the parent's own gap too"
