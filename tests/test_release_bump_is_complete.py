"""`make bump-version` must write every file that carries the version.

It used to write `pyproject.toml` alone, so the `sync-version` and `uv-lock`
pre-commit hooks then rewrote `bootstrap.toml` and `uv.lock` and **aborted the
first `git commit` of every release**. Re-running the identical commit worked,
so it was survivable — and it had been survived often enough to be written into
the release runbook as expected behaviour, which is what a papercut looks like
once it has stopped being read as a defect.

It was never harmless. A pre-commit hook that aborts a commit does not stop the
next command in the script, so `git commit -am ... ; git push` pushes the
UNCHANGED head — a foot-gun the same runbook warns about separately, and which
this behaviour armed on every release. The rule "the release commit is four
files; two means you pushed a half-bump" existed only because the bump did not
write four files.

The gate below runs the real script against a throwaway copy of the two
manifests rather than reading the Makefile and agreeing with itself — a check
that can only fail by coincidence is a description, not a control.

`uv.lock` is not covered here: refreshing it is a `uv lock` call, so there is
nothing of jm's to test. `tests/test_lint_ssot.py` guards the Makefile wiring.

Running the script under `sys.executable` is what caught that it hard-imported
`tomllib` — fine in the dev env (3.12) where it had only ever run as a
pre-commit hook, and a `ModuleNotFoundError` on the 3.9 and 3.10 legs of the
CI matrix. A script exercised on one interpreter is untested on the other five.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "sync_version.py"


def _run(root: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *flags],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _fixture(tmp_path: Path, version: str = "9.9.9") -> Path:
    """A copy of the two manifests with pyproject already bumped.

    Copies the real files so the regexes are exercised against the shapes that
    actually ship, not a hand-written approximation of them.
    """
    shutil.copy2(REPO / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy2(REPO / "bootstrap.toml", tmp_path / "bootstrap.toml")
    pp = tmp_path / "pyproject.toml"
    text = pp.read_text(encoding="utf-8")
    bumped, n = _sub_version(text, version)
    assert n == 1, "pyproject.toml's version line did not match"
    pp.write_text(bumped, encoding="utf-8")
    return tmp_path


def _sub_version(text: str, version: str) -> tuple[str, int]:
    import re

    return re.subn(
        r'^version = "[^"]*"',
        f'version = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )


def _version_of(path: Path) -> str:
    import re

    m = re.search(
        r'^version\s*=\s*"([^"]*)"',
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert m, f"no version line in {path.name}"
    return m.group(1)


def test_the_bump_carries_bootstrap_toml(tmp_path):
    """The write itself — with `--exit-zero`, as `bump-version` calls it."""
    root = _fixture(tmp_path)
    r = _run(root, "--exit-zero")
    assert r.returncode == 0, (
        f"bump path must not fail on a change:\n{r.stdout}"
    )
    assert _version_of(root / "bootstrap.toml") == "9.9.9"


def test_after_the_bump_the_hook_finds_nothing(tmp_path):
    """The papercut, stated directly.

    This is the assertion that fails if `bump-version` ever stops carrying
    `bootstrap.toml`: the hook would then have work to do at commit time, which
    is exactly the aborted first commit.
    """
    root = _fixture(tmp_path)
    assert _run(root, "--exit-zero").returncode == 0
    second = _run(root)  # no flag — the hook's own invocation
    assert second.returncode == 0, (
        "the sync-version hook still has work to do straight after a bump, so "
        f"the first release commit will abort:\n{second.stdout}"
    )
    assert second.stdout.strip() == ""


def test_the_hook_still_fails_on_an_unsynced_tree(tmp_path):
    """`--exit-zero` must not have disarmed the gate.

    Without the flag a change is still a finding — that is the pre-commit
    convention the hook relies on to make the user re-stage the file.
    """
    root = _fixture(tmp_path)
    r = _run(root)
    assert r.returncode == 1, "the hook stopped reporting an unsynced tree"
    assert "updated bootstrap.toml" in r.stdout


def test_the_shipped_tree_is_already_in_sync():
    """The two manifests on disk agree right now.

    Cheap, and it catches a half-bump that reached `main` — the state the old
    behaviour could produce by pushing an unchanged head after an aborted
    commit.
    """
    assert _version_of(REPO / "pyproject.toml") == _version_of(
        REPO / "bootstrap.toml"
    )
