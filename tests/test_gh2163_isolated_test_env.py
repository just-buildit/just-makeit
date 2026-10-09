"""gh-2163: the isolated test environment imports only what it declares, and runs
the interpreter CI asked for.

`PYTEST_ISOLATED` is `uv run --no-project`, which is meant to keep the rest of
the environment out of the suite. It does not. uv still discovers a virtual
environment, from VIRTUAL_ENV or from a `.venv` above the cwd, and layers the
`--with` env over it. A package in that venv then imports on a laptop and
fails in CI, which is the single-environment blind spot gh-1442 was about.

The leak condition is built here, in tmp_path, so the gate runs on every box
including CI: a throwaway venv holding a sentinel module, named by VIRTUAL_ENV.

The fix is `--isolated`. An explicit `--python <path>` also stops the overlay,
but it overrides UV_PYTHON, which setup-uv sets per CI leg, so every leg would
test one interpreter. `--isolated` keeps the interpreter request in force.

Three assertions, each of which the old pin fails:

- premise: a plain `uv run --no-project` under VIRTUAL_ENV does import the
  sentinel. If that stops being true the gate below would pass vacuously.
- isolation: `PYTEST_ISOLATED` does not import the sentinel.
- version: with UV_PYTHON set to the version uv has, the isolated interpreter
  reports exactly that version.

Sabotage proof: removing `--isolated` turns the isolation assertion red;
pinning `--python /usr/bin/python3` turns the version assertion red.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PROBE = (
    "import sys, importlib.util as u; "
    "print('sentinel=' + str(u.find_spec('sentinel_gh2163') is not None) + "
    "' version=%d.%d' % sys.version_info[:2])"
)


def _checkout_interpreter() -> str:
    """The interpreter a bare `uv run` in the checkout selects.

    uv reads `.python-version` from the cwd, so the probe's inner `uv run`
    wants the repo's version. A venv at any other version is skipped by uv's
    interpreter search, and the sentinel is then invisible for a reason that
    has nothing to do with the leak. Measured: a 3.14 venv under a 3.12
    `.python-version` is skipped, a 3.12 venv is seen.
    """
    return subprocess.run(
        ["uv", "python", "find"],
        cwd=str(REPO),
        check=True,
        capture_output=True,
        text=True,
        env=_inherited(),
    ).stdout.strip()


def _venv_with_sentinel(tmp_path: Path) -> tuple[Path, str]:
    """A venv that uv made, holding a module the suite must never see.

    The venv is made at the version the checkout selects, not at this test's
    own interpreter: under a pinned parent (`uv run --python /usr/bin/python3`)
    the test runs on a different version than the probe will request, and the
    premise would then fail for a reason that is not the leak. The version is
    returned for the UV_PYTHON assertion.
    """
    venv = tmp_path / "leak-venv"
    subprocess.run(
        ["uv", "venv", "-q", str(venv), "--python", _checkout_interpreter()],
        check=True,
        capture_output=True,
        text=True,
    )
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    sp = subprocess.run(
        [str(py), "-c", "import site; print(site.getsitepackages()[0])"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (Path(sp) / "sentinel_gh2163.py").write_text("x = 1\n", encoding="utf-8")
    version = subprocess.run(
        [str(py), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return venv, version


#: The probe's whole environment: what it needs to run, and nothing a parent
#: runner exported. A `uv run` parent sets VIRTUAL_ENV and a recursion counter,
#: and CI sets UV_PYTHON per leg, and any of them would make the probe depend on
#: who launched the suite. The test states the environment it means.
_PROBE_ENV_KEEP = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "TMPDIR",
    "TEMP",
)


def _inherited() -> dict[str, str]:
    return {k: os.environ[k] for k in _PROBE_ENV_KEEP if k in os.environ}


def _probe(rule: str, env: dict[str, str]) -> str:
    """Run *rule* (a make recipe line) from the repo, with the Makefile's own
    variables, and return its last output line.

    The probe rule lives here, not in the Makefile: a rule the Makefile defines
    is a shared target, and the standard gates refuse one.
    """
    makefile = f"include {REPO / 'Makefile'}\nisolation-probe:\n\t@{rule}\n"
    r = subprocess.run(
        ["make", "-s", "-f", "-", "isolation-probe"],
        cwd=str(REPO),
        input=makefile,
        capture_output=True,
        text=True,
        env={**_inherited(), **env},
        timeout=900,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.strip().splitlines()[-1]


def _parse(line: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in line.split())


def test_premise_a_plain_run_does_import_the_sentinel(tmp_path: Path) -> None:
    """Without the fix the leak is real. If it were not, the isolation test
    below would pass for the wrong reason."""
    assert shutil.which("uv"), (
        "uv is required: the test environment is made by it"
    )
    venv, _ = _venv_with_sentinel(tmp_path)
    line = _probe(
        f'uv run --no-project --with pytest --with-editable . python -c "{PROBE}"',
        {"VIRTUAL_ENV": str(venv)},
    )
    assert _parse(line)["sentinel"] == "True", line


def test_the_isolated_suite_does_not_import_the_sentinel(
    tmp_path: Path,
) -> None:
    venv, _ = _venv_with_sentinel(tmp_path)
    line = _probe(
        f'$(PYTEST_ISOLATED) python -c "{PROBE}"', {"VIRTUAL_ENV": str(venv)}
    )
    assert _parse(line)["sentinel"] == "False", (
        "the isolated suite imported a module from the venv it was not given: "
        "PYTEST_ISOLATED is layering over a discovered environment (--isolated)"
    )


def test_the_isolated_suite_runs_the_interpreter_uv_was_asked_for(
    tmp_path: Path,
) -> None:
    venv, version = _venv_with_sentinel(tmp_path)
    line = _probe(
        f'$(PYTEST_ISOLATED) python -c "{PROBE}"',
        {"VIRTUAL_ENV": str(venv), "UV_PYTHON": version},
    )
    assert _parse(line)["version"] == version, (
        f"UV_PYTHON={version} was asked for and the suite ran "
        f"{_parse(line)['version']}: an explicit --python overrides the request "
        "CI makes per matrix leg"
    )
