"""gh-1587: `jm script` replays a project's `[project]` dependencies.

`_script` reconstructed `jm new` from four hand-listed flags, so a project
scaffolded with ``--pkg-module``, ``--find-package`` or ``--c-dep`` replayed
as a bare ``jm new`` -- its external-deps block, installed config and ``.pc``
silently lost every dependency (gh-808's class). Each spellable entry now
replays as its flag, quoted where a version bound needs it; a table entry
(``{ name = ..., cflags = ... }``), which has no CLI spelling, is named in a
NOTE.

GATE: a project's `[project]` find_packages, pkg_modules and c_deps survive
      `jm script` and a replay of it, and an entry with no CLI spelling is
      named in the script rather than dropped.
"""

from __future__ import annotations

from pathlib import Path

from _jmrun import replay_script, run_cli
from just_makeit import _config as C

_KEYS = ("find_packages", "pkg_modules", "c_deps")


def _project(tmp_path: Path) -> Path:
    r = run_cli(
        "new", "sp",
        "--pkg-module", "zlib >= 1.2",
        "--find-package", "Threads",
        "--c-dep", "vendored",
        "--object", "g",
        cwd=tmp_path,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "sp"
    cfg = C.load(root)
    cfg["project"]["find_packages"].append(
        {"name": "Hdr", "cflags": "-I/opt/hdr/include"}
    )
    C.save(root, cfg)
    return root


def test_every_spellable_dependency_replays(tmp_path):
    (tmp_path / "a").mkdir()
    orig = _project(tmp_path / "a")
    script = run_cli("script", cwd=orig)
    assert script.returncode == 0, script.stderr
    (tmp_path / "b").mkdir()
    replayed = replay_script(script.stdout, tmp_path / "b")

    want = C.load(orig)["project"]
    got = C.load(replayed)["project"]
    for key in _KEYS:
        spelled = [e for e in want.get(key, []) if isinstance(e, str)]
        assert spelled, key  # the probe declares one of each
        assert got.get(key, []) == spelled, key


def test_a_table_entry_is_named_not_dropped(tmp_path):
    orig = _project(tmp_path)
    text = run_cli("script", cwd=orig).stdout
    assert "# NOTE: [project] find_packages entry" in text
    assert '"Hdr"' in text and '"-I/opt/hdr/include"' in text
