"""gh-2459 (part 2 of 2): under ``[project] layout = "nested"`` a
component's C follows its module.

gh-2427 put a component's header, test and bench under its module's path;
its C stayed in ``native/src/<name>/``. `_incpath.src_dir` is now
``native/src/<core_dir>`` -- ``native/src/<module package>/<name>`` under
nested, ``native/src/<name>`` in the flat and mirrored layouts -- and the
root wiring names that path. A switch, or a module ``package`` change, moves
the C file by file from where jm last put it -- computed from the record
`apply` keeps in the manifest (``applied_layout``, ``applied_package``;
`_incpath.applied`), never from a file in the tree.

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
   is never a former place, in any layout: nothing is listed to move,
   `apply` moves and rewrites none of it, `status --check` is clean.
5. Removing a component whose source directory is an ancestor of another's
   leaves the other's C, and leaves no empty directory behind.
6. The record, not the tree (review of #2481): a component declared over
   a vendored directory carrying jm's hook line, or a verbatim copy of
   another jm project's (F1), and an empty submodule directory the author
   wires (F2), are never a former place, in any layout; nor is a module
   declared since the last `apply`. A flat project carries no record and
   returning to flat removes it; `jm script` never replays it; a project
   switched before the record moves only its C; a file jm has no record of
   placing is named, never moved.

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
    return {m.group(1) for m in _libwiring.jm_blocks(text, cfg)}


def _assert_in_place(root: Path) -> None:
    cfg = C.load(root)
    names = INC.native_names(cfg)
    for name in names:
        d = INC.src_path(root, name, cfg)
        assert (d / "CMakeLists.txt").is_file(), d
        assert INC.src_below(name, cfg) == INC.core_dir(name, cfg)
    deps = set(C.c_deps(cfg))
    assert _wired(root) - deps == {INC.src_below(n, cfg) for n in names}
    # Settled: the record is what is declared, so nothing has a former place.
    assert INC.relocations(root) == []


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
    assert INC.src_dir("fir", INC.applied(cfg)) == "native/src/fir"
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
    was = INC.applied(cfg)
    assert INC.src_dir("fir", was) == "native/src/radio/fir"
    assert INC.src_dir("radio", was) == "native/src/radio/radio"
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
    assert INC.relocations(root) == []
    assert {m.group(1) for m in _libwiring.jm_blocks(text, cfg)}.isdisjoint(
        {"third_party/fir", "third_party/amp", "third_party/dsp"}
    )
    _jm(root, "status", "--check")
    before = _digest(root)
    _jm(root, "apply")
    assert _digest(root) == before

    # Then a switch: jm's own directories move, the vendored tree does not.
    _declare(root, "" if layout == "nested" else "nested")
    cfg = C.load(root)
    was = INC.applied(cfg)
    for name in ("fir", "dsp"):
        assert INC.src_below(name, was) == INC.src_below(
            name, _with(cfg, layout)
        )
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


# ── 6. the record, not the tree (review of #2481) ──────────────────────────

_VENDOR_CMAKE = (
    "add_library(vendor_fir STATIC vendor_fir.c)\n"
    "# local tweaks, if any\n"
    "include(${CMAKE_CURRENT_LIST_DIR}/fir_extra.cmake OPTIONAL)\n"
)


@pytest.mark.parametrize("layout", ["", "mirrored", "nested"])
def test_a_newly_declared_component_never_takes_a_vendored_dir(
    tmp_path, layout
):
    """F1: the author vendors ``native/src/third_party/fir`` (its own target,
    jm's hook line at the end) and a verbatim copy of another jm project's
    ``fir`` directory, wires both, then declares ``fir`` with a dropped-in
    fragment. A component with no record has no former place: `apply`
    writes ``fir`` where it goes and touches none of theirs."""
    _jm(tmp_path, "new", "donor")
    donor = tmp_path / "donor"
    _jm(donor, "object", "fir")
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, layout)
    _jm(root, "object", "amp")
    _jm(root, "apply")
    tp = root / "native" / "src" / "third_party"
    (tp / "fir").mkdir(parents=True)
    (tp / "fir" / "CMakeLists.txt").write_text(_VENDOR_CMAKE, "utf-8")
    (tp / "fir" / "vendor_fir.c").write_text(
        "int vendor_fir(void) { return 42; }\n", "utf-8"
    )
    shutil.copytree(INC.src_path(donor, "fir"), tp / "donor_fir" / "fir")
    top = root / "CMakeLists.txt"
    top.write_text(
        top.read_text(encoding="utf-8")
        + "add_subdirectory(native/src/third_party/fir)\n"
        + "add_subdirectory(native/src/third_party/donor_fir/fir)\n",
        encoding="utf-8",
    )
    foreign = {p: p.read_bytes() for p in tp.rglob("*") if p.is_file()}
    shutil.copy(donor / "objects" / "fir.toml", root / "objects" / "fir.toml")
    assert INC.relocations(root) == []
    _jm(root, "apply")
    for path, data in foreign.items():
        assert path.read_bytes() == data, path
    assert sorted(p for p in tp.rglob("*") if p.is_file()) == sorted(foreign)
    text = top.read_text(encoding="utf-8")
    assert "add_subdirectory(native/src/third_party/fir)\n" in text
    assert "add_subdirectory(native/src/third_party/donor_fir/fir)\n" in text
    assert (INC.src_path(root, "fir") / "fir_core.c").is_file()
    _jm(root, "status", "--check")


@pytest.mark.parametrize("layout", ["", "mirrored", "nested"])
def test_an_empty_wired_directory_is_never_a_place_jm_left(tmp_path, layout):
    """F2: an uninitialised submodule -- an empty ``native/src/ext/amp`` --
    wired by the author, guarded and unguarded. A missing CMakeLists is not
    a record: `status` reports nothing stale, `apply` keeps the lines."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, layout)
    _jm(root, "module", "dsp")
    _jm(root, "object", "amp", "--module", "dsp")
    _jm(root, "apply")
    (root / "native" / "src" / "ext" / "amp").mkdir(parents=True)
    top = root / "CMakeLists.txt"
    top.write_text(
        top.read_text(encoding="utf-8")
        + "if(EXISTS ${CMAKE_SOURCE_DIR}/native/src/ext/amp/CMakeLists.txt)\n"
        + "  add_subdirectory(native/src/ext/amp)\nendif()\n"
        + "add_subdirectory(native/src/ext/amp)\n",
        encoding="utf-8",
    )
    before = top.read_bytes()
    r = run_cli("status", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "STALE" not in r.stdout, r.stdout
    _jm(root, "apply")
    assert top.read_bytes() == before


def test_a_flat_project_carries_no_record(tmp_path):
    """The record is written only where it differs from the default, so a
    flat project's manifest is what it has always been; a nested one
    carries it, and returning to flat takes it out again."""
    root = _scaffold(tmp_path, "")
    texts = {p: p.read_text("utf-8") for p in root.rglob("*.toml")}
    for text in texts.values():
        assert INC.APPLIED_LAYOUT not in text
        assert INC.APPLIED_PACKAGE not in text
    _declare(root, "nested")
    _jm(root, "apply")
    cfg = C.load(root)
    assert cfg["project"][INC.APPLIED_LAYOUT] == "nested"
    assert (
        cfg["module"][_MODULE][INC.APPLIED_PACKAGE]
        == C.module_paths(_MODULE).pypath
    )
    _declare(root, "")
    _jm(root, "apply")
    assert {p: p.read_text("utf-8") for p in root.rglob("*.toml")} == texts


def test_script_does_not_replay_the_record(nested):
    """``applied_*`` are derived: the replayed project's own `apply`
    writes them, so `jm script` never emits them."""
    assert INC.APPLIED_LAYOUT in (nested / "just-makeit.toml").read_text(
        "utf-8"
    )
    r = _jm(nested, "script")
    assert "applied_" not in r.stdout, r.stdout


def test_a_project_switched_before_the_record_moves_only_its_c(tmp_path):
    """A nested project from jm 0.118: headers, tests and benches nested,
    C flat, no record. It reads as last applied flat, so nothing already
    nested is found at a flat place, and its C -- which 0.118 never nested
    -- moves on the first `apply`."""
    root = _scaffold(tmp_path, "nested")
    cfg = C.load(root)
    old = {}
    for name in INC.native_names(cfg):
        here = INC.src_path(root, name, cfg)
        flat = root / "native" / "src" / name
        if here != flat:
            shutil.move(str(here), str(flat))
            old[INC.src_below(name, cfg)] = name
    top = root / "CMakeLists.txt"
    text = top.read_text(encoding="utf-8")
    for below, name in old.items():
        text = text.replace(
            f"add_subdirectory(native/src/{below})",
            f"add_subdirectory(native/src/{name})",
        )
    top.write_text(text, encoding="utf-8")
    for toml in root.rglob("*.toml"):
        toml.write_text(
            re.sub(
                r"^applied_(layout|package) = [^\n]*\n",
                "",
                toml.read_text("utf-8"),
                flags=re.M,
            ),
            encoding="utf-8",
        )
    headers = {p: p.read_bytes() for p in (root / INC.INC_DIR).rglob("*.h")}
    _jm(root, "apply")
    for path, data in headers.items():
        assert path.read_bytes() == data, path
    _assert_in_place(root)
    _jm(root, "status", "--check")


@pytest.mark.parametrize("prefix", [True, False])
def test_a_file_jm_has_no_record_of_is_named_not_moved(tmp_path, prefix):
    """A component moved by hand (or by a jm before the record) sits where
    jm never recorded putting it. jm moves nothing: with a C prefix the
    collision `apply` refuses says where the file is and where it goes
    (gh-2463); without one `status` advises the same."""
    _jm(tmp_path, "new", "p", *(() if prefix else ("--no-c-prefix",)))
    root = tmp_path / "p"
    _jm(root, "module", "dsp")
    _jm(root, "object", "fir", "--module", "dsp")
    _jm(root, "apply")
    here = INC.src_path(root, "fir")
    _declare(root, "nested")
    _jm(root, "apply")
    nested_dir = INC.src_path(root, "fir")
    shutil.move(str(nested_dir), str(here))
    hint = (
        "native/src/fir/CMakeLists.txt belongs to `fir`, but jm has no"
        " record of placing it there, and the manifest puts it at"
        " native/src/dsp/fir/CMakeLists.txt"
    )
    if prefix:
        r = run_cli("apply", cwd=root)
        assert r.returncode != 0
        assert hint in " ".join(r.stderr.split()), r.stderr
        assert "already declares" not in r.stderr, r.stderr
    else:
        r = run_cli("status", cwd=root)
        assert "ADVICE (1)" in r.stdout, r.stdout
        assert "? native/src/fir/CMakeLists.txt" in r.stdout
    assert here.is_dir() and not nested_dir.exists()


@pytest.mark.parametrize("change", ["package", "layout"])
def test_a_module_declared_since_the_last_apply_has_no_former_place(
    tmp_path, change
):
    """F1 for modules: in a nested project jm has recorded, the author
    vendors another jm project's ``gainv`` at ``native/src/third_party/
    gainv`` -- the very place a nested module ``third_party`` puts it --
    then declares that module, holding a ``gainv``, packaged elsewhere or
    with the project switched back to flat. The module carries no
    ``applied_package``: no record, so no former place, and the vendored
    copy stays."""
    _jm(tmp_path, "new", "donor")
    donor = tmp_path / "donor"
    _jm(donor, "object", "gainv")
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, "nested")
    _jm(root, "module", "dsp")
    _jm(root, "object", "fir", "--module", "dsp")
    _jm(root, "apply")
    vendored = root / "native" / "src" / "third_party" / "gainv"
    shutil.copytree(INC.src_path(donor, "gainv"), vendored)
    foreign = {p: p.read_bytes() for p in vendored.rglob("*") if p.is_file()}
    top = root / "CMakeLists.txt"
    line = "add_subdirectory(native/src/third_party/gainv)\n"
    top.write_text(top.read_text(encoding="utf-8") + line, encoding="utf-8")
    if change == "package":
        _jm(root, "module", "third_party", "--package", "vend")
        want = "native/src/vend/gainv"
    else:
        _declare(root, "")
        _jm(root, "module", "third_party")
        want = "native/src/gainv"
    _jm(root, "object", "gainv", "--module", "third_party")
    assert "gainv" in INC.unplaced(root)
    _jm(root, "apply")
    for path, data in foreign.items():
        assert path.read_bytes() == data, path
    assert INC.src_dir("gainv", root) == want
    assert (INC.src_path(root, "gainv") / "gainv_core.c").is_file()
    assert top.read_text(encoding="utf-8").count(line) == 1
    assert "gainv" not in INC.unplaced(root)


# ── 7. jm writes, and moves, only what it can show is its own ─────────────

_VENDOR_ONLY = "add_library(vendor_fir STATIC vendor_fir.c)\n"


def _vendor(
    root: Path,
    below: str,
    kind: str = "vendorfir",
    donor: "Path | None" = None,
) -> dict:
    """A vendored ``native/src/<below>/``, wired by the author; returns its
    bytes, to compare after. Every shape here names files the way jm names
    ``fir``'s, so nothing but jm's own render can tell it from jm's:

    - ``vendorfir``: its own target over ``fir_ext.c`` / ``fir_taps.c``;
    - ``fir_core``: a library that happens to be called ``fir_core``;
    - ``donor``: a verbatim copy of another jm project's ``fir``.
    """
    d = root / "native" / "src" / below
    if kind == "donor":
        shutil.copytree(INC.src_path(donor, "fir"), d)
    else:
        d.mkdir(parents=True)
        lib = "vendorfir" if kind == "vendorfir" else "fir_core"
        (d / "CMakeLists.txt").write_text(
            f"add_library({lib} STATIC fir_ext.c fir_taps.c)\n"
            f"target_include_directories({lib} PUBLIC"
            " ${CMAKE_CURRENT_LIST_DIR})\n",
            "utf-8",
        )
        (d / "fir_ext.c").write_text(
            "int fir_ext_gain(void){return 1;}\n", "utf-8"
        )
        (d / "fir_taps.c").write_text(
            "int fir_taps(void){return 2;}\n", "utf-8"
        )
    top = root / "CMakeLists.txt"
    top.write_text(
        top.read_text("utf-8") + f"add_subdirectory(native/src/{below})\n",
        encoding="utf-8",
    )
    return {p: p.read_bytes() for p in d.rglob("*") if p.is_file()}


def _untouched(root: Path, foreign: dict, below: str) -> None:
    for path, data in foreign.items():
        assert path.read_bytes() == data, path
    d = root / "native" / "src" / below
    assert sorted(p for p in d.rglob("*") if p.is_file()) == sorted(foreign)
    line = f"add_subdirectory(native/src/{below})\n"
    assert (root / "CMakeLists.txt").read_text("utf-8").count(line) == 1


@pytest.mark.parametrize(
    "layout,kind,how",
    [
        (lay, kind, how)
        for lay in ("", "mirrored", "nested")
        for kind in ("vendorfir", "fir_core", "header")
        for how in ("cli", "fragment")
    ]
    # At `jm object` a verbatim copy of another project's `fir` is refused
    # too; declared by a fragment, a copy holding `fir_core.c` is the one
    # shape jm cannot tell from a component it placed (a known limit, in the
    # PR).
    + [(lay, "donor", "cli") for lay in ("", "mirrored", "nested")],
)
def test_a_new_place_holding_a_vendored_dir_is_refused(
    tmp_path, layout, how, kind
):
    """R1: the place a new component goes already holds the author's files
    -- under nested ``native/src/<package>/`` overlaps names like
    ``third_party``. Whatever they are named, and whatever their CMakeLists
    says, they are not what jm renders there: jm refuses, naming them, and
    writes nothing, whether the component comes from `jm object` or a
    fragment. ``header``: a vendored ``fir_core.h`` carrying exactly the
    guard jm would write, in a project with no C prefix."""
    _jm(tmp_path, "new", "donor", "--no-c-prefix")
    _jm(tmp_path / "donor", "object", "fir")
    _jm(tmp_path, "new", "p", "--no-c-prefix")
    root = tmp_path / "p"
    _declare(root, layout)
    _jm(root, "object", "amp")
    _jm(root, "module", "tp", "--package", "third_party")
    _jm(root, "apply")
    probe = C.with_module_member(C.load(root), "tp", "fir")
    below = INC.src_below("fir", probe)
    if kind == "header":
        from just_makeit import _csym

        vh = INC.core_h(root, "fir", probe)
        vh.parent.mkdir(parents=True, exist_ok=True)
        guard = _csym.upper(probe, "fir") + "_CORE_H"
        vh.write_text(
            f"#ifndef {guard}\n#define {guard}\n/* VENDOR */\n#endif\n",
            "utf-8",
        )
        foreign = {vh: vh.read_bytes()}
        named = INC.rel(INC.core_dir("fir", probe), probe)
    else:
        foreign = _vendor(root, below, kind, tmp_path / "donor")
        named = f"native/src/{below}/"
    if how == "cli":
        r = run_cli("object", "fir", "--module", "tp", cwd=root)
    else:
        frag = root / "objects" / "fir.toml"
        frag.write_text(
            (tmp_path / "donor" / "objects" / "fir.toml").read_text("utf-8"),
            encoding="utf-8",
        )
        mod = root / "modules" / "tp.toml"
        mod.write_text(
            mod.read_text("utf-8").replace(
                "objects = []", 'objects = ["fir"]'
            ),
            encoding="utf-8",
        )
        assert '"fir"' in mod.read_text("utf-8")
        r = run_cli("apply", cwd=root)
    assert r.returncode != 0, r.stdout
    text = " ".join(r.stderr.split())
    assert named in text, text
    assert "its own" in text, text
    for path, data in foreign.items():
        assert path.read_bytes() == data, path
    if kind != "header":
        _untouched(root, foreign, below)


@pytest.mark.parametrize("case", ["package", "layout"])
def test_a_record_pointing_at_a_vendored_dir_is_refused(tmp_path, case):
    """N2: a hand-edited (or stale) record names a vendored directory as a
    component's former place. jm checks the record against the tree first:
    the CMakeLists there is not the one jm renders, so it refuses, naming
    both places and the record, and moves and strips nothing."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, "nested" if case == "package" else "mirrored")
    _jm(root, "module", "dsp")
    _jm(root, "object", "fir", "--module", "dsp")
    _jm(root, "apply")
    core = INC.src_path(root, "fir") / "fir_core.c"
    core.write_text(core.read_text("utf-8") + "/* AUTHOR */\n", "utf-8")
    frag = root / "modules" / "dsp.toml"
    if case == "package":
        foreign = _vendor(root, "third_party/fir")
        frag.write_text(
            re.sub(
                r'^applied_package = "[^"]*"',
                'applied_package = "third_party"',
                frag.read_text("utf-8"),
                flags=re.M,
            ),
            encoding="utf-8",
        )
        below = "third_party/fir"
    else:
        foreign = _vendor(root, "dsp/fir")
        top = root / "just-makeit.toml"
        top.write_text(
            top.read_text("utf-8").replace(
                'applied_layout = "mirrored"', 'applied_layout = "nested"'
            ),
            encoding="utf-8",
        )
        frag.write_text(
            frag.read_text("utf-8").replace(
                "[module.dsp]\n", '[module.dsp]\napplied_package = "dsp"\n'
            ),
            encoding="utf-8",
        )
        below = "dsp/fir"
    # ...and a vendored header where that record puts fir's.
    vh = INC.header_root(root) / below / "fir_core.h"
    vh.parent.mkdir(parents=True, exist_ok=True)
    vh.write_text(
        "#ifndef VENDOR_FIR_H\n#define VENDOR_FIR_H\n#endif\n", "utf-8"
    )
    foreign[vh] = vh.read_bytes()
    before = _digest(root)
    for cmd in ("status", "apply"):
        r = run_cli(cmd, cwd=root)
        assert r.returncode != 0, (cmd, r.stdout)
        text = " ".join((r.stdout + r.stderr).split())
        assert f"last placed `fir` at native/src/{below}/" in text, text
        assert "The record may be stale" in text, text
        assert "Merge anything" not in text, text
    assert _digest(root) == before
    # `upgrade` makes the same moves, so it asks the same check first.
    r = run_cli("upgrade", cwd=root)
    assert r.returncode != 0, r.stdout
    assert "The record may be stale" in " ".join(r.stderr.split())
    for path, data in foreign.items():
        assert path.read_bytes() == data, path
    assert core.read_text("utf-8").endswith("/* AUTHOR */\n")


def test_a_package_changed_before_the_first_apply_names_the_fix(tmp_path):
    """N3: a nested project last applied by a jm before the record (its C
    flat, no record) whose package changed before its first apply here.
    jm cannot show the flat directory's CMakeLists is what it renders for
    either package, so it refuses -- saying to apply once before changing
    the package -- instead of a C-prefix collision."""
    root = _scaffold(tmp_path, "nested")
    cfg = C.load(root)
    package = C.module_package_resolved(cfg, _MODULE)
    for name in INC.native_names(cfg):
        here = INC.src_path(root, name, cfg)
        flat = root / "native" / "src" / name
        if here != flat:
            shutil.move(str(here), str(flat))
    top = root / "CMakeLists.txt"
    top.write_text(
        top.read_text("utf-8").replace(
            f"native/src/{package}/", "native/src/"
        ),
        encoding="utf-8",
    )
    for toml in root.rglob("*.toml"):
        toml.write_text(
            re.sub(
                r"^applied_\w+ = [^\n]*\n",
                "",
                toml.read_text("utf-8"),
                flags=re.M,
            ),
            encoding="utf-8",
        )
    frag = root / "modules" / f"{C.module_paths(_MODULE).cname}.toml"
    frag.write_text(
        frag.read_text("utf-8").replace(
            f'[module."{_MODULE}"]\n',
            f'[module."{_MODULE}"]\npackage = "front"\n',
        ),
        encoding="utf-8",
    )
    assert 'package = "front"' in frag.read_text("utf-8")
    before = _digest(root)
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0
    text = " ".join(r.stderr.split())
    assert "jm has no record of placing" in text, text
    assert "run `jm apply` once on this version" in text, text
    assert "choose another c_prefix" not in text, text
    assert _digest(root) == before


def test_a_package_a_merge_dropped_is_named_not_collided(tmp_path):
    """N3: a merge keeps one side's module table, so the package the files
    sit under is in no manifest and no record. jm moves nothing; the
    refusal names where each file is (found by its content, as advice) and
    where the manifest puts it -- not a C-prefix collision."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, "nested")
    _jm(root, "module", "dsp")
    _jm(root, "object", "fir", "--module", "dsp")
    _jm(root, "apply")
    frag = root / "modules" / "dsp.toml"
    plain = frag.read_text("utf-8")
    frag.write_text(
        plain.replace("[module.dsp]\n", '[module.dsp]\npackage = "front"\n'),
        encoding="utf-8",
    )
    _jm(root, "apply")
    frag.write_text(plain, encoding="utf-8")  # the other side's table
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0
    text = " ".join(r.stderr.split())
    assert "native/src/front/fir/CMakeLists.txt belongs to `fir`" in text, text
    assert "choose another c_prefix" not in text, text


def test_only_refuses_while_files_wait_to_move(tmp_path):
    """N4: moving is a whole-project step, recorded by a full apply;
    `apply --only` refuses while a move is pending, and moves nothing."""
    root = _scaffold(tmp_path, "")
    _declare(root, "nested")
    before = _digest(root)
    r = run_cli("apply", "--only=fir", cwd=root)
    assert r.returncode != 0
    assert "Run `jm apply`" in " ".join(r.stderr.split()), r.stderr
    assert _digest(root) == before
    _jm(root, "apply")
    _assert_in_place(root)


def test_a_missing_record_is_advice_not_drift(tmp_path):
    """A project last applied before the record: `status --check` says so
    in jm's own words and does not fail on it, and reports no module
    fragment as stale."""
    root = _scaffold(tmp_path, "nested")
    for toml in root.rglob("*.toml"):
        toml.write_text(
            re.sub(
                r"^applied_\w+ = [^\n]*\n",
                "",
                toml.read_text("utf-8"),
                flags=re.M,
            ),
            encoding="utf-8",
        )
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RECORD" in r.stdout, r.stdout
    assert "STALE" not in r.stdout, r.stdout


def test_an_author_file_at_a_flat_test_path_stays_unrecorded(tmp_path):
    """A mirrored project last applied before the record, with the author's
    own file at the flat test path beside jm's mirrored test. It reads as
    last applied flat, but jm's own build names the mirrored test, not the
    flat one: the flat file is not jm's, so nothing is listed to move and
    no "merge then delete" advice is given about it."""
    root = _scaffold(tmp_path, "mirrored")
    for toml in root.rglob("*.toml"):
        toml.write_text(
            re.sub(
                r"^applied_\w+ = [^\n]*\n",
                "",
                toml.read_text("utf-8"),
                flags=re.M,
            ),
            encoding="utf-8",
        )
    mine = root / "native" / "tests" / "test_fir_core.c"
    mine.write_text("/* AUTHOR-FLAT-PATH */\n", "utf-8")
    assert not [o for o, _n in INC.relocations(root) if (root / o).exists()]
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SUPERSEDED" not in r.stdout, r.stdout
    _jm(root, "apply")
    assert mine.read_text("utf-8") == "/* AUTHOR-FLAT-PATH */\n"
    assert INC.native_path(root, "test", "fir").is_file()


@pytest.mark.parametrize("what", ["src", "header"])
@pytest.mark.parametrize("start", ["", "mirrored"])
def test_a_switch_never_moves_into_a_vendored_dir(tmp_path, start, what):
    """N1, by a switch: under nested ``fir`` of module ``third_party`` goes
    to ``native/src/third_party/fir``, which already holds the author's
    vendored directory. jm's own files may be moved from their verified
    place, but never into a directory jm cannot show is its own: the apply
    is refused and the tree is left exactly as it was."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, start)
    _jm(root, "module", "third_party")
    _jm(root, "object", "fir", "--module", "third_party")
    _jm(root, "apply")
    _declare(root, "nested")
    below = INC.src_below("fir", C.load(root))
    assert below == "third_party/fir"
    if what == "src":
        foreign = _vendor(root, below)
        want = f"which already hold native/src/{below}/CMakeLists.txt"
    else:
        # Only a vendored header where fir's goes.
        vh = INC.core_h(root, "fir")
        vh.parent.mkdir(parents=True)
        vh.write_text("#ifndef VENDOR_H\n#define VENDOR_H\n#endif\n", "utf-8")
        foreign = {vh: vh.read_bytes()}
        want = f"which already hold {INC.core_rel('fir', root)}"
    before = _digest(root)
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0, r.stdout
    assert want in " ".join(r.stderr.split()), r.stderr
    assert _digest(root) == before
    if what == "src":
        _untouched(root, foreign, below)
    for path, data in foreign.items():
        assert path.read_bytes() == data, path


@pytest.mark.parametrize("start", ["", "mirrored"])
def test_a_verified_core_with_riders_jm_keeps_still_moves(tmp_path, start):
    """R2: the recorded place's CMakeLists is jm's render plus a rider jm
    itself keeps across every apply -- an author's ``if(VAR)`` wiring block
    (gh-271). That is still jm's file: the switch verifies it and moves the
    component, rider and all."""
    _jm(tmp_path, "new", "p")
    root = tmp_path / "p"
    _declare(root, start)
    _jm(root, "module", "dsp")
    _jm(root, "object", "fir", "--module", "dsp")
    _jm(root, "apply")
    cml = INC.src_path(root, "fir") / "CMakeLists.txt"
    rider = (
        "if(MY_EXTRA_LIB)\n"
        "  target_link_libraries(fir_core PUBLIC ${MY_EXTRA_LIB})\n"
        "endif()"
    )
    cml.write_text(cml.read_text("utf-8").rstrip("\n") + "\n\n" + rider + "\n")
    _jm(root, "apply")
    assert rider in cml.read_text("utf-8")  # jm keeps it
    _jm(root, "status", "--check")
    _declare(root, "nested")
    r = _jm(root, "apply")
    assert "renamed" in r.stdout, r.stdout
    moved = INC.src_path(root, "fir") / "CMakeLists.txt"
    assert moved != cml and moved.is_file() and not cml.exists()
    assert rider in moved.read_text("utf-8")
    _assert_in_place(root)
    _jm(root, "status", "--check")
