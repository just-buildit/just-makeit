"""gh-2642: a test run that collects nothing is a failure, everywhere in jm.

pytest exits 5 when it collects nothing. ``jm test`` and the examples gate
(`run_generated_pytest`) both read that as a pass, for gh-1950's
functions-only project, which had no Python test. Since gh-2127 (a contract
test per functions-only module) and gh-2640 (``test_project.py`` in every
project) that shape is gone, so an empty run means the tests were lost.
unittest's empty run before Python 3.12 exits 0, the same pass by another
spelling, in ``jm test``'s fallback.

`_testrun` is now the one place a pytest or unittest command line is built
and its exit read, and each caller says whether an empty collection passes.
`TestOneRunner` refuses a command line spelled anywhere else, derived from
the source, so a new runner cannot skip it.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli  # noqa: E402

from just_makeit import _build, _testrun  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "src" / "just_makeit"
_HELPER = _PKG / "_testrun.py"


def _empty_project(tmp_path: Path, *args: str) -> Path:
    """`jm new p`, its one test deleted: the issue's trigger."""
    r = run_cli("new", "p", *args, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    smoke = root / "src" / "p" / "tests" / "test_project.py"
    assert smoke.is_file(), "jm new no longer writes the smoke test"
    smoke.unlink()
    return root


class TestAnEmptyRunFails:
    """``run_generated_pytest``'s half is in
    ``tests/test_gh1089_generated_pytest_gate.py``, beside its other cases."""

    def test_jm_test_fails_when_pytest_collects_nothing(self, tmp_path):
        """The issue's trigger: on main this exits 0 and prints
        ``pytest collected no tests ... passing.``"""
        root = _empty_project(tmp_path, "--pytest")
        if not _build._has_pytest(_build._project_python(root)):
            pytest.fail("the project's interpreter has no pytest")

        r = run_cli("test", cwd=root)

        assert r.returncode != 0, r.stdout + r.stderr
        assert "pytest ran no tests" in r.stderr, r.stderr

    def test_jm_test_fails_when_unittest_runs_nothing(
        self, tmp_path, monkeypatch
    ):
        """The fallback, on every Python: before 3.12 ``unittest discover``
        exits 0 on an empty run, so this passed there on main."""
        root = _empty_project(tmp_path)
        monkeypatch.setattr(_build, "_has_pytest", lambda python: False)

        r = run_cli("test", cwd=root)

        assert r.returncode != 0, r.stdout + r.stderr
        assert "unittest ran no tests" in r.stderr, r.stderr

    def test_the_unittest_runner_reads_an_empty_run_as_empty(self, tmp_path):
        """`UNITTEST_SNIPPET` exits 5 on the interpreter running this suite,
        whichever it is, and 0 once a test exists."""
        start = tmp_path / "src"
        start.mkdir()
        run = _testrun.unittest(
            sys.executable, str(start), empty_passes=False,
            capture_output=True,
        )  # fmt: skip
        assert run.collected_nothing and not run.passed, run.proc
        (start / "test_one.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_it(self):\n        pass\n",
            encoding="utf-8",
        )
        run = _testrun.unittest(
            sys.executable, str(start), empty_passes=False,
            capture_output=True,
        )  # fmt: skip
        assert run.passed, run.proc


class TestBenchStillPassesNoBenchmarks:
    """The one caller that answers yes: a project may ship only C
    benchmarks, so ``jm bench`` reads an empty collection as no Python
    benchmarks rather than a failure."""

    def test_an_empty_listing_is_no_files(self, tmp_path):
        from just_makeit import _bench

        (tmp_path / "src").mkdir()
        assert _bench._python_bench_files(tmp_path, sys.executable) == []


def _runner_lines(node: ast.AST) -> "list[str]":
    """What a list or tuple literal runs, when it is a test command line.

    ``-m pytest`` / ``-m unittest`` adjacent, or ``pytest`` as the program
    of a ``uv run`` prefix (not the value of a ``--with``, which installs
    it). Starred and computed elements are not words, so the helper's own
    ``[*launcher, "pytest", *args]`` is the one shape it does not read; the
    helper's file is excluded by path below instead.
    """
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    words = [
        e.value
        if isinstance(e, ast.Constant) and isinstance(e.value, str)
        else None
        for e in node.elts
    ]
    found = []
    for i, word in enumerate(words):
        prev = words[i - 1] if i else None
        if word in ("pytest", "unittest") and prev == "-m":
            found.append(f"-m {word}")
        elif (
            word == "pytest"
            and words[:2] == ["uv", "run"]
            and prev != "--with"
        ):
            found.append("uv run ... pytest")
    return found


def _sources() -> "list[Path]":
    """Every Python file jm ships or runs as a tool, but the helper.

    The package (not its templates, which are the generated project's
    code, nor the bundled examples), ``docker/`` and ``scripts/``.
    """
    pkg = [
        p
        for p in _PKG.rglob("*.py")
        if not {"templates", "examples"} & set(p.relative_to(_PKG).parts)
    ]
    tools = [
        *(_REPO / "docker").rglob("*.py"),
        *(_REPO / "scripts").rglob("*.py"),
    ]
    return [p for p in pkg + tools if p != _HELPER]


class TestOneRunner:
    def test_no_test_command_line_is_spelled_outside_the_helper(self):
        """A pytest or unittest command line outside `_testrun` reads its
        own exit code, which is how exit 5 was a pass in two places."""
        offenders = []
        for path in _sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                for what in _runner_lines(node):
                    rel = path.relative_to(_REPO)
                    offenders.append(f"{rel}:{node.lineno}: {what}")
        assert not offenders, (
            "run pytest / unittest through just_makeit._testrun, which says "
            "whether an empty collection passes:\n" + "\n".join(offenders)
        )

    def test_the_detector_reads_every_spelling_it_names(self):
        """Armed: each shape the detector claims to read is read, and the
        dependency spellings it must not read are not."""

        def lines(src: str) -> "list[str]":
            return _runner_lines(ast.parse(src, mode="eval").body)

        assert lines('[py, "-m", "pytest", "src/"]') == ["-m pytest"]
        assert lines('(py, "-m", "unittest", "discover")') == ["-m unittest"]
        assert lines('["uv", "run", "--with", "x", "pytest", "-v"]') == [
            "uv run ... pytest"
        ]
        assert lines('["uv", "run", "--with", "pytest", "python"]') == []
        assert lines('[py, "-m", "pip", "install", "pytest"]') == []

    def test_the_sources_hold_the_runners_this_issue_moved(self):
        """The scan reaches the files that held runners before the fix,
        so a scan over the wrong tree cannot pass by finding nothing."""
        rels = {p.relative_to(_REPO).as_posix() for p in _sources()}
        for held in (
            "src/just_makeit/_build.py",
            "src/just_makeit/_bench.py",
            "src/just_makeit/_scripts.py",
            "docker/build_examples.py",
        ):
            assert held in rels, held

    def test_every_caller_answers_empty_passes(self):
        """`empty_passes` is keyword-only with no default, so a call that
        does not state it is a TypeError, not a silent choice."""
        with pytest.raises(TypeError):
            _testrun.pytest([sys.executable, "-m"], ["--version"])
        done = subprocess.CompletedProcess([], _testrun.NO_TESTS_COLLECTED)
        assert not _testrun.Outcome(done, empty_passes=False).passed
