"""gh-1583: one place answers "is this project's header layout prefixed?".

The layout is a fact about each project, read from its manifest's schema:
from ``_incpath.PREFIXED_SCHEMA`` on, headers live under ``native/inc/<pkg>/``.
``_incpath.prefixed(owner)`` is the one answer, and every path and spelling
jm emits asks it. A second place that compared the schema itself -- or a
global switch standing in for it -- would be a peer implementation of the
question, and part 3's migration flips the answer: two copies would disagree
the day it lands.

GATE: nothing outside _incpath compares a schema to the layout threshold,
      names PREFIXED_SCHEMA, or reintroduces a global PREFIXED switch.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "just_makeit"
OWNER = "_incpath.py"


def _modules():
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        # jm's own modules: not the code templates (their `<<slots>>` are not
        # Python) nor the bundled examples' step scripts.
        if {"templates", "examples"} & set(rel.parts):
            continue
        yield rel, ast.parse(path.read_text(encoding="utf-8"), str(path))


def _is_schema_call(node) -> bool:
    f = node.func if isinstance(node, ast.Call) else None
    name = getattr(f, "attr", None) or getattr(f, "id", None)
    return name == "schema_version"


def _asks_the_layout_question(node) -> bool:
    """A comparison of `schema_version(...)` against 8 or PREFIXED_SCHEMA."""
    if not isinstance(node, ast.Compare):
        return False
    sides = [node.left, *node.comparators]
    if not any(_is_schema_call(s) for s in sides):
        return False
    for s in sides:
        if isinstance(s, ast.Constant) and s.value == 8:
            return True
        if (
            getattr(s, "attr", None) == "PREFIXED_SCHEMA"
            or getattr(s, "id", None) == "PREFIXED_SCHEMA"
        ):
            return True
    return False


def _findings():
    found = []
    for rel, tree in _modules():
        if rel.name == OWNER:
            continue
        for node in ast.walk(tree):
            if _asks_the_layout_question(node):
                found.append(f"{rel}:{node.lineno}: compares the schema")
            name = getattr(node, "attr", None) or getattr(node, "id", None)
            if name == "PREFIXED_SCHEMA":
                found.append(f"{rel}:{node.lineno}: names PREFIXED_SCHEMA")
            if name == "PREFIXED":
                found.append(f"{rel}:{node.lineno}: a global PREFIXED switch")
    return sorted(set(found))


def test_only_incpath_asks_whether_a_project_is_prefixed():
    assert _findings() == [], (
        "ask `_incpath.prefixed(owner)`; the layout is decided in one place:\n"
        + "\n".join(_findings())
    )


def test_the_owner_really_asks_it():
    # A gate that finds no question anywhere would pass measuring nothing:
    # the owner must hold the one comparison the others are refused.
    tree = ast.parse((SRC / OWNER).read_text(encoding="utf-8"))
    assert any(_asks_the_layout_question(n) for n in ast.walk(tree))
