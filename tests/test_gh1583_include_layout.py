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


def _is_str(node, value: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def _ends_with_native(node) -> bool:
    """*node* is a path expression whose last segment is ``"native"``:
    ``x / "native"``, ``Path("native")`` or ``Path(x, "native")``.

    gh-1583 part 3: the first version of this gate read only the
    ``x / "native" / "inc"`` shape, and `_method` spelled the root as
    ``Path("native") / "inc"`` -- so under the prefixed layout it looked for
    a header where none was, and `jm method` on a built-in name wrote a stub
    that declared the member twice.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _is_str(node.right, "native")
    if isinstance(node, ast.Call) and node.args:
        return _is_str(node.args[-1], "native")
    return _is_str(node, "native")


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
                and _is_str(node.right, "inc")
                and _ends_with_native(node.left)
            ):
                hits.append(f'{rel}:{node.lineno}  ... / "native" / "inc"')
            if isinstance(node, ast.Call) and any(
                _is_str(a, "native") and _is_str(b, "inc")
                for a, b in zip(node.args, node.args[1:])
            ):
                hits.append(f'{rel}:{node.lineno}  Path(..., "native", "inc")')
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


#: A core header's include spelling built from a name: ``{x}/{x}_core.h``.
_CORE_SPELLING = re.compile(r"^\{\}/\{\}_core\.h$")


def test_no_core_header_spelling_bypasses_the_owner():
    """A DEFAULT include spelling of a core header goes through `_incpath`.

    gh-1583 part 3. The gate above reads strings that say ``#include``; a
    default such as ``C.capsule_header(...) or f"{backing}/{backing}_core.h"``
    is the same spelling held in a variable and wrapped in ``#include`` later,
    so it was invisible -- and under the prefixed layout every handle,
    capsule and composer module that relied on it emitted an include that
    does not resolve. An f-string handed straight to an ``INC.*`` call is
    that call's argument, not a spelling of its own.
    """
    bad = []
    for rel, tree in _modules():
        prose = _prose(tree)
        owned = {
            id(arg)
            for call in ast.walk(tree)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "INC"
            for arg in call.args
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr) or id(node) in prose:
                continue
            text = "".join(
                p.value if isinstance(p, ast.Constant) else "{}"
                for p in node.values
            )
            if _CORE_SPELLING.match(text) and id(node) not in owned:
                bad.append(f"{rel}:{node.lineno}  {text}")
    assert bad == [], (
        "spell a core header's include through INC.core_include(comp, cfg):\n"
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
