"""gh-984: `jm status` reports a component that reaches no C library.

gh-981 shipped for an unknown number of releases because nothing downstream
could report it. The component built, its Python extension imported, `ctest`
passed, the header installed — and the symbols were in no library. `jm status
--check` exited 0 throughout, and the first observer was a C consumer months
later, who could not link.

The finding is the invariant #982's generator gate already encodes, asked of a
real tree instead of a freshly scaffolded one: whatever declares a `_core`
OBJECT library must be folded into every combined library the root declares,
and no wiring line may name a core that no component declares.

Two properties are worth stating, because both were arrived at by measuring
rather than by reasoning:

- **`stale CMakeLists.txt` is not enough on its own.** After #982 the manifest
    replay emits the wiring, so an affected project's root CMakeLists does now
    differ from the replay and `--check` fails. But `stale` says only "apply
    would rewrite this file" — it does not say that components are in no
    library, and a project that has allowed `CMakeLists.txt` (the file is
    `partial`, so it drifts as soon as the author touches it) sees nothing at
    all. UNWIRED names the core, and its per-component allow key means quieting
    the file's routine drift does not quiet this.
- **A dangling line survived every `jm apply`.** `_SUBDIR_BLOCK` requires an
    `add_subdirectory` immediately above, so an orphaned `target_sources` on its
    own was reported by nothing and fixed by nothing — while cmake resolves
    `$<TARGET_OBJECTS:>` at *configure* time, so the project did not build.
    Reporting a finding no command can clear is the shape this repo has already
    paid for once, so `apply` learnt to drop it.

Driven through the CLI, on gh-975's rule: the defect is about what a user sees
from `jm status`, and a repro built on the private API is what got gh-958 filed
wrongly.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

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
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin", "NO_COLOR": "1"},
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A standalone object and a function-only module — gh-981's own shape,
    and one component of each kind so a finding about one is visibly not a
    finding about the other."""
    assert _cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    assert (
        _cli(
            "object", "engine", "--state", "g:double:1.0", cwd=root
        ).returncode
        == 0
    )
    assert _cli("module", "mpsk", cwd=root).returncode == 0
    assert (
        _cli(
            "function",
            "mpsk_map",
            "--module",
            "mpsk",
            "--param",
            "x:double",
            "--return-type",
            "double",
            cwd=root,
        ).returncode
        == 0
    )
    # Reach the state a real project is in before measuring, or every
    # assertion below stands on a tree that was already drifting.
    assert _cli("apply", cwd=root).returncode == 0
    baseline = _cli("status", "--check", cwd=root)
    assert baseline.returncode == 0, baseline.stdout
    return root


def _unwire(root: Path, core: str) -> None:
    """Strip *core*'s wiring, leaving its `add_subdirectory` — the exact state
    a pre-gh981 jm left, where the component builds and ships nothing."""
    p = root / "CMakeLists.txt"
    body = p.read_text(encoding="utf-8")
    stripped = re.sub(
        rf"^target_sources\(\w+ PRIVATE \$<TARGET_OBJECTS:{core}>\)\n",
        "",
        body,
        flags=re.M,
    )
    assert stripped != body, f"scaffold has no {core} wiring to remove"
    assert f"add_subdirectory(native/src/{core[: -len('_core')]})" in stripped
    p.write_text(stripped, encoding="utf-8")


def _allow(root: Path, pattern: str) -> None:
    manifest = root / "just-makeit.toml"
    body = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        body.replace(
            "[project]", f'[project]\nstatus_allow = ["{pattern}"]', 1
        ),
        encoding="utf-8",
    )


# ── The finding ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("core", ["mpsk_core", "engine_core"])
def test_unwired_core_is_reported_and_gates(project: Path, core: str):
    _unwire(project, core)
    r = _cli("status", "--check", cwd=project)
    assert r.returncode == 1, r.stdout
    assert "UNWIRED (1)" in r.stdout, r.stdout
    assert core in r.stdout
    assert "OK — up to date" not in r.stdout


def test_the_report_names_which_libraries_it_is_missing_from(project: Path):
    """gh-981's half-wired state — in the shared library and not the static
    archive — must read as the partial thing it is. That asymmetry is what
    made the original bug look inconsistent rather than systematic."""
    p = project / "CMakeLists.txt"
    body = p.read_text(encoding="utf-8")
    p.write_text(
        body.replace(
            "target_sources(p_lib_static PRIVATE"
            " $<TARGET_OBJECTS:mpsk_core>)\n",
            "",
        ),
        encoding="utf-8",
    )
    r = _cli("status", "--check", cwd=project)
    assert r.returncode == 1
    assert "missing from p_lib_static" in r.stdout, r.stdout
    assert "p_lib," not in r.stdout


def test_the_finding_is_what_gates_not_the_stale_file_beside_it(
    project: Path,
):
    """An exit code of 1 does NOT show this finding gates.

    An unwired tree is also a `stale CMakeLists.txt` — after #982 the replay
    emits the wiring the real file lacks — and that alone makes `--check`
    return 1. Every assertion above about `returncode == 1` is therefore
    satisfied by a jm that reports UNWIRED and counts nothing, which was
    measured: dropping the finding from `drift_count` left this file green.

    The contribution only shows in the *difference* between counting it and
    allowing it, which nothing else here can supply.
    """
    _unwire(project, "mpsk_core")
    counted = json.loads(_cli("status", "--json", cwd=project).stdout)
    _allow(project, "CMakeLists.txt:mpsk_core")
    allowed = json.loads(_cli("status", "--json", cwd=project).stdout)

    assert allowed["unwired_cores"][0]["allowed"] is True
    # Same tree, same stale file; the only difference is whether the unwired
    # core counts.
    assert counted["drift"] == allowed["drift"] + 1


def test_json_carries_it(project: Path):
    _unwire(project, "mpsk_core")
    payload = json.loads(_cli("status", "--json", cwd=project).stdout)
    assert payload["unwired_cores"] == [
        {
            "core": "mpsk_core",
            "component": "mpsk",
            "targets": ["p_lib", "p_lib_static"],
            "allowed": False,
        }
    ]


def test_apply_clears_it(project: Path):
    """The finding must be one a command can act on — a report with no fix is
    how a papercut becomes permanent."""
    _unwire(project, "mpsk_core")
    assert _cli("apply", cwd=project).returncode == 0
    r = _cli("status", "--check", cwd=project)
    assert r.returncode == 0, r.stdout
    assert "UNWIRED" not in r.stdout


# ── Suppression ──────────────────────────────────────────────────────────────


def test_status_allow_on_the_file_exempts_every_core(project: Path):
    """gh-975's contract, kept. A project that has taken its root CMakeLists
    over says so once — jm looks for one exact `target_sources` spelling, so
    an author who links their cores another way would otherwise be handed a
    finding they cannot clear."""
    _unwire(project, "mpsk_core")
    _allow(project, "CMakeLists.txt")
    r = _cli("status", "--check", cwd=project)
    assert "[status_allow]" in r.stdout, r.stdout
    assert "unwired (!)" not in r.stdout


def test_status_allow_can_name_one_core(project: Path):
    """...and the per-component key is why the blanket one is not the only
    option. Exempting a helper core kept out of the library on purpose must
    not also exempt the next component added."""
    _unwire(project, "mpsk_core")
    _unwire(project, "engine_core")
    _allow(project, "CMakeLists.txt:mpsk_core")
    r = _cli("status", "--check", cwd=project)
    assert r.returncode == 1, r.stdout
    assert "UNWIRED (2)" in r.stdout
    assert "engine_core" in r.stdout
    # The allowed one is listed and not counted; the other one gates.
    assert "1 unwired (!)" in r.stdout, r.stdout


# ── The mirror ───────────────────────────────────────────────────────────────


def _orphan_wiring(root: Path) -> None:
    """A `target_sources` for a core no component declares, with no
    `add_subdirectory` above it — what an interrupted removal or a bad merge
    leaves, and what `_SUBDIR_BLOCK` cannot see."""
    p = root / "CMakeLists.txt"
    p.write_text(
        p.read_text(encoding="utf-8").replace(
            "# ── Modules",
            "target_sources(p_lib PRIVATE $<TARGET_OBJECTS:ghost_core>)\n\n"
            "# ── Modules",
            1,
        ),
        encoding="utf-8",
    )


def test_dangling_wiring_is_reported_and_gates(project: Path):
    _orphan_wiring(project)
    r = _cli("status", "--check", cwd=project)
    assert r.returncode == 1, r.stdout
    assert "DANGLING (1)" in r.stdout
    assert "ghost_core" in r.stdout


def test_dangling_is_not_suppressible(project: Path):
    """There is no reading of status_allow under which a tree cmake cannot
    configure is what the author meant."""
    _orphan_wiring(project)
    _allow(project, "CMakeLists.txt")
    r = _cli("status", "--check", cwd=project)
    assert r.returncode == 1, r.stdout
    assert "DANGLING (1)" in r.stdout


def test_apply_drops_dangling_wiring_and_keeps_the_real_ones(project: Path):
    _orphan_wiring(project)
    assert _cli("apply", cwd=project).returncode == 0
    body = (project / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "ghost_core" not in body
    # The components that DO exist keep both of their lines — a sweep that
    # removes the orphan by removing everything would pass the line above.
    for core in ("engine_core", "mpsk_core"):
        assert body.count(f"$<TARGET_OBJECTS:{core}>") == 2, body
    assert _cli("status", "--check", cwd=project).returncode == 0


# ── The detector is armed ────────────────────────────────────────────────────


def test_a_clean_project_reports_neither(project: Path):
    """The negative case, stated once. Every assertion above is about a
    finding appearing; this is the one that would catch a detector wired to
    fire on a correct tree."""
    r = _cli("status", "--check", cwd=project)
    assert r.returncode == 0, r.stdout
    assert "UNWIRED" not in r.stdout
    assert "DANGLING" not in r.stdout
    assert "OK — up to date" in r.stdout


def test_the_detector_reads_the_same_line_the_generator_writes():
    """The property `_libwiring` exists to hold.

    Emitter and detector living in two modules is what let gh-981 happen at
    the generator level, and a detector looking for a line the writer does
    not write is the same failure one layer out — it reports nothing, forever,
    and reads as green. Here it is unfalsifiable by construction: both go
    through `wiring_line`, and this fails if either stops.
    """
    sys.path.insert(0, str(SRC))
    from just_makeit import _libwiring

    line = _libwiring.wiring_line("pkg_lib", "x_core")
    # The writer emits exactly it...
    assert (
        _libwiring.cmake_core_wiring(
            "add_library(pkg_lib SHARED a.c)\n", "pkg", ["x_core"]
        )
        == line
    )
    # ...and the reader finds nothing once it is present.
    assert (
        _libwiring.cmake_core_wiring(
            f"add_library(pkg_lib SHARED a.c)\n{line}", "pkg", ["x_core"]
        )
        == ""
    )
    # ...and the detector's own pattern matches it.
    assert _libwiring._WIRING.match(line)
