"""gh-2087: no command rebuilds an object a `no_generate` module owns.

A rebuild -- `_regenerate.run` -- deletes a component's files and has
`jm apply` write them again from the manifest. `apply` writes nothing of a
`no_generate` module, so on one of its objects the delete was the whole of
it: `jm add --state` removed the object's `_core.c`, with the author's
edits in it, its header, CMakeLists, binding fragment, C test and bench,
and exited 0; `apply` then said there was nothing to do. `jm remove state`
and `jm regenerate`, with or without `--discard`, did the same.

GATE: every function in jm that calls `_regenerate.run` -- read from the
      source, so a caller added later fails
      `test_every_rebuild_caller_has_a_command` until it has one -- is
      reached by a command in `COMMANDS`, and each of those, run on an
      object in a `no_generate` module,

      - exits 1 with one ``error:`` line naming `no_generate` and the route
        instead, editing the C by hand, and
      - leaves the WHOLE tree byte-identical: the manifest, and the
        author's line in `_core.c`.

      Each runs as given (``--force``) and again with the prompt answered
      yes: a refusal reached only after the manifest was saved, or after
      the author was asked, is red either way. Each must also pass through
      the caller it is listed under, so the table cannot cover a caller by
      name alone.

The same commands on an object `apply` does write are held to leaving the
tree `apply` writes by `tests/test_gh2057_verb_leaves_what_apply_writes.py`
(`add-state`, `remove-state`, `regenerate-*`), so the refusal reaches no
further than `no_generate`.
"""

from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _regenerate

SRC = Path(_regenerate.__file__).resolve().parent

#: The author's line in `_core.c`: what the rebuild deleted, and what the
#: byte-identical tree has to still hold.
AUTHOR = "/* gh-2087: written by the author */\n"

#: The caller of `_regenerate.run` (``<module>.<function>``) -> the commands
#: that reach it. Held to the source by
#: `test_every_rebuild_caller_has_a_command`.
COMMANDS: "dict[str, tuple[tuple[str, ...], ...]]" = {
    "just_makeit._add.run": (
        ("add", "--object", "o", "--state", "y:double:0", "--force"),
    ),
    "just_makeit._remove._remove_state": (
        ("remove", "state", "x", "--object", "o", "--force"),
    ),
    "just_makeit._cli._main": (
        ("regenerate", "o", "--force"),
        ("regenerate", "o", "--force", "--discard"),
    ),
}


def _regenerate_names(tree: ast.Module) -> "tuple[set[str], set[str]]":
    """The names a module binds to `_regenerate`, and to its ``run``."""
    module, run = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (
                    node.module in (None, "just_makeit")
                    and alias.name == "_regenerate"
                ):
                    module.add(alias.asname or alias.name)
                elif (node.module or "").endswith(
                    "_regenerate"
                ) and alias.name == "run":
                    run.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "just_makeit._regenerate":
                    assert alias.asname, (
                        "`import just_makeit._regenerate` without `as`:"
                        " teach rebuild_callers() to read the dotted call"
                    )
                    module.add(alias.asname)
    return module, run


def rebuild_callers() -> "set[str]":
    """Every function in jm that calls `_regenerate.run`, read from the
    source: ``<module>.<function>``, the innermost enclosing ``def``."""
    out = set()
    for path in sorted(SRC.rglob("*.py")):
        modname = ".".join(path.relative_to(SRC.parent).with_suffix("").parts)
        if modname.startswith(("just_makeit.templates", "just_makeit.ex")):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module, run = _regenerate_names(tree)
        if not module and not run:
            continue

        def visit(node: ast.AST, where: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, child.name)
                    continue
                if isinstance(child, ast.Call):
                    fn = child.func
                    if (
                        isinstance(fn, ast.Attribute)
                        and fn.attr == "run"
                        and isinstance(fn.value, ast.Name)
                        and fn.value.id in module
                    ) or (isinstance(fn, ast.Name) and fn.id in run):
                        out.add(f"{modname}.{where}")
                visit(child, where)

        visit(tree, "<module>")
    return out


def _ok(root: Path, *argv: str) -> None:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])


@pytest.fixture(scope="module")
def shape(tmp_path_factory) -> Path:
    """An object ``o`` with a state field, in module ``mod``, which the
    author then declared `no_generate` -- with a reason, as `status`
    requires -- and whose `_core.c` they have since written in."""
    base = tmp_path_factory.mktemp("gh2087")
    _ok(base, "new", "p")
    root = base / "p"
    _ok(root, "module", "mod")
    _ok(root, "object", "o", "--module", "mod")
    _ok(root, "add", "--object", "o", "--state", "x:double:0", "--force")
    cfg = C.load(root)
    cfg["module"]["mod"]["no_generate"] = "true"
    cfg["module"]["mod"]["no_generate_reason"] = "hand-written by gh-2087"
    C.save(root, cfg)
    core_c = root / "native" / "src" / "o" / "o_core.c"
    with core_c.open("a", encoding="utf-8", newline="\n") as f:
        f.write(AUTHOR)
    return root


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _frames() -> "list[str]":
    """``<module>.<function>`` of every frame above the caller's."""
    out, frame = [], sys._getframe(2)
    while frame is not None:
        out.append(f"{frame.f_globals.get('__name__')}.{frame.f_code.co_name}")
        frame = frame.f_back
    return out


def test_every_rebuild_caller_has_a_command():
    """A new caller of `_regenerate.run` fails here until `COMMANDS` gives
    it a command, which the test below then holds to the refusal."""
    found = rebuild_callers()
    assert set(COMMANDS) == found, (
        f"callers with no command: {sorted(found - set(COMMANDS))};"
        f" commands for no caller: {sorted(set(COMMANDS) - found)}"
    )


@pytest.mark.parametrize("answered", [False, True], ids=["force", "yes"])
@pytest.mark.parametrize(
    "caller, argv",
    [
        pytest.param(c, argv, id=" ".join(argv))
        for c, argvs in COMMANDS.items()
        for argv in argvs
    ],
)
def test_a_rebuild_of_a_no_generate_object_is_refused(
    shape, tmp_path, monkeypatch, caller, argv, answered
):
    root = tmp_path / "p"
    shutil.copytree(shape, root, symlinks=True)
    before = _tree(root)
    assert before["native/src/o/o_core.c"].decode().endswith(AUTHOR)

    # Where the refusal was asked from, for the last assertion. Spied, not
    # required: on a tree without the predicate the command must still go
    # red on what it did to the tree, which is the bug.
    reached: "list[list[str]]" = []
    refuse = getattr(_regenerate, "refuse_hand_written", None)

    def spy(*args, **kwargs):
        reached.append(_frames())
        return refuse(*args, **kwargs)

    monkeypatch.setattr(_regenerate, "refuse_hand_written", spy, raising=False)
    if answered:
        argv = tuple(a for a in argv if a != "--force")
    r = run_cli(*argv, cwd=root, stdin="y\n" if answered else "")

    after = _tree(root)
    changed = sorted(
        rel
        for rel in set(before) | set(after)
        if before.get(rel) != after.get(rel)
    )
    assert not changed, (
        f"{list(argv)} exited {r.returncode} and changed {changed}"
    )
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert r.returncode == 1 and len(errors) == 1, (
        argv,
        (r.stdout + r.stderr)[-3000:],
    )
    assert "`no_generate`" in errors[0], errors[0]
    assert "Edit the C by hand" in errors[0], errors[0]
    assert any(caller in frames for frames in reached), (
        f"{list(argv)} never passed through {caller}: {reached}"
    )
