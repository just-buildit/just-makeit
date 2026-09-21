"""gh-1443: a generated stub used ``Any`` without importing it.

doppler's ring shape -- a method over a record element -- annotates
``NDArray[Any]``. The module stub imported ``final`` and not ``Any``, so
``rings.pyi`` referenced an undefined name twice and mypy rejected it.

`_uses_any` ENUMERATED the surfaces that produce ``Any``: a varargs or
`manual_stub` method (gh-428) and an ``object``-valued container property
(gh-543). It had no arm for a record element.

The enumeration could never have been complete. ``Any`` is the FALLBACK of
both type maps -- ``_CTYPE_TO_PY.get(ctype, "Any")`` and
``_CTYPE_TO_NP.get(elem, "Any")`` -- so every C type jm does not know
renders it, and the set of surfaces is unbounded by construction. So it is
asked of the rendered text now, exactly as `_uses_os` already was for the
same class of bug (gh-353 / gh-623 / gh-1272).

GATE: a generated stub never references a name it does not import.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

from _jmrun import run_cli


def _undefined_names(src: str) -> set:
    """Names loaded at any scope in a stub and bound nowhere in it.

    A `.pyi` has no runtime, so `import`, `def`, `class` and assignment are
    the only binders -- which makes this exact for the question asked,
    rather than the approximation the same helper is in a runtime module.
    """
    tree = ast.parse(src)
    bound: set = {"__name__", "__file__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
    used = {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }
    return {n for n in used - bound if not hasattr(builtins, n)}


def _ring(tmp_path: Path) -> Path:
    """doppler's shape: a module object with a record written and read."""
    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "q", cwd=root).returncode == 0
    proj = root / "q"
    assert run_cli("module", "m", cwd=proj).returncode == 0
    assert (
        run_cli(
            "object",
            "r",
            "--module",
            "m",
            "--no-state",
            "--no-step",
            "--init-param",
            "capacity:size_t:16",
            cwd=proj,
        ).returncode
        == 0
    )
    assert (
        run_cli(
            "record",
            "r",
            "iq_t",
            "--field",
            "i:int16_t",
            "--field",
            "q:int16_t",
            cwd=proj,
        ).returncode
        == 0
    )
    assert (
        run_cli(
            "method",
            "r",
            "write",
            "--module",
            "m",
            "--arg-type",
            "iq_t[]",
            "--return-type",
            "bool",
            cwd=proj,
        ).returncode
        == 0
    )
    assert run_cli("apply", cwd=proj).returncode == 0
    return proj / "src" / "q" / "m" / "m.pyi"


class TestAGeneratedStubBindsEveryNameItUses:
    def test_the_record_shape_imports_any(self, tmp_path):
        """The instance: `NDArray[Any]` with no `from typing import Any`."""
        src = _ring(tmp_path).read_text()
        assert "NDArray[Any]" in src, "fixture no longer renders Any"
        assert _undefined_names(src) == set(), _undefined_names(src)

    def test_it_is_not_imported_when_nothing_uses_it(self, tmp_path):
        """The other direction: an unused import is ruff's `F401`.

        Narrowing a lookup drops its riders -- so the same rendering that
        must GAIN the import must not gain it everywhere.
        """
        root = tmp_path / "w"
        root.mkdir()
        assert run_cli("new", "p", cwd=root).returncode == 0
        proj = root / "p"
        assert (
            run_cli(
                "object",
                "o",
                "--arg-type",
                "double",
                "--return-type",
                "double",
                cwd=proj,
            ).returncode
            == 0
        )
        assert run_cli("apply", cwd=proj).returncode == 0
        src = (proj / "src" / "p" / "o.pyi").read_text()
        assert "Any" not in src, src
        assert _undefined_names(src) == set()

    def test_prose_alone_does_not_pull_the_import(self):
        """A name only in a docstring would be an unused import."""
        from just_makeit import _stubs

        assert _stubs._uses_any('def f() -> int:\n    """Any."""\n') is False
        assert _stubs._uses_any("x: NDArray[Any]") is True
