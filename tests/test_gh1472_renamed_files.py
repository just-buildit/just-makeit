"""gh-1472: a file jm renamed is renamed, not re-created beside the old one.

gh-935 renamed ``jb.toml`` to ``bootstrap.toml``. On a project scaffolded
before it, `apply` created a fresh default ``bootstrap.toml`` beside the
author's ``jb.toml`` and said only ``create  bootstrap.toml``: whatever the
author had added to the old file -- ``[runtime.*]`` packages -- was silently
absent from the one now maintained, and nothing said the old one was dead.
`stale_project` papered over it with a hand-written ``unlink``.

Now `_createonly.RENAMED` declares the rename once, and three commands read
it: `apply` holds the new file back and says why, `upgrade` renames the old
one (edits and all), and `status` names it until it is gone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli

EDIT = '\n[runtime.apt]\npackages = ["libfoo-dev"]\n'


@pytest.fixture
def old_project(tmp_path: Path) -> Path:
    """A project as jm left it before gh-935: its bootstrap declaration is
    ``jb.toml``, and the author has added to it."""
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
    new = root / "bootstrap.toml"
    (root / "jb.toml").write_text(new.read_text() + EDIT)
    new.unlink()
    return root


def test_apply_does_not_create_the_new_name_beside_the_old(old_project):
    r = run_cli("apply", cwd=old_project)
    assert r.returncode == 0, r.stderr
    out = r.stdout + r.stderr
    assert not (old_project / "bootstrap.toml").exists()
    assert "create  bootstrap.toml" not in out
    assert "jb.toml is now called bootstrap.toml" in out
    assert "just-makeit upgrade" in out


def test_status_names_it_and_does_not_count_it_as_drift(old_project):
    r = run_cli("status", "--check", cwd=old_project)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SUPERSEDED (1)" in r.stdout
    assert "jb.toml" in r.stdout


def test_upgrade_renames_it_and_keeps_the_edits(old_project):
    r = run_cli("upgrade", cwd=old_project)
    assert r.returncode == 0, r.stderr
    assert "renamed jb.toml -> bootstrap.toml" in r.stdout
    assert not (old_project / "jb.toml").exists()
    assert "libfoo-dev" in (old_project / "bootstrap.toml").read_text()

    again = run_cli("upgrade", cwd=old_project)
    assert "jb.toml" not in again.stdout
    s = run_cli("status", "--check", cwd=old_project)
    assert s.returncode == 0, s.stdout
    assert "SUPERSEDED" not in s.stdout


def test_upgrade_leaves_both_alone_when_both_exist(old_project):
    """An older jm already created the new one beside the author's: which
    holds the edits is not jm's to guess."""
    both = old_project / "bootstrap.toml"
    both.write_text("# jm's default\n")
    r = run_cli("upgrade", cwd=old_project)
    assert r.returncode == 0, r.stderr
    assert (old_project / "jb.toml").is_file()
    assert both.read_text() == "# jm's default\n"
    assert "Merge anything you added to jb.toml into bootstrap.toml" in (
        r.stdout
    )
