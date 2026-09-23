"""gh-1438: a branch may not edit a CHANGELOG section that already shipped.

`changelog-check` passed PR #1437 with its entry inside the released
`## [0.82.1]`, because it asks only whether CHANGELOG.md was touched. #1500
then did it for real: it merged after v0.87.1 with its gh-1493 entry inside
`[0.87.1]`, so 0.87.2 would have shipped without it.

`scripts/changelog_sections_check.py` holds the rule and its doctests hold
the pure comparison. This file runs the script the way `make lint` does,
against real git histories seeded in a temporary repo, because the parts
that can only go wrong in git are here: the merge base, the tag lookup, and
the exit code the make recipe reads.
"""

from __future__ import annotations

import doctest
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "changelog_sections_check.py"

RELEASED = (
    "## [Unreleased]\n"
    "\n"
    "## [1.0.0] - 2026-01-01\n"
    "\n"
    "### Fixed\n"
    "\n"
    "- **One.** Shipped in 1.0.0.\n"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, text: str, msg: str) -> str:
    (repo / "CHANGELOG.md").write_text(text, encoding="utf-8")
    _git(repo, "add", "CHANGELOG.md")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, text: str = RELEASED) -> "tuple[Path, str]":
    """A repo whose base commit carries *text*, tagged v1.0.0."""
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    base = _commit(repo, text, "base")
    _git(repo, "tag", "v1.0.0")
    return repo, base


def _check(repo: Path, base: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), base],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_doctests_pass():
    spec = importlib.util.spec_from_file_location("csc", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = doctest.testmod(mod)
    assert result.attempted > 0
    assert result.failed == 0


def test_inert_on_main(tmp_path: Path):
    repo, base = _repo(tmp_path)
    r = _check(repo, base)
    assert r.returncode == 0, r.stdout
    assert "no released section edited" in r.stdout


def test_entry_under_unreleased_passes(tmp_path: Path):
    repo, base = _repo(tmp_path)
    _commit(
        repo,
        RELEASED.replace(
            "## [Unreleased]\n", "## [Unreleased]\n\n- **Two.** New.\n"
        ),
        "entry",
    )
    r = _check(repo, base)
    assert r.returncode == 0, r.stdout


def test_entry_in_released_section_fails_naming_it(tmp_path: Path):
    repo, base = _repo(tmp_path)
    _commit(repo, RELEASED + "- **Two.** Misplaced.\n", "entry")
    r = _check(repo, base)
    assert r.returncode == 1, r.stdout
    assert "## [1.0.0]  (changed)" in r.stdout


def test_removed_released_section_fails(tmp_path: Path):
    repo, base = _repo(tmp_path)
    _commit(repo, "## [Unreleased]\n", "drop history")
    r = _check(repo, base)
    assert r.returncode == 1, r.stdout
    assert "## [1.0.0]  (removed)" in r.stdout


def test_release_rename_passes(tmp_path: Path):
    """A release renames [Unreleased] and may polish its own new section.

    v0.87.1's release commit added a `### Docs` entry to `[0.87.1]`, so the
    new section is not required to equal the old `[Unreleased]` body.
    """
    pending = RELEASED.replace(
        "## [Unreleased]\n", "## [Unreleased]\n\n- **Two.** New.\n"
    )
    repo, base = _repo(tmp_path, pending)
    _commit(
        repo,
        pending.replace(
            "## [Unreleased]\n",
            "## [Unreleased]\n\n## [1.1.0] - 2026-02-01\n",
        ),
        "release",
    )
    released = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    _commit(
        repo,
        released.replace("- **Two.** New.\n", "- **Two.** New.\n- Docs.\n"),
        "polish",
    )
    r = _check(repo, base)
    assert r.returncode == 0, r.stdout


def test_release_with_edit_to_an_older_section_fails(tmp_path: Path):
    pending = RELEASED.replace(
        "## [Unreleased]\n", "## [Unreleased]\n\n- **Two.** New.\n"
    )
    repo, base = _repo(tmp_path, pending)
    _commit(
        repo,
        pending.replace(
            "## [Unreleased]\n",
            "## [Unreleased]\n\n## [1.1.0] - 2026-02-01\n",
        ).replace("Shipped in 1.0.0.", "Shipped in 1.0.0, reworded."),
        "release plus a sneaky edit",
    )
    r = _check(repo, base)
    assert r.returncode == 1, r.stdout
    assert "## [1.0.0]  (changed)" in r.stdout
    assert "1.1.0" not in r.stdout


def test_restoring_what_the_tag_shipped_passes(tmp_path: Path):
    """The #1500 correction: move a misplaced entry back out."""
    repo, _ = _repo(tmp_path)
    bad = _commit(repo, RELEASED + "- **Two.** Misplaced.\n", "the mistake")
    _commit(
        repo,
        RELEASED.replace(
            "## [Unreleased]\n", "## [Unreleased]\n\n- **Two.** Moved.\n"
        ),
        "the correction",
    )
    r = _check(repo, bad)
    assert r.returncode == 0, r.stdout


def test_a_correction_that_is_not_the_tag_fails(tmp_path: Path):
    repo, _ = _repo(tmp_path)
    bad = _commit(repo, RELEASED + "- **Two.** Misplaced.\n", "the mistake")
    _commit(repo, RELEASED + "- **Two.** Reworded.\n", "not a restore")
    r = _check(repo, bad)
    assert r.returncode == 1, r.stdout


def test_hung_off_lint():
    """The gate needs an execution home: CI runs `make lint` only."""
    text = (REPO / "local.mk").read_text(encoding="utf-8")
    assert "\nlint: changelog-sections-check\n" in text
    assert "scripts/changelog_sections_check.py" in text
