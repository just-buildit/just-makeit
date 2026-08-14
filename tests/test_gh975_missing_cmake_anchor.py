"""gh-975: a top CMakeLists with no splice anchor loses the wiring, silently.

Every cmake splice locates its sentinel by string and treats an absent one as
nothing to do — `_splice_cmake_components._insert` returns the text unchanged,
`_add_cmake_block_for` returns False. That value is indistinguishable from
"already correct", so a project whose `CMakeLists.txt` lacks `# ── Modules`
lost its module wiring outright: `jm apply` printed nothing, `jm status
--check` exited 0 saying "OK — up to date", and `cmake --build --target help`
had **zero** targets for the module.

The file is classified `PARTIAL` (gh-959) — `apply` writes only into the
regions it owns and a whole-file diff would report the author's own targets as
jm being behind, forever — so nothing compared it. What can be compared
without reading a line the author wrote is whether jm's **anchors** are still
there: they are machine-written comments that exist for nothing else.

Driven through the CLI rather than the private API, because the defect is
about what a user sees from `jm apply` and `jm status`, and because a repro
built on the private API is what got gh-958 filed wrongly.

The gates here are three:

- `test_the_anchor_set_is_not_stale` — every anchor jm claims must appear in a
    freshly scaffolded project, so the map cannot keep entries for sentinels
    the templates no longer emit.
- `test_no_anchor_is_hard_coded_beside_the_map` — the registration-free half.
    It reads `_apply.py`'s own source and fails on a `# ── ` sentinel literal
    that is not one of the four named constants, so a *new* anchored splice
    cannot be added without deciding whether its anchor is required.
- the behaviour tests, parametrized over the map, so an anchor added to it is
    covered with no edit here.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _apply  # noqa: E402

SRC = Path(__file__).parent.parent / "src"


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A module object and a standalone one, so both anchors carry wiring."""
    r = _cli("new", "p", "--module", "filt", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    root = tmp_path / "p"
    r = _cli(
        "object",
        "biq",
        "--module",
        "filt",
        "--state",
        "g:double:1.0",
        cwd=root,
    )
    assert r.returncode == 0, r.stderr
    r = _cli("object", "gain", "--state", "g:double:1.0", cwd=root)
    assert r.returncode == 0, r.stderr
    # Scaffolding a standalone object after a module one leaves the top
    # CMakeLists and the umbrella header for `apply` to reconcile — true on
    # stock main too, and nothing to do with this issue. Reach the state a
    # real project is in before measuring, or every assertion below is
    # standing on a tree that was already drifting.
    assert _cli("apply", cwd=root).returncode == 0
    baseline = _cli("status", "--check", cwd=root)
    assert baseline.returncode == 0, baseline.stdout
    return root


def _strip_anchor(root: Path, anchor: str) -> None:
    """Remove *anchor*'s whole sentinel block, as an older jm would have.

    The closing `# ─────` rule goes with it: what is being modelled is a file
    that never had the block, not one someone half-deleted.
    """
    p = root / "CMakeLists.txt"
    body = p.read_text(encoding="utf-8")
    m = re.search(rf"\n{re.escape(anchor)}.*?\n# ─+\n", body, re.S)
    assert m, f"scaffold has no {anchor} block to remove"
    p.write_text(body.replace(m.group(0), "\n"), encoding="utf-8")


# ── The gates ────────────────────────────────────────────────────────────────


def test_the_anchor_set_is_not_stale(project: Path):
    """Every anchor jm requires is one a fresh scaffold actually writes.

    A required sentinel the templates stopped emitting would fail every
    project at once, and would do it on the *next* release rather than here.
    """
    body = (project / "CMakeLists.txt").read_text(encoding="utf-8")
    absent = [a for a in _apply.CMAKE_SPLICE_ANCHORS if a not in body]
    assert not absent, f"anchors no scaffold emits: {absent}"


def test_no_anchor_is_hard_coded_beside_the_map(project: Path):
    """A sentinel literal in `_apply.py` must be one of the named constants.

    The map is only the source of truth if nothing splices against a string
    that bypasses it. This reads the module's own source, so a new anchored
    splice added with an inline `"# ── Something"` fails here and has to
    decide, explicitly, whether its anchor is one jm requires.

    Two sabotages, both measured: splicing on an unregistered `"# ── Plugins"`
    fails, and so does quietly deleting an entry from the map — the constant's
    own definition line is a literal, so it survives the deletion and lands on
    the wrong side of this comparison. That second one matters more than it
    looks: the behaviour tests below are parametrized over the map, so an
    entry removed from it would otherwise take its own coverage with it.

    Inlining a literal that IS in the map passes, and should: the map still
    requires the anchor and `status` still checks for it.
    """
    src = (SRC / "just_makeit" / "_apply.py").read_text(encoding="utf-8")
    known = {
        *_apply.CMAKE_SPLICE_ANCHORS,
        # Created on demand rather than spliced into, so its absence is
        # normal and `apply` fixes it — see CMAKE_SPLICE_ANCHORS' docstring.
        _apply._EXTDEPS_BEGIN,
        _apply._EXTDEPS_END,
    }
    literals = {m.group(1) for m in re.finditer(r'"(# ── [^"]*)"', src)}
    assert literals <= known, (
        "sentinel literal(s) in _apply.py that no constant names: "
        f"{sorted(literals - known)}. Add the anchor to "
        "CMAKE_SPLICE_ANCHORS (so `status` requires it) or say in a comment "
        "why its absence is not a lost write."
    )


# ── Behaviour ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("anchor", sorted(_apply.CMAKE_SPLICE_ANCHORS))
def test_status_reports_and_gates(project: Path, anchor: str):
    """The whole defect: `--check` said OK over a tree jm could not maintain.

    Sabotage: drop the `unanchored_entries` term from `drift_count` and the
    exit code goes back to 0 with the section still printed — which is why
    both are asserted.
    """
    _strip_anchor(project, anchor)
    r = _cli("status", "--check", cwd=project)
    assert "UNANCHORED (1)" in r.stdout, r.stdout
    assert f"no `{anchor}` line" in r.stdout
    assert "OK — up to date" not in r.stdout
    assert r.returncode == 1


@pytest.mark.parametrize("anchor", sorted(_apply.CMAKE_SPLICE_ANCHORS))
def test_apply_says_it_could_not_write(project: Path, anchor: str):
    """`apply` printed nothing at all, and then said "nothing to do"."""
    _strip_anchor(project, anchor)
    r = _cli("apply", cwd=project)
    out = r.stdout + r.stderr
    assert f"has no `{anchor}` line" in out, out
    assert "nothing to do" not in out
    # The gating mark, not the advisory one: `status --check` fails on this.
    assert "warning !" in out


def test_json_carries_it(project: Path):
    _strip_anchor(project, "# ── Modules")
    r = _cli("status", "--json", cwd=project)
    payload = json.loads(r.stdout)
    assert payload["unanchored"] == [
        {
            "path": "CMakeLists.txt",
            "anchor": "# ── Modules",
            "allowed": False,
        }
    ]
    assert payload["drift"] == 1


def test_status_allow_suppresses_it(project: Path):
    """A project that wires its own targets says so once.

    Suppressible, unlike gh-426's dropped symbol: the wiring is the author's
    to keep, and jm has no way to know they have not done it in a file it
    never reads.
    """
    _strip_anchor(project, "# ── Modules")
    manifest = project / "just-makeit.toml"
    body = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        body.replace(
            "[project]", '[project]\nstatus_allow = ["CMakeLists.txt"]', 1
        ),
        encoding="utf-8",
    )
    r = _cli("status", "--check", cwd=project)
    assert "[status_allow]" in r.stdout, r.stdout
    assert "1 unanchored (allowed)" in r.stdout
    assert r.returncode == 0


def test_a_pristine_project_is_silent(project: Path):
    """The false-positive direction, on every shape the fixture builds."""
    r = _cli("status", "--check", cwd=project)
    assert "UNANCHORED" not in r.stdout
    assert r.returncode == 0
    r = _cli("apply", cwd=project)
    assert "no `#" not in r.stdout + r.stderr


def test_the_wiring_really_is_lost(project: Path):
    """The outcome the report is about, asserted rather than described.

    Not a proxy for the finding — the reason it gates. With the anchor gone
    the module's `add_subdirectory` cannot be re-spliced by any number of
    `jm apply` runs, so cmake never descends into the module at all.
    """
    _strip_anchor(project, "# ── Modules")
    cm = project / "CMakeLists.txt"
    cm.write_text(
        cm.read_text(encoding="utf-8").replace(
            "add_subdirectory(native/src/filt)\n", ""
        ),
        encoding="utf-8",
    )
    _cli("apply", cwd=project)
    assert "add_subdirectory(native/src/filt)" not in cm.read_text(
        encoding="utf-8"
    )
