"""gh-2065: where a module's Python lives has one owner.

`C.module_package_resolved(cfg, module)` (gh-2054) is THE answer: the
module's ``[module.X] package`` when declared, else its own ``pypath``. The
same answer was also spelled inline as ``C.module_package(cfg, m) or
mp.pypath`` -- or through the ``capsule_package`` alias, or as
``package or mp.pypath`` in ``make_module_ctx`` -- at 20 sites across ten
modules. They all agreed when this was written. Peer spellings drift,
though, and gh-2054 was exactly this pair drifting: the imports read the
module id's path and the ``__init__.py`` writer read the package.

The gate reads jm's own source, so it needs no registration. It refuses a
``pypath`` used as the fallback of an ``or``, or as a branch of a
conditional on a package reader, in any function but the two listed in
:data:`ALLOWED`, each with its reason. An allowed site that stops spelling
it inline fails too, so the list cannot go stale.

GATE: a module's package directory is spelled only by
      C.module_package_resolved.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).parent.parent / "src" / "just_makeit"

#: `<module path>:<function>` -> why it may spell the default inline.
ALLOWED = {
    "_config.py:module_package_resolved": "the owner itself",
    "_object.py:_regenerate_module_now": (
        "the re-export __init__.py writer: gh-2054's gate checks every "
        "generated import against what it writes, so it must not share the "
        "owner it checks"
    ),
}

#: The raw readers of ``[module.X] package``: a conditional on one of them
#: that falls back to a pypath is the same default spelled another way.
_RAW = frozenset({"module_package", "capsule_package", "handle_package"})


def _is_pypath(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "pypath"


def _reads_raw(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and (
            getattr(n.func, "attr", None) in _RAW
            or getattr(n.func, "id", None) in _RAW
        )
        for n in ast.walk(node)
    )


def inline_defaults(source: str) -> list[tuple[str, int]]:
    """Every `(function, line)` in *source* spelling the package default.

    ``<anything> or <expr>.pypath``, and ``a if <package read> else b``
    with a ``.pypath`` branch.
    """
    out: list[tuple[str, int]] = []

    def visit(node: ast.AST, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            here = fn
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                here = child.name
            if (
                isinstance(child, ast.BoolOp)
                and isinstance(child.op, ast.Or)
                and _is_pypath(child.values[-1])
            ) or (
                isinstance(child, ast.IfExp)
                and _reads_raw(child.test)
                and (_is_pypath(child.body) or _is_pypath(child.orelse))
            ):
                out.append((here, child.lineno))
            visit(child, here)

    visit(ast.parse(source), "<module>")
    return out


def _sites() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted(PKG.rglob("*.py")):
        # Templates are render inputs with `<<slot>>` placeholders, not code.
        if "templates" in path.parts:
            continue
        rel = path.relative_to(PKG).as_posix()
        for fn, line in inline_defaults(path.read_text(encoding="utf-8")):
            found.setdefault(f"{rel}:{fn}", []).append(line)
    return found


def test_no_inline_spelling_of_the_package_default():
    stray = {k: v for k, v in _sites().items() if k not in ALLOWED}
    assert stray == {}, (
        "read `C.module_package_resolved(cfg, module)` (gh-2054, gh-2065) "
        "instead of spelling `module_package(...) or ...pypath`:\n"
        + "\n".join(
            f"  {k} (line {', '.join(map(str, v))})" for k, v in stray.items()
        )
    )


@pytest.mark.parametrize("site", sorted(ALLOWED))
def test_each_allowed_site_still_spells_it_once(site: str):
    """An exemption naming a site that no longer spells the default would
    hide a new one there; and the oracle routed through the owner would
    leave gh-2054's gate checking the owner against itself."""
    assert len(_sites().get(site, [])) == 1, (site, ALLOWED[site])


@pytest.mark.parametrize(
    "snippet, flagged",
    [
        (
            "def f(cfg, m, mp):\n    return C.module_package(cfg, m) or mp.pypath\n",
            1,
        ),
        (
            "def f(cfg, m):\n"
            "    return C.capsule_package(cfg, m) or C.module_paths(m).pypath\n",
            1,
        ),
        ("def f(package, mp):\n    return package or mp.pypath\n", 1),
        (
            "def f(cfg, m, mp):\n"
            "    p = C.module_package(cfg, m)\n"
            "    return mp.pypath if not C.module_package(cfg, m) else p\n",
            1,
        ),
        ("def f(cfg, m):\n    return C.module_package_resolved(cfg, m)\n", 0),
        ("def f(a, b):\n    return a or b\n", 0),
        ("def f(mp):\n    return mp.pypath\n", 0),
    ],
    ids=["raw", "alias", "param", "ifexp", "owner", "plain-or", "bare"],
)
def test_the_scan_is_armed(snippet: str, flagged: int):
    """Each spelling the default took is flagged; the owner's call, an
    unrelated `or` and a bare pypath are not."""
    assert len(inline_defaults(snippet)) == flagged
