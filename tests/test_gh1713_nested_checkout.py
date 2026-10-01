"""gh-1713: another checkout inside a project is not the project.

`jm status` copied the project directory, whole, into its scratch and counted
every file in it as manifest-owned. A git worktree placed below the root --
Claude Code puts agent worktrees at ``<repo>/.claude/worktrees/agent-*``, and
excludes them in ``.git/info/exclude`` -- is a different checkout, usually on
another branch. In doppler one held 3414 of the 41870 paths `status` walked;
here it doubles the count (35 -> 70). And a file edited in that other
checkout while `status` ran read as STALE in this one, with `jm apply` --
which would write into the other branch's tree -- as the advice.

A directory holding its own ``.git`` -- a directory for a clone, a FILE
for a linked worktree or a submodule -- is now another checkout: never
copied into the scratch, never compared, never counted, and never read by
`apply`'s before-picture. It is recognised without git at all, so the rule
holds outside a repository too.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli

from just_makeit import _apply

_WT = ".claude/worktrees/agent-x"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _owned_count(out: str) -> int:
    m = re.search(r"(\d+) manifest-owned file\(s\) match", out)
    assert m, out
    return int(m.group(1))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    r = run_cli(
        "object",
        "gain",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    assert r.returncode == 0, r.stderr
    return root


@pytest.fixture
def repo(project: Path) -> Path:
    """The project as a git repository with an agent worktree inside it,
    excluded the way Claude Code excludes one."""
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    _git(project, "init", "-q")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")
    with (project / ".git" / "info" / "exclude").open("a") as f:
        f.write("**/.claude/worktrees/\n")
    return project


def test_an_ignored_worktree_is_not_counted(repo: Path) -> None:
    before = run_cli("status", "--check", cwd=repo)
    assert before.returncode == 0, before.stdout + before.stderr
    n = _owned_count(before.stdout)

    _git(repo, "worktree", "add", "-q", _WT, "-b", "other")
    assert (repo / _WT / "CMakeLists.txt").is_file()

    after = run_cli("status", "--check", cwd=repo)
    assert after.returncode == 0, after.stdout + after.stderr
    assert _owned_count(after.stdout) == n, after.stdout


def test_an_edit_in_the_other_checkout_is_not_stale_here(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The symptom the issue reported: another branch's file, changed while
    `status` ran -- an agent working in its worktree -- named as STALE here.
    The edit is made from inside the replay, after the copy, which is the
    window a concurrent writer has."""
    _git(repo, "worktree", "add", "-q", _WT, "-b", "other")
    other = repo / _WT / "native" / "src" / "gain" / "gain_ext.c"
    assert other.is_file()

    real_run = _apply.run

    def run_while_the_other_branch_edits(*args, **kwargs):
        with other.open("a") as f:
            f.write("/* the other branch's work */\n")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(_apply, "run", run_while_the_other_branch_edits)
    r = run_cli("status", "--check", cwd=repo)
    assert ".claude" not in r.stdout + r.stderr, r.stdout + r.stderr
    assert r.returncode == 0, r.stdout + r.stderr


def test_apply_does_not_digest_the_other_checkout(repo: Path) -> None:
    """`apply`'s before-picture (gh-1474) walks by the same rules: it never
    writes into another checkout, so it has no reason to read one."""
    _git(repo, "worktree", "add", "-q", _WT, "-b", "other")
    digests = _apply._tree_digests(repo)
    assert "CMakeLists.txt" in digests
    assert not [k for k in digests if k.startswith(".claude/")], digests


def test_a_nested_checkout_is_skipped_without_git(project: Path) -> None:
    """No repository at the root, so nothing to ask git: the ``.git`` marker
    alone keeps a nested clone out."""
    before = run_cli("status", "--check", cwd=project)
    assert before.returncode == 0, before.stdout + before.stderr
    n = _owned_count(before.stdout)

    # A whole second checkout of the project, as a clone or worktree is --
    # manifest included -- built beside it and moved in.
    elsewhere = project.parent / "elsewhere"
    shutil.copytree(project, elsewhere)
    (elsewhere / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
    (project / "vendor").mkdir()
    shutil.move(str(elsewhere), str(project / "vendor" / "other"))

    after = run_cli("status", "--check", cwd=project)
    assert after.returncode == 0, after.stdout + after.stderr
    assert _owned_count(after.stdout) == n, after.stdout
