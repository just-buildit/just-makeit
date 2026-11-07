"""gh-2529: a ``no_state`` object's ``reset`` is a member like any other's.

GATES:

1. The predicate agrees with the product. For every combination of
   ``--no-state`` and ``--no-reset``, ``reset`` is a member jm reasons about
   (`_builtins.absorbable_members`, `_config.view_member_doc_keys`, a view
   entry's `_config.is_view_override`) exactly when the generated binding
   emits a ``reset`` ``PyMethodDef`` row -- the product is the oracle, not a
   restated condition. Before, `_builtins.builtin_method_names` dropped
   ``reset`` under ``no_state or no_reset`` while the binding drops it under
   ``no_reset`` alone (gh-542), so a stateless object's bound ``reset()``
   was no member.
2. The consequence, end to end: a view of a ``no_state`` object whose
   ``reset`` entry carries only a ``doc`` keeps ``reset() -> None`` with the
   view's text verbatim on both faces, `apply` leaves the header's
   ``<stem>_reset`` declaration alone, and the project compiles. Before,
   the entry was read as a NEW method: ``reset() -> complex`` with the
   parent's Example appended, the header rewritten, and the fragment failing
   with ``void value not ignored``.
"""

from __future__ import annotations

import ast
import inspect
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _compilers import default_cc
from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit._builtins import absorbable_members


from test_gh2498_view_member_doc_override import _runtime_docs

VIEW_DOC = "VIEWDOC reset for the stateless view.\n\nExamples\n--------\n"
VIEW_DOC += ">>> 1 + 1\n2\n"


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


def _scaffold(base: Path, flags: "tuple[str, ...]") -> Path:
    _ok("new", "pp", cwd=base)
    root = base / "pp"
    _ok("module", "m", cwd=root)
    _ok(
        "object", "rc", "--module", "m", *flags,
        "--arg-type", "float _Complex", "--return-type", "float _Complex",
        cwd=root,
    )  # fmt: skip
    _ok(
        "view", "rc", "Corr", "--module", "m",
        "--create-fn", "pp_rc_create_corr",
        cwd=root,
    )  # fmt: skip
    return root


def _fragment(root: Path, cls: str) -> str:
    return (
        root / "native" / "src" / "m" / f"m_ext_{cls.lower()}.c"
    ).read_text(encoding="utf-8")


FLAGS = {
    "plain": (),
    "no_state": ("--no-state",),
    "no_reset": ("--no-reset",),
    "no_state+no_reset": ("--no-state", "--no-reset"),
}


@pytest.mark.parametrize("case", list(FLAGS))
def test_reset_is_a_member_iff_the_binding_binds_it(tmp_path, case):
    root = _scaffold(tmp_path, FLAGS[case])
    bound = {
        cls: '{"reset",' in _fragment(root, cls) for cls in ("Rc", "Corr")
    }
    # Both faces agree with each other first, or "the binding" is no answer.
    assert bound["Rc"] == bound["Corr"], bound
    # And with the flag that removes it: no_reset, never no_state.
    assert bound["Rc"] == ("--no-reset" not in FLAGS[case]), bound
    cfg = C.load(root)
    entry = {"name": "reset", "doc": "D."}
    asked = {
        "absorbable_members": "reset" in absorbable_members(cfg, "rc"),
        "view_member_doc_keys": "reset" in C.view_member_doc_keys(cfg, "rc"),
        "is_view_override": C.is_view_override(cfg, "rc", entry),
    }
    wrong = {k: v for k, v in asked.items() if v != bound["Rc"]}
    assert not wrong, (case, bound["Rc"], wrong)


def _skip_reason() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


@pytest.fixture(scope="module")
def overridden(tmp_path_factory) -> "tuple[Path, str, str]":
    """A ``no_state`` object whose view overrides ``reset``'s doc.

    Returns the root, the header before the override, and what the override
    command printed.
    """
    root = _scaffold(tmp_path_factory.mktemp("gh2529"), ("--no-state",))
    header = root / "native" / "inc" / "pp" / "rc" / "rc_core.h"
    before = header.read_text(encoding="utf-8")
    printed = _ok(
        "method", "rc", "reset", "--module", "m", "--view", "Corr",
        "--doc", VIEW_DOC,
        cwd=root,
    )  # fmt: skip
    printed += _ok("apply", cwd=root)
    return root, before, printed


def _reset(root: Path, cls: str) -> ast.FunctionDef:
    pyi = (root / "src" / "pp" / "m" / "m.pyi").read_text(encoding="utf-8")
    (klass,) = (
        n
        for n in ast.parse(pyi).body
        if isinstance(n, ast.ClassDef) and n.name == cls
    )
    (fn,) = (
        f
        for f in klass.body
        if isinstance(f, ast.FunctionDef) and f.name == "reset"
    )
    return fn


class TestViewOverrideOfAStatelessReset:
    def test_manifest_entry_is_the_override_alone(self, overridden):
        root, _, _ = overridden
        view = C._find_view(C.load(root), "rc", "Corr")
        assert [set(m) for m in C.view_methods(view)] == [{"name", "doc"}]

    @pytest.mark.parametrize("cls", ["Rc", "Corr"])
    def test_stub_reset_returns_none(self, overridden, cls):
        root, _, _ = overridden
        fn = _reset(root, cls)
        assert ast.unparse(fn.args) == "self", ast.unparse(fn.args)
        assert ast.unparse(fn.returns) == "None"

    def test_stub_doc_is_the_views_text(self, overridden):
        root, _, _ = overridden
        doc = ast.get_docstring(_reset(root, "Corr"))
        assert doc == inspect.cleandoc(VIEW_DOC)

    def test_runtime_doc_is_the_views_text(self, overridden):
        root, _, _ = overridden
        doc = _runtime_docs(_fragment(root, "Corr")).get("reset")
        assert doc == inspect.cleandoc(VIEW_DOC)

    def test_header_untouched_and_not_warned_about(self, overridden):
        root, before, printed = overridden
        header = root / "native" / "inc" / "pp" / "rc" / "rc_core.h"
        assert header.read_text(encoding="utf-8") == before
        assert "replacing the declaration" not in printed

    def test_project_compiles(self, overridden):
        reason = _skip_reason()
        if reason:
            pytest.skip(reason)
        root, _, _ = overridden
        build = root / "build"
        for cmd in (
            [
                "cmake", "-S", str(root), "-B", str(build),
                f"-DPython3_EXECUTABLE={sys.executable}",
            ],
            ["cmake", "--build", str(build)],
        ):  # fmt: skip
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
            assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
