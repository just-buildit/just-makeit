"""gh-2172, gh-2816: a CI test leg must run the Python it names.

Each leg that names a Python -- ``test (<os>, <py>)``, ``Examples (<os>,
<py>)``, ``Examples (windows-latest, clang-cl, <py>)`` -- reaches ``make``
with its interpreter given only as ``UV_PYTHON``, which ``astral-sh/setup-uv``
exports from the leg's ``python-version``. Every test run goes through
``uv run``, which honours it. A ``--python`` / ``-p`` pin on that ``uv run``,
a uv default that changes, or a ``.python-version`` file would run every leg
on one interpreter, and the job names and ``CI passed`` would read exactly
as they do now.

Two halves:

* **Runtime** -- `_legpython.mismatch`, asked by ``tests/conftest.py`` at the
  start of every pytest session on CI, so every leg that runs jm's tests is
  checked, whatever files it collects. `test_a_ci_leg_runs_the_python_it_names`
  adds the refusal of a leg with no ``UV_PYTHON`` at all.
* **Static** -- off CI there is no leg, so the runtime half has nothing to
  compare. `test_no_leg_pins_an_interpreter` reads what each leg would RUN:
  the ``make`` targets ``ci.yml`` gives a Python-named job, dry-run with
  ``make -n`` so every variable is expanded (gh-2816: reading the
  ``PYTEST_ISOLATED`` assignment's text missed ``-p`` and a pin spelled in
  ``PYTEST_DEPS``), and refuses a pytest that is launched any way but a
  ``uv run`` with no interpreter pin.

* **setup-python legs** -- artifact.yml's ``Smoke test (<os>, py<ver>)``
  legs take their Python from ``actions/setup-python`` and call it as
  ``python``, ``python3`` and ``pip``, with no ``UV_PYTHON`` and no jm suite.
  Each runs ``tests/_legpython.py <python-version>`` before any step that
  runs Python; `test_every_smoke_leg_asks_before_it_uses_python` refuses a
  Python-named job there that does not.

`.python-version` at the root (tracked, `3.12`) does not redirect a leg: uv
gives `UV_PYTHON` precedence over it, and setup-uv sets `UV_PYTHON` per leg.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from _legpython import mismatch

_REPO = Path(__file__).resolve().parent.parent
_CI = _REPO / ".github" / "workflows" / "ci.yml"


def test_a_ci_leg_runs_the_python_it_names():
    """The suite's own interpreter against the leg's ``UV_PYTHON``."""
    if not os.environ.get("UV_PYTHON"):
        # A leg without UV_PYTHON runs on whatever uv finds: the matrix
        # would be silently one interpreter. Refused on CI, not skipped.
        # Off CI there is no leg to check, and the test passes: a skip
        # here would be unlisted in the allow-list and turn `make test` red.
        assert os.environ.get("GITHUB_ACTIONS") != "true", (
            "GITHUB_ACTIONS is set but UV_PYTHON is not: setup-uv did not "
            "name this leg's Python, so the leg would run uv's default"
        )
    why = mismatch(os.environ.get("UV_PYTHON", ""), tuple(sys.version_info))
    assert why is None, why


@pytest.mark.parametrize(
    "env, running, refused",
    [
        pytest.param({}, (3, 12), False, id="off-ci"),
        pytest.param({"UV_PYTHON": "3.12"}, (3, 12), False, id="agrees"),
        pytest.param({"UV_PYTHON": "3.13.1"}, (3, 13), False, id="patch"),
        pytest.param({"UV_PYTHON": "3.13"}, (3, 12), True, id="pinned"),
        pytest.param({"UV_PYTHON": "3.9"}, (3, 13), True, id="minor-only"),
        pytest.param({"UV_PYTHON": "cpython"}, (3, 12), True, id="no-ver"),
    ],
)
def test_mismatch_reads_each_shape(env, running, refused):
    """Offline, so the comparison is sabotage-provable on any box."""
    assert (mismatch(env.get("UV_PYTHON", ""), running) is not None) is refused


def test_conftest_asks_it_at_every_session_start(tmp_path):
    """The runtime half reaches every leg only through the conftest hook;
    a child session with a mismatched ``UV_PYTHON`` must refuse to run."""
    other = f"3.{sys.version_info.minor + 1}"
    child = tmp_path
    (child / "test_x.py").write_text("def test_x():\n    pass\n", "utf-8")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PYTEST_", "COV_CORE"))
    }
    env.update(
        GITHUB_ACTIONS="true",
        UV_PYTHON=other,
        PYTHONPATH=os.pathsep.join(
            [str(Path(__file__).parent), str(_REPO / "src")]
        ),
    )
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "conftest",
            "-p",
            "no:cacheprovider",
            "-q",
            "--rootdir",
            str(child),
            str(child / "test_x.py"),
        ],
        cwd=child,
        env=env,
        capture_output=True,
        text=True,
    )
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert f"names Python {other}" in out, out


# -- the static half (gh-2816) ---------------------------------------------

#: uv's spellings of an interpreter pin on ``uv run``.
_PIN_FLAGS = ("--python", "-p")


def _leg_targets() -> "list[list[str]]":
    """The ``make`` arguments of every step in a job named for a Python."""
    jobs = yaml.safe_load(_CI.read_text(encoding="utf-8"))["jobs"]
    found = []
    for job in jobs.values():
        if "matrix.python-version" not in str(job.get("name", "")):
            continue
        for step in job.get("steps", []):
            script = str(step.get("run", "")).replace("\\\n", " ")
            for line in script.splitlines():
                if line.split()[:1] == ["make"]:
                    found.append(shlex.split(line, comments=True)[1:])
    return found


def _commands(dry_run: str) -> "list[list[str]]":
    """Each simple command in ``make -n`` output, as words."""
    text = dry_run.replace("\\\n", " ")
    cmds = []
    for line in text.splitlines():
        lex = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        cmd: "list[str]" = []
        for word in lex:
            if word and set(word) <= set(";&|()"):
                cmds.append(cmd)
                cmd = []
            else:
                cmd.append(word)
        cmds.append(cmd)
    return [c for c in cmds if c]


def pytest_pins(cmd: "list[str]") -> "list[str]":
    """Why *cmd*, if it runs pytest, does not run it on the leg's Python.

    A pytest is launched by ``uv run [opts] pytest`` (``pytest`` not the value
    of a ``--with``), or bare (``pytest``, ``python -m pytest``). The bare
    forms do not read ``UV_PYTHON`` at all; the ``uv run`` form does unless
    an option or an inline ``UV_PYTHON=`` overrides it.

    Examples
    --------
    >>> pytest_pins(["uv", "run", "--with", "x", "pytest", "-p", "no:y"])
    []
    >>> pytest_pins(["uv", "run", "-p", "3.13", "pytest"])
    ['uv run pins -p']
    >>> pytest_pins(["uv", "run", "--python=3.13", "--with", "pytest", "pytest"])
    ['uv run pins --python=3.13']
    >>> pytest_pins(["UV_PYTHON=3.13", "uv", "run", "pytest"])
    ['UV_PYTHON=3.13 overrides the leg']
    >>> pytest_pins(["python3", "-m", "pytest", "tests"])
    ['pytest launched without uv run']
    >>> pytest_pins(["echo", "pytest"])
    []
    """
    i = 0
    why = []
    while i < len(cmd) and "=" in cmd[i] and not cmd[i].startswith("-"):
        if cmd[i].startswith("UV_PYTHON="):
            why.append(f"{cmd[i]} overrides the leg")
        i += 1
    words = cmd[i:]
    if words[:2] == ["uv", "run"]:
        opts = []
        for j, word in enumerate(words[2:], start=2):
            if word == "pytest" and words[j - 1] != "--with":
                break
            opts.append(word)
        else:
            return []  # not a pytest
        for word in opts:
            for flag in _PIN_FLAGS:
                # `-p 3.13`, `-p3.13`, `--python 3.13`, `--python=3.13`
                if word == flag or word.startswith(
                    flag + "=" if flag.startswith("--") else flag
                ):
                    why.append(f"uv run pins {word}")
        return why
    bare = Path(words[0]).name == "pytest" if words else False
    if bare or words[1:3] == ["-m", "pytest"]:
        return why + ["pytest launched without uv run"]
    return []


def test_no_leg_pins_an_interpreter():
    """Every pytest a Python-named leg's ``make`` step runs is a ``uv run``
    with no interpreter pin, read from ``make -n``: the expanded command, so
    a pin in any variable the recipe reaches is in it."""
    targets = _leg_targets()
    assert targets, "ci.yml has no make step in a job named for a Python"
    found = []
    seen = 0
    for args in targets:
        r = subprocess.run(
            ["make", "-n", *args],
            cwd=_REPO,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        for cmd in _commands(r.stdout):
            if "pytest" in cmd:
                seen += 1
            found += [f"make {' '.join(args)}: {w}" for w in pytest_pins(cmd)]
    assert seen, "no leg's make step runs pytest: the scan read nothing"
    assert not found, "\n".join(found)


def test_the_examples_hold():
    """The doctests above are the readers' shapes; run them here, since
    ``make test`` collects no doctest from tests/."""
    import doctest

    import _legpython

    for module in (_legpython, sys.modules[__name__]):
        result = doctest.testmod(module)
        assert result.attempted and not result.failed, module


# -- the setup-python legs (artifact.yml) -----------------------------------

_ARTIFACT = _REPO / ".github" / "workflows" / "artifact.yml"
_ASKS = "_legpython.py"
_LEG = "${{ matrix.python-version }}"


def _python_named_jobs(path: Path) -> "dict[str, dict]":
    jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    return {
        name: job
        for name, job in jobs.items()
        if "matrix.python-version" in str(job.get("name", ""))
    }


def test_every_smoke_leg_asks_before_it_uses_python():
    """artifact.yml's legs take their Python from ``setup-python`` and call
    it as ``python``, ``python3`` and ``pip``: no ``UV_PYTHON``, no jm suite
    and so no conftest. Each asks `_legpython` itself, with its matrix
    version, before any step that runs Python."""
    jobs = _python_named_jobs(_ARTIFACT)
    assert jobs, "artifact.yml has no job named for a Python"
    for name, job in jobs.items():
        runs = [str(s["run"]) for s in job.get("steps", []) if "run" in s]
        first = next(
            (i for i, r in enumerate(runs) if re.search(r"\bpy|\bpip", r)),
            None,
        )
        assert first is not None, f"{name}: no step runs Python"
        asks = runs[first]
        assert _ASKS in asks and _LEG in asks, (
            f"{name}: the first step that runs Python does not ask "
            f"tests/{_ASKS} whether it is the leg's:\n{asks}"
        )
        if "windows" not in name:
            for word in ("python3 ", "pip --version"):
                assert word in asks, f"{name}: {word.strip()} is not asked"


def test_the_script_refuses_another_python():
    """The step's own exit codes, under the suite's interpreter."""
    script = str(Path(__file__).parent / _ASKS)
    mine = f"{sys.version_info.major}.{sys.version_info.minor}"
    other = f"3.{sys.version_info.minor + 1}"
    run = [sys.executable, script]
    assert subprocess.run([*run, mine], capture_output=True).returncode == 0
    r = subprocess.run([*run, other], capture_output=True, text=True)
    assert r.returncode == 1 and f"names Python {other}" in r.stderr, r
    assert subprocess.run([*run, ""], capture_output=True).returncode == 2
