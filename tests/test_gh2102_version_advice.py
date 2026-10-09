"""gh-2102: `status`'s VERSION advice names the command that does the sync.

The advice said "Sync whichever is wrong", which reads as a hand edit of every
generated copy. Since gh-2069 `jm config version <v>` writes the manifest and
every copy in one step, so the advice names it, with the value filled in.

GATE: each `jm config version …` command the advice prints, run on the tree,
leaves `status --check` at 0 with no VERSION finding.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli

OLD = "0.1.0"
NEW = "2.4.0"

#: Every command the advice prints, between its backticks.
ADVICE = re.compile(r"`(jm config version [^`]+)`")


def _bumped_manifest_only(root: Path) -> None:
    """The stale state the advice is for: the manifest moved, the copies did
    not. Done by hand, because `jm config version` would write them too."""
    p = root / "just-makeit.toml"
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace(f'version = "{OLD}"', f'version = "{NEW}"', 1))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    assert run_cli("new", "vp", cwd=tmp_path).returncode == 0
    root = tmp_path / "vp"
    assert run_cli("object", "eng", cwd=root).returncode == 0
    _bumped_manifest_only(root)
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "VERSION (" in r.stdout
    return root


def test_the_advice_names_the_manifest_command(project: Path) -> None:
    r = run_cli("status", cwd=project)
    assert f"`jm config version {NEW}`" in r.stdout, r.stdout
    assert "Sync whichever is wrong" not in r.stdout


def test_every_printed_command_clears_the_drift(
    project: Path, tmp_path: Path
) -> None:
    """Run each advised command in its own copy of the stale tree, so one
    command's write cannot make the next one pass by accident."""
    r = run_cli("status", cwd=project)
    commands = ADVICE.findall(r.stdout)
    assert commands, r.stdout
    for i, cmd in enumerate(commands):
        work = tmp_path / f"try{i}"
        shutil.copytree(project, work)
        version = cmd.split()[-1]
        assert run_cli("config", "version", version, cwd=work).returncode == 0
        after = run_cli("status", "--check", cwd=work)
        assert after.returncode == 0, (cmd, after.stdout + after.stderr)
        assert "VERSION (" not in after.stdout, cmd
