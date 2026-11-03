"""A view documents ITSELF on both faces, never its parent or a synthetic id.

GATE: a view's stub and runtime faces diverge only where its parent's do.

gh-504 renders a view's ``.pyi`` class by overlaying the view on a copy of
its parent's manifest table (`_stubs.view_overlay`) under a synthetic id,
while its binding (`_object._make_view_ctx`) is built from the parent and
the view directly. Every key the binding reads from the VIEW that the
overlay left as the parent's made the two faces describe different
identities, and doppler found them one at a time:

- gh-2406: the stub derived its C stem from the synthetic id, so a property
  setter's Raises named ``<stem>__view_<v>_set_<prop>``, a function no
  header declares, while the runtime named the setter it calls.
- gh-2408: the stub's class Raises quoted the parent's
  ``create_error_message``, while the runtime raised the view's -- and the
  runtime ``tp_doc`` rendered no Raises or Warns at all.
- found fixing those: the stub listed the parent's warnings (a view has its
  own, gh-509) and advertised the parent's ``stream()`` (a view's binding
  has none); the runtime ``__enter__`` / ``__exit__`` prose named the
  parent's class.

So the invariant is gated rather than listed: **for every class the module
stub declares, compare each member's two faces; a view may diverge only
where its parent does.** A member that diverges on the parent too is not a
view's identity bug, and is out of this gate's reach by construction. The
cases come from the stub and the fragments, so a member added to either is
compared with no edit here.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _docsync as DS  # noqa: E402

PARENT_MSG = "RC: bad parameter"
VIEW_MSG = "MRC: bad pulse"


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """A view differing from its parent in every way a view can.

    Its own create_error, its own warning, a streamable parent, and a
    property whose setter may refuse -- each a key the binding reads from
    the view, or a C symbol the stub spells. The default `c_prefix` is
    kept, so the parent's stem and the synthetic id differ in spelling.
    """
    base = tmp_path_factory.mktemp("gh2406")
    _ok("new", "pp", cwd=base)
    root = base / "pp"
    _ok("module", "m", cwd=root)
    _ok(
        "object", "rc", "--module", "m", "--streamable", "--arg-type", "void",
        "--state", "gain:double:1.0", "--state", "weak:bool:false",
        cwd=root,
    )  # fmt: skip
    _ok(
        "view", "rc", "MRC", "--module", "m",
        "--create-fn", "pp_rc_create_matched",
        cwd=root,
    )  # fmt: skip
    _ok(
        "property", "rc", "rate", "--module", "m", "--type", "double",
        "--writable", "--error", "ValueError",
        cwd=root,
    )  # fmt: skip
    _ok(
        "error", "rc", "--module", "m", "--category", "ValueError",
        "--message", PARENT_MSG,
        cwd=root,
    )  # fmt: skip
    _ok(
        "error", "rc", "--module", "m", "--view", "MRC",
        "--category", "ValueError", "--message", VIEW_MSG,
        cwd=root,
    )  # fmt: skip
    _ok(
        "warning", "rc", "--module", "m", "--condition", "weak",
        "--message", "parent is weak",
        cwd=root,
    )  # fmt: skip
    _ok(
        "warning", "rc", "--module", "m", "--view", "MRC",
        "--condition", "weak", "--message", "view is weak",
        cwd=root,
    )  # fmt: skip
    _ok("apply", cwd=root)
    return root


def _literal(field: str) -> str:
    """A C doc field's string literals, joined and unescaped."""
    text = "".join(DS._STR_LIT_RE.findall(field))
    return text.encode("utf-8").decode("unicode_escape")


def _runtime(frag: Path, cls: str) -> "dict[str, str]":
    """``{member: doc}`` from one binding fragment; ``<class>`` is tp_doc.

    The class's own ``tp_doc`` is the one in ``<cls>Type``: a streamable
    fragment declares its block iterator's type first.
    """
    text = frag.read_text(encoding="utf-8")
    mask = DS._code_mask(text)
    at = mask.index(f"PyTypeObject {cls}Type")
    out = {"<class>": _literal(DS._tp_doc_span(text[at:], mask[at:])[2])}
    for array_re in (DS._METHODS_RE, DS._GETSET_RE):
        for name, (_, _, field) in DS._doc_slots(text, mask, array_re).items():
            doc = _literal(field)
            # A method's runtime doc opens with its synopsis line, which the
            # stub carries as the signature instead.
            head, _, rest = doc.partition("\n")
            if re.match(rf"^{re.escape(name)}\(.*\)", head):
                doc = rest
            out[name] = doc
    return out


def _stub(cls: ast.ClassDef) -> "dict[str, str]":
    out = {"<class>": ast.get_docstring(cls) or ""}
    for fn in cls.body:
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(fn.name, ast.get_docstring(fn) or "")
    return out


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _faces(root: Path) -> "dict[str, dict]":
    """Per class: its parent's class name, and each face's members.

    Derived: the classes are the module stub's, each mapped to its fragment
    by the frag id the binding writes, and to its parent by the manifest.
    """
    cfg = C.load(root)
    parent_of: dict[str, str] = {}
    for obj in C.module_objects(cfg, "m"):
        cls = C.resolved_class_name(cfg, obj)
        parent_of[cls] = cls
        for view in C.views(cfg, obj):
            parent_of[view["class_name"]] = cls
    pyi = ast.parse(
        (root / "src" / "pp" / "m" / "m.pyi").read_text(encoding="utf-8")
    )
    out = {}
    for cls in (n for n in pyi.body if isinstance(n, ast.ClassDef)):
        frag = root / "native" / "src" / "m" / f"m_ext_{cls.name.lower()}.c"
        out[cls.name] = {
            "parent": parent_of[cls.name],
            "stub": _stub(cls),
            "runtime": _runtime(frag, cls.name),
        }
    return out


def _divergent(face: dict) -> "set[tuple[str, str]]":
    """``("member", n)`` for a member one face lacks, ``("doc", n)`` for
    one documented differently -- kept apart, so a parent's doc divergence
    on a member cannot excuse a view's stub declaring one it lacks."""
    stub, rt = face["stub"], face["runtime"]
    # The slots no method table carries are on the stub alone.
    slots = {"__init__", "__iter__", "__aiter__", "__next__"}
    lacking = {n for n in stub if n not in rt and n not in slots}
    lacking |= {n for n in rt if n not in stub}
    return {("member", n) for n in lacking} | {
        ("doc", n)
        for n in set(stub) & set(rt)
        if _norm(stub[n]) != _norm(rt[n])
    }


class TestViewFacesAgree:
    def test_a_view_diverges_only_where_its_parent_does(self, project):
        faces = _faces(project)
        views = {c: f for c, f in faces.items() if f["parent"] != c}
        assert views, "the fixture declares a view; none was compared"
        wrong = {
            cls: sorted(_divergent(face) - _divergent(faces[face["parent"]]))
            for cls, face in views.items()
        }
        wrong = {c: w for c, w in wrong.items() if w}
        assert not wrong, (
            "these view members document something different on the .pyi "
            "and at runtime, where the parent's two faces agree -- one face "
            f"describes the parent or the synthetic id: {wrong}"
        )

    def test_the_comparison_reached_what_the_view_declares(self, project):
        """Agreement on members that say nothing would prove nothing."""
        view = _faces(project)["MRC"]
        for face in ("stub", "runtime"):
            cls_doc = view[face]["<class>"]
            assert VIEW_MSG in cls_doc, (face, cls_doc)
            assert PARENT_MSG not in cls_doc, (face, cls_doc)
            assert "view is weak" in cls_doc, (face, cls_doc)
            assert "parent is weak" not in cls_doc, (face, cls_doc)
            assert "``pp_rc_set_rate``" in view[face]["rate"], face
            assert "Enter a context manager" in view[face]["__enter__"]
        assert "stream" not in view["stub"], "the view's binding has none"
        assert "stream" in _faces(project)["Rc"]["stub"], "parent streams"

    def test_no_stub_spells_the_synthetic_id(self, project):
        pyi = (project / "src" / "pp" / "m" / "m.pyi").read_text("utf-8")
        assert "__view_" not in pyi
