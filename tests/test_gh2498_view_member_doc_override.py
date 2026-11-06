"""gh-2498: a view's override of a member its parent has is an OVERRIDE.

GATE: for a view, an entry naming a member the parent already has and
carrying only ``doc`` and/or ``error_message`` --

(a) makes the view's ``doc`` the member's WHOLE docstring on both faces:
    nothing of the parent header (prose, Parameters, Returns, Raises,
    Examples) is appended, so there is one ``Examples`` section, the view's;
(b) keeps the member the parent's: a ``reset`` or ``<m>_max_out`` entry is
    not a new method, so its signature stays the built-in's and `apply`
    neither warns about nor rewrites the header's declaration;
(c) may carry its own ``error_message`` for a shared method, which is
    binding text, not the C call.

Before: doppler#2886's ``Corr`` (a view of ``Corr2D``'s core) rendered its
own ``execute`` doc followed by the parent's sections and a second
``Examples`` block; a ``reset`` doc became ``reset() -> complex`` and
`apply` replaced ``<stem>_reset()``'s declaration; and an
``error_message`` was refused.

The members are not listed here: (a) overrides every member
`_config.view_member_doc_keys` says a view may document, so a member that
gains a doc path is covered with no edit, and an empty derivation fails.
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _stubs as S  # noqa: E402

#: The header block a view's override must NOT inherit any part of.
PARENT_BLOCK = (
    " * @brief PARENTBRIEF 2-D execute.\n *\n"
    " * PARENTPROSE frame layout.\n *\n"
    " * @param state  s\n * @param in  PARENTPARAM input.\n"
    " * @param n_in  n\n * @param out  o\n"
    " * @return PARENTRET count.\n *\n"
    " * @code\n * c = PARENTEXAMPLE()\n * @endcode\n"
)
SCAFFOLD_BLOCK = (
    " * @brief execute.\n *\n * @param state\n * @param in\n"
    " * @param n_in\n * @param out\n * @return\n"
)
PARENT_MSG = "parent failed"
VIEW_MSG = "view failed"


def _view_doc(member: str) -> str:
    return (
        f"VIEWDOC {member} for the 1-D frame.\n\n"
        "Examples\n--------\n>>> 1 + 1\n2\n"
    )


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> "tuple[Path, dict]":
    """Parent ``rc`` with an authored ``execute``, and three views of it.

    ``Corr`` overrides every member's doc through the CLI, ``CorrT`` writes
    the overrides by hand in the manifest (doppler's path), and ``CorrE``
    carries only an ``error_message`` for ``execute``. Returns the root and
    what each step printed, for the `apply` assertions.
    """
    base = tmp_path_factory.mktemp("gh2498")
    _ok("new", "pp", cwd=base)
    root = base / "pp"
    _ok("module", "m", cwd=root)
    _ok(
        "object", "rc", "--module", "m",
        "--arg-type", "float _Complex", "--return-type", "float _Complex",
        "--state", "gain:double:1.0",
        cwd=root,
    )  # fmt: skip
    _ok(
        "method", "rc", "execute", "--module", "m",
        "--arg-type", "float _Complex[]", "--return-type", "float _Complex",
        "--variable-output", "--max-out", "64",
        "--error-on-empty", "--error-message", PARENT_MSG,
        cwd=root,
    )  # fmt: skip
    for cls in ("Corr", "CorrT", "CorrE", "CorrF"):
        _ok(
            "view", "rc", cls, "--module", "m",
            "--create-fn", f"pp_rc_create_{cls.lower()}",
            cwd=root,
        )  # fmt: skip
    h = root / "native" / "inc" / "pp" / "rc" / "rc_core.h"
    text = h.read_text(encoding="utf-8")
    assert SCAFFOLD_BLOCK in text
    h.write_text(text.replace(SCAFFOLD_BLOCK, PARENT_BLOCK), encoding="utf-8")
    _ok("apply", cwd=root)
    header = h.read_text(encoding="utf-8")

    members = sorted(C.view_member_doc_keys(C.load(root), "rc"))
    out = {"members": members, "header": header}
    # Every override is RECORDED rather than asserted here: a refusal is a
    # finding of `test_every_override_is_accepted`, not a fixture error.
    for member in members:
        out[f"cli {member}"] = run_cli(
            "method", "rc", member, "--module", "m", "--view", "Corr",
            "--doc", _view_doc(member),
            cwd=root,
        )  # fmt: skip
    out["cli message"] = run_cli(
        "method", "rc", "execute", "--module", "m", "--view", "CorrE",
        "--error-message", VIEW_MSG,
        cwd=root,
    )  # fmt: skip
    # The same override with the parent's signature restated in full: the
    # signature is compared, and the message is not part of it.
    out["cli restated"] = run_cli(
        "method", "rc", "execute", "--module", "m", "--view", "CorrF",
        "--arg-type", "float _Complex[]", "--return-type", "float _Complex",
        "--variable-output", "--max-out", "64",
        "--error-on-empty", "--error-message", VIEW_MSG,
        cwd=root,
    )  # fmt: skip
    # `jm view` already wrote both fragments, and apply never re-renders
    # a wrapper body in a sacred one -- a changed message is reported by
    # `status`, the parent's included. Its own remedy: delete, re-apply.
    for frag_id in ("corre", "corrf"):
        (root / "native" / "src" / "m" / f"m_ext_{frag_id}.c").unlink()
    frag = root / "objects" / "rc.toml"
    rows = "".join(
        f'[[rc.views.methods]]\nname = "{m}"\ndoc = """{_view_doc(m)}"""\n\n'
        for m in ("execute", "execute_max_out", "reset")
    )
    ftext = frag.read_text(encoding="utf-8")
    # Under CorrT's own table: the views are written in declaration order,
    # so the rows go just before CorrE's.
    at = ftext.index('[[rc.views]]\nclass_name = "CorrE"')
    frag.write_text(ftext[:at] + rows + ftext[at:], encoding="utf-8")
    out["apply"] = run_cli("apply", cwd=root)
    return root, out


def _classes(root: Path) -> "dict[str, ast.ClassDef]":
    pyi = (root / "src" / "pp" / "m" / "m.pyi").read_text(encoding="utf-8")
    return {
        n.name: n for n in ast.parse(pyi).body if isinstance(n, ast.ClassDef)
    }


def _members(cls: ast.ClassDef) -> "dict[str, ast.FunctionDef]":
    return {f.name: f for f in cls.body if isinstance(f, ast.FunctionDef)}


def _fragment(root: Path, cls: str) -> str:
    return (
        root / "native" / "src" / "m" / f"m_ext_{cls.lower()}.c"
    ).read_text(encoding="utf-8")


def _runtime_docs(c_text: str) -> "dict[str, str]":
    """``{member: runtime __doc__}``, its synopsis line dropped."""
    from just_makeit import _docsync as DS

    mask = DS._code_mask(c_text)
    out = {}
    for name, (_, _, field) in DS._doc_slots(
        c_text, mask, DS._METHODS_RE
    ).items():
        doc = "".join(DS._STR_LIT_RE.findall(field))
        doc = doc.encode("utf-8").decode("unicode_escape")
        head, _, rest = doc.partition("\n")
        if re.match(rf"^{re.escape(name)}\(.*\)", head):
            doc = rest
        out[name] = doc.strip("\n")
    return out


def _signature(fn: ast.FunctionDef) -> str:
    ret = ast.unparse(fn.returns) if fn.returns else ""
    return f"({ast.unparse(fn.args)}) -> {ret}"


def test_every_override_is_accepted(project):
    _, out = project
    refused = {
        k: r.stdout + r.stderr
        for k, r in out.items()
        if k.startswith(("cli", "apply")) and r.returncode
    }
    assert not refused, refused


class TestDocReplacesTheWholeDocstring:
    """(a): the view's text, exactly, on both faces."""

    def test_members_are_derived_not_empty(self, project):
        _, out = project
        assert {"execute", "execute_max_out", "reset"} <= set(out["members"])

    @pytest.mark.parametrize("cls", ["Corr", "CorrT"])
    def test_stub_is_the_views_text(self, project, cls):
        root, out = project
        members = _members(_classes(root)[cls])
        names = out["members"] if cls == "Corr" else ["execute", "reset"]
        wrong = {
            m: ast.get_docstring(members[m])
            for m in names
            if ast.get_docstring(members[m]) != inspect.cleandoc(_view_doc(m))
        }
        assert not wrong, wrong

    @pytest.mark.parametrize("cls", ["Corr", "CorrT"])
    def test_runtime_is_the_views_text(self, project, cls):
        root, out = project
        docs = _runtime_docs(_fragment(root, cls))
        names = out["members"] if cls == "Corr" else ["execute", "reset"]
        wrong = {
            m: docs.get(m)
            for m in names
            if docs.get(m) != inspect.cleandoc(_view_doc(m))
        }
        assert not wrong, wrong

    def test_one_examples_section_and_no_parent_text(self, project):
        root, _ = project
        stub = ast.get_docstring(_members(_classes(root)["Corr"])["execute"])
        runtime = _runtime_docs(_fragment(root, "Corr"))["execute"]
        for face in (stub, runtime):
            assert face.count("Examples\n--------") == 1, face
            assert "PARENT" not in face, face

    def test_parent_keeps_its_header_doc(self, project):
        root, _ = project
        stub = ast.get_docstring(_members(_classes(root)["Rc"])["execute"])
        assert "PARENTPROSE" in stub and "PARENTEXAMPLE" in stub
        assert "PARENTPROSE" in _runtime_docs(_fragment(root, "Rc"))["execute"]


class TestOverrideKeepsTheParentsMember:
    """(b): an override is never a new method."""

    def test_signatures_are_the_parents(self, project):
        root, out = project
        classes = _classes(root)
        parent = _members(classes["Rc"])
        for cls in ("Corr", "CorrT"):
            view = _members(classes[cls])
            wrong = {
                m: (_signature(view[m]), _signature(parent[m]))
                for m in out["members"]
                if _signature(view[m]) != _signature(parent[m])
            }
            assert not wrong, (cls, wrong)

    def test_reset_returns_none(self, project):
        root, _ = project
        for cls in ("Corr", "CorrT"):
            reset = _members(_classes(root)[cls])["reset"]
            assert ast.unparse(reset.returns) == "None", cls

    def test_method_table_flags_are_the_parents(self, project):
        root, _ = project
        parent = S.method_table_flags(_fragment(root, "Rc"))
        for cls in ("Corr", "CorrT"):
            assert S.method_table_flags(_fragment(root, cls)) == parent, cls

    def test_header_untouched_and_not_warned_about(self, project):
        root, out = project
        h = root / "native" / "inc" / "pp" / "rc" / "rc_core.h"
        assert h.read_text(encoding="utf-8") == out["header"]
        printed = "".join(
            r.stdout + r.stderr
            for k, r in out.items()
            if k.startswith(("cli", "apply"))
        )
        assert "replacing the declaration" not in printed

    def test_manifest_entry_is_the_override_alone(self, project):
        root, _ = project
        view = C._find_view(C.load(root), "rc", "Corr")
        assert {m["name"]: set(m) for m in C.view_methods(view)} == {
            m["name"]: {"name", "doc"} for m in C.view_methods(view)
        }

    def test_status_is_clean(self, project):
        root, _ = project
        r = run_cli("status", "--check", cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr


class TestErrorMessagePerView:
    """(c): a message-only override is accepted and rendered."""

    @pytest.mark.parametrize("cls", ["CorrE", "CorrF"])
    def test_view_raises_its_own_message(self, project, cls):
        root, _ = project
        view, parent = _fragment(root, cls), _fragment(root, "Rc")
        assert f'"{VIEW_MSG}"' in view and f'"{PARENT_MSG}"' not in view
        assert f'"{PARENT_MSG}"' in parent and f'"{VIEW_MSG}"' not in parent

    @pytest.mark.parametrize("cls", ["CorrE", "CorrF"])
    def test_both_faces_document_it(self, project, cls):
        root, _ = project
        stub = ast.get_docstring(_members(_classes(root)[cls])["execute"])
        assert f"``{VIEW_MSG}``" in stub
        assert (
            f"``{VIEW_MSG}``"
            in (_runtime_docs(_fragment(root, cls))["execute"])
        )

    def test_a_member_with_no_refusal_takes_no_message(self, project):
        root, _ = project
        r = run_cli(
            "method", "rc", "reset", "--module", "m", "--view", "CorrE",
            "--error-message", VIEW_MSG,
            cwd=root,
        )  # fmt: skip
        assert r.returncode != 0
        assert "declares none" in r.stderr
