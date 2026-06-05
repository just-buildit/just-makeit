"""Regression tests for cross-module extra_link_libs propagation (gh-160).

When a module object declares `extra_link_libs` referencing another module's
OBJECT library, that lib must be linked onto (a) the object's own `_core`
OBJECT lib (PUBLIC) and (b) the aggregating Python extension module — not just
test/bench. Otherwise the Python `.so` fails with `undefined symbol`.
"""

from __future__ import annotations

from pathlib import Path

from just_makeit._new import run as jm_new
from just_makeit._object import run as jm_object
from just_makeit import _config as C


def _two_modules(root: Path) -> Path:
    proj = root / "test_proj"
    jm_new("test_proj", proj, modules=["modA", "modB"])
    jm_object(
        proj,
        "obj_a",
        "modA",
        state_vars=[("v", "int", "0")],
        arg_type="int",
        return_type="int",
        impl_body="return state->v + x;",
    )
    jm_object(
        proj,
        "obj_b",
        "modB",
        state_vars=[("v", "int", "0")],
        arg_type="int",
        return_type="int",
        impl_body="return state->v + x;",
        extra_link_libs=["obj_a_core"],
    )
    return proj


def test_module_object_extra_link_libs_persisted(tmp_path: Path):
    proj = _two_modules(tmp_path)
    # gh-160: previously dropped from the manifest for module objects.
    assert C.component_extra_link_libs(C.load(proj), "obj_b") == ["obj_a_core"]


def test_object_core_gets_public_link(tmp_path: Path):
    proj = _two_modules(tmp_path)
    obj_cmake = (
        proj / "native" / "src" / "obj_b" / "CMakeLists.txt"
    ).read_text()
    # PUBLIC link on the OBJECT lib itself (was only on test/bench before).
    assert "target_link_libraries(obj_b_core PUBLIC" in obj_cmake
    assert "obj_a_core" in obj_cmake


def test_aggregating_module_extension_links_dep(tmp_path: Path):
    proj = _two_modules(tmp_path)
    mod_cmake = (
        proj / "native" / "src" / "modB" / "CMakeLists.txt"
    ).read_text()
    # The Python extension must link obj_a_core directly — CMake does not pull
    # a PUBLIC-linked OBJECT lib's objects transitively through another OBJECT
    # lib into the final .so.
    ext = mod_cmake[mod_cmake.index("Python3_add_library(modB") :]
    block = ext[: ext.index("Python3::NumPy")]
    assert "obj_a_core" in block


def test_no_extra_link_libs_no_spurious_core_link(tmp_path: Path):
    # A plain module object must not get a target_link_libraries(<c>_core ...).
    proj = tmp_path / "p"
    jm_new("p", proj, modules=["m"])
    jm_object(
        proj,
        "plain",
        "m",
        state_vars=[("v", "int", "0")],
        arg_type="int",
        return_type="int",
    )
    obj_cmake = (
        proj / "native" / "src" / "plain" / "CMakeLists.txt"
    ).read_text()
    assert "target_link_libraries(plain_core PUBLIC" not in obj_cmake
