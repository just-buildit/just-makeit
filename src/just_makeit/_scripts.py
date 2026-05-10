"""Console script entry points for bundled shell utilities."""
import importlib.resources
import os
import sys


def _run(name: str) -> None:
    pkg = importlib.resources.files("just_makeit.scripts")
    with importlib.resources.as_file(pkg / name) as path:
        os.execlp("bash", "bash", str(path), *sys.argv[1:])


def install_deps() -> None:
    _run("install-deps.sh")


def docker_e2e() -> None:
    _run("docker-e2e.sh")
