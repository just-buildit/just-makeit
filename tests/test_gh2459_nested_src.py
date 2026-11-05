"""gh-2459 (part 2 of 2): under ``[project] layout = "nested"`` a
component's C follows its module.

gh-2427 put a component's header, test and bench under its module's path;
its C stayed in ``native/src/<name>/``. `_incpath.src_dir` is now
``native/src/<core_dir>`` -- ``native/src/<module package>/<name>`` under
nested, ``native/src/<name>`` in the flat and mirrored layouts -- and the
root wiring names that path. A switch, or a module ``package`` change, moves
the C file by file from where the root wiring last put it
(`_incpath.recorded_src`), never from a directory matched in the tree.

GATES:

1. Every component's C (each object's, a module's own core with its
   functions and binding, a composer's) sits in its ``src_dir``, the root
   wiring names exactly those paths, and nothing is on record as having
   moved.
2. Switching flat <-> nested and mirrored <-> nested, each way, carries the
   author's C beside the core, a hook beside the CMakeLists and an edited
   test; `status --check` is clean at each step and the round trip is
   byte-identical.
3. Changing a module's ``package`` under nested moves its C, both ways.
4. A foreign tree in jm's own shapes -- a vendored ``native/src/third_party``
   holding a copy of jm's own rendered CMakeLists, wired by the author --
   is never a former place, in any layout: nothing is recorded, `apply`
   moves and rewrites none of it, `status --check` is clean.
5. Removing a component whose source directory is an ancestor of another's
   leaves the other's C, and leaves no empty directory behind.

The e2e build of a nested project (two objects, one depending on the other,
a module function, a standalone object) and of a composer with a bridge is
gh-2427's: its fixtures now scaffold the nested source tree, and
``test_the_nested_project_builds_and_imports`` /
``test_a_composer_bridge_lands_beside_its_core_under_nested`` build them,
run ``ctest`` and import them. Its oracle
(``test_nested_is_mirrored_with_the_module_directories_moved``) moves
``native/src/`` too.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _incpath as INC
from just_makeit import _libwiring
from test_gh2427_nested_layout import (
    _MODULE,
    _assert_settled,
    _author_edits,
    _composer_project,
    _declare,
    _digest,
    _jm,
    _scaffold,
)

_HELPER = "/* AUTHOR: a helper beside the core */\nint fir_helper(void);\n"
_HOOK = "# AUTHOR: my own CMake for fir's directory\n"


def _wired(root: Path) -> "set[str]":
    """The source directories jm's root wiring names."""
    text = (root / "CMakeLists.txt").read_text(encoding="utf-8")
    cfg = C.load(root)
    return {m.group(1) for m in _libwiring.jm_blocks(text, cfg, root)}


def _assert_in_place(root: Path) -> None:
    cfg = C.load(root)
    names = INC.native_names(cfg)
    for name in names:
        d = INC.src_path(root, name, cfg)
        assert (d / "CMakeLists.txt").is_file(), d
        assert INC.src_below(name, cfg) == INC.core_dir(name, cfg)
    deps = set(C.c_deps(cfg))
    assert _wired(root) - deps == {INC.src_below(n, cfg) for n in names}
    assert INC.recorded_src(root, cfg) == {}


# ── 1. every component's C in its src_dir ───────────────────────────────────


@pytest.fixture(scope="module")
def nested(tmp_path_factory) -> Path:
    return _scaffold(tmp_path_factory.mktemp("nested"), "nested")


def test_every_component_s_c_sits_in_its_src_dir(nested):
    _assert_in_place(nested)
    package = C.module_package_resolved(C.load(nested), _MODULE)
    for name in ("fir", "gain", "rx_ss_burst"):
        assert INC.src_dir(name, nested) == f"native/src/{package}/{name}"
        assert not (nested / "native" / "src" / name).exists(), name
    # The module's own core and its function, and each object's binding
    # fragment, sit in the module's directory.
    module_dir = INC.src_path(nested, "rx_ss_burst")
    for f in (
        "rx_ss_burst_core.c",
        "clip.c",
        "rx_ss_burst_ext.c",
        "rx_ss_burst_ext_fir.c",
        "rx_ss_burst_ext_gain.c",
    ):
        assert (module_dir / f).is_file(), f
    # A standalone object does not move.
    assert INC.src_dir("amp", nested) == "native/src/amp"
    _jm(nested, "status", "--check")


def test_a_composer_s_c_sits_in_its_src_dir(tmp_path):
    root = _composer_project(tmp_path, "nested")
    _assert_in_place(root)
    assert INC.src_dir("playlist", root) == "native/src/playlist/playlist"
    # A c_dep is the author's, and stays where it is wired.
    assert (root / "native" / "src" / "backing" / "CMakeLists.txt").is_file()
    assert "backing" in _wired(root)
    _jm(root, "status", "--check")


@pytest.fixture(scope="module")
def mirrored(tmp_path_factory) -> Path:
    return _scaffold(tmp_path_factory.mktemp("mirrored"), "mirrored")


def test_a_nested_source_file_is_classified_as_its_flat_twin(nested, mirrored):
    """What `apply` may rewrite and what is the author's (`_createonly`) is
    asked of each file: a nested source file is answered as the same file
    in a layout that keeps one level -- the sacred ``_core.c`` stays the
    author's, the binding stays jm's."""
    from just_makeit import _createonly

    checked = 0
    for p in sorted((nested / "native" / "src").rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(nested).as_posix()
        twin = INC.src_layout_free(rel, nested)
        got = _createonly.classify(rel, nested)
        want = _createonly.classify(twin, mirrored)
        assert (got and got.kind) == (want and want.kind), rel
        checked += rel != twin
    assert checked >= 8, checked
    core = _createonly.classify(
        INC.src_rel("fir", "fir_core.c", nested), nested
    )
    assert core is not None and core.kind == _createonly.AUTHOR


def test_a_c_dep_never_nests(tmp_path):
    """A ``c_deps`` directory is the author's, wired where it is: a c_dep
    sharing a module member's name keeps ``native/src/<dep>``."""
    from just_makeit import _apply

    cfg = {
        "project": {"name": "p", "layout": "nested", "c_deps": ["fir"]},
        "module": {"rx": {"objects": ["fir"]}},
        "fir": {},
    }
    assert INC.src_dir("fir", cfg) == "native/src/rx/fir"
    text = (
        f"{_libwiring.COMPONENTS_SENTINEL}\n# ──\n\n"
        f"{_libwiring.MODULES_SENTINEL}\n# ──\n"
    )
    real, temp = tmp_path / "real.txt", tmp_path / "temp.txt"
    real.write_text(text, encoding="utf-8")
    temp.write_text(text, encoding="utf-8")
    _apply._splice_cmake_components(real, temp, cfg)
    assert INC.c_dep_line("fir") in real.read_text(encoding="utf-8")
    assert "native/src/rx/fir" not in real.read_text(encoding="utf-8")


# ── 2. switching carries the author's C, both ways ─────────────────────────


def _native(root: Path) -> "dict[str, str]":
    """:func:`_digest` of the C tree and the root wiring: a ``package``
    change also moves the module's Python, which is not this gate's."""
    return {
        k: v
        for k, v in _digest(root).items()
        if k.startswith("native/") or k == "CMakeLists.txt"
    }


def _author_c(root: Path) -> None:
    """The author's own C beside fir's core, and its directory's hook."""
    d = INC.src_path(root, "fir")
    (d / "fir_helper.c").write_text(_HELPER, encoding="utf-8")
    (d / "fir_extra.cmake").write_text(_HOOK, encoding="utf-8")


def _assert_author_c(root: Path) -> None:
    d = INC.src_path(root, "fir")
    assert (d / "fir_helper.c").read_text(encoding="utf-8") == _HELPER
    assert (d / "fir_extra.cmake").read_text(encoding="utf-8") == _HOOK


@pytest.mark.parametrize("start", ["", "mirrored"])
def test_switching_to_nested_and_back_moves_the_c(tmp_path, start):
    root = _scaffold(tmp_path, start)
    _author_edits(root)
    _author_c(root)
    old = INC.src_rel("fir", "fir_helper.c", root)
    before = _digest(root)
    _assert_in_place(root)

    _declare(root, "nested")
    cfg = C.load(root)
    assert INC.recorded_src(root, cfg)["fir"] == "fir"
    r = run_cli("status", cwd=root)
    assert old in r.stdout.split("SUPERSEDED", 1)[-1], r.stdout
    r = _jm(root, "apply")
    assert "Materialized 0 new file(s)" in r.stdout, r.stdout
    assert f"renamed {old} -> " in r.stdout
    assert INC.src_dir("fir", root) == "native/src/rx/ss/burst/fir"
    _assert_author_c(root)
    _assert_settled(root)
    _assert_in_place(root)

    _declare(root, start)
    _jm(root, "apply")
    _assert_author_c(root)
    _assert_settled(root)
    _assert_in_place(root)
    after = _digest(root)
    assert {k for k in after if after.get(k) != before.get(k)} | {
        k for k in before if k not in after
    } == set()


def test_upgrade_moves_the_c_too(tmp_path):
    root = _scaffold(tmp_path, "")
    _author_c(root)
    _declare(root, "nested")
    _jm(root, "upgrade")
    # Moved by `upgrade` alone; `apply` then rewires the root, as it
    # re-renders every glue file `upgrade` leaves.
    _assert_author_c(root)
    assert not (root / "native" / "src" / "fir").exists()
    _jm(root, "apply")
    _assert_author_c(root)
    _assert_in_place(root)
    _jm(root, "status", "--check")


def test_a_module_under_an_objects_own_name_moves_only_its_files(tmp_path):
    """An object ``fir`` of ``[module."fir.sub"]``: its flat ``native/src/fir``
    becomes an ancestor of its nested ``native/src/fir/sub/fir``. The files
    jm names after it move one by one -- never the directory into itself --
    and a file of the author's not named for it stays where it was."""
    root = tmp_path / "p"
    _jm(tmp_path, "new", "p")
    _jm(root, "module", "fir.sub")
    _jm(root, "object", "fir", "--module", "fir.sub")
    _jm(root, "apply")
    old = INC.src_path(root, "fir")
    (old / "fir_helper.c").write_text(_HELPER, encoding="utf-8")
    (old / "NOTES.md").write_text("mine\n", encoding="utf-8")
    _declare(root, "nested")
    _jm(root, "apply")
    new = INC.src_path(root, "fir")
    assert new == old / "sub" / "fir"
    assert (new / "fir_helper.c").read_text(encoding="utf-8") == _HELPER
    assert (new / "fir_core.c").is_file()
    assert not (old / "fir_core.c").exists()
    assert (old / "NOTES.md").read_text(encoding="utf-8") == "mine\n"
    _jm(root, "status", "--check")


# ── 3. a package change moves the C ─────────────────────────────────────────


def _set_package(root: Path, package: "str | None") -> None:
    frag = root / "modules" / "radio.toml"
    text = frag.read_text(encoding="utf-8")
    text = re.sub(r'^package = "[^"]*"\n', "", text, flags=re.M)
    if package:
        text = text.replace(
            "[module.radio]\n", f'[module.radio]\npackage = "{package}"\n', 1
        )
    frag.write_text(text, encoding="utf-8")


def test_changing_a_modules_package_moves_its_c(tmp_path):
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, "nested")
    _jm(root, "module", "radio")
    _jm(root, "object", "fir", "--module", "radio")
    _jm(root, "apply")
    assert (root / "modules" / "radio.toml").is_file()
    _author_c(root)
    before = _native(root)
    assert INC.src_dir("fir", root) == "native/src/radio/fir"

    _set_package(root, "front/end")
    cfg = C.load(root)
    assert INC.recorded_src(root, cfg) == {
        "fir": "radio/fir",
        "radio": "radio/radio",
    }
    _jm(root, "apply")
    assert INC.src_dir("fir", root) == "native/src/front/end/fir"
    _assert_author_c(root)
    assert not (root / "native" / "src" / "radio").exists()
    _assert_in_place(root)
    _jm(root, "status", "--check")

    _set_package(root, None)
    _jm(root, "apply")
    _assert_author_c(root)
    _assert_in_place(root)
    _jm(root, "status", "--check")
    assert _native(root) == before


# ── 4. a foreign tree is never a former place ───────────────────────────────


@pytest.mark.parametrize("layout", ["", "mirrored", "nested"])
def test_a_foreign_tree_in_jms_shape_is_never_a_former_place(tmp_path, layout):
    """A vendored ``native/src/third_party/`` the author wires: ``fir`` and
    ``amp`` hold a copy of jm's own rendered CMakeLists for that name --
    jm's hook line and all -- and ``dsp`` a CMakeLists of its own. In every
    layout a settled project's root wires each component where it is, so
    the root wiring records no former place and nothing moves; and when the
    project then switches layout, only jm's own directories move: the
    vendored ``dsp``, wired ahead of jm's line for the module, has no jm
    hook and is never taken for the module's old place."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, layout)
    _jm(root, "module", "dsp")
    _jm(root, "object", "fir", "--module", "dsp")
    _jm(root, "object", "amp")
    _jm(root, "apply")
    tp = root / "native" / "src" / "third_party"
    for name in ("fir", "amp", "dsp"):
        d = tp / name
        d.mkdir(parents=True)
        if name == "dsp":
            (d / "CMakeLists.txt").write_text("# vendored\n", "utf-8")
        else:
            shutil.copy(
                INC.src_path(root, name) / "CMakeLists.txt",
                d / "CMakeLists.txt",
            )
        (d / f"{name}_core.c").write_text("/* vendored */\n", "utf-8")
        (d / f"{name}_helper.c").write_text("/* vendored */\n", "utf-8")
    top = root / "CMakeLists.txt"
    text = top.read_text(encoding="utf-8")
    # One wired at the end of the file, two at the end of jm's Components
    # run -- ahead of jm's own line for the module.
    text += "add_subdirectory(native/src/third_party/fir)\n"
    close = "\n# ── Modules"
    text = text.replace(
        close,
        "add_subdirectory(native/src/third_party/amp)\n"
        "add_subdirectory(native/src/third_party/dsp)\n" + close,
        1,
    )
    top.write_text(text, encoding="utf-8")
    foreign = {p: p.read_bytes() for p in tp.rglob("*") if p.is_file()}
    lines = [
        ln
        for ln in text.splitlines()
        if ln.startswith("add_subdirectory(native/src/third_party/")
    ]
    assert len(lines) == 3

    cfg = C.load(root)
    assert INC.recorded_src(root, cfg) == {}
    moved = [o for o, _n in INC.relocations(root) if (root / o).exists()]
    assert moved == [], moved
    assert {
        m.group(1) for m in _libwiring.jm_blocks(text, cfg, root)
    }.isdisjoint({"third_party/fir", "third_party/amp", "third_party/dsp"})
    _jm(root, "status", "--check")
    before = _digest(root)
    _jm(root, "apply")
    assert _digest(root) == before

    # Then a switch: jm's own directories move, the vendored tree does not.
    _declare(root, "" if layout == "nested" else "nested")
    cfg = C.load(root)
    assert INC.recorded_src(root, cfg) == {
        "fir": INC.src_below("fir", _with(cfg, layout)),
        "dsp": INC.src_below("dsp", _with(cfg, layout)),
    }
    _jm(root, "apply")
    for path, data in foreign.items():
        assert path.read_bytes() == data, path
    text = top.read_text(encoding="utf-8")
    for line in lines:
        assert text.count(line + "\n") == 1, line
    _assert_in_place(root)
    _jm(root, "status", "--check")


def _with(cfg: dict, layout: str) -> dict:
    """*cfg* declaring *layout* (``""``: none)."""
    project = {k: v for k, v in cfg["project"].items() if k != "layout"}
    if layout:
        project["layout"] = layout
    return {**cfg, "project": project}


# ── 5. remove takes only its own part ──────────────────────────────────────


def test_remove_keeps_a_component_s_c_below_the_removed_one(tmp_path):
    """A standalone ``rx`` sits above every member of ``[module."rx.ss"]``:
    removing it takes its own files, never the members' C."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, "nested")
    _jm(root, "object", "rx")
    _jm(root, "module", "rx.ss")
    _jm(root, "object", "fir", "--module", "rx.ss")
    _jm(root, "apply")
    inner = INC.src_path(root, "fir")
    assert INC.src_path(root, "rx") in inner.parents
    core = inner / "fir_core.c"
    core.write_text(
        core.read_text(encoding="utf-8") + "/* AUTHOR */\n", encoding="utf-8"
    )
    kept = {p: p.read_bytes() for p in inner.parent.rglob("*.c")}
    _jm(root, "remove", "object", "rx", "--force")
    assert not (INC.src_path(root, "rx") / "rx_core.c").exists()
    for path, data in kept.items():
        assert path.read_bytes() == data, path
    _jm(root, "status", "--check")

    _jm(root, "remove", "module", "rx.ss", "--force")
    empty = [
        p.relative_to(root).as_posix()
        for p in (root / "native").rglob("*")
        if p.is_dir() and not any(p.iterdir())
    ]
    assert empty == []
    assert not (root / "native" / "src" / "rx").exists()
