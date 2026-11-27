"""gh-2128: `jm dry-run` names the backend's build, not always cmake.

`cmd_dry_run` always printed a `cmake -B build` configure, with jm's own
interpreter as `Python3_EXECUTABLE`, whatever `[project] build` said. On a
`build = "make"` project that line described a build the project does not run,
and on a CMake project it named jm's interpreter rather than the project's
`.venv`, which is the interpreter `jm build` uses (gh-1896).

GATE: the dry-run on a make scaffold names `make`, not `cmake`; on a CMake
scaffold it names the project's `.venv` interpreter when one exists.
"""

from __future__ import annotations

import re
from pathlib import Path

from _jmrun import run_cli


def test_a_make_scaffold_dry_run_names_make_not_cmake(tmp_path: Path) -> None:
    root = tmp_path / "mk"
    r = run_cli(
        "new", "mk", str(root), "--object", "g", "--build-system", "make"
    )
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("dry-run", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    # `make` resolves to an absolute path, as cmake's does, so match its tail.
    assert re.search(r"build:\s+\S*make PYTHON=", r.stdout), r.stdout
    # Only the backend's command lines are read: the header echoes the
    # project path, which may hold "cmake" in any part (a TMPDIR named for
    # it, gh-2843). A make build has no configure step, and its build line
    # names no cmake executable.
    steps = re.findall(r"^\s*(configure|build):\s*(.*)$", r.stdout, re.M)
    assert [label for label, _ in steps] == ["build"], r.stdout
    assert not re.search(r"(^|[\s/])cmake(\s|$)", steps[0][1]), r.stdout


def test_a_cmake_scaffold_dry_run_uses_the_project_venv(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cm"
    r = run_cli("new", "cm", str(root), "--object", "g")
    assert r.returncode == 0, r.stdout + r.stderr
    venv_python = root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    r = run_cli("dry-run", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"-DPython3_EXECUTABLE={venv_python}" in r.stdout, r.stdout
