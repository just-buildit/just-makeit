"""Console script entry points for bundled shell utilities."""

import importlib.resources
import os
import shutil
import sys


def _run_sh(name: str) -> None:
    pkg = importlib.resources.files("just_makeit.scripts")
    with importlib.resources.as_file(pkg / name) as path:
        os.execlp("bash", "bash", str(path), *sys.argv[1:])


def _run_ps1(name: str) -> None:
    pkg = importlib.resources.files("just_makeit.scripts")
    # Prefer pwsh (PowerShell 7+) over powershell (Windows PowerShell 5.x).
    exe = "pwsh" if shutil.which("pwsh") else "powershell"
    # Translate GNU-style --check to PS switch -Check.
    ps_args = ["-Check" if a == "--check" else a for a in sys.argv[1:]]
    with importlib.resources.as_file(pkg / name) as path:
        os.execvp(
            exe,
            [
                exe,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(path),
                *ps_args,
            ],
        )


def install_deps() -> None:
    if sys.platform == "win32":
        _run_ps1("install-deps.ps1")
    else:
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
        ]
    )
    sys.exit(result.returncode)
