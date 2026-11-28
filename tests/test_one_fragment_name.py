"""One spelling of a module member's binding-fragment file name.

A module's ``<cname>_ext.c`` aggregates one ``<cname>_ext_<id>.c`` per
member. Fifteen sites spelled that name by hand -- the adopter, the apply
walk, the enum splice, the docs sync, the object writer, the link check, the
status report, the renderer's header and ``#include``, and the manifest's
own fragment-collision refusal -- so a change of the name would have been
fifteen edits and a missed one a project `status` calls clean. They all ask
`_incpath.fragment_name` now.

GATES:

1. Registration-free: no jm module but `_incpath` builds a fragment file
   name from a formatted value -- ``f"{cname}_ext_{id}.c"``,
   ``cname + "_ext_" + id + ".c"``, ``"%s_ext_%s.c" % ...`` or
   ``"{}_ext_{}.c".format(...)``. The hand-written hooks
   (``_ext_<id>_extra.c``, ``_ext_prologue.c``) are other files with their
   own writers and are let through, as is a glob (``*_ext_*.c``), which
   names no member.
2. The detector sees every shape it guards and lets through what it allows.
3. The answer itself.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from just_makeit import _incpath as INC

SRC = Path(INC.__file__).parent

#: Fragment suffixes that are the author's hooks, not a fragment.
_HOOKS = ("_extra", "_prologue")

_FMT = re.compile(r"_ext_(?:\{[^}]*\}|%s|%\([^)]*\)s)(?P<tail>[^\"']*)")


def _is_hook(tail: str) -> bool:
    return tail.startswith(_HOOKS)


def _docstrings(tree: ast.AST) -> "set[int]":
    ids = set()
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                ids.add(id(body[0].value))
    return ids


def hand_spellings(source: str) -> "list[int]":
    """Line numbers where *source* builds a fragment file name by hand.

    >>> hand_spellings('x = f"{cname}_ext_{fid}.c"\\n')
    [1]
    >>> hand_spellings('x = f"{cname}_ext_{fid}_extra.c"\\n')
    []
    >>> hand_spellings('x = c + "_ext_" + fid + ".c"\\n')
    [1]
    >>> hand_spellings('x = "%s_ext_%s.c" % (c, f)\\n')
    [1]
    >>> hand_spellings('x = "{}_ext_{}.c".format(c, f)\\n')
    [1]
    >>> hand_spellings('x = f"{cname}_ext_*.c"\\n')
    []
    >>> hand_spellings('x = f"{cname}_ext.c"\\n')
    []
    >>> hand_spellings("def f():\\n    '_ext_{x}.c'\\n    return 1\\n")
    []
    """
    tree = ast.parse(source)
    skip = _docstrings(tree)
    out: "list[int]" = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            vals = node.values
            for i, v in enumerate(vals[:-1]):
                if not (
                    isinstance(v, ast.Constant)
                    and isinstance(v.value, str)
                    and v.value.endswith("_ext_")
                    and isinstance(vals[i + 1], ast.FormattedValue)
                ):
                    continue
                rest = vals[i + 2 :]
                tail = (
                    rest[0].value
                    if rest
                    and isinstance(rest[0], ast.Constant)
                    and isinstance(rest[0].value, str)
                    else ""
                )
                if tail.startswith(".c") and not _is_hook(tail):
                    out.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip:
                continue
            m = _FMT.search(node.value)
            if m and m["tail"].endswith(".c") and not _is_hook(m["tail"]):
                out.append(node.lineno)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # name + "_ext_" + id + ".c": a "_ext_" constant followed by a
            # ".c" constant later in the same chain.
            parts, cur = [], node
            while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Add):
                parts.insert(0, cur.right)
                cur = cur.left
            parts.insert(0, cur)
            consts = [
                p.value
                for p in parts
                if isinstance(p, ast.Constant) and isinstance(p.value, str)
            ]
            if "_ext_" in consts and any(c.startswith(".c") for c in consts):
                out.append(node.lineno)
    return sorted(set(out))


def test_the_detector_doctests() -> None:
    import doctest
    import sys

    result = doctest.testmod(sys.modules[__name__])
    assert result.attempted >= 8 and result.failed == 0


def _sites() -> "list[tuple[str, int]]":
    found = []
    for path in sorted(SRC.glob("*.py")):
        if path.name == "_incpath.py":
            continue
        for line in hand_spellings(path.read_text(encoding="utf-8")):
            found.append((path.name, line))
    return found


def test_no_module_spells_a_fragment_name_by_hand() -> None:
    assert _sites() == [], (
        "a fragment file name is `_incpath.fragment_name(cname, id)`; "
        "hand-spelled at: " + ", ".join(f"{f}:{n}" for f, n in _sites())
    )


def test_the_gate_scans_the_tree_it_guards() -> None:
    names = {p.name for p in SRC.glob("*.py")}
    assert {"_apply.py", "_object.py", "_config.py", "_render.py"} <= names


def test_the_owner_spells_it_once() -> None:
    owner = (SRC / "_incpath.py").read_text(encoding="utf-8")
    assert hand_spellings(owner) != []


@pytest.mark.parametrize(
    ("cname", "frag_id", "want"),
    [
        ("m", "a", "m_ext_a.c"),
        ("dsp_filters", "fir", "dsp_filters_ext_fir.c"),
    ],
)
def test_fragment_name(cname: str, frag_id: str, want: str) -> None:
    assert INC.fragment_name(cname, frag_id) == want
