"""Every component's OBJECT library reaches the combined C library (gh-981).

A generated project ships two combined C libraries built from the same
sources — ``lib<pkg>.so`` and ``lib<pkg>.a`` — and a component contributes to
them only through an explicit

    target_sources(<pkg>_lib{,_static} PRIVATE $<TARGET_OBJECTS:<X>_core>)

line in the *root* CMakeLists. Nothing about building the component implies
that line: its OBJECT library compiles, the Python extension links it
directly, the C test links it directly, `jm status --check` is clean and the
whole Python suite passes with the symbol in no shipped library at all. The
only observer is a C consumer, who gets `undefined reference`.

That silence is why the emitter was allowed to fork into three copies which
each shipped a different subset:

===============================  ========  ===============
path                             ``_lib``  ``_lib_static``
===============================  ========  ===============
standalone object                yes       **no**
object inside a module           yes       yes
a module's own core (functions)  **no**    **no**
===============================  ========  ===============

So the gate cannot be "assert `_module.py` calls the helper" — that is a
description of today's fix, and the next generator to grow its own path
would pass it. It asserts the *invariant*, derived from the tree: whatever
declares a ``_core`` OBJECT library must be folded into every combined
library the root declares. A new component kind is covered the moment a
shape using it is scaffolded, with no list to update.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from just_makeit._apply import run as jm_apply
from just_makeit._function import run as jm_function
from just_makeit._module import run as jm_module
from just_makeit._new import run as jm_new
from just_makeit._object import run as jm_object
from just_makeit._remove import run as jm_remove


# ── The invariant ────────────────────────────────────────────────────────────


def declared_cores(proj: Path) -> dict[str, str]:
    """Every ``<X>_core`` OBJECT library the project declares -> its native
    dir. Read out of the generated ``native/src/*/CMakeLists.txt`` files, so
    the expectation is derived from the tree rather than from a list here."""
    found: dict[str, str] = {}
    for cmake in sorted((proj / "native" / "src").glob("*/CMakeLists.txt")):
        text = cmake.read_text(encoding="utf-8")
        for core in re.findall(r"^add_library\((\w+) OBJECT\b", text, re.M):
            found[core] = cmake.parent.name
    return found


def lib_targets(proj: Path) -> list[str]:
    """The combined C library targets the root CMakeLists declares."""
    text = (proj / "CMakeLists.txt").read_text(encoding="utf-8")
    return re.findall(r"^add_library\((\w+_lib(?:_static)?) ", text, re.M)


def unwired(proj: Path) -> list[str]:
    """``"<core> -> <target>"`` for every OBJECT library that is missing from
    a combined library. Empty is the invariant holding."""
    root_text = (proj / "CMakeLists.txt").read_text(encoding="utf-8")
    targets = lib_targets(proj)
    assert targets, "project declares no combined C library to check against"
    return [
        f"{core} -> {target}"
        for core in declared_cores(proj)
        for target in targets
        if f"target_sources({target} PRIVATE $<TARGET_OBJECTS:{core}>)"
        not in root_text
    ]


def dangling(proj: Path) -> list[str]:
    """``target_sources`` lines naming a ``_core`` no component declares.

    The mirror of :func:`unwired`, and the failure mode a removal introduces:
    CMake rejects a `$<TARGET_OBJECTS:>` for a target that does not exist, and
    it does so at *configure* time, so the project stops building entirely.
    """
    root_text = (proj / "CMakeLists.txt").read_text(encoding="utf-8")
    cores = declared_cores(proj)
    return [
        core
        for core in re.findall(
            r"^target_sources\(\w+ PRIVATE \$<TARGET_OBJECTS:(\w+)>\)",
            root_text,
            re.M,
        )
        if core not in cores
    ]


# ── The shapes ───────────────────────────────────────────────────────────────


def _standalone(proj: Path) -> None:
    """A standalone object: its own `.so`, wired under `# ── Components`."""
    jm_new("proj", proj)
    jm_object(proj, "engine", None, state_vars=[("gain", "double", "1.0")])


def _new_with_object(proj: Path) -> None:
    """`jm new --object` — the same shape, written by a different path."""
    jm_new("proj", proj, object_names=["engine"])


def _module_object(proj: Path) -> None:
    """An object inside a module: two cores, one shared `.so`."""
    jm_new("proj", proj)
    jm_module(proj, "filt")
    jm_object(proj, "fir", "filt", state_vars=[("n", "int", "4")])


def _bare_module(proj: Path) -> None:
    """A module and nothing in it yet. Its `<mod>_core` already exists and is
    already compiled, so it must already be wired — and this is the one shape
    that reaches `_module.run` without any later command regenerating the
    module behind it, which is what makes it load-bearing here rather than a
    weaker variant of `function_only_module`."""
    jm_new("proj", proj)
    jm_module(proj, "mpsk")


def _function_only_module(proj: Path) -> None:
    """gh-981's own shape: a module whose entire C surface is functions, so
    its `<mod>_core` is the only place its symbols live."""
    jm_new("proj", proj)
    jm_module(proj, "mpsk")
    jm_function(
        proj,
        "mpsk_map",
        module="mpsk",
        params=[("x", "double")],
        return_type="double",
        impl_body="return x;",
    )


def _collocated_module(proj: Path) -> None:
    """A module named after one of its objects: the object's `add_library`
    stands in for the module's own, so exactly one core must be wired."""
    jm_new("proj", proj)
    jm_module(proj, "agc")
    jm_object(proj, "agc", "agc", state_vars=[("g", "double", "1.0")])


def _nested_module(proj: Path) -> None:
    """A dotted module id — the native dir and the CMake target are the cname
    (`dsp_filters`), not the dotted name, so the wiring must key off that."""
    jm_new("proj", proj)
    jm_module(proj, "dsp.filters")
    jm_object(proj, "fir", "dsp.filters", state_vars=[("n", "int", "4")])


def _mixed(proj: Path) -> None:
    """Every shape at once, in one project — the arrangement doppler has, and
    the one where a per-object emitter looks like it works because some
    object's `depends_on` happens to rescue an unrelated module."""
    jm_new("proj", proj)
    jm_object(proj, "engine", None, state_vars=[("gain", "double", "1.0")])
    jm_module(proj, "filt")
    jm_object(proj, "fir", "filt", state_vars=[("n", "int", "4")])
    jm_module(proj, "util")
    jm_function(
        proj,
        "saturate",
        module="util",
        params=[("x", "double")],
        return_type="double",
        impl_body="return x;",
    )


SHAPES = {
    "standalone": _standalone,
    "new_with_object": _new_with_object,
    "module_object": _module_object,
    "bare_module": _bare_module,
    "function_only_module": _function_only_module,
    "collocated_module": _collocated_module,
    "nested_module": _nested_module,
    "mixed": _mixed,
}


@pytest.fixture(params=sorted(SHAPES))
def shape(request, tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    SHAPES[request.param](proj)
    return proj


# ── The gate ─────────────────────────────────────────────────────────────────


def test_every_core_reaches_every_combined_library(shape: Path):
    assert unwired(shape) == []


def test_no_wiring_names_a_core_that_does_not_exist(shape: Path):
    assert dangling(shape) == []


def test_static_and_shared_carry_the_same_cores(shape: Path):
    """The two libraries are built from the same sources and installed as a
    pair, so a core in one and not the other ships a `lib<pkg>.a` that is a
    subset of the `.so` — which is how the standalone path shipped an archive
    holding nothing but `<pkg>_version` for as long as it did."""
    root_text = (shape / "CMakeLists.txt").read_text(encoding="utf-8")
    per_target: dict[str, set[str]] = {}
    for target, core in re.findall(
        r"^target_sources\((\w+) PRIVATE \$<TARGET_OBJECTS:(\w+)>\)",
        root_text,
        re.M,
    ):
        per_target.setdefault(target, set()).add(core)
    assert set(per_target) == set(lib_targets(shape))
    assert len(set(map(frozenset, per_target.values()))) == 1


# ── The gate is armed ────────────────────────────────────────────────────────
#
# Every assertion above passes on an empty result set, and gh-981 is exactly a
# case where the tree looked complete because the wrong thing was enumerated.
# These two PERFORM the drift rather than describing it: they break a correct
# project the way each real bug broke it, and require the detector to say so.


@pytest.mark.parametrize("dropped", ["_lib", "_lib_static"])
def test_detector_catches_a_stripped_wiring_line(tmp_path: Path, dropped: str):
    proj = tmp_path / "proj"
    _function_only_module(proj)
    assert unwired(proj) == []

    cmake = proj / "CMakeLists.txt"
    kept = [
        ln
        for ln in cmake.read_text(encoding="utf-8").splitlines(keepends=True)
        if not (
            ln.startswith(f"target_sources(proj{dropped} PRIVATE")
            and "mpsk_core" in ln
        )
    ]
    cmake.write_text("".join(kept), encoding="utf-8")

    assert unwired(proj) == [f"mpsk_core -> proj{dropped}"]


def test_detector_catches_a_core_declared_but_never_wired(tmp_path: Path):
    """The gh-981 shape itself: a component whose `add_subdirectory` is
    present — so it builds, and the project looks wired — while nothing folds
    its core into a library."""
    proj = tmp_path / "proj"
    _mixed(proj)
    assert unwired(proj) == []

    cmake = proj / "CMakeLists.txt"
    text = re.sub(
        r"^target_sources\(\w+ PRIVATE \$<TARGET_OBJECTS:util_core>\)\n",
        "",
        cmake.read_text(encoding="utf-8"),
        flags=re.M,
    )
    assert "add_subdirectory(native/src/util)" in text
    cmake.write_text(text, encoding="utf-8")

    assert sorted(unwired(proj)) == [
        "util_core -> proj_lib",
        "util_core -> proj_lib_static",
    ]


# ── Retrofit and removal ─────────────────────────────────────────────────────


def test_apply_retrofits_a_project_missing_the_wiring(tmp_path: Path):
    """A project scaffolded before gh-981 has the lines nowhere on disk, and
    `jm apply` is the documented way to pick up wiring a newer jm emits. It
    replays the manifest through the real generators into a temp tree and
    reconciles the root CMakeLists against it, so this needs no retrofit code
    of its own — but that is a property of the splice, not a guarantee, and it
    is what the affected downstream projects will actually run."""
    proj = tmp_path / "proj"
    _mixed(proj)
    cmake = proj / "CMakeLists.txt"
    stripped = re.sub(
        r"^target_sources\(\w+ PRIVATE \$<TARGET_OBJECTS:\w+_core>\)\n",
        "",
        cmake.read_text(encoding="utf-8"),
        flags=re.M,
    )
    cmake.write_text(stripped, encoding="utf-8")
    # engine_core, fir_core, filt_core, util_core — across both libraries.
    assert len(unwired(proj)) == 8

    jm_apply(proj)

    assert unwired(proj) == []
    assert dangling(proj) == []


def test_touching_a_pre_gh981_module_rewires_it_without_apply(tmp_path: Path):
    """A project scaffolded before gh-981 heals on the next command that
    regenerates the module, not only on an explicit `jm apply`.

    `_regenerate_module_now` rewrites the module's own CMakeLists on `jm
    function` / `jm method` / `jm object --module` / `jm view`, and reconciles
    the root against what it just wrote. Without that, an affected project
    stays broken through every ordinary edit and only a command nobody has a
    reason to run repairs it.
    """
    proj = tmp_path / "proj"
    _function_only_module(proj)
    cmake = proj / "CMakeLists.txt"
    cmake.write_text(
        re.sub(
            r"^target_sources\(\w+ PRIVATE \$<TARGET_OBJECTS:mpsk_core>\)\n",
            "",
            cmake.read_text(encoding="utf-8"),
            flags=re.M,
        ),
        encoding="utf-8",
    )
    assert len(unwired(proj)) == 2

    jm_function(
        proj,
        "mpsk_demap",
        module="mpsk",
        params=[("x", "double")],
        return_type="double",
        impl_body="return x;",
    )

    assert unwired(proj) == []


def test_removing_a_module_leaves_no_dangling_wiring(tmp_path: Path):
    """The mirror of the fix. A `target_sources` naming a deleted target is a
    *configure*-time error, so leaving one behind does not degrade the build,
    it stops it."""
    proj = tmp_path / "proj"
    _mixed(proj)
    assert unwired(proj) == []

    jm_remove(proj, "module", "util", force=True)

    assert dangling(proj) == []
    assert unwired(proj) == []
    root_text = (proj / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "util_core" not in root_text
