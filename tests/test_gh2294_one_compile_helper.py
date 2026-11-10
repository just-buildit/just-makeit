"""gh-2294: a test compiles C through `tests/_cbuild.py`, and nowhere else.

A test that builds jm's output without CMake wrote its compiler command by
hand, in the ``cc`` dialect: ``-shared`` (``-bundle`` on macOS),
``-fPIC``, ``-std=c11``, ``-I``, ``-o``. clang-cl, the compiler a Windows
project builds with, takes none of the structural ones, so not one of
those fixtures could run on Windows -- and none did: the only Windows job
ran the bundled examples, never this suite. gh-2283's composer doc faces
and gh-2292's iterator-dealloc probe had their Windows evidence from
doppler's CI, not jm's.

`_cbuild.build` speaks both dialects; every fixture states what it builds
and the helper spells it. Two arms keep it the only spelling, read from
every test file's AST so a new file is covered the day it exists, and a
docstring or comment that quotes a flag is not a finding:

- **a structural flag** -- ``-shared``, ``-bundle``, ``-fPIC`` -- as a
  string in code. Each means "I am writing a link line by hand".
- **a compiler run**: a list whose first element is a compiler -- a name
  bound from a `_compilers` / `_cbuild` lookup, the lookup itself, or a
  compiler's name handed to ``subprocess`` -- other than a query
  (``--version``, ``-print-file-name=``).

The exceptions are counted, so a new run in an excepted file is still a
finding, and each says why the compiler is named on purpose: the two
example tests that run their README's ``gcc`` line (gh-1976), and the pkg-config
consumer tests, whose subject is the ``cc $(pkg-config ...)`` line
docs/c-library.md hands a reader -- cc dialect by construction, because
pkg-config prints cc flags.

The second half of the issue is the Windows leg: ci.yml's
`examples-windows` job runs ``make test-compiled``, which runs every file
that imports the helper (the Makefile derives the list; nothing names a
file). `test_windows_job_runs_the_compiled_tests` refuses that step going
away.

GATE: a test builds C or C++ through `_cbuild.build`, and the Windows CI
      job runs every test that does.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest
from test_gh1648_exports_run_on_windows import CI_YML, _job_block

TESTS = Path(__file__).parent
ROOT = TESTS.parent

#: The one module allowed to spell a compiler command, and the one that
#: answers which compiler (its ASan probe runs ``cc -print-file-name``).
HELPERS = {"_cbuild.py", "_compilers.py"}

#: Flags that only a hand-written compile or link line carries.
STRUCTURAL = frozenset({"-shared", "-bundle", "-fPIC", "-fpic"})

#: Compiler names, as a string handed to ``subprocess``.
COMPILER_NAMES = frozenset(
    {"cc", "gcc", "clang", "c++", "g++", "clang++", "clang-cl", "cl"}
)

#: The calls that answer "which compiler" (`_compilers`, `_cbuild`).
LOOKUPS = frozenset(
    {"default_cc", "default_cxx", "find_compiler", "compiler_for"}
)

#: Arguments that ask a compiler about itself rather than build anything.
_QUERY = re.compile(r"^(--version|-dump\w+|-print-[\w-]+(=.*)?|-v)$")

#: The example tests that run their README's ``gcc`` line step by step
#: (gh-1976; `test_gh1931_shared_test_helpers` excepts their lookups).
_README_RUNS_GCC = {
    "test_example_fir_filter.py": 1,
    "test_example_running_stats.py": 1,
}

#: Files that run the pkg-config consumer line docs/c-library.md gives a
#: reader, ``cc $(pkg-config --cflags --libs <pkg>) ...``, by count.
_PKG_CONFIG_CONSUMERS = {
    "test_gh1452_consumer_links_installed_tree.py": 2,
    "test_gh1573_pc_requires_private.py": 1,
    "test_gh1576_dep_usage_both_faces.py": 1,
    "test_gh1584_consumer_matrix.py": 1,
    "test_gh1599_public_flags.py": 1,
}

#: Every counted exception: the file, and the compiler runs it may hold.
EXCEPTIONS = {**_README_RUNS_GCC, **_PKG_CONFIG_CONSUMERS}

SUBPROCESS_CALLS = frozenset(
    {"run", "call", "check_call", "check_output", "Popen", "_run"}
)


def _callee(n: ast.AST) -> "str | None":
    """The name a call calls: ``f`` for ``f(...)`` and ``m.f(...)``."""
    f = n.func if isinstance(n, ast.Call) else None
    if isinstance(f, ast.Attribute):
        return f.attr
    return f.id if isinstance(f, ast.Name) else None


def _is_lookup(n: ast.AST) -> bool:
    """A lookup call, or an expression with one in it (``a() or b()``)."""
    return any(_callee(x) in LOOKUPS for x in ast.walk(n))


def _compiler_names(tree: ast.Module) -> "set[str]":
    """Names this module binds to a compiler lookup's answer."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value:
            if _is_lookup(node.value):
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                )
                out |= {t.id for t in targets if isinstance(t, ast.Name)}
    return out


def _argvs(tree: ast.Module) -> "set[int]":
    """ids of the list literals handed to ``subprocess`` as argv."""
    return {
        id(node.args[0])
        for node in ast.walk(tree)
        if _callee(node) in SUBPROCESS_CALLS
        and node.args
        and isinstance(node.args[0], (ast.List, ast.Tuple))
    }


def _has_option(seq: ast.AST) -> bool:
    """Whether a list after its head holds a ``-`` option: a command, not
    a list of names (``parametrize("cc", ["gcc", "clang"])``)."""
    return any(
        isinstance(e, ast.Constant)
        and isinstance(e.value, str)
        and e.value.startswith("-")
        for e in seq.elts[1:]
    )


def _is_query(seq: ast.AST) -> bool:
    rest = seq.elts[1:]
    return bool(rest) and all(
        isinstance(e, ast.Constant)
        and isinstance(e.value, str)
        and _QUERY.match(e.value)
        for e in rest
    )


def compiler_runs(tree: ast.Module) -> "list[int]":
    """Lines of a list that runs a compiler: what a hand-written compile is.

    Its first element is a compiler -- a name bound from a lookup, a
    lookup call, or a compiler's name -- and the list is a command:
    ``subprocess``'s argv, or one carrying a ``-`` option. A list that
    merely holds a compiler (``(gcc, env)``, a parametrize list) is not
    one, and neither is a query (``[cc, "--version"]``).
    """
    names = _compiler_names(tree)
    argvs = _argvs(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
            continue
        head = node.elts[0]
        compiler = (
            (isinstance(head, ast.Name) and head.id in names)
            or (isinstance(head, ast.Call) and _is_lookup(head))
            or (
                isinstance(head, ast.Constant) and head.value in COMPILER_NAMES
            )
        )
        hit = compiler and (id(node) in argvs or _has_option(node))
        if hit and not _is_query(node):
            out.append(node.lineno)
    return sorted(out)


def structural_flags(tree: ast.Module) -> "list[int]":
    """Lines whose code spells a flag only a hand-written link line has."""
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in STRUCTURAL
    )


def _test_files() -> "list[Path]":
    me = Path(__file__).name
    return sorted(
        p
        for p in TESTS.rglob("*.py")
        if p.name not in HELPERS and p.name != me
    )


def _findings() -> "dict[str, tuple[list[int], list[int]]]":
    out = {}
    for path in _test_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        runs, flags = compiler_runs(tree), structural_flags(tree)
        if runs or flags:
            out[path.relative_to(TESTS).as_posix()] = (runs, flags)
    return out


def test_no_hand_written_compile_outside_the_helper():
    """Every compile and link line in the suite is `_cbuild`'s."""
    bad = []
    for rel, (runs, flags) in sorted(_findings().items()):
        allowed = EXCEPTIONS.get(rel, 0)
        if flags or len(runs) != allowed:
            bad.append(f"{rel}: compiler runs at {runs} (allowed "
                       f"{allowed}), structural flags at {flags}")  # fmt: skip
    assert not bad, (
        "build C through tests/_cbuild.py (gh-2294) -- it speaks clang-cl "
        "as well as cc:\n  " + "\n  ".join(bad)
    )


def test_every_exception_still_runs_its_compiler():
    """A counted exception is exact: one no longer needed is removed."""
    found = _findings()
    stale = {
        rel: n
        for rel, n in EXCEPTIONS.items()
        if len(found.get(rel, ([], []))[0]) != n
    }
    assert not stale, f"exceptions whose count no longer holds: {stale}"


@pytest.mark.parametrize(
    "src, runs, flags",
    [
        ("import subprocess\nsubprocess.run(['cc', 'a.c'])\n", [2], []),
        ("cmd = ['gcc', '-O2', 'demo.c']\n", [1], []),
        ("_CC = default_cc()\nx = [_CC, '-c', 'a.c']\n", [2], []),
        ("x = [default_cxx(), '-c', 'a.cpp']\n", [1], []),
        (
            "_CC = a() or find_compiler('clang')\ny = (_CC, '-c', 'a.c')\n",
            [2],
            [],
        ),
        ("link = ['-shared']\n", [], [1]),
        # Not findings: a query, a parametrize list, a docstring.
        ("_CC = default_cc()\nsubprocess.run([_CC, '--version'])\n", [], []),
        ("_CC = default_cc()\nx = [_CC, '-print-file-name=a.so']\n", [], []),
        ("pytest.mark.parametrize('cc', ['gcc', 'clang'])\n", [], []),
        ("gcc = find_compiler('gcc')\nx = (gcc, env)\n", [], []),
        ('"""Pass -shared to cc."""\n', [], []),
    ],
)
def test_the_detector_sees_each_shape(src, runs, flags):
    """Each shape the hand-written compiles took, and each it must not
    mistake for one."""
    tree = ast.parse(src)
    assert (compiler_runs(tree), structural_flags(tree)) == (runs, flags)


def _compiled_tests() -> "list[str]":
    """The files ``make test-compiled`` runs, as make derives them."""
    r = subprocess.run(
        # The rule arrives on stdin, not through --eval: macOS ships GNU
        # make 3.81, which has no --eval.
        ["make", "-s", "-f", "Makefile", "-f", "-", "_jm-compiled"],
        input="_jm-compiled: ; @echo $(COMPILED_TESTS)\n",
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


def test_compiled_tests_are_every_helper_user():
    """`make test-compiled` runs the files that import `_cbuild`, minus the
    project-env ones, which have their own Windows line."""
    users = sorted(
        p.relative_to(ROOT).as_posix()
        for p in TESTS.glob("test_*.py")
        if re.search(
            r"^(?:import _cbuild\b|from _cbuild import )",
            p.read_text(encoding="utf-8"),
            re.M,
        )
    )
    compiled = _compiled_tests()
    assert len(compiled) >= 20, compiled
    assert set(compiled) <= set(users), set(compiled) - set(users)


def test_windows_job_runs_the_compiled_tests():
    """The Windows job runs ``make test-compiled`` (gh-2294)."""
    block = _job_block(CI_YML.read_text(encoding="utf-8"), "examples-windows")
    assert re.search(r"^\s+run: make test-compiled\b", block, re.M), block
