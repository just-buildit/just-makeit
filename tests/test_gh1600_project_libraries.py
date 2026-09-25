"""gh-1600: `[project.libraries.<name>]` -- a project installs more than one
library.

The consumer-facing half -- the .pc, the exported COMPONENTS, the runtime/dev
split, a platform-guarded library absent where it is not built -- is gated by
`scripts/consumer-smoke.sh`'s `delta` package, built and consumed for real.
These are the jm-side rules no consumer build can see: an additional library
is refused when the manifest or the tree cannot build it as declared, and jm
itself never folds a claimed core into lib<pkg> too.

GATE: a declared library renders into the managed install block, applies
      idempotently and leaves `status --check` clean; a manifest or tree it
      cannot be built from is refused before anything is written; jm never
      wires one of its cores into lib<pkg>, and lib<pkg>'s targets are read by
      name, never by prefix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _libwiring as W

_OBJ_CMAKE = (
    "add_library(util_obj OBJECT util.c)\n"
    "set_target_properties(util_obj PROPERTIES POSITION_INDEPENDENT_CODE ON)\n"
)


def _project(tmp_path: Path, table: str) -> Path:
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    util = root / "native" / "src" / "util"
    util.mkdir(parents=True)
    (util / "util.c").write_text("int p_util(void) { return 1; }\n")
    (util / "CMakeLists.txt").write_text(_OBJ_CMAKE)
    text = (root / "just-makeit.toml").read_text(encoding="utf-8")
    text = text.replace("[project]\n", '[project]\nc_deps = ["util"]\n', 1)
    (root / "just-makeit.toml").write_text(text + "\n" + table, "utf-8")
    return root


_UTIL = '[project.libraries.util]\ncores = ["util_obj"]\n'


def test_a_library_renders_applies_idempotently_and_status_is_clean(tmp_path):
    root = _project(tmp_path, _UTIL)
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    cm = (root / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "add_library(p_util_lib SHARED $<TARGET_OBJECTS:util_obj>)" in cm
    assert "target_link_libraries(p_util_lib PUBLIC p_lib)" in cm
    assert 'list(APPEND JM_LIBRARIES "p_util:util")' in cm
    again = run_cli("apply", cwd=root)
    assert "update  CMakeLists.txt" not in again.stdout, again.stdout
    check = run_cli("status", "--check", cwd=root)
    assert check.returncode == 0, run_cli("status", cwd=root).stdout


def test_jm_never_folds_a_claimed_core_into_lib_pkg(tmp_path):
    """A jm component's own core, claimed by a library: every apply would
    wire it into lib<pkg> -- and then refuse the tree it had just written."""
    root = _project(tmp_path, '[project.libraries.gl]\ncores = ["g_core"]\n')
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    pairs = W.folded_pairs(root)
    assert ("p_lib", "g_core") not in pairs, sorted(pairs)
    assert ("p_gl_lib", "g_core") in pairs, sorted(pairs)
    assert run_cli("apply", cwd=root).returncode == 0


def test_a_core_the_tree_does_not_declare_is_refused(tmp_path):
    root = _project(
        tmp_path, '[project.libraries.util]\ncores = ["nope_obj"]\n'
    )
    before = (root / "CMakeLists.txt").read_bytes()
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1
    assert "`nope_obj` is not an OBJECT library the tree declares" in r.stderr
    assert (root / "CMakeLists.txt").read_bytes() == before


def test_a_core_also_folded_into_lib_pkg_is_refused(tmp_path):
    root = _project(tmp_path, _UTIL)
    cm = root / "CMakeLists.txt"
    cm.write_text(
        cm.read_text()
        + "target_sources(p_lib PRIVATE $<TARGET_OBJECTS:util_obj>)\n"
    )
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1
    assert "`util_obj` is also folded into p_lib" in r.stderr, r.stderr


def test_a_core_folded_in_by_lib_pkgs_own_add_library_is_refused(tmp_path):
    """The other spelling of a fold (gh-991): the object as an argument of
    ``add_library(p_lib SHARED ...)`` itself, not a ``target_sources`` line."""
    root = _project(tmp_path, _UTIL)
    cm = root / "CMakeLists.txt"
    text = cm.read_text(encoding="utf-8")
    head = text.index("add_library(p_lib SHARED")
    src = text.index("native/src/p_lib.c)", head)
    cm.write_text(
        text[:src] + "$<TARGET_OBJECTS:util_obj> " + text[src:],
        encoding="utf-8",
    )
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "`util_obj` is also folded into p_lib;" in r.stderr, r.stderr


def test_status_check_names_a_core_hand_folded_into_lib_pkg(tmp_path):
    """The line added after a clean apply: `status` replays the project the
    way apply does, and the replay refuses -- so `--check` fails, by name."""
    root = _project(tmp_path, _UTIL)
    assert run_cli("apply", cwd=root).returncode == 0
    cm = root / "CMakeLists.txt"
    cm.write_text(
        cm.read_text()
        + "target_sources(p_lib PRIVATE $<TARGET_OBJECTS:util_obj>)\n"
    )
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "`util_obj` is also folded into p_lib" in r.stderr, r.stderr


def _under_subdir(root: Path, comp: str, line: str) -> None:
    """Put *line* directly beneath jm's ``add_subdirectory`` for *comp* --
    where jm's own wiring run sits, and where an author would write theirs."""
    cm = root / "CMakeLists.txt"
    text = cm.read_text(encoding="utf-8")
    sub = f"add_subdirectory(native/src/{comp})\n"
    assert text.count(sub) == 1, text
    cm.write_text(text.replace(sub, sub + line), encoding="utf-8")


def test_an_authors_line_under_jms_subdirectory_is_still_the_authors(
    tmp_path,
):
    """jm writes only ``<X>_core`` lines under an ``add_subdirectory`` it
    manages; any other target there is the author's. So the one pattern
    (`_libwiring.SUBDIR_BLOCK`) that apply lifts and that the refusal
    excuses as jm's must not reach it: here it would excuse the fold."""
    root = _project(tmp_path, _UTIL)
    assert run_cli("apply", cwd=root).returncode == 0
    _under_subdir(
        root,
        "util",
        "target_sources(p_lib PRIVATE $<TARGET_OBJECTS:util_obj>)\n",
    )
    r = run_cli("apply", cwd=root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "`util_obj` is also folded into p_lib" in r.stderr, r.stderr


def test_apply_keeps_an_authors_wiring_under_a_no_generate_module(tmp_path):
    """A ``no_generate`` module gets a bare ``add_subdirectory`` and no
    wiring, so its author folds a non-``_core`` core in by hand, beneath it.
    apply replaces jm's blocks from the replay; a pattern that took that line
    as jm's would delete it on every apply."""
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    toml = root / "just-makeit.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8")
        + "\n[module.timing]\nno_generate = true\nobjects = []\n"
        + 'no_generate_reason = "hand-written"\n',
        encoding="utf-8",
    )
    d = root / "native" / "src" / "timing"
    d.mkdir(parents=True)
    (d / "t.c").write_text("int p_t(void) { return 1; }\n")
    (d / "CMakeLists.txt").write_text(
        _OBJ_CMAKE.replace("util_obj", "timing_obj").replace("util.c", "t.c")
    )
    assert run_cli("apply", cwd=root).returncode == 0
    wiring = "".join(
        f"target_sources({t} PRIVATE $<TARGET_OBJECTS:timing_obj>)\n"
        for t in ("p_lib", "p_lib_static")
    )
    _under_subdir(root, "timing", wiring)
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    cm = (root / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "add_subdirectory(native/src/timing)\n" + wiring in cm, cm


@pytest.mark.parametrize(
    "table, why",
    [
        ('[project.libraries.static]\ncores = ["a"]\n', "would collide"),
        ('[project.libraries.p]\ncores = ["a"]\n', "would collide"),
        ("[project.libraries.x]\ncores = []\n", "cores must list"),
        ('[project.libraries.x]\ncores = ["a", "a"]\n', "listed twice"),
        ('[project.libraries.x]\ncores = ["a"]\nbogus = 1\n', "unknown key"),
        (
            '[project.libraries.x]\ncores = ["a"]\n'
            '[project.libraries.y]\ncores = ["a"]\n',
            "already in [project.libraries.x]",
        ),
        (
            '[project.libraries.x]\ncores = ["a"]\nplatforms = ["beos"]\n',
            "unknown platform",
        ),
    ],
    ids=["static", "pkg", "empty", "twice", "key", "two-libs", "platform"],
)
def test_a_manifest_it_cannot_build_is_refused(tmp_path, table, why, capsys):
    root = _project(tmp_path, table)
    with pytest.raises(SystemExit):
        C.project_libraries(C.load(root))
    assert why in capsys.readouterr().err


def test_lib_pkg_is_read_by_name_not_prefix():
    text = (
        "add_library(p_lib SHARED x.c)\nadd_library(p_lib_static STATIC x.c)\n"
        "add_library(p_util_lib SHARED $<TARGET_OBJECTS:u>)\n"
    )
    assert W.lib_targets(text, "p") == ["p_lib", "p_lib_static"]


def test_a_platform_guarded_library_is_wholly_inside_its_guard():
    text = W.libraries_cmake(
        {
            "project": {
                "name": "p",
                "libraries": {
                    "w": {"cores": ["w_obj"], "platforms": ["windows"]}
                },
            }
        }
    )
    lines = text.splitlines()
    assert lines[0] == "if(WIN32)" and lines[-1] == "endif()", text
    assert all(ln.startswith("  ") for ln in lines[1:-1]), text


def test_jm_script_names_the_library_rather_than_dropping_it(tmp_path):
    root = _project(tmp_path, _UTIL)
    text = run_cli("script", cwd=root).stdout
    assert "# NOTE: [project.libraries.util] has no CLI flag" in text, text
