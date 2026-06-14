"""`jm apply` resolves the collocated OBJECT-core link block (gh-160).

A module whose object shares its name (collocated, e.g. module "ddc" + object
"ddc") renders its OBJECT-core CMake inside the module CMakeLists. gh-160 added
a ``<<extra_link_on_object_core>>`` slot that PUBLIC-links the module's
``extra_link_libs`` onto that OBJECT lib. The ``jm object`` path filled it, but
``jm apply`` (which rebuilds the collocated CMakeLists) did not — so the literal
placeholder leaked into the generated file and broke the build. This guards
that apply resolves it.
"""

import io
import contextlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit import _config as C  # noqa: E402


def _no_toolchain():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _no_toolchain()


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def test_apply_resolves_collocated_object_core_link(tmp_path):
    dest = tmp_path / "p"
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "ddc")
    # Collocated object: object name == module name.
    _silent(
        object_run,
        dest,
        "ddc",
        module="ddc",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    # Declare module-level extra_link_libs, then regenerate via apply.
    cfg = C.load(dest)
    cfg["module"]["ddc"]["extra_link_libs"] = ["foo_core", "m"]
    C.save(dest, cfg)
    _silent(apply_run, dest)

    cmake = (dest / "native/src/ddc/CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    # No unresolved template placeholder of any kind leaked.
    assert "<<" not in cmake and ">>" not in cmake
    # The OBJECT lib is PUBLIC-linked against the declared libs.
    assert "target_link_libraries(ddc_core PUBLIC" in cmake
    assert "foo_core" in cmake


def test_apply_no_extra_libs_leaves_no_object_core_link(tmp_path):
    dest = tmp_path / "p"
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "ddc")
    _silent(
        object_run,
        dest,
        "ddc",
        module="ddc",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    _silent(apply_run, dest)
    cmake = (dest / "native/src/ddc/CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    assert "<<" not in cmake  # placeholder resolved to empty
    assert "target_link_libraries(ddc_core PUBLIC" not in cmake


# ── gh-174: component-level extra_link_libs reach a module object via apply ───
def test_apply_injects_module_object_component_link(tmp_path):
    dest = tmp_path / "p"
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "mymod")
    _silent(
        object_run,
        dest,
        "myobj",
        module="mymod",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    cfg = C.load(dest)
    cfg["myobj"]["extra_link_libs"] = ["cjson"]
    cfg["myobj"]["extra_include_dirs"] = ["${CMAKE_SOURCE_DIR}/vendor"]
    C.save(dest, cfg)
    _silent(apply_run, dest)
    cmake = (dest / "native/src/myobj/CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    assert "target_link_libraries(myobj_core PUBLIC" in cmake
    assert "cjson" in cmake
    assert "${CMAKE_SOURCE_DIR}/vendor" in cmake
    # idempotent — second apply adds nothing
    before = cmake
    _silent(apply_run, dest)
    assert (dest / "native/src/myobj/CMakeLists.txt").read_text(
        encoding="utf-8"
    ) == before


def test_apply_no_component_libs_leaves_module_object_cmake(tmp_path):
    dest = tmp_path / "p"
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "mymod")
    _silent(
        object_run,
        dest,
        "myobj",
        module="mymod",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    before = (dest / "native/src/myobj/CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    _silent(apply_run, dest)
    assert (dest / "native/src/myobj/CMakeLists.txt").read_text(
        encoding="utf-8"
    ) == before  # no-op when no component libs


# ── gh-254: link=true is *additive* for a collocated composing object ─────────


def _link_block(cmake, target):
    import re

    m = re.search(
        r"target_link_libraries\(" + re.escape(target) + r"\b.*?\)",
        cmake,
        re.S,
    )
    assert m, f"no link block for {target}"
    return m.group()


def _collocated_with_dep(dest):
    """Module ddc with a sibling `lo` and a collocated `ddc` object that
    composes `lo` via depends_on link=true (the doppler ddc/ddcr shape)."""
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "ddc")
    _silent(
        object_run,
        dest,
        "lo",
        module="ddc",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    _silent(
        object_run,
        dest,
        "ddc",
        module="ddc",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
        depends_on=[{"name": "lo", "link": True}],
    )


def test_collocated_composed_dep_links_test_bench_and_so(tmp_path):
    dest = tmp_path / "p"
    _collocated_with_dep(dest)
    cmake = (dest / "native/src/ddc/CMakeLists.txt").read_text()
    # additive: the composed sibling core is on the object's OWN test/bench/core
    # (so test_ddc_core resolves lo_* symbols ddc_core.c calls) AND the .so.
    assert "lo_core" in _link_block(cmake, "test_ddc_core")
    assert "lo_core" in _link_block(cmake, "bench_ddc_core")
    assert "lo_core" in _link_block(cmake, "ddc_core PUBLIC")
    assert "lo_core" in _link_block(cmake, "ddc PRIVATE")


def test_collocated_dep_survives_apply(tmp_path):
    # The path #254 actually broke: jm apply rebuilds the collocated CMakeLists.
    dest = tmp_path / "p"
    _collocated_with_dep(dest)
    _silent(apply_run, dest)
    cmake = (dest / "native/src/ddc/CMakeLists.txt").read_text()
    assert "<<" not in cmake and ">>" not in cmake
    assert "lo_core" in _link_block(cmake, "test_ddc_core")
    assert "lo_core" in _link_block(cmake, "bench_ddc_core")


def test_collocated_no_dep_has_no_object_core_link(tmp_path):
    # No depends_on, no module extra_libs -> no PUBLIC object-core link, and
    # test/bench stay at the bare `<obj>_core m` (no churn for the common case).
    dest = tmp_path / "p"
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "ddc")
    _silent(
        object_run,
        dest,
        "ddc",
        module="ddc",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    cmake = (dest / "native/src/ddc/CMakeLists.txt").read_text()
    assert "target_link_libraries(ddc_core PUBLIC" not in cmake
    assert "PRIVATE ddc_core m" in _link_block(cmake, "test_ddc_core")


def test_collocated_composed_dep_builds_e2e(tmp_path):
    """The real regression: ddc_core.c calls a lo_* symbol; without the
    additive link, test_ddc_core/bench_ddc_core fail with `undefined
    reference`. Build the whole project to prove the symbols resolve."""
    if _SKIP:
        pytest.skip(_SKIP)
    dest = tmp_path / "p"
    _collocated_with_dep(dest)
    # Make the collocated core actually compose the sibling.
    h = dest / "native/inc/ddc/ddc_core.h"
    h.write_text(
        h.read_text()
        .replace(
            "#include <stddef.h>",
            '#include <stddef.h>\n#include "lo/lo_core.h"',
            1,
        )
        .replace(
            "return (float)x;",
            "return (float)x + lo_get_g((const lo_state_t *)0);",
        )
    )
    cfg = subprocess.run(
        ["cmake", "-S", str(dest), "-B", str(dest / "build")],
        capture_output=True,
        text=True,
    )
    assert cfg.returncode == 0, cfg.stderr
    bld = subprocess.run(
        ["cmake", "--build", str(dest / "build")],
        capture_output=True,
        text=True,
    )
    assert bld.returncode == 0, (
        "collocated composing object failed to link "
        f"(gh-254):\n{bld.stdout}\n{bld.stderr}"
    )
