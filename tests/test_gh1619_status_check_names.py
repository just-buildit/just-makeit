"""gh-1619: `jm status --check` names every file its exit code counts.

`--check` is the command a downstream's CI runs. It collapses the report to
a summary on purpose, and until gh-1619 that collapse took the MISSING and
STALE listings with it: a tree with one stale file exited 1 and printed
``summary: 39 OK, 0 missing, 1 stale.`` -- the CI log said something was
wrong and never said what.

The oracle is `--json`, not a list of file kinds written here: every entry it
reports missing or stale (and not allowed) must appear in the `--check`
output, so a file kind added later is covered without registering it.

GATE: every path `status --json` counts as missing or stale is printed by
      `status --check`, which still exits 1; the advisory listings stay
      collapsed.
"""

from __future__ import annotations

import json
from pathlib import Path

from _jminc import INC_ROOT
from _jmrun import run_cli


def _drifted(tmp_path: Path) -> Path:
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    # One of each: a regenerated file edited (STALE), a create-only file
    # deleted (MISSING).
    umbrella = root / INC_ROOT / "p.h"
    umbrella.write_text(umbrella.read_text(encoding="utf-8") + "\n", "utf-8")
    (root / "native" / "tests" / "test_g_core.c").unlink()
    return root


def test_check_names_every_file_it_counts(tmp_path):
    root = _drifted(tmp_path)
    data = json.loads(run_cli("status", "--json", cwd=root).stdout)
    counted = {
        e["path"]: e["state"]
        for e in data["entries"]
        if e["state"] in ("missing", "stale") and not e["allowed"]
    }
    # Not vacuous: the fixture really produced both kinds.
    assert set(counted.values()) == {"missing", "stale"}, counted

    check = run_cli("status", "--check", cwd=root)
    assert check.returncode == 1, check.stdout + check.stderr
    lines = check.stdout.splitlines()
    for path, state in counted.items():
        mark = "+" if state == "missing" else "~"
        assert f"  {mark} {path}" in lines, (path, check.stdout)


def test_check_keeps_the_advisory_listings_collapsed(tmp_path):
    """The collapse is still there for what it was for: a `status_allow`ed
    file is not drift, and `--check` does not list it."""
    root = _drifted(tmp_path)
    rel = (root / INC_ROOT / "p.h").relative_to(root).as_posix()
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "[project]\n",
            f'[project]\nstatus_allow = ["{rel}"]\n',
            1,
        ),
        "utf-8",
    )
    plain = run_cli("status", cwd=root).stdout
    assert rel in plain, plain
    check = run_cli("status", "--check", cwd=root).stdout
    assert rel not in check, check
    assert "  + native/tests/test_g_core.c" in check.splitlines(), check
