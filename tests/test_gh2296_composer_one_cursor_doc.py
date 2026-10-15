"""gh-2296: a composer's execute, compose and stream say they share a cursor.

The backing C has one read position (create -> execute* -> destroy, with no
reset), and all three members advance it. ``compose()`` after a preview
``execute(n)`` returns only what remains, and a drained composer yields
nothing. The docstrings described a fresh pass each time: compose() said
"the full sequence". The owner ruled for a doc fix, not a behaviour change,
so each member states the shared cursor on BOTH faces. Both faces come from
the one table gh-2283 made, so the compiled module is the check.
"""

from __future__ import annotations

import ast

from test_gh2283_composer_doc_faces import built  # noqa: F401  (fixture)

#: What each member must say, read from the compiled ``.so`` and the stub.
_SAYS = ("one cursor", "Build a new Mix to start over.")


def _stub_doc(tree: ast.Module, cls: str, name: str) -> str:
    (node,) = [
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls
    ]
    (fn,) = [
        m
        for m in node.body
        if isinstance(m, ast.FunctionDef) and m.name == name
    ]
    return " ".join((ast.get_docstring(fn) or "").split())


def test_each_reader_states_the_shared_cursor(built) -> None:  # noqa: F811
    tree, mod = built
    for name in ("execute", "compose", "stream"):
        runtime = " ".join((getattr(mod.Mix, name).__doc__ or "").split())
        stub = _stub_doc(tree, "Mix", name)
        for face, text in (("runtime", runtime), ("stub", stub)):
            for phrase in _SAYS:
                assert phrase in text, f"Mix.{name} {face}: {text!r}"


def test_compose_no_longer_promises_the_full_sequence(built) -> None:  # noqa: F811
    tree, _ = built
    summary = _stub_doc(tree, "Mix", "compose").split(".")[0]
    assert summary == "Compose what remains of the sequence into one array"
