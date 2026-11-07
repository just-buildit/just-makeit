"""gh-2528: a view method's ``doc`` is its WHOLE docstring, added or not.

GATE: a method a view ADDS -- binding a C function of its own with ``fn``,
or the derived ``<stem>_<name>`` -- takes the view's ``doc`` as its whole
docstring on both faces (the ``.pyi`` and the binding's runtime
``__doc__``), exactly as an override of a parent member does (gh-2498).
Nothing of the C function's header block is appended: that block is
shared with every other class binding the function, so it is not the
view's to describe.

And the view's text stays the VIEW's: a second view binding the same
``fn`` with no ``doc`` keeps the header-derived docstring, and a third
with its own ``doc`` gets its own text.

Before: doppler's ``CorrDetector`` and ``CorrDetector2D`` both add ``push``
on one C function; a ``doc`` on one replaced only the summary line, and
the header's Parameters, Returns and Examples followed it.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli
from test_gh2498_view_member_doc_override import (
    _classes,
    _fragment,
    _members,
    _runtime_docs,
)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402

#: The shared C function's header block: none of it may reach a view's doc.
HEADER_BLOCK = (
    " * @brief PUSHBRIEF push a frame.\n *\n"
    " * PUSHPROSE shared by every class.\n *\n"
    " * @param state  s\n * @param x  PUSHPARAM input.\n"
    " * @param x_len  n\n"
    " * @return PUSHRET count.\n *\n"
    " * @code\n * c = PUSHEXAMPLE()\n * @endcode\n"
)
SCAFFOLD_BLOCK = (
    " * @brief push.\n *\n * @param state\n * @param x\n"
    " * @param x_len\n * @return\n"
)


def _view_doc(cls: str, member: str) -> str:
    return (
        f"VIEWDOC {cls}.{member} for its own frame.\n\n"
        "Returns\n-------\nfloat\n    The view's count.\n\n"
        "Examples\n--------\n>>> 1 + 1\n2\n"
    )


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


def _push(root: Path, cls: str, *extra: str) -> None:
    _ok(
        "method", "rc", "push", "--module", "m", "--view", cls,
        "--fn", "pp_rc_push",
        "--arg-type", "float[]", "--return-type", "float",
        *extra,
        cwd=root,
    )  # fmt: skip


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> "tuple[Path, str]":
    """Parent ``rc`` with no ``push``, and three views adding it on one fn.

    ``Hand`` writes its ``doc`` into the manifest by hand and then applies
    (doppler's path, the issue's trial); ``Cli`` passes ``--doc`` and also
    adds ``peek`` on the derived symbol; ``Bare`` declares no ``doc``.
    Returns the root and what `apply` printed.
    """
    base = tmp_path_factory.mktemp("gh2528")
    _ok("new", "pp", cwd=base)
    root = base / "pp"
    _ok("module", "m", cwd=root)
    _ok(
        "object", "rc", "--module", "m",
        "--arg-type", "float", "--return-type", "float",
        cwd=root,
    )  # fmt: skip
    for cls in ("Hand", "Cli", "Bare"):
        _ok(
            "view", "rc", cls, "--module", "m",
            "--create-fn", f"pp_rc_create_{cls.lower()}",
            cwd=root,
        )  # fmt: skip
    _push(root, "Hand")
    _push(root, "Cli", "--doc", _view_doc("Cli", "push"))
    _push(root, "Bare")
    _ok(
        "method", "rc", "peek", "--module", "m", "--view", "Cli",
        "--return-type", "float", "--doc", _view_doc("Cli", "peek"),
        cwd=root,
    )  # fmt: skip
    h = root / "native" / "inc" / "pp" / "rc" / "rc_core.h"
    text = h.read_text(encoding="utf-8")
    assert SCAFFOLD_BLOCK in text
    h.write_text(text.replace(SCAFFOLD_BLOCK, HEADER_BLOCK), encoding="utf-8")
    frag = root / "objects" / "rc.toml"
    ftext = frag.read_text(encoding="utf-8")
    anchor = 'fn = "pp_rc_push"\n'
    at = ftext.index(anchor)  # Hand's entry: the views are in order
    doc = f'doc = """{_view_doc("Hand", "push")}"""\n'
    frag.write_text(
        ftext[: at + len(anchor)] + doc + ftext[at + len(anchor) :],
        encoding="utf-8",
    )
    assert C._find_view(C.load(root), "rc", "Hand")["methods"][0]["doc"]
    return root, _ok("apply", cwd=root)


@pytest.mark.parametrize(
    "cls, member", [("Hand", "push"), ("Cli", "push"), ("Cli", "peek")]
)
def test_doc_is_the_whole_docstring_on_both_faces(project, cls, member):
    project, _ = project
    want = inspect.cleandoc(_view_doc(cls, member))
    stub = ast.get_docstring(_members(_classes(project)[cls])[member])
    runtime = _runtime_docs(_fragment(project, cls))[member]
    assert (stub, runtime) == (want, want)


def test_a_view_without_doc_keeps_the_header_docstring(project):
    project, _ = project
    stub = ast.get_docstring(_members(_classes(project)["Bare"])["push"])
    runtime = _runtime_docs(_fragment(project, "Bare"))["push"]
    for face in (stub, runtime):
        assert "PUSHPROSE" in face and "PUSHEXAMPLE" in face, face
        assert "VIEWDOC" not in face, face


def test_the_parent_keeps_its_own_members(project):
    project, _ = project
    stub = _classes(project)["Rc"]
    assert "push" not in _members(stub)
    assert "VIEWDOC" not in _fragment(project, "Rc")


def test_status_is_clean(project):
    project, _ = project
    r = run_cli("status", "--check", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_sectioned_doc_is_not_reported_as_duplicated(project):
    """It renders alone, so no generated section is printed beside it."""
    _, printed = project
    assert "numpy section heading" not in printed, printed


def test_a_doc_two_members_would_share_is_refused(tmp_path):
    """Two members of one view on one C function: neither doc is its own."""
    _ok("new", "pp", cwd=tmp_path)
    root = tmp_path / "pp"
    _ok("module", "m", cwd=root)
    _ok("object", "rc", "--module", "m", cwd=root)
    _ok(
        "view", "rc", "Two", "--module", "m",
        "--create-fn", "pp_rc_create_two",
        cwd=root,
    )  # fmt: skip
    _push(root, "Two")
    r = run_cli(
        "method", "rc", "push2", "--module", "m", "--view", "Two",
        "--fn", "pp_rc_push",
        "--arg-type", "float[]", "--return-type", "float",
        "--doc", "VIEWDOC push2.",
        cwd=root,
    )  # fmt: skip
    assert r.returncode != 0
    assert "cannot be its own docstring" in r.stderr, r.stderr
    assert "push also" in r.stderr and "'pp_rc_push'" in r.stderr
