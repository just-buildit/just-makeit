"""Console script entry points for bundled shell utilities."""

import os
import sys
from importlib.resources import as_file, files


def _run_sh(name: str) -> None:
    pkg = files("just_makeit.scripts")
    with as_file(pkg / name) as path:
        os.execlp("bash", "bash", str(path), *sys.argv[1:])


def install_deps() -> None:
    _run_sh("install-deps.sh")


def docker_e2e() -> None:
    _run_sh("docker-e2e.sh")


def run_tests() -> None:
    """Run pytest in an isolated environment. Extra args pass through.

    ``pytest-xdist`` is installed but **not enabled**: this is a shipped entry
    point, so a caller's suite may not be parallel-safe and jm cannot know.
    Making the plugin available lets the caller decide — jm's own CI runs
    ``jm-run-tests -n auto --dist load``, which is a ~3x saving on a suite that
    was measured at 299s serial and 106s parallel.

    There is deliberately **no timeout**. There used to be one at 600s, against
    a macOS/3.9 leg measured at 464s — 1.3x of headroom on a shipped runner
    whose whole job is running an arbitrary suite. Worse, exceeding it raised
    ``TimeoutExpired`` mid-run, so the failure arrived as a traceback with no
    pytest summary and no indication of which test was executing. A CI job
    should bound its own wall-clock (`timeout-minutes:`); a test runner
    capping the tests is a truncation dressed as a failure.
    """
    import subprocess

    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            "--with",
            "pytest",
            "--with",
            "pytest-xdist",
            "--with",
            "numpy",
            "--with",
            "just-buildit",
            "--with",
            "tomlkit",
            "pytest",
            "-v",
            *sys.argv[1:],
        ],
    )
    sys.exit(result.returncode)
