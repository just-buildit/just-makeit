"""gh-2070: a module's glue has ONE render, whatever the module holds.

Removing a module's last object left ``native/src/<mod>/CMakeLists.txt`` and
``<mod>_ext.c`` STALE. There were two renders of a module: the one every
member verb and ``jm remove`` end in (``_object._regenerate_module``), and a
second in ``_module.run`` for a module with nothing in it -- the one ``jm
module`` wrote and ``apply`` replays an empty module through. They differed
in the core's include scope, the aggregator's banner, the gh-1351
``<mod>_extra.cmake`` hook (which the empty render dropped, so ``apply``
warned it was removing jm's own include) and the ``--extra-*`` keys (which
the empty render never read). gh-1199 was the same two sites with another
symptom.

The fix is not to make the two agree but to have one: ``_module.run`` saves
the manifest and calls the module render. The gates:

- ``test_the_verb_leaves_what_apply_writes`` (gh-2057): its ratchet held
  ``remove object`` on every module shape; those entries went with the fix.
- ``test_gh1351_extra_cmake``'s sweep of the scaffolded tree, whose fixture
  now holds an empty module.
- here, what those two do not reach: one render site per module template,
  so a second render cannot grow back, and the paths that empty a module
  without an object -- its last function going -- or never fill one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _render as R
from test_gh2057_verb_leaves_what_apply_writes import findings

SRC = Path(R.__file__).parent

_FN = ("--param", "x:double", "--return-type", "double")


def _module_templates() -> "set[str]":
    """Every template `_render` holds for an object module's files.

    Read from the module rather than listed, so a new one is held here
    without being named.
    """
    return {
        name
        for name, value in vars(R).items()
        if name.isupper() and "MODULE" in name and isinstance(value, str)
    }


def _render_sites() -> "dict[str, set[str]]":
    """template -> the functions anywhere in jm that reference it.

    By the AST, so a formatter's layout is not a finding; the function is
    the innermost one enclosing the reference. The assignment that defines
    a template is at module level and so is no site.
    """
    names = _module_templates()
    sites: "dict[str, set[str]]" = {n: set() for n in names}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("templates/"):
            continue  # the templates themselves, which are not jm's code
        text = path.read_text(encoding="utf-8")
        if not any(n in text for n in names):
            continue  # only a cost: the AST below is what decides
        tree = ast.parse(text)

        def visit(node: ast.AST, fn: str) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = node.name
            ref = (
                node.id
                if isinstance(node, ast.Name)
                else node.attr
                if isinstance(node, ast.Attribute)
                else None
            )
            if ref in sites and fn:
                sites[ref].add(f"{rel}::{fn}")
            for child in ast.iter_child_nodes(node):
                visit(child, fn)

        visit(tree, "")
    return sites


def test_the_sweep_sees_the_module_templates():
    """The two this issue was about, so an empty sweep cannot pass."""
    assert {"CMAKE_LISTS_MODULE", "MODULE_EXT_C_HEADER"} <= (
        _module_templates()
    )


def test_every_module_template_has_one_render_site():
    """Two functions rendering one module file is gh-1199 and gh-2070: the
    second is the one some path takes, and it drifts. A second caller
    goes through the first function instead."""
    many = {t: sorted(s) for t, s in _render_sites().items() if len(s) > 1}
    assert not many, many


def _ok(root: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-2000:])


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _ok(tmp_path, "new", "p")
    return tmp_path / "p"


def test_removing_a_modules_last_function(project: Path):
    """The function-side peer of the issue's repro, which the gh-2057
    matrix has no shape for: a module holding only a function."""
    _ok(project, "module", "mod")
    _ok(project, "function", "f", "--module", "mod", *_FN)
    _ok(project, "remove", "function", "f", "--module", "mod", "--force")
    assert findings(project) == frozenset()


def test_an_empty_modules_init_is_python(project: Path):
    """The one render now also writes a new module's re-export
    ``__init__.py``, which ``_module.run`` used to seed itself: with no
    exports it must carry no ``from .<leaf> import`` line, which would be
    a SyntaxError with nothing after ``import``."""
    _ok(project, "module", "mod")
    text = (project / "src/p/mod/__init__.py").read_text(encoding="utf-8")
    ast.parse(text)
    assert "from .mod import" not in text
    assert "__all__ = []" in text


def test_a_new_modules_extras_reach_its_cmakelists(project: Path):
    """``jm module --extra-include-dirs`` saved the key and rendered a
    CMakeLists without it; the directory arrived with the first member."""
    _ok(
        project,
        "module",
        "mod",
        "--extra-include-dirs",
        "${FOO_INCLUDE_DIR}",
        "--extra-link-libs",
        "foo",
    )
    text = (project / "native/src/mod/CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    assert "${FOO_INCLUDE_DIR}" in text
    assert "    foo\n" in text
    assert findings(project) == frozenset()
