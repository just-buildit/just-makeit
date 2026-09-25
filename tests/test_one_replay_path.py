"""One way to replay a project: `_apply.replay_project`.

`apply` never ran `_replay` bare. It runs it inside the scopes that make it
correct -- `deferred_module_regen` above all, so a module regenerates once
everything it references has been replayed -- and `status` reads that same
tree. #1596's `jm adopt --packaging` called `_replay` directly, and on
doppler (a module whose `init_param object = "dp_tlm"` names a component
whose capsule property is replayed later) it refused outright: "component
'dp_tlm' publishes no capsule". A second replay path is a peer
implementation, and this one had drifted the day it was written.

GATE: nothing but `_apply.replay_project` calls `_replay`, so every replay
      of a project is apply's.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "just_makeit"


def _replay_calls() -> "list[str]":
    """``file:line (enclosing function)`` of every call to ``_replay``."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        # jm's own modules: not the code templates (their `<<slots>>` are not
        # Python) and not the bundled examples' step scripts.
        if {"templates", "examples"} & set(path.relative_to(SRC).parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                name = (
                    f.attr
                    if isinstance(f, ast.Attribute)
                    else getattr(f, "id", None)
                )
                if name == "_replay":
                    found.append(
                        f"{path.relative_to(SRC)}:{node.lineno} ({fn.name})"
                    )
    return found


def test_only_replay_project_replays():
    calls = _replay_calls()
    # Nested functions are walked by each enclosing def, so dedupe by site.
    sites = sorted(set(calls))
    outside = [c for c in sites if not c.endswith("(replay_project)")]
    assert outside == [], (
        "call `_apply.replay_project`, not `_replay`: the replay is only "
        "correct inside apply's scopes:\n" + "\n".join(outside)
    )
    # And the one sanctioned caller is really there: a gate that finds no
    # call at all would pass while measuring nothing.
    assert any(c.startswith("_apply.py:") for c in sites), sites
