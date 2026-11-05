"""gh-2488: ``jm status`` compares against ONE read of the project.

``status`` copies the project to a scratch tree, replays the manifest over
the copy, then diffs. The diff's "before" side used to be the LIVE file,
read again after the replay, so anything written in between -- a
``.pytest_cache`` entry, an author's notes, by pytest or an editor while
``make lint`` ran -- differed from the copy and was reported STALE, "`jm
apply` will rewrite", although apply never touches it. doppler's drift
check went red on exactly that.

The window is made deterministic here: ``_apply.run`` is wrapped, so the
hook writes to the REAL tree after the copy and before the comparison,
every time, with no loop and no sleep. Three files:

* ``notes/mine.txt`` and ``.pytest_cache/x``, which jm does not own,
  rewritten during the run: absent from the report, human and JSON.
* ``src/demo/fir.pyi``, owned and stale before status starts, rewritten
  again during the run: this run cannot compare it, so it is reported as
  changed during the run and gates -- never as STALE.
* ``src/demo/amp.pyi``, owned and stale before status starts, untouched
  during the run: still STALE. The regression guard.

A second window sits between the two copies: the snapshot, then the scratch
the replay runs on. ``shutil.copytree`` is wrapped so the live tree changes
right after the first one -- an unowned file, a create-only ``Makefile``
edit, a superseded ``jb.toml``. Every finding must still be the snapshot's:
a scratch copied from the live tree, or an OUTDATED / SUPERSEDED check that
reads the live tree, reports them.

And each temp tree has a parent of its own: a project directory named like
one of them (``jm-snapshot`` crashed ``copytree``) still reports.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _status

_UNOWNED = ("notes/mine.txt", ".pytest_cache/x")
_RACED = "src/demo/fir.pyi"
_STALE = "src/demo/amp.pyi"


@pytest.fixture
def proj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clean two-object project whose replay writes to the real tree."""
    assert run_cli("new", "demo", cwd=tmp_path).returncode == 0
    root = tmp_path / "demo"
    for obj in ("fir", "amp"):
        r = run_cli("object", obj, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    for rel in _UNOWNED:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("before\n", encoding="utf-8")
    # Both owned stubs drift BEFORE status starts, so the replay changes
    # them; only one is written again while it runs.
    for rel in (_RACED, _STALE):
        with (root / rel).open("a", encoding="utf-8") as f:
            f.write("# edited by hand\n")

    real_run = _status._apply.run
    ticks = iter(range(1_000_000))

    def racing_run(*args, **kwargs):
        # Between status's read of the project and its comparison: the
        # moment a concurrent pytest, ruff or editor writes.
        n = next(ticks)
        for rel in (*_UNOWNED, _RACED):
            with (root / rel).open("a", encoding="utf-8") as f:
                f.write(f"# written during the run {n}\n")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(_status._apply, "run", racing_run)
    return root


def test_human_report(proj: Path) -> None:
    r = run_cli("status", cwd=proj)
    out = r.stdout + r.stderr
    for rel in _UNOWNED:
        assert rel not in out, out
    stale_block = out.split("STALE (", 1)[1].split("\n\n", 1)[0]
    assert _STALE in stale_block, out
    assert _RACED not in stale_block, out
    raced_block = out.split("CHANGED WHILE STATUS RAN", 1)[1]
    assert _RACED in raced_block.split("\n\n", 1)[0], out
    assert r.returncode != 0, out


def test_json_report(proj: Path) -> None:
    r = run_cli("status", "--json", cwd=proj)
    for rel in _UNOWNED:
        assert rel not in r.stdout, r.stdout
    rep = json.loads(r.stdout)
    states = {e["path"]: e["state"] for e in rep["entries"]}
    assert states.get(_STALE) == "stale", states
    assert _RACED not in states, states
    assert [e["path"] for e in rep["changed_during_run"]] == [_RACED]
    assert r.returncode != 0


def test_check_gates_on_a_file_it_could_not_compare(proj: Path) -> None:
    # The genuinely stale stub is fixed by hand: only the raced one is
    # left, and `--check` still must not pass over it.
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    with (proj / _RACED).open("a", encoding="utf-8") as f:
        f.write("# edited by hand\n")
    r = run_cli("status", "--check", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "STALE (" not in out, out
    assert "changed-during-run (!)" in out, out
    for rel in _UNOWNED:
        assert rel not in out, out


@pytest.fixture
def late_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> "Iterator[tuple[Path, str]]":
    """A clean project written to between status's two copies.

    Returns the root and the report of an unhooked run, the baseline.
    """
    assert run_cli("new", "demo", cwd=tmp_path).returncode == 0
    root = tmp_path / "demo"
    r = run_cli("object", "fir", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    base = run_cli("status", cwd=root)
    assert base.returncode == 0, base.stdout + base.stderr

    real_copytree = _status.shutil.copytree
    done: list[bool] = []

    def copy_then_write(src, dst, *args, **kwargs):
        out = real_copytree(src, dst, *args, **kwargs)
        # `copytree` recurses through its own global name, so a
        # subdirectory's copy comes through here too: wait for the whole
        # read of the project root.
        if not done and Path(src) == root:
            # Right after the snapshot: the live tree moves on.
            done.append(True)
            (root / "notes").mkdir(exist_ok=True)
            (root / "notes" / "late.txt").write_text("x\n", encoding="utf-8")
            with (root / "Makefile").open("a", encoding="utf-8") as f:
                f.write("# written after the snapshot\n")
            (root / "jb.toml").write_text("", encoding="utf-8")
        return out

    monkeypatch.setattr(_status.shutil, "copytree", copy_then_write)
    yield root, base.stdout
    assert done, "the hook never saw status copy the project root"


def test_findings_are_the_snapshots(late_writes: "tuple[Path, str]") -> None:
    root, base = late_writes
    r = run_cli("status", cwd=root)
    out = r.stdout + r.stderr
    assert "notes/late.txt" not in out, out
    assert "CHANGED WHILE STATUS RAN" not in out, out
    assert "OUTDATED (" not in out, out
    assert "SUPERSEDED (" not in out, out
    assert "jb.toml" not in out, out
    assert r.returncode == 0, out
    assert out == base, out


def test_json_findings_are_the_snapshots(
    late_writes: "tuple[Path, str]",
) -> None:
    root, _base = late_writes
    r = run_cli("status", "--json", cwd=root)
    assert "notes/late.txt" not in r.stdout, r.stdout
    rep = json.loads(r.stdout)
    assert rep["changed_during_run"] == [], rep["changed_during_run"]
    assert rep["outdated"] == [], rep["outdated"]
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize(
    "dirname", ["jm-snapshot", "jm-replay", "snapshot", "scratch", "replay"]
)
def test_a_project_named_like_a_temp_tree(
    tmp_path: Path, dirname: str
) -> None:
    assert run_cli("new", "demo", cwd=tmp_path).returncode == 0
    root = (tmp_path / "demo").rename(tmp_path / dirname)
    r = run_cli("object", "fir", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK — up to date" in r.stdout, r.stdout
