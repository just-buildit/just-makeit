"""No pipeline under pipefail ends in a reader that stops early.

``producer | grep -q`` (or ``head``, ``sed 1q``, an awk ``exit``) exits
before the producer has finished writing; the producer takes SIGPIPE (rc
141), and under ``pipefail`` the pipeline fails though the reader got its
answer. Under ``!`` the answer flips. Whether it happens is timing:
canonical's close-keywords step failed 3 of 3 runs on one box and passed on
the runner. jm had 32 such lines: artifact.yml's installed-version checks,
docker-e2e.sh's, the cmake version reads in install.sh and jm-install-deps.

The rules are canonical's vendored ``scripts/pipefail-readers.py``; ``make
lint-pipefail-readers`` runs it over every tracked shell script that sets
pipefail and every workflow, and ``.pre-commit-config.yaml`` dispatches to
it, so ``make lint`` (what CI runs) holds the tree to it. These tests hold
the wiring: the target passes on the tree, reads the files it guards, and
the hook reaches it.
"""

from __future__ import annotations

import pathlib
import re
import shlex
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _make(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-s", "--no-print-directory", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_the_tree_has_no_pipe_into_an_early_reader() -> None:
    p = _make("lint-pipefail-readers")
    assert p.returncode == 0, p.stdout + p.stderr
    m = re.search(r"pipefail-readers: (\d+) file\(s\)", p.stdout)
    assert m, p.stdout + p.stderr
    # The workflows and the pipefail scripts: a list that shrank to a
    # handful has stopped reading what it guards.
    assert int(m.group(1)) >= 10, p.stdout


def test_the_scan_reads_the_scripts_it_was_written_for() -> None:
    # The `git ls-files` the recipe runs, its pathspecs read off the
    # command make would run and handed to git as a list -- no shell.
    p = _make("-n", "lint-pipefail-readers")
    assert p.returncode == 0, p.stderr
    m = re.search(r"\$\(git ls-files ([^)]*)\)", p.stdout)
    assert m, p.stdout
    files = subprocess.run(
        ["git", "ls-files", *shlex.split(m.group(1))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    for f in (
        ".github/workflows/artifact.yml",
        "src/just_makeit/scripts/docker-e2e.sh",
        "src/just_makeit/scripts/install-deps.sh",
        "install.sh",
    ):
        assert f in files, f
    # Vendored files are canonical's to hold.
    assert "scripts/release-watch.sh" not in files


def test_pre_commit_dispatches_to_it() -> None:
    cfg = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert re.search(
        r"(?m)^\s+entry: make -s lint-pipefail-readers\s*$", cfg
    ), "no hook dispatches to `make -s lint-pipefail-readers`"
