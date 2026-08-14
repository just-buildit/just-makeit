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


# ── Hand-written CMakeLists (gh-988) ─────────────────────────────────────────
#
# Every fixture above is scaffolded by jm, and jm always writes `add_library`
# at column 1 — so none of them can express the input that breaks a
# column-anchored regex. That is why gh-988 shipped: the detector could not
# see a core declared inside a conditional, called its perfectly good wiring
# DANGLING, and `apply` deleted it. A `c_dep` is the shape that gets this
# right, because jm never rewrites its CMakeLists — the file really is the
# author's, indentation and all.


def _c_dep_project(tmp_path: Path) -> Path:
    """A project with a hand-owned `c_dep` whose core is declared inside an
    `if()`, and wired into both libraries from the root.

    Modelled on doppler's `wfmcompose`, where the guard is real (`timing_core`
    is POSIX-only) — the indentation is not a style choice there, it is what a
    conditional target looks like.
    """
    assert _cli("new", "p", "--c-dep", "pacing", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    (root / "native" / "inc" / "pacing").mkdir(parents=True, exist_ok=True)
    (root / "native" / "src" / "pacing").mkdir(parents=True, exist_ok=True)
    (root / "native" / "inc" / "pacing" / "pacing_core.h").write_text(
        "#ifndef PACING_CORE_H\n#define PACING_CORE_H\n"
        "double pacing_now(void);\n#endif\n",
        encoding="utf-8",
    )
    (root / "native" / "src" / "pacing" / "pacing_core.c").write_text(
        '#include "pacing/pacing_core.h"\n'
        "double pacing_now(void) { return 0.0; }\n",
        encoding="utf-8",
    )
    (root / "native" / "src" / "pacing" / "CMakeLists.txt").write_text(
        "# pacing — hand-owned c_dep; jm emits only the add_subdirectory.\n"
        "if(UNIX)\n"
        "    add_library(pacing_core OBJECT\n"
        "        ${CMAKE_SOURCE_DIR}/native/src/pacing/pacing_core.c)\n"
        "    target_include_directories(pacing_core PUBLIC\n"
        "        ${CMAKE_SOURCE_DIR}/native/inc)\n"
        "endif()\n",
        encoding="utf-8",
    )
    cmake = root / "CMakeLists.txt"
    cmake.write_text(
        cmake.read_text(encoding="utf-8").replace(
            "# ── Modules",
            "target_sources(p_lib PRIVATE $<TARGET_OBJECTS:pacing_core>)\n"
            "target_sources(p_lib_static PRIVATE"
            " $<TARGET_OBJECTS:pacing_core>)\n\n# ── Modules",
            1,
        ),
        encoding="utf-8",
    )
    assert _cli("object", "engine", cwd=root).returncode == 0
    assert _cli("apply", cwd=root).returncode == 0
    return root


def test_an_indented_declaration_is_not_reported_dangling(tmp_path: Path):
    """Note what this asserts LAST, and why.

    The fixture ends with `jm apply` to reach a clean baseline — and a broken
    apply *destroys the evidence*: it strips the wiring, after which there is
    no line left to call dangling and `status` is quiet for the wrong reason.
    Measured, not guessed: with the reader reverted to column-1 this test
    passed while its two neighbours went red. Asserting the lines survived is
    what gives it teeth of its own.
    """
    root = _c_dep_project(tmp_path)
    r = _cli("status", "--check", cwd=root)
    assert "DANGLING" not in r.stdout, r.stdout
    assert "pacing_core" not in r.stdout, r.stdout
    assert r.returncode == 0, r.stdout
    body = (root / "CMakeLists.txt").read_text(encoding="utf-8")
    assert body.count("pacing_core") == 2, body


def test_apply_does_not_delete_an_indented_core_s_wiring(tmp_path: Path):
    """The destructive half, and the reason gh-988 was a stop-everything.

    `apply` strips a wiring line whose core it cannot find — so a core it
    cannot SEE gets its correct wiring removed, silently unwiring a real
    symbol from both libraries. That is the failure gh-981 was filed about,
    reintroduced by the fix for it.
    """
    root = _c_dep_project(tmp_path)
    cmake = root / "CMakeLists.txt"
    assert cmake.read_text(encoding="utf-8").count("pacing_core") == 2

    assert _cli("apply", cwd=root).returncode == 0

    body = cmake.read_text(encoding="utf-8")
    assert body.count("pacing_core") == 2, body
    for target in ("p_lib", "p_lib_static"):
        assert (
            f"target_sources({target} PRIVATE"
            " $<TARGET_OBJECTS:pacing_core>)" in body
        ), body
    # ...and the declaration the reader has to find is still indented, so a
    # future reformat of the fixture cannot quietly retire this test.
    decl = (root / "native" / "src" / "pacing" / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    assert "\n    add_library(pacing_core OBJECT" in decl, decl


def test_a_genuinely_orphaned_line_is_still_removed(tmp_path: Path):
    """Reading generously must not stop the strip working at all — otherwise
    the gh-988 fix would trade a false positive for a dead feature."""
    root = _c_dep_project(tmp_path)
    cmake = root / "CMakeLists.txt"
    cmake.write_text(
        cmake.read_text(encoding="utf-8").replace(
            "# ── Modules",
            "target_sources(p_lib PRIVATE $<TARGET_OBJECTS:ghost_core>)\n\n"
            "# ── Modules",
            1,
        ),
        encoding="utf-8",
    )
    assert "DANGLING (1)" in _cli("status", "--check", cwd=root).stdout
    assert _cli("apply", cwd=root).returncode == 0
    body = cmake.read_text(encoding="utf-8")
    assert "ghost_core" not in body
    assert body.count("pacing_core") == 2, body


def test_a_core_declared_in_a_NESTED_cmakelists_is_found(tmp_path: Path):
    """The residual gap gh-988 was one instance of, found by sabotaging.

    Widening the reader to tolerate indentation is not the whole property —
    the reader also has to LOOK in the right places. It globbed
    `native/src/*/CMakeLists.txt`, one level, which was never a decision: it
    is just the shape jm's own scaffolds have. A hand-owned `c_dep` is free
    to nest, and a core the reader misses is one whose correct wiring `apply`
    deletes, exactly as an indented one was.

    This is the test the `read generously` half deserved. The first version
    asserted the *stripper* stayed anchored at column 1 instead, and sabotaging
    that was silent — because the stripper only ever removes a line whose core
    is unknown, so anchoring it changes no outcome that `known` does not
    already decide. Keeping it anchored is defence in depth, not a behaviour;
    saying so in a test would have been decoration.
    """
    root = _c_dep_project(tmp_path)
    nested = root / "native" / "src" / "pacing" / "clock"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "clock_core.c").write_text(
        "int clock_tick(void) { return 0; }\n", encoding="utf-8"
    )
    (nested / "CMakeLists.txt").write_text(
        "if(UNIX)\n"
        "    add_library(clock_core OBJECT\n"
        "        ${CMAKE_SOURCE_DIR}/native/src/pacing/clock/clock_core.c)\n"
        "endif()\n",
        encoding="utf-8",
    )
    cmake = root / "CMakeLists.txt"
    cmake.write_text(
        cmake.read_text(encoding="utf-8").replace(
            "# ── Modules",
            "target_sources(p_lib PRIVATE $<TARGET_OBJECTS:clock_core>)\n"
            "target_sources(p_lib_static PRIVATE"
            " $<TARGET_OBJECTS:clock_core>)\n\n# ── Modules",
            1,
        ),
        encoding="utf-8",
    )

    r = _cli("status", "--check", cwd=root)
    assert "DANGLING" not in r.stdout, r.stdout

    assert _cli("apply", cwd=root).returncode == 0
    body = cmake.read_text(encoding="utf-8")
    assert body.count("clock_core") == 2, body
    assert body.count("pacing_core") == 2, body


def test_wiring_from_a_component_cmakelists_counts(tmp_path: Path):
    """A core reaches the library if ANY CMakeLists says so, not only the root.

    doppler wires its POSIX-only `timing_core` from the component's own
    CMakeLists — deliberately, to keep a conditional out of the jm-managed
    block — and reading only the root called five correctly-shipped cores
    UNWIRED. cmake does not care which file said it; neither should the check.

    This is the same defect as the indented `add_library` one directory over:
    a reader looking in too few places. It is a separate test because it is a
    separate place.
    """
    root = _c_dep_project(tmp_path)
    cmake = root / "CMakeLists.txt"
    # Take the wiring out of the root entirely...
    cmake.write_text(
        re.sub(
            r"^target_sources\(\w+ PRIVATE"
            r" \$<TARGET_OBJECTS:pacing_core>\)\n",
            "",
            cmake.read_text(encoding="utf-8"),
            flags=re.M,
        ),
        encoding="utf-8",
    )
    # ...and put it in the component's own file, under its guard.
    comp = root / "native" / "src" / "pacing" / "CMakeLists.txt"
    comp.write_text(
        comp.read_text(encoding="utf-8").replace(
            "endif()",
            "    target_sources(p_lib PRIVATE"
            " $<TARGET_OBJECTS:pacing_core>)\n"
            "    target_sources(p_lib_static PRIVATE"
            " $<TARGET_OBJECTS:pacing_core>)\n"
            "endif()",
            1,
        ),
        encoding="utf-8",
    )
    r = _cli("status", "--check", cwd=root)
    assert "UNWIRED" not in r.stdout, r.stdout
    assert "pacing_core" not in r.stdout, r.stdout


# ── STALE ALLOW and the finding key (gh-991) ─────────────────────────────────


def test_a_working_wiring_exemption_is_not_called_stale(project: Path):
    """`CMakeLists.txt:<core>` is a FINDING key, not a path.

    The stale-allow scan tests every pattern against the managed-file list, so
    a per-component wiring exemption could never match and was reported as
    suppressing nothing — in the same run that printed `[status_allow]` beside
    the finding it was suppressing. Both cannot be true.

    Worse than contradictory: the message says a leftover pattern "keeps every
    check off", so the reader deletes it, and the one spelling that DOES match
    a managed file is the blanket `CMakeLists.txt` that gh-984 exists to avoid.
    Following the advice re-opens gh-981.
    """
    _unwire(project, "mpsk_core")
    _allow(project, "CMakeLists.txt:mpsk_core")
    r = _cli("status", cwd=project)
    assert "[status_allow]" in r.stdout, r.stdout
    assert "STALE ALLOW" not in r.stdout, r.stdout


def test_an_exemption_naming_no_core_is_still_stale(project: Path):
    """Validated, not merely skipped — otherwise the fix would trade a false
    positive for a blind spot, and a renamed component would leave a pattern
    behind with nothing ever saying so."""
    _allow(project, "CMakeLists.txt:ghost_core")
    r = _cli("status", cwd=project)
    assert "STALE ALLOW (1)" in r.stdout, r.stdout
    assert "CMakeLists.txt:ghost_core" in r.stdout


# ── A core may ship in a library that is not <pkg>_lib (gh-991) ──────────────


def _second_library(root: Path, kind: str, core: str) -> None:
    """Declare an extra library built directly from *core*'s objects, the way
    doppler builds `doppler_stream` — objects as `add_library` ARGUMENTS, not
    via `target_sources`."""
    cmake = root / "CMakeLists.txt"
    cmake.write_text(
        cmake.read_text(encoding="utf-8").replace(
            "# ── Modules",
            f"add_library(extra {kind}\n"
            f"    $<TARGET_OBJECTS:{core}>)\n\n# ── Modules",
            1,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("kind", ["SHARED", "STATIC"])
def test_a_core_in_a_second_library_is_not_unwired(
    tmp_path: Path, project: Path, kind: str
):
    """doppler's shape, and two of its five findings were this.

    `<pkg>_lib` is where jm would put a core, not the only place it may
    legitimately live. A project that builds a second library — doppler's
    `doppler_stream`, POSIX-only and optional — ships those symbols, and there
    is nothing for a reader to do about which library they are in.
    """
    _unwire(project, "mpsk_core")
    _second_library(project, kind, "mpsk_core")
    r = _cli("status", "--check", cwd=project)
    assert "UNWIRED" not in r.stdout, r.stdout
    assert "mpsk_core" not in r.stdout, r.stdout


def test_a_core_only_in_a_python_extension_is_STILL_unwired(project: Path):
    """The exclusion that makes the above safe, and the one that would
    silently retire this entire check if it were ever loosened.

    Every core is linked into a Python extension — that is what jm builds. A
    `MODULE` is not a library a C consumer can link, so counting one would
    answer "shipped" for every component forever, which is precisely the
    gh-981 state: reachable from Python, reachable from no C consumer. That is
    the whole defect, so it gets its own test rather than a comment.
    """
    _unwire(project, "mpsk_core")
    _second_library(project, "MODULE", "mpsk_core")
    r = _cli("status", "--check", cwd=project)
    assert "UNWIRED (1)" in r.stdout, r.stdout
    assert "mpsk_core" in r.stdout


def test_a_core_in_no_library_at_all_is_still_unwired(project: Path):
    """The negative control: reading more places must not mean finding
    everything shipped."""
    _unwire(project, "mpsk_core")
    r = _cli("status", "--check", cwd=project)
    assert "UNWIRED (1)" in r.stdout, r.stdout
