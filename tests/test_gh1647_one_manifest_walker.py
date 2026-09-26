"""gh-1647: every respell `jm upgrade` makes to C also reaches the manifest.

jm renders a header body FROM the manifest's C-bearing strings (``impl``,
``create_impl``, a ``type``). A respell that rewrites the project's C files
but not those strings is undone by the next ``jm apply``. It happened twice:
the ``c_prefix`` respell (gh-1653) and the complex respell (gh-1647), each
written as a C-file pass only.

So the rule is structural, read from `_upgrade`'s source rather than listed:
every function there that respells C code -- a call to the code-only
primitive ``_respell_code_only`` or to ``_csym.respell_c`` -- also passes
the manifest through the one walker, ``_csym.respell_manifest_c`` (directly,
or through ``respell_manifest``, which is it plus the ``*_impl_file``
follow-up). A third respell cannot forget the manifest without this failing.

GATE: every C-respelling function in `_upgrade` visits the manifest's C
      through `_csym.respell_manifest_c`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from just_makeit import _upgrade

#: What respells C code, and what visits the manifest's C.
RESPELLS = {"_respell_code_only", "respell_c"}
WALKERS = {"respell_manifest_c", "respell_manifest"}


def _called(fn: ast.AST) -> "set[str]":
    """Names this function calls, bare (``f()``) or as attributes
    (``CSYM.f()``), and names it passes as a value (``walker(text, f)``)."""
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
            for a in n.args:
                if isinstance(a, ast.Name):
                    out.add(a.id)
    return out


def respellers_without_the_walker(source: str) -> "list[str]":
    """The top-level functions of *source* that respell C and never visit
    the manifest's C.

    >>> respellers_without_the_walker(
    ...     "def a(t):\\n    return _respell_code_only(t)\\n")
    ['a']
    >>> respellers_without_the_walker(
    ...     "def a(t):\\n    _respell_code_only(t)\\n"
    ...     "    respell_manifest_c(t, _respell_code_only)\\n")
    []
    """
    tree = ast.parse(source)
    bad = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name in RESPELLS:
            continue
        called = _called(fn)
        if called & RESPELLS and not called & WALKERS:
            bad.append(fn.name)
    return bad


def test_every_c_respell_visits_the_manifest():
    source = Path(_upgrade.__file__).read_text(encoding="utf-8")
    assert respellers_without_the_walker(source) == [], (
        "these `_upgrade` functions respell C but not the manifest's C, "
        "so `jm apply` renders the old spelling back from it (gh-1647): "
        "pass the manifest through `_csym.respell_manifest_c`"
    )


def test_the_rule_found_both_respells():
    """Not vacuous: the scan sees the complex and the c_prefix respells."""
    tree = ast.parse(Path(_upgrade.__file__).read_text(encoding="utf-8"))
    respellers = {
        fn.name
        for fn in tree.body
        if isinstance(fn, ast.FunctionDef)
        and fn.name not in RESPELLS
        and _called(fn) & RESPELLS
    }
    assert {"_repair_complex_spelling", "_respell_c_prefix"} <= respellers
