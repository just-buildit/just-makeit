"""No test may import a module that does not exist on jm's oldest Python.

`tomllib` is stdlib only from 3.11; jm supports down to 3.9 and `_config`
guards the import with a `tomli` fallback. A bare `import tomllib` in a test
is not a failure — it is a **collection error**, so the whole module vanishes
from the run on every older leg while the newest leg stays green.

That has now happened twice. The first time left a comment in
`test_gh491_manifest_comments.py` explaining the guard; the second time
(gh-1114) was written on a 3.12 laptop, passed 6314 tests locally, and broke
`ubuntu-24.04-arm / 3.10` in CI. A note is not a control, so this is the
control.

Deliberately a source scan rather than a runtime check: on 3.11+ the bare
import succeeds, so nothing at runtime can tell the two spellings apart. The
mistake is in the text.

GATE: no test imports a module missing from jm's oldest supported Python.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

TESTS = Path(__file__).parent
SRC = TESTS.parent / "src"

#: Modules that exist only above jm's floor. Keep this keyed to
#: `requires-python`, not to the interpreter running the suite.
_ABOVE_FLOOR = {"tomllib": (3, 11)}


def _bare_imports(tree: ast.AST) -> set:
    """Names imported anywhere, other than inside a `try`.

    A guarded import lives in a `Try`, so excluding those is what separates a
    guarded import from a bare one. Everything else is walked — module level,
    inside a function, inside a class body.

    **It used to walk `tree.body` only**, because the instance it was written
    for was module level and produced a *collection* error. A function-level
    one is a runtime failure instead, and within the same session one was
    written into `test_gh1126_composer_settings.py` and the same CI leg went
    red again — with this gate already in place. A gate written from one
    instance encodes that instance's SHAPE rather than the rule; the rule here
    is "this import fails below 3.11", which says nothing about placement.
    """
    guarded = {
        id(n)
        for t in ast.walk(tree)
        if isinstance(t, ast.Try)
        for stmt in t.body
        for n in ast.walk(stmt)
    }
    out = set()
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


def _floor() -> tuple:
    text = (SRC.parent / "pyproject.toml").read_text(encoding="utf-8")
    line = next(
        ln for ln in text.splitlines() if ln.startswith("requires-python")
    )
    # `>=3.9` -> (3, 9)
    ver = line.split(">=")[1].strip().strip('"').strip("'")
    return tuple(int(p) for p in ver.split("."))


def test_no_test_imports_above_the_supported_floor():
    floor = _floor()
    offenders = []
    for f in sorted(TESTS.rglob("test_*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for name in _bare_imports(tree):
            need = _ABOVE_FLOOR.get(name)
            if need and need > floor:
                offenders.append(f"{f.name}: bare `import {name}`")
    assert not offenders, (
        "these are collection errors on every leg below "
        f"{'.'.join(map(str, max(_ABOVE_FLOOR.values())))}, so the module "
        "silently drops out of the run instead of failing:\n  "
        + "\n  ".join(offenders)
        + "\nGuard it the way `_config` does:\n"
        "    try:\n        import tomllib\n"
        "    except ModuleNotFoundError:\n        import tomli as tomllib"
    )


def test_the_scan_can_actually_see_a_bare_import():
    """A scan that finds nothing must be proven armed, not assumed so."""
    tree = ast.parse("import tomllib\n")
    assert "tomllib" in _bare_imports(tree)


def test_the_scan_sees_a_FUNCTION_level_import():
    """The placement the first version missed (2026-08-24).

    Module level is a collection error and loud; a function-level one is a
    runtime failure on the older leg only. Both are the same rule.
    """
    tree = ast.parse("def t():\n    import tomllib\n")
    assert "tomllib" in _bare_imports(tree)


def test_the_scan_sees_a_CLASS_level_import():
    tree = ast.parse("class T:\n    import tomllib\n")
    assert "tomllib" in _bare_imports(tree)


def test_a_guarded_import_inside_a_function_is_not_an_offender():
    tree = ast.parse(
        "def t():\n    try:\n        import tomllib\n"
        "    except ModuleNotFoundError:\n        import tomli as tomllib\n"
    )
    assert "tomllib" not in _bare_imports(tree)


def test_a_guarded_import_is_not_an_offender():
    tree = ast.parse(
        "try:\n    import tomllib\nexcept ModuleNotFoundError:\n"
        "    import tomli as tomllib\n"
    )
    assert "tomllib" not in _bare_imports(tree)


def test_the_floor_is_read_from_pyproject():
    """Keyed to `requires-python`, so raising the floor retires this on its
    own rather than leaving a check nobody can remove."""
    assert _floor() <= tuple(sys.version_info[:2])


#: Keyword arguments that exist only above jm's floor, as ``(method, kwarg)``.
#: ``Path.write_text(newline=...)`` is 3.10+; on 3.9 it is a TypeError at the
#: call, so a fixture using it ERRORS every test that needs it on the 3.9 leg
#: only (#1658's first CI run: `ubuntu-24.04-arm, 3.9`). jm's own spelling is
#: ``_textio.write_text``, which is LF on every platform and every Python.
_ABOVE_FLOOR_KWARGS = {("write_text", "newline"): (3, 10)}

#: Everything that runs under jm's supported Pythons: the suite, jm itself
#: (bundled examples included -- `just-makeit example` runs them), and the
#: repo's scripts.
_SCANNED = (TESTS, SRC, SRC.parent / "scripts", SRC.parent / "docker")


def _kwargs_above_floor(
    tree: ast.AST, floor: tuple
) -> "list[tuple[int, str]]":
    """``(line, "method(kwarg=)")`` for each call passing a keyword the floor
    lacks. By method NAME: a receiver's type is not in the text, and no
    other `write_text` in this repo takes ``newline``."""
    out = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ):
            continue
        for kw in node.keywords:
            need = _ABOVE_FLOOR_KWARGS.get((node.func.attr, kw.arg))
            if need and need > floor:
                out.append((node.lineno, f"{node.func.attr}({kw.arg}=)"))
    return out


def test_no_call_passes_a_keyword_above_the_supported_floor():
    floor = _floor()
    offenders = []
    for base in _SCANNED:
        for f in sorted(base.rglob("*.py")):
            # jm's templates are Python only once rendered (`<<Component>>`).
            if "templates" in f.relative_to(base).parts:
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            for line, what in _kwargs_above_floor(tree, floor):
                rel = f.relative_to(SRC.parent).as_posix()
                offenders.append(f"{rel}:{line}: {what}")
    assert not offenders, (
        "a TypeError on every leg below the keyword's Python; write LF "
        "through `just_makeit._textio.write_text` instead:\n  "
        + "\n  ".join(offenders)
    )


def test_the_kwarg_scan_can_actually_see_one():
    tree = ast.parse('p.write_text(s, encoding="utf-8",\n    newline="\\n")\n')
    assert _kwargs_above_floor(tree, (3, 9)) == [(1, "write_text(newline=)")]
