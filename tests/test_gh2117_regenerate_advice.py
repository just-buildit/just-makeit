"""gh-2117: advice never sends a `no_generate` object to `jm regenerate`.

`jm regenerate` refuses an object in a `no_generate` module (gh-2087):
`apply` writes none of that module, so nothing would rebuild what the
command deletes. Advice naming it -- `jm bind`'s refusal of a declared
component (gh-2072), the built-in body jm could not withdraw (gh-994), the
`create()` signature drift (gh-1076) -- led such an object to that refusal,
and only then to the real route: edit the C by hand.

GATE: every message in jm that names `jm regenerate` -- read from the
      source, every string that is not a docstring -- is either spelled by
      `_regenerate.rebuild_route`, the one sentence that asks
      `C.hand_written_module` and names the hand route for a `no_generate`
      object, or sits in `UNREACHABLE` with the reason it cannot be about
      one. A new message naming `jm regenerate` fails
      `test_every_regenerate_advice_is_routed` until it is one or the
      other, and an entry whose site no longer names it fails too.

      The reachable sites are then triggered on an object in a
      `no_generate` module whose header disagrees with its manifest: `jm
      bind`'s refusal, and every line `jm status` prints (the CTOR finding
      among them, which `_ctorsig.drift` no longer reports for an object
      jm writes nothing of), name no `jm regenerate`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _regenerate

SRC = Path(_regenerate.__file__).resolve().parent

#: A message naming the command as something to run.
_NAMES_IT = re.compile(r"\b(jm|just-makeit) regenerate\b")

#: The one site that spells the advice, asking `C.hand_written_module`.
ROUTE = "just_makeit._regenerate.rebuild_route"

#: ``<module>.<qualname>`` -> why its message cannot reach an object in a
#: `no_generate` module. Held to the source both ways.
UNREACHABLE: "dict[str, str]" = {
    "just_makeit._cli._main": (
        "the usage line of `jm regenerate` itself, printed for a bad argv"
    ),
    "just_makeit._ctorsig.CtorDrift.describe": (
        "`_ctorsig.drift` builds no CtorDrift for an object in a"
        " `no_generate` module (gh-2117); `test_status_names_no_regenerate`"
        " triggers one"
    ),
    "just_makeit._extrahook.describe": (
        "tells the reader NOT to run it (gh-1216)"
    ),
    "just_makeit._status.run": (
        "names `<component>` among files the replay renders, and the replay"
        " renders none of a `no_generate` module (gh-2116);"
        " `test_status_names_no_regenerate` reads every line it prints"
    ),
}


def _docstrings(tree: ast.Module) -> "set[int]":
    out = set()
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
                out.add(id(body[0].value))
    return out


def advice_sites() -> "set[str]":
    """Every ``<module>.<qualname>`` whose code holds a string naming `jm
    regenerate`: the innermost enclosing ``def`` / ``class`` path, or
    ``<module>`` at file scope. An f-string's literal parts are read too."""
    out = set()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC.parent).with_suffix("").parts
        modname = ".".join(rel)
        if modname.startswith(("just_makeit.templates", "just_makeit.ex")):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstrings(tree)

        def visit(node: ast.AST, where: str) -> None:
            for child in ast.iter_child_nodes(node):
                here = where
                if isinstance(
                    child,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                ):
                    here = f"{where}.{child.name}" if where else child.name
                if (
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and id(child) not in docs
                    and _NAMES_IT.search(child.value)
                ):
                    out.add(f"{modname}.{where or '<module>'}")
                visit(child, here)

        visit(tree, "")
    return out


def test_every_regenerate_advice_is_routed():
    sites = advice_sites()
    assert ROUTE in sites, "rebuild_route no longer names `jm regenerate`"
    stray = sorted(sites - {ROUTE} - set(UNREACHABLE))
    assert not stray, (
        f"{stray} name `jm regenerate` themselves: spell the advice through"
        " `_regenerate.rebuild_route`, which names the hand route for an"
        " object in a `no_generate` module, or say in UNREACHABLE why the"
        " message cannot be about one"
    )
    gone = sorted(set(UNREACHABLE) - sites)
    assert not gone, f"UNREACHABLE entries naming nothing now: {gone}"


def _ok(root: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])


@pytest.fixture(scope="module")
def shape(tmp_path_factory) -> Path:
    """Object ``o`` in module ``mod``, applied, then declared `no_generate`
    -- with a reason -- and given an init param the header's `create()`
    does not take: the gh-1076 drift, on an object jm writes none of."""
    base = tmp_path_factory.mktemp("gh2117")
    _ok(base, "new", "p")
    root = base / "p"
    _ok(root, "module", "mod")
    _ok(root, "object", "o", "--module", "mod")
    cfg = C.load(root)
    cfg["module"]["mod"]["no_generate"] = "true"
    cfg["module"]["mod"]["no_generate_reason"] = "hand-written by gh-2117"
    cfg["o"].setdefault("init_params", []).append(
        {"name": "k", "type": "int", "default": "4"}
    )
    C.save(root, cfg)
    _ok(root, "apply")
    return root


def test_bind_names_the_hand_route(shape):
    r = run_cli("bind", "o", cwd=shape)
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert r.returncode == 1 and len(errors) == 1, r.stdout + r.stderr
    assert not _NAMES_IT.search(errors[0]), errors[0]
    assert "by hand" in errors[0] and "`no_generate`" in errors[0], errors[0]
    # Nor the generated route's other half: `apply` writes none of it.
    assert "`jm apply` owns" not in errors[0], errors[0]
    assert "run `jm apply`" not in errors[0], errors[0]


@pytest.mark.parametrize("argv", [("status",), ("status", "--check")])
def test_status_names_no_regenerate(shape, argv):
    r = run_cli(*argv, cwd=shape)
    out = r.stdout + r.stderr
    assert not _NAMES_IT.search(out), out[-3000:]
    assert "CTOR" not in out, out[-3000:]
