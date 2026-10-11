"""A throwaway git repository, for a test whose question only git answers.

Two floors are held to one promise (gh-2108, gh-2038): a branch that
changes one owner's entry -- an issue's ratchet file, a gate's declaration
-- merges with a branch that changes another's, in either order, with
nothing to resolve. "Merges cleanly" is git's answer, not something a test
can read off the files, so these ask git, in a repository of their own
under ``tmp_path``.

Isolated from the developer's git in the two ways that bite: GIT_* from a
suite run inside a hook would aim every command at the outer repository
(`test_release_bump_is_complete` scrubs it for the same reason), and an
identity, signing or hook setting from the user's config would make a
commit fail or prompt. So every command runs with GIT_* scrubbed and those
settings on its own command line.

Examples
--------
Two branches deleting two different files merge either way round:

>>> import tempfile
>>> with tempfile.TemporaryDirectory() as tmp:
...     repo = Repo(Path(tmp) / "r")
...     for name in "ab":
...         _ = (repo.root / name).write_text(name, encoding="utf-8")
...     repo.commit("base")
...     repo.branch("x", lambda root: (root / "a").unlink())
...     repo.branch("y", lambda root: (root / "b").unlink())
...     repo.conflicts("x", "y"), repo.conflicts("y", "x")
([], [])

and two that change one line do not:

>>> with tempfile.TemporaryDirectory() as tmp:
...     repo = Repo(Path(tmp) / "r")
...     _ = (repo.root / "a").write_text("1\\n", encoding="utf-8")
...     repo.commit("base")
...     for b in "xy":
...         repo.branch(b, lambda root, b=b: (root / "a").write_text(b))
...     repo.conflicts("x", "y")
['a']
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

_CONFIG = (
    "-c", "user.name=jm-test",
    "-c", "user.email=jm-test@example.invalid",
    "-c", "commit.gpgsign=false",
    "-c", "core.hooksPath=/dev/null",
)  # fmt: skip


class Repo:
    """A git repository at *root*, on branch ``main``, with no commit."""

    #: The branch `conflicts` leaves the merge it made on.
    MERGED = "scratch-merge"

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True)
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")

    def git(
        self, *args: str, check: bool = True
    ) -> "subprocess.CompletedProcess[str]":
        """``git *args`` here. A failure fails the test unless *check* is
        false, when the caller reads the result itself."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        r = subprocess.run(
            ["git", *_CONFIG, *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0 or not check, (args, r.stdout + r.stderr)
        return r

    def commit(self, message: str) -> None:
        """Commit the whole tree, deletions included."""
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def branch(
        self,
        name: str,
        edit: "Callable[[Path], object]",
        message: str = "",
    ) -> None:
        """Branch *name* off ``main``: *edit* (handed the root), committed
        as *message* (default: *name*). The tree is left on ``main``."""
        self.git("checkout", "-q", "-B", name, "main")
        edit(self.root)
        self.commit(message or name)
        self.git("checkout", "-q", "main")

    def conflicts(self, first: str, second: str) -> "list[str]":
        """Merge *first* and then *second* onto ``main``, on the branch
        `MERGED`: the paths git could not merge, or ``[]`` when both went in
        clean. The tree is left on ``main``."""
        self.git("checkout", "-q", "-B", self.MERGED, "main")
        out: "list[str]" = []
        for ref in (first, second):
            r = self.git("merge", "-q", "--no-edit", ref, check=False)
            if r.returncode:
                unmerged = self.git("diff", "--name-only", "--diff-filter=U")
                out = unmerged.stdout.split() or [r.stdout + r.stderr]
                self.git("merge", "--abort", check=False)
                break
        self.git("checkout", "-q", "main")
        return out
