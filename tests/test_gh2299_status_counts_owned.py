"""gh-2299: ``jm status`` counts only the files jm owns as matches.

``status`` copies the project to a scratch tree, replays the manifest over
it, and counts each file the replay left byte-identical. A file jm never
writes -- a ``.pytest_cache`` entry, a ``.ruff_cache`` blob, an author's
notes -- is left identical by definition, so each one counted as a
"manifest-owned file" that matched. Found by doppler: two near-identical
trees reported 3825 and 3811, a difference of exactly their untracked files.

The count is now the files the replay renders plus the manifest it renders
from (``_config.manifest_files``: a fragment is read, never rendered). Two
checks, because each misses what the other catches:

* ``test_a_stray_file_is_not_a_match`` adds the issue's three strays and
  requires the count not to move. Counting the copy again turns it red.
* ``test_a_tree_jm_wrote_is_all_owned`` is the independent oracle: in a
  project only jm has written, every file IS jm's, so the count equals the
  files on disk. Dropping the manifest fragments from the owned set (the
  replay alone does not hold ``objects/<obj>.toml``) turns it red, where the
  first check cannot see it: it drops one file from both counts alike.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _status

#: The issue's reproduction: two tool caches and a file of the author's.
_STRAYS = {
    ".pytest_cache/v/a": "x\n",
    ".ruff_cache/b": "y\n",
    "notes.txt": "z\n",
}


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    """A fresh project with one object, in the split-fragment layout."""
    assert run_cli("new", "demo", cwd=tmp_path).returncode == 0
    root = tmp_path / "demo"
    r = run_cli("object", "fir", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (root / "objects" / "fir.toml").is_file()
    return root


def _ok(root: Path) -> int:
    r = run_cli("status", "--json", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return int(json.loads(r.stdout)["ok"])


def test_a_stray_file_is_not_a_match(proj: Path) -> None:
    before = _ok(proj)
    for rel, text in _STRAYS.items():
        path = proj / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert _ok(proj) == before


def test_a_tree_jm_wrote_is_all_owned(proj: Path) -> None:
    on_disk = [p.as_posix() for p in _status._walk_managed(proj)]
    assert "objects/fir.toml" in on_disk
    assert _ok(proj) == len(on_disk), on_disk
