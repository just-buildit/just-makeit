"""gh-2244: every call of the in-process runner starts as a new jm process.

`run_cli` runs many jm commands in one Python process (gh-1374). Three
advisories deduplicated in module-level sets that lived for that process
(`_keys._SEEN`, `_extrahook._SEEN`, `_config._RETIRED_PLATFORMS_SAID`), so
once one printed in a pytest worker every later call skipped it, and a test
asserting the warning ABSENT passed whatever jm did: gh-2115's ``out_cols``
check stayed green with ``out_cols`` removed from `_keys.METHOD_KEYS`.

Now every such memo is declared through `_perprocess.per_process`, and
`run_cli` calls `_perprocess.fresh`, which empties each one. The warnings
dedup through one set, `_report.warn(..., once=True)`.

What this file holds, without a list of the memos to hold it to:

- every module-level value that a function in the package mutates or
  rebinds, and every ``functools`` cache, is FOUND by reading the source;
  each must be declared per-process, or be named in `_NOT_PER_PROCESS` with
  why a new process would see the same thing -- so a new memo fails here
  until someone answers that question for it;
- every declared one is emptied by `run_cli`, by behaviour: filled, then
  read back after a call;
- a warning one call prints, the next call prints again, and one call
  still prints it once.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _jmrun import run_cli  # noqa: E402

_PKG = Path(__file__).resolve().parent.parent / "src" / "just_makeit"

#: Method names that change a container in place.
_MUTATORS = frozenset(
    {
        "add",
        "append",
        "appendleft",
        "clear",
        "discard",
        "extend",
        "insert",
        "pop",
        "popitem",
        "remove",
        "setdefault",
        "update",
    }
)

#: Decorators that keep a function's results for the life of the process.
_CACHES = frozenset({"cache", "lru_cache"})

#: Scoped to one command: bound on entry to a context manager (or a
#: function's ``try``) and put back on every way out, so a command that
#: ends -- raising or not -- leaves the initial value behind. Checked by
#: behaviour below, after a call.
SCOPED = "scoped"

#: Module-level state that is not per process, and why a new process would
#: see the same thing. A finding absent from here and not declared through
#: `per_process` fails `test_every_process_memo_is_declared_or_explained`;
#: an entry here that the scan no longer finds fails as stale.
_NOT_PER_PROCESS = {
    ("_csym", "_JM_MACROS"): (
        "parsed from jm's own jm_perf.h template: the same in every process"
    ),
    ("_pythons", "_jm_metadata"): (
        "jm's installed metadata: the same in every process, and a fresh "
        "read cost ~0.6 s on 3.9 (#2633)"
    ),
    ("_createonly", "_compile"): (
        "a pure function of its arguments: a cold cache answers the same"
    ),
    ("_textio", "_TOML_WRITES"): (
        "a generation counter: `_incpath` compares it only for change, so a "
        "value left by an earlier command cannot be mistaken for a write"
    ),
    ("_report", "_gating"): (
        "zeroed by `_report.reset`, which its one reader (`apply`) calls "
        "before it counts"
    ),
    ("_config", "_SCRATCH_WRITES"): SCOPED,
    ("_config", "_REPLAY_GRAPH"): SCOPED,
    ("_config", "_DEFERRED"): SCOPED,
    ("_object", "_DEFERRED_REGEN"): SCOPED,
    ("_undo", "_active"): SCOPED,
    ("_undo", "_awaiting_root"): SCOPED,
    ("__init__", "globals()"): (
        "the lazy `__version__` and re-exports: jm's install, the same in "
        "every process, and a failed version lookup is not kept (gh-1166)"
    ),
    ("_perprocess", "_REGISTRY"): (
        "the registry itself: what is declared, not what was seen"
    ),
}


def _modules():
    """Every module of the package, as (dotted name below it, path).

    A ``.py`` is a module when every directory from it up to the package
    has an ``__init__.py``: the templates and examples jm copies into a
    project are not, and run in that project's process, not jm's.
    """
    for path in sorted(_PKG.rglob("*.py")):
        rel = path.relative_to(_PKG)
        dirs = [_PKG.joinpath(*rel.parts[:i]) for i in range(len(rel.parts))]
        if not all((d / "__init__.py").is_file() for d in dirs):
            continue
        yield ".".join(rel.with_suffix("").parts), path


def _decorator_name(node: ast.expr) -> str:
    """``cache`` for ``@functools.cache``, ``@cache`` or ``@cache()``."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _scan(tree: ast.Module) -> "dict[str, ast.AST]":
    """Every process memo in one module: name -> what makes it one.

    A module-level name is a memo when a function mutates it in place (a
    `_MUTATORS` call, or a subscript store or delete) without having a
    local of that name, or declares it ``global``; a function is one when
    a `_CACHES` decorator keeps its results; and a module whose function
    stores into ``globals()`` is one, under the name ``globals()``.
    Mutation in module-level code is construction, not memory, and is not
    counted.
    """
    bound: "dict[str, ast.AST]" = {}
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name):
                bound[t.id] = node
    found: "dict[str, ast.AST]" = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(_decorator_name(d) in _CACHES for d in fn.decorator_list):
            found[fn.name] = fn
        declared = set()
        local = {
            a.arg
            for a in (
                *fn.args.posonlyargs,
                *fn.args.args,
                *fn.args.kwonlyargs,
            )
        }
        for node in ast.walk(fn):
            if isinstance(node, ast.Global):
                declared.update(node.names)
            elif isinstance(node, ast.Name) and isinstance(
                node.ctx, ast.Store
            ):
                local.add(node.id)
        for node in ast.walk(fn):
            name = None
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _MUTATORS
                and isinstance(node.func.value, ast.Name)
            ):
                name = node.func.value.id
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and isinstance(node.value, ast.Name)
            ):
                name = node.value.id
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "globals"
            ):
                found["globals()"] = node
            if name in bound and (name in declared or name not in local):
                found[name] = bound[name]
        for name in declared & bound.keys():
            found[name] = bound[name]
    return found


def _declared(node: ast.AST) -> bool:
    """Whether the binding *node* goes through `per_process`."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return any(
            _decorator_name(d) == "per_process" for d in node.decorator_list
        )
    value = getattr(node, "value", None)
    return (
        isinstance(value, ast.Call)
        and _decorator_name(value.func) == "per_process"
    )


def _memos() -> "dict[tuple[str, str], ast.AST]":
    out = {}
    for module, path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, node in _scan(tree).items():
            out[(module, name)] = node
    return out


_MEMOS = _memos()
_DECLARED = sorted(k for k, node in _MEMOS.items() if _declared(node))


def test_the_scan_is_armed():
    """It finds the memos gh-2244 was about, now declared -- and the
    warning set they became. A scan that found nothing would pass every
    other test here."""
    assert ("_report", "_SAID") in _DECLARED
    assert ("_incpath", "_CFG_CACHE") in _DECLARED
    assert ("_object", "_MEMBER_DOC_CACHE") in _DECLARED
    assert ("_pythons", "_jm_metadata") in _MEMOS
    assert ("_undo", "_active") in _MEMOS


def test_the_scan_finds_each_shape():
    """A mutated set, a subscript store, a ``global`` and a cache are each
    found; a local of the same name and import-time construction are not."""
    tree = ast.parse(
        "import functools\n"
        "SAID = set()\nTABLE = {}\nCOUNT = 0\nBUILT = []\nSHADOW = set()\n"
        "BUILT.append(1)\n"
        "def a(x):\n    SAID.add(x)\n"
        "def b(k):\n    TABLE[k] = 1\n"
        "def c():\n    global COUNT\n    COUNT += 1\n"
        "def d():\n    SHADOW = set()\n    SHADOW.add(1)\n"
        "def g(v):\n    globals()['LAZY'] = v\n"
        "@functools.lru_cache(maxsize=None)\ndef e():\n    return 1\n"
    )
    assert sorted(_scan(tree)) == ["COUNT", "SAID", "TABLE", "e", "globals()"]


def test_every_process_memo_is_declared_or_explained():
    undeclared = sorted(
        k
        for k, node in _MEMOS.items()
        if not _declared(node) and k not in _NOT_PER_PROCESS
    )
    assert not undeclared, (
        f"module state a function changes, neither declared per process "
        f"nor explained: {undeclared}. If a new jm process would start it "
        f"empty, declare it -- `x = per_process(set())` from "
        f"`just_makeit._perprocess` -- so `run_cli` empties it between "
        f"commands (gh-2244). Otherwise add it to `_NOT_PER_PROCESS` here "
        f"with why a new process would see the same thing."
    )
    stale = sorted(
        k for k in _NOT_PER_PROCESS if k not in _MEMOS or _declared(_MEMOS[k])
    )
    assert not stale, f"_NOT_PER_PROCESS names what is not there: {stale}"


def _import(module: str):
    """The module `_modules` named *module*, a package by its ``__init__``."""
    dotted = f"just_makeit.{module}".removesuffix(".__init__")
    return importlib.import_module(dotted)


def _fill(state) -> None:
    if isinstance(state, dict):
        state["gh-2244 sentinel"] = None
    elif isinstance(state, set):
        state.add("gh-2244 sentinel")
    elif isinstance(state, list):
        state.append("gh-2244 sentinel")
    else:
        pytest.fail(f"no way to fill a {type(state).__name__}: add one")


def _ids(keys) -> "list[str]":
    return [f"{module}.{name}" for module, name in keys]


@pytest.mark.parametrize("module,name", _DECLARED, ids=_ids(_DECLARED))
def test_run_cli_starts_each_declared_memo_empty(module, name, tmp_path):
    state = getattr(_import(module), name)
    _fill(state)
    assert run_cli("--version", cwd=tmp_path).returncode == 0
    assert not state, f"{module}.{name} survived into the next command"


_SCOPED = sorted(k for k, why in _NOT_PER_PROCESS.items() if why == SCOPED)


#: Commands that together reach every scope a SCOPED row is reset by.
#: `_undo.guard` resets `_undo._active` and `_undo._awaiting_root`: a
#: creating command refused before it writes, one that succeeds, and a
#: mutating one that succeeds each leave it a different way. `apply`'s
#: replay context managers reset the rest (`_config._DEFERRED`,
#: `_REPLAY_GRAPH`, `_SCRATCH_WRITES`, `_object._DEFERRED_REGEN`). A
#: command that opens neither (`--version`) checks nothing (gh-2852).
#: Each row: id, the commands in order, the last one's exit code, and a
#: phrase its stderr must hold (so a refusal is the expected one).
_SCOPED_RUNS = (
    (
        "refused new",
        (("new", "p"), ("new", "p")),
        1,
        "already exists and is not empty",
    ),
    ("new", (("new", "p"),), 0, ""),
    ("mutating", (("new", "p"), ("object", "g")), 0, ""),
    ("apply", (("new", "p"), ("object", "g"), ("apply",)), 0, ""),
)


@pytest.mark.parametrize(
    "runs,last_rc,says",
    [(r, rc, says) for _, r, rc, says in _SCOPED_RUNS],
    ids=[i for i, _, _, _ in _SCOPED_RUNS],
)
@pytest.mark.parametrize("module,name", _SCOPED, ids=_ids(_SCOPED))
def test_scoped_state_is_put_back_by_a_command(
    module, name, tmp_path, runs, last_rc, says
):
    """What `SCOPED` claims: after a command, the initial value is back --
    after each way out of each scope that resets a row, not only after a
    command that enters none of them."""
    node = _MEMOS[(module, name)]
    initial = ast.literal_eval(node.value)

    def cwd(argv: "tuple[str, ...]") -> Path:
        # `new` runs beside the project; every other verb runs inside it.
        return tmp_path if argv[0] == "new" else tmp_path / "p"

    *setup, last = runs
    for argv in setup:
        r = run_cli(*argv, cwd=cwd(argv))
        assert r.returncode == 0, (argv, r.stdout + r.stderr)
    r = run_cli(*last, cwd=cwd(last))
    assert r.returncode == last_rc, (last, r.stdout + r.stderr)
    assert says in r.stderr, (last, r.stderr)
    mod = _import(module)
    assert getattr(mod, name) == initial


_NOTICE = "no longer does anything"


def _windows_project(tmp_path: Path) -> Path:
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    manifest = root / "just-makeit.toml"
    text = manifest.read_text(encoding="utf-8")
    assert text.count("[project]\n") == 1
    manifest.write_text(
        text.replace(
            "[project]\n", '[project]\nplatforms = ["linux", "windows"]\n'
        ),
        encoding="utf-8",
    )
    return root


def test_a_warning_one_call_printed_the_next_prints_again(tmp_path):
    """The issue's contract: every call reads like a fresh ``jm``."""
    root = _windows_project(tmp_path)
    first = run_cli("status", cwd=root)
    second = run_cli("status", cwd=root)
    assert _NOTICE in first.stderr, first.stdout + first.stderr
    assert _NOTICE in second.stderr, second.stdout + second.stderr


def test_one_command_still_says_it_once(tmp_path):
    """Within one process the memory is kept: `apply` loads the manifest
    for the real tree and again for its temp scaffold."""
    root = _windows_project(tmp_path)
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (r.stdout + r.stderr).count(_NOTICE) == 1, r.stdout + r.stderr
