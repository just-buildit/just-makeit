"""gh-2597: jm's CLI is run in-process by ONE function.

Four in-process runners had grown: ``tests/_jmrun.run_cli``, an ``_jm`` in
the ``enum_constants`` example, ``_Log.jm`` in ``stale_project`` and
``run_jm`` in ``visual_studio``. They had drifted already: only the first
cleared jm's manifest cache before a command (gh-2095) and captured output
at the FILE DESCRIPTOR (gh-1374), so in the other three a cmake, ctest or
pytest child's output went straight past the capture, and an assertion
that something was absent would have looked at nothing.

``just_makeit._example.run_cli`` is now the one runner -- in the package,
because the wheel ships the examples and they call it -- and
``tests/_jmrun.run_cli`` adapts it. This file refuses a call to
``just_makeit._cli.main`` anywhere else in ``src/`` or ``tests/``, read off
the syntax tree: every spelling that imports ``main`` or ``_cli`` and calls
it. A file whose SUBJECT is ``main()`` itself -- what it does with argv,
with a monkeypatched command, with an exception -- is allow-listed below
with the reason, and the allow-list is held to files that still call it.

(A ``main()`` run in a CHILD process is ``test_gh1374_cli_in_process.py``'s
gate; this one is about this process.)

GATE: no ``just_makeit._cli.main`` call outside ``_example.run_cli`` and
      the allow-listed files.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "just_makeit"
TESTS = ROOT / "tests"

#: The one place a CLI call belongs.
RUNNER = SRC / "_example.py"

#: Files whose subject is ``main()`` itself, so they call it directly.
CALLS_MAIN = {
    "tests/test_cli_dispatch.py": (
        "unit tests of main()'s dispatch: each patches one command's run()"
        " and asserts main() routed argv to it, or raised SystemExit"
        " (pytest.raises) for a bad command line"
    ),
    "tests/test_cli_extra.py": (
        "unit tests of main()'s own branches -- colour, schema warning,"
        " argument errors -- asserting the SystemExit it raises and what"
        " capsys saw, with commands patched"
    ),
}


def _main_calls(source: str) -> list[int]:
    """Line numbers where *source* calls ``just_makeit._cli.main``.

    Every spelling: ``main`` imported from the ``just_makeit._cli`` module
    (aliased or not) then called; ``from just_makeit import _cli [as y]``
    or ``import
    just_makeit._cli as y`` then ``y.main()``; and the dotted
    ``just_makeit._cli.main()``. An import alone is not a call.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "just_makeit._cli":
                names |= {
                    a.asname or a.name for a in node.names if a.name == "main"
                }
            elif node.module == "just_makeit":
                modules |= {
                    a.asname or a.name for a in node.names if a.name == "_cli"
                }
        elif isinstance(node, ast.Import):
            modules |= {
                a.asname
                for a in node.names
                if a.name == "just_makeit._cli" and a.asname
            }
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in names:
            lines.append(node.lineno)
        elif isinstance(f, ast.Attribute) and f.attr == "main":
            if isinstance(f.value, ast.Name) and f.value.id in modules:
                lines.append(node.lineno)
            elif ast.unparse(f.value) == "just_makeit._cli":
                lines.append(node.lineno)
    return sorted(lines)


#: Python templates are rendered into a user's project, which has no jm CLI
#: to call, and are not valid Python until their ``<<slots>>`` are filled.
TEMPLATES = SRC / "templates"


def _files() -> list[Path]:
    files = [
        p
        for p in sorted(SRC.rglob("*.py")) + sorted(TESTS.rglob("*.py"))
        if TEMPLATES not in p.parents
    ]
    # A glob that finds nothing would make this file vacuous.
    assert len(files) > 300, f"only {len(files)} files under src/ and tests/"
    return files


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


@pytest.mark.parametrize("path", _files(), ids=_rel)
def test_main_is_called_only_by_the_one_runner(path):
    if path == RUNNER or _rel(path) in CALLS_MAIN:
        return
    lines = _main_calls(path.read_text(encoding="utf-8"))
    assert not lines, (
        f"{_rel(path)} calls just_makeit._cli.main at line(s) {lines}. Run"
        " jm through just_makeit._example.run_cli / run_jm (tests:"
        " `from _jmrun import run_cli`), the one runner that clears the"
        " manifest cache and captures at the file descriptor (gh-2597). If"
        " the file's subject is main() itself, add it to CALLS_MAIN here"
        " with the reason."
    )


def test_the_runner_still_calls_main():
    """The exemption is only right while the runner is where the call is."""
    assert _main_calls(RUNNER.read_text(encoding="utf-8"))


@pytest.mark.parametrize("rel,reason", sorted(CALLS_MAIN.items()))
def test_each_allow_listed_file_still_calls_main(rel, reason):
    """An exemption that no longer applies is a licence nobody revoked."""
    path = ROOT / rel
    assert path.exists(), f"{rel} is allow-listed but does not exist"
    assert _main_calls(path.read_text(encoding="utf-8")), (
        f"{rel} no longer calls main() -- drop it from CALLS_MAIN"
    )
    assert reason.strip(), f"{rel} must say why"


def test_the_detector_reads_every_spelling():
    """Built from parts, so the literals in CALLS_MAIN cannot satisfy it."""
    mod = "just_makeit." + "_cli"
    assert _main_calls(f"from {mod} import main\nmain()\n") == [2]
    assert _main_calls(f"from {mod} import main as m\nm()\n") == [2]
    assert _main_calls("from just_makeit import _cli\n_cli.main()\n") == [2]
    assert _main_calls(f"import {mod} as c\nc.main()\n") == [2]
    assert _main_calls(f"import {mod}\n{mod}.main()\n") == [2]
    # An import alone, and another module's main, are not calls of it.
    assert _main_calls(f"from {mod} import main\n") == []
    assert _main_calls("from other import main\nmain()\n") == []


def _child_prints(monkeypatch, stream: str) -> None:
    """Make ``jm example`` run a CHILD that writes to *stream*'s fd."""
    import subprocess
    import sys

    from just_makeit import _example

    code = f"import sys; sys.{stream}.write('from a child\\n')"
    monkeypatch.setattr(
        _example,
        "run",
        lambda name: subprocess.run([sys.executable, "-c", code], check=True),
    )


def test_a_childs_output_is_in_the_result(monkeypatch):
    """jm's cmake / ctest / pytest children write to fd 1 and fd 2.

    ``redirect_stdout`` alone misses them (gh-1374): the line would go to
    the terminal, and an assertion that something is absent from the
    result would pass having looked at nothing.
    """
    from just_makeit._example import run_cli

    _child_prints(monkeypatch, "stdout")
    r = run_cli("example", "x")
    assert r.returncode == 0, r.stderr
    assert "from a child" in r.stdout

    _child_prints(monkeypatch, "stderr")
    r = run_cli("example", "x")
    assert "from a child" in r.stderr and "from a child" not in r.stdout


def test_merged_output_holds_both_streams(monkeypatch):
    """`run_jm`'s one stream: a child's stderr lands in it too."""
    from just_makeit._example import run_jm

    _child_prints(monkeypatch, "stderr")
    assert "from a child" in run_jm(".", "example", "x")
