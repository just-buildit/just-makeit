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
    import subprocess

    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            "--with",
            "pytest",
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
        timeout=600,
    )
    sys.exit(result.returncode)
