"""``make test``'s pytest never reads ``uv.lock`` (gh-2773).

gh-2770: tomlkit 0.15.3 removed a private method jm called, and every
fresh install of jm broke at once. What caught it was the ``test`` matrix:
``make test`` runs pytest under ``PYTEST_ISOLATED``, ``uv run
--no-project``, which resolves jm's runtime dependencies fresh, at the
newest versions its requirements allow, whatever ``uv.lock`` pins. Run
45347360245 installed ``tomlkit==0.15.3`` within a minute of its release
and went red. Coverage, which runs locked (``uv run --group dev``), passed.

So the suite is jm's unlocked-resolve gate, and nothing named that
property: ``test_gh2163_isolated_test_env.py`` holds ``--isolated`` and the
interpreter. Dropping ``--no-project`` would quietly put every leg back on
the lock, where the next gh-2770 stays green until a user installs jm.

The probe runs ``PYTEST_ISOLATED``, taken from the Makefile itself, inside
a throwaway project whose ``uv.lock`` is not TOML. A command that reads the
lock fails to parse it; one that does not never notices. The premise test
shows the same command without ``--no-project`` does fail there, so the
gate cannot pass because the lock went unread for some other reason.

Sabotage: ``scripts/sabotage.py Makefile 'run --no-project --isolated'
'run --isolated' -- pytest tests/test_gh2773_suite_ignores_lock.py`` turns
``test_the_suite_does_not_read_the_lock`` red.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: What the probe keeps of the caller's environment. A `uv run` parent's
#: VIRTUAL_ENV, or a CI leg's UV_* settings, must not decide the answer.
_KEEP = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TMPDIR", "TEMP")

PYPROJECT = """\
[project]
name = "probe-gh2773"
version = "0.0.0"
dependencies = []

[build-system]
requires = ["just-buildit>=0.3.6"]
build-backend = "just_buildit"
"""


def _project(tmp_path: Path) -> Path:
    """A buildable project whose lock no reader can parse."""
    proj = tmp_path / "probe"
    pkg = proj / "src" / "probe_gh2773"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (proj / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (proj / "uv.lock").write_text("not a lock [[[\n", encoding="utf-8")
    return proj


def _probe(command: str, proj: Path) -> subprocess.CompletedProcess[str]:
    """Run *command* (make syntax, so ``$(PYTEST_ISOLATED)`` expands from
    jm's Makefile) in *proj*.

    The rule arrives on stdin, not through --eval: macOS ships GNU make
    3.81, which has no --eval.
    """
    makefile = (
        f"include {ROOT / 'Makefile'}\n"
        f"lock-probe:\n\t@cd '{proj}' && {command} "
        "python -c 'print(\"probe-ran\")'\n"
    )
    return subprocess.run(
        ["make", "-s", "-f", "-", "lock-probe"],
        cwd=str(ROOT),
        input=makefile,
        capture_output=True,
        text=True,
        env={k: os.environ[k] for k in _KEEP if k in os.environ},
        timeout=900,
    )


def test_premise_a_project_run_does_read_the_lock(tmp_path: Path) -> None:
    """Without ``--no-project`` the same command parses the lock and dies.
    If that stopped being true, the gate below would pass vacuously."""
    assert shutil.which("uv"), "uv is required: the test env is made by it"
    r = _probe("$(UV) run --isolated $(PYTEST_DEPS)", _project(tmp_path))
    assert r.returncode != 0, r.stdout + r.stderr
    assert "uv.lock" in r.stderr, r.stderr


def test_the_suite_does_not_read_the_lock(tmp_path: Path) -> None:
    r = _probe("$(PYTEST_ISOLATED)", _project(tmp_path))
    assert r.returncode == 0 and "probe-ran" in r.stdout, (
        "PYTEST_ISOLATED read the project's uv.lock: `make test` would run "
        "the suite on the locked versions, not the ones a fresh install "
        "resolves, and a dependency release like gh-2770's would stay "
        "green (keep `--no-project`)\n" + r.stdout + r.stderr
    )
