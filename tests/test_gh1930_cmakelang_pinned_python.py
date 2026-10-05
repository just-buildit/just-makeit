"""gh-1930: cmakelang runs under a pinned Python, and a crash reads as one.

CPython 3.15 refuses capturing groups in ``re.Scanner``, and cmakelang's
last release builds its lexer exactly that way, so under 3.15 every
cmake-lint and cmake-format run dies on its first file (exit 2, a
traceback on stderr). The allowed-to-fail 3.15 leg showed it as
``cmake-lint found violations in generated project`` over an EMPTY list:
the ``stale_project`` golden ran the cmake-lint of the Python under test,
and printed stdout alone.

The repair, held here:

- **One declaration.** The Makefile's ``CMAKELANG`` runs cmakelang under
  ``CMAKELANG_PYTHON``, with ``--no-project`` (a ``--python`` in project mode
  replaces ``.venv``) and at the version pyproject.toml's dev group pins --
  read from there, written nowhere else. ``CMAKE_FORMAT`` and ``CMAKE_LINT``
  are that prefix plus the tool, and the recipes that run the tests hand
  ``CMAKE_LINT`` over in the environment, as consumer-smoke is handed ``JM``
  (gh-1625).
- **One reader.** ``tests/_cmakelint.py`` is the only way a test runs
  cmake-lint. The two copies it replaced each ran a bare ``cmake-lint`` from
  PATH, and one returned quietly when there was none. No test may name the
  tool as a program again.
- **A crash is not a finding.** cmake-lint exits 1 for findings and 2 for an
  internal error; the reader says which, and always shows stderr.

GATE: cmakelang runs under the Makefile's CMAKELANG_PYTHON at
      pyproject.toml's pin, the one command the lint recipe and the tests'
      reader both use; a cmake-lint crash is reported as a crash, never as
      findings.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import _cmakelint

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
#: The tools cmakelang installs.
TOOLS = ("cmake-lint", "cmake-format")


@pytest.fixture(autouse=True)
def _project_env_elsewhere(tmp_path, monkeypatch):
    """Point any PROJECT env uv would build at a scratch path.

    The cmakelang command must never touch the project env: with a
    ``--python`` and no ``--no-project``, uv rebuilds ``.venv`` under that
    interpreter. A regression -- or a sabotage proving this file -- must not
    be able to do that to the checkout running the suite, and the scratch
    path is how the pinned-Python test sees it happen.
    """
    env = tmp_path / "project-env"
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(env))
    return env


def _make_value(var: str) -> str:
    """*var* exactly as make expands it from this repo's makefiles.

    ``$(info)`` rather than ``echo``: the value carries shell quoting, which
    an ``echo`` would consume. The rule arrives on stdin, not ``--eval``:
    macOS ships GNU make 3.81, which has no ``--eval``.
    """
    r = subprocess.run(
        [
            "make",
            "-s",
            "--no-print-directory",
            "-f",
            "Makefile",
            "-f",
            "-",
            "_jm-value",
        ],
        input=f"_jm-value: ; @: $(info $({var}))\n",
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    value = r.stdout.rstrip("\n")
    assert value, f"make expands {var} to nothing"
    return value


def _pyproject_pin() -> str:
    """The cmakelang version the dev group pins, exactly."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    reqs = [
        r
        for r in data["dependency-groups"]["dev"]
        if isinstance(r, str) and re.match(r"cmakelang\b", r)
    ]
    assert len(reqs) == 1, f"the dev group pins cmakelang {len(reqs)}x"
    m = re.fullmatch(r"cmakelang==([\w.]+)", reqs[0])
    assert m, (
        f"{reqs[0]!r} is not an exact pin, which the Makefile's "
        "CMAKELANG_VERSION reads"
    )
    return m.group(1)


# ── one declaration ─────────────────────────────────────────────────────────


def test_the_version_is_pyprojects_and_no_makefile_restates_it():
    pin = _pyproject_pin()
    argv = shlex.split(_make_value("CMAKELANG"))
    assert f"cmakelang[yaml]=={pin}" in argv, (
        f"CMAKELANG does not install pyproject.toml's cmakelang {pin} with "
        f"its yaml extra:\n{argv}"
    )
    makefiles = [ROOT / "Makefile", *sorted(ROOT.glob("*.mk"))]
    restated = [p.name for p in makefiles if pin in p.read_text("utf-8")]
    assert not restated, (
        f"{restated} restate cmakelang's version {pin}: read it from "
        "pyproject.toml, as CMAKELANG_VERSION does, or the two drift"
    )


def test_both_tools_are_the_one_prefix():
    prefix = _make_value("CMAKELANG")
    for var, tool in (("CMAKE_LINT", TOOLS[0]), ("CMAKE_FORMAT", TOOLS[1])):
        assert _make_value(var) == f"{prefix} {tool}", (
            f"{var} is not $(CMAKELANG) {tool}"
        )


def test_cmakelang_runs_under_its_pinned_python_not_the_suites(
    _project_env_elsewhere,
):
    """Asked for the suite's own Python, the command still runs its pin.

    ``UV_PYTHON`` is what a CI leg's setup-uv exports for the Python under
    test (3.15 on the pre-release leg). Run from the repo root, where uv
    finds the project, as the lint recipe and the tests' reader both do --
    and the project env is left alone.
    """
    prefix = shlex.split(_make_value("CMAKELANG"))
    suite = "%d.%d" % sys.version_info[:2]
    probe = (
        "import sys, cmakelang, yaml; "
        "print('%d.%d' % sys.version_info[:2], cmakelang.__version__)"
    )
    r = subprocess.run(
        [*prefix, "python", "-c", probe],
        cwd=ROOT,
        env={**os.environ, "UV_PYTHON": suite},
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, r.stderr
    python, version = r.stdout.split()
    assert python == _make_value("CMAKELANG_PYTHON"), (
        f"cmakelang ran under Python {python}, not CMAKELANG_PYTHON "
        f"(the suite is {suite})"
    )
    assert version == _pyproject_pin()
    assert not _project_env_elsewhere.exists(), (
        "running cmakelang built the PROJECT env: without --no-project, a "
        "--python rebuilds .venv under that interpreter"
    )


def test_cmake_lint_reads_a_file_under_it(tmp_path):
    """The point of the pin: the lexer runs. Under 3.15 this exits 2."""
    cm = tmp_path / "CMakeLists.txt"
    cm.write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(x C)\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [*shlex.split(_make_value("CMAKE_LINT")), "--", str(cm)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, _cmakelint.failure(r, str(cm))


def test_cmake_format_reads_the_repos_yaml_config():
    """Without the yaml extra, cmake-format dies importing ``yaml``.

    The lint recipe formats the CMake templates against
    ``.cmake-format.yaml``; a template it already formatted must check
    clean.
    """
    template = "src/just_makeit/templates/cmake/libm.cmake"
    assert (ROOT / template).is_file(), template
    r = subprocess.run(
        [*shlex.split(_make_value("CMAKE_FORMAT")), "--check", template],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def _helper_users() -> "list[str]":
    """The test files that run cmake-lint, by the import that does it.

    Not this one: it drives the reader with stand-ins of its own.
    """
    return sorted(
        p.relative_to(ROOT).as_posix()
        for p in TESTS.glob("test_*.py")
        if p.name != Path(__file__).name
        and re.search(r"^import _cmakelint$", p.read_text("utf-8"), re.M)
    )


@pytest.mark.parametrize("target", ["test", "test-examples"])
def test_the_recipe_hands_its_tests_make_s_cmake_lint(target):
    """Every pytest a recipe runs over a cmake-lint user carries CMAKE_LINT.

    Read from the plan make would run, as gh-1625 reads ``JM`` from
    consumer-smoke's.
    """
    users = _helper_users()
    assert "tests/test_examples.py" in users, users
    plan = subprocess.run(
        ["make", "-n", "--no-print-directory", target],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    handed = f"CMAKE_LINT='{_make_value('CMAKE_LINT')}' "
    runs = [
        seg
        for seg in re.split(r"&&|\n", plan)
        if "pytest" in seg and any(u in seg.split() for u in users)
    ]
    assert runs, f"`make {target}` runs no cmake-lint user {users}:\n{plan}"
    bare = [seg.strip()[:120] for seg in runs if handed not in seg]
    assert not bare, (
        f"`make {target}` runs a cmake-lint user without the Makefile's "
        f"CMAKE_LINT, so its reader fails:\n" + "\n".join(bare)
    )


def test_no_test_runs_cmakelang_itself():
    """A bare ``cmake-lint`` is the suite's Python's -- the 3.15 crash.

    Registration-free: every module under tests/ is read, so a new one is
    covered the moment it exists. The names are refused as whole string
    constants, which is how an argv or a ``shutil.which`` spells them.
    """
    own = Path(__file__).name
    bad = []
    for path in sorted(TESTS.glob("*.py")):
        if path.name == own:
            continue
        tree = ast.parse(path.read_text("utf-8"))
        bad += [
            f"{path.relative_to(ROOT)}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value in TOOLS
        ]
    assert not bad, (
        f"{bad} run cmakelang from the Python under test: use "
        "tests/_cmakelint.py, which runs the Makefile's CMAKE_LINT"
    )


# ── a crash is not a finding ────────────────────────────────────────────────

#: What cmake-lint printed on stderr under 3.15, abridged.
_TRACEBACK = (
    "ERROR An internal error occured. Please consider filing a bug report "
    "at github.com/cheshirekow/cmakelang/issues\n"
    "Traceback (most recent call last):\n"
    '  File ".../cmakelang/lex/__init__.py", line 135, in tokenize\n'
    "    scanner = re.Scanner([\n"
    "ValueError: Cannot use capturing groups in re.Scanner\n"
)


def _stand_in(tmp_path: Path, code: int, stderr: str = "") -> str:
    """A cmake-lint that exits *code*, echoing its argv as its findings."""
    script = tmp_path / "cmake_lint_stand_in.py"
    script.write_text(
        "import sys\n"
        f"if {code} == 1:\n"
        "    print('[C0103] Invalid name:', *sys.argv[1:])\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({code})\n",
        encoding="utf-8",
    )
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"


@pytest.fixture
def cmakelists(tmp_path):
    cm = tmp_path / "proj" / "CMakeLists.txt"
    cm.parent.mkdir()
    cm.write_text("project(x C)\n", encoding="utf-8")
    return cm


def test_a_crash_is_reported_as_a_crash(tmp_path, monkeypatch, cmakelists):
    monkeypatch.setenv(_cmakelint.ENV, _stand_in(tmp_path, 2, _TRACEBACK))
    with pytest.raises(AssertionError) as e:
        _cmakelint.check([cmakelists], "generated project")
    msg = str(e.value)
    assert "cmake-lint crashed" in msg, msg
    assert "Cannot use capturing groups in re.Scanner" in msg, msg
    assert "found violations" not in msg, msg


def test_findings_are_reported_as_findings_with_both_streams(
    tmp_path, monkeypatch, cmakelists
):
    monkeypatch.setenv(_cmakelint.ENV, _stand_in(tmp_path, 1, "a warning\n"))
    with pytest.raises(AssertionError) as e:
        _cmakelint.check([cmakelists], "generated project")
    msg = str(e.value)
    assert "found violations in generated project" in msg, msg
    assert "crashed" not in msg, msg
    # The finding (stdout) names the file it was handed, after the disabled
    # formatting codes; stderr is shown too.
    assert f"{' '.join(_cmakelint.DISABLED)} -- {cmakelists}" in msg, msg
    assert "a warning" in msg, msg


def test_a_clean_run_passes(tmp_path, monkeypatch, cmakelists):
    monkeypatch.setenv(_cmakelint.ENV, _stand_in(tmp_path, 0))
    _cmakelint.check([cmakelists], "generated project")


def test_no_cmake_lint_fails_rather_than_passing(monkeypatch, cmakelists):
    """The reader this replaced returned quietly when it found no tool."""
    monkeypatch.delenv(_cmakelint.ENV, raising=False)
    with pytest.raises(pytest.fail.Exception, match="CMAKE_LINT is unset"):
        _cmakelint.check([cmakelists], "generated project")
