"""gh-1583: one owner of the header layout, `_incpath`.

Headers are moving under the package (``native/inc/<pkg>/``, included as
``"<pkg>/<comp>/<comp>_core.h"``) so an installed project's headers cannot
collide with another's. Before that change the include root was spelled by
hand about 75 times across jm's modules and in every C template's
``#include`` -- so the move would have been an edit at each, and every one
missed a bug. `_incpath` owns the layout now; the move is one flag there.

These gates keep it the only owner. Each reads jm's source as source (the
AST, or the template's directives), so prose in a comment or a docstring is
not a finding:

1. No module but `_incpath` spells the include root: no ``"native/inc"``
   string and no ``... / "native" / "inc"`` path. (`_installhistory` is
   exempt: it is GENERATED, a record of what released jms rendered.)
2. Every C/H template ``#include`` of a header jm writes under the include
   root carries ``/*<<inc_prefix>>*/``, which `render()` fills from
   `_incpath`. The exceptions are the harness headers that live BESIDE the
   file including them, found relative to it rather than through ``-I``.
3. Every Python string that emits an ``#include`` of a jm-generated header
   (``_core.h``, ``_bridge.h``, ``clib_common.h``, ``jm_perf.h``) spells it
   through `_incpath` or the slot.

GATE: only `_incpath` spells the include root or a generated header's
      include path, in jm's modules and in its C templates alike.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "src" / "just_makeit"

#: The owner, and a GENERATED record of old renders (it must spell what
#: released jms spelled).
_EXEMPT = {"_incpath.py", "_installhistory.py"}


def _modules():
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG)
        # Templates are not Python (their `<<slots>>`); the examples' step
        # scripts are the examples', not jm's.
        if {"templates", "examples"} & set(rel.parts) or rel.name in _EXEMPT:
            continue
        yield rel, ast.parse(path.read_text(encoding="utf-8"), str(path))


def _prose(tree) -> "set[int]":
    """ids of string constants that are docstrings or bare string statements."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            out.add(id(node.value))
    return out


def test_only_incpath_spells_the_include_root():
    hits = []
    for rel, tree in _modules():
        prose = _prose(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "native/inc" in node.value
                and id(node) not in prose
            ):
                hits.append(f"{rel}:{node.lineno}  {node.value[:60]!r}")
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Div)
                and isinstance(node.right, ast.Constant)
                and node.right.value == "inc"
                and isinstance(node.left, ast.BinOp)
                and isinstance(node.left.right, ast.Constant)
                and node.left.right.value == "native"
            ):
                hits.append(f'{rel}:{node.lineno}  ... / "native" / "inc"')
    assert hits == [], (
        "spell the include root through `_incpath` (INC.core_h, INC.path, "
        "INC.rel, INC.inc_dir, INC.CMAKE_INC ...):\n" + "\n".join(hits)
    )


#: Harness headers written BESIDE their includers (native/tests,
#: native/benchmarks): a quoted include finds them in the including file's
#: own directory, not through -I, so they are not under the include root.
_LOCAL_HEADERS = {"jm_test.h", "jm_bench.h"}
_INCLUDE = re.compile(r'^#include "([^"]+)"', re.M)


def test_every_template_include_of_a_generated_header_uses_the_slot():
    bad = []
    for path in sorted((PKG / "templates" / "c").rglob("*")):
        if not path.is_file():
            continue
        for m in _INCLUDE.finditer(path.read_text(encoding="utf-8")):
            target = m.group(1)
            if target in _LOCAL_HEADERS:
                continue
            if not target.startswith("/*<<inc_prefix>>*/"):
                bad.append(f'{path.relative_to(PKG)}: #include "{target}"')
    assert bad == [], (
        "an #include of a header under the include root starts with "
        '"/*<<inc_prefix>>*/":\n' + "\n".join(bad)
    )


_GENERATED = re.compile(r"(_core\.h|_bridge\.h|clib_common\.h|jm_perf\.h)$")


def _spelled_by_owner(node) -> bool:
    """An f-string's include path goes through INC.* or carries the slot."""
    for part in ast.walk(node):
        if (
            isinstance(part, ast.Attribute)
            and isinstance(part.value, ast.Name)
            and part.value.id == "INC"
        ):
            return True
        if isinstance(part, ast.Constant) and "<<inc_prefix>>" in str(
            part.value
        ):
            return True
    return False


def _include_target(node) -> "str | None":
    """The text after `#include "` in a string or f-string, up to its quote."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.JoinedStr):
        text = "".join(
            p.value if isinstance(p, ast.Constant) else "{}"
            for p in node.values
        )
    else:
        return None
    m = re.search(r'#include "([^"]*)"', text)
    return m.group(1) if m else None


def test_every_emitted_include_of_a_generated_header_is_spelled_by_the_owner():
    bad = []
    for rel, tree in _modules():
        prose = _prose(tree)
        for node in ast.walk(tree):
            if id(node) in prose:
                continue
            target = _include_target(node)
            if target is None or not _GENERATED.search(target):
                continue
            if isinstance(node, ast.JoinedStr) and _spelled_by_owner(node):
                continue
            if "<<inc_prefix>>" in target:
                continue
            bad.append(f'{rel}:{node.lineno}  #include "{target}"')
    assert bad == [], (
        "spell an emitted #include of a jm-generated header through "
        "`_incpath` (INC.include / INC.core_include) or <<inc_prefix>>:\n"
        + "\n".join(bad)
    )


def test_the_gates_see_what_they_guard():
    """A gate that finds nothing to read passes while measuring nothing."""
    modules = list(_modules())
    assert len(modules) > 50, len(modules)
    includes = [
        m.group(1)
        for p in (PKG / "templates" / "c").rglob("*")
        if p.is_file()
        for m in _INCLUDE.finditer(p.read_text(encoding="utf-8"))
    ]
    assert sum(t.startswith("/*<<inc_prefix>>*/") for t in includes) >= 10
