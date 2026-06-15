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


def test_collocated_dedup_dep_and_extra_lib(tmp_path):
    # A dep core that also appears in the module's extra_link_libs must be
    # linked once, not twice (dedup of extra_libs ∪ dep cores).
    dest = tmp_path / "p"
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
    cfg = C.load(dest)
    cfg["module"]["ddc"]["extra_link_libs"] = ["lo_core"]  # also lists the dep
    C.save(dest, cfg)
    _silent(apply_run, dest)
    cmake = (dest / "native/src/ddc/CMakeLists.txt").read_text()
    assert _link_block(cmake, "test_ddc_core").count("lo_core") == 1
    assert _link_block(cmake, "ddc_core PUBLIC").count("lo_core") == 1


# ── gh-271: apply reconciles a CHANGED depends_on on a non-collocated object ──


def _measure_module(dest):
    """Module `measure` with non-collocated `base`, `extra`, and a `tone`
    object that initially depends only on `base` (the doppler `measure` shape:
    object name never equals the module name)."""
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "measure")
    for obj in ("base", "extra"):
        _silent(
            object_run,
            dest,
            obj,
            module="measure",
            state_vars=[("g", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
    _silent(
        object_run,
        dest,
        "tone",
        module="measure",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
        depends_on=[{"name": "base", "link": True}],
    )


def _set_depends_on(dest, obj, names):
    cfg = C.load(dest)
    cfg[obj]["depends_on"] = [{"name": n, "link": True} for n in names]
    C.save(dest, cfg)


def test_apply_reconciles_added_dep_on_existing_object(tmp_path):
    # The gh-271 bug: once tone_core already had a PUBLIC link block (from its
    # original `base` dep), apply's surgical add skipped it, so a newly added
    # dep never reached tone's own _core/test/bench link lines.
    dest = tmp_path / "p"
    _measure_module(dest)
    cmake = dest / "native/src/tone/CMakeLists.txt"
    assert "extra_core" not in cmake.read_text()
    _set_depends_on(dest, "tone", ["base", "extra"])
    _silent(apply_run, dest)
    text = cmake.read_text()
    for target in ("tone_core PUBLIC", "test_tone_core", "bench_tone_core"):
        block = _link_block(text, target)
        assert "base_core" in block, target
        assert "extra_core" in block, target
    # idempotent — a second apply changes nothing.
    before = cmake.read_text()
    _silent(apply_run, dest)
    assert cmake.read_text() == before


def test_apply_reconciles_removed_dep_on_existing_object(tmp_path):
    dest = tmp_path / "p"
    _measure_module(dest)
    _set_depends_on(dest, "tone", ["base", "extra"])
    _silent(apply_run, dest)
    # Now drop `extra` back out — the stale link line must be reconciled away.
    _set_depends_on(dest, "tone", ["base"])
    _silent(apply_run, dest)
    text = (dest / "native/src/tone/CMakeLists.txt").read_text()
    assert "extra_core" not in text
    assert "base_core" in _link_block(text, "test_tone_core")


def test_apply_reconcile_preserves_external_block(tmp_path):
    # A hand-added if(VAR) external-library block must survive the reconcile
    # overwrite (the gh-174 guarantee jm cannot re-derive from the manifest).
    dest = tmp_path / "p"
    _measure_module(dest)
    cmake = dest / "native/src/tone/CMakeLists.txt"
    cmake.write_text(
        cmake.read_text().rstrip() + "\n\nif(DOPPLER_C_LIB)\n"
        "  target_link_libraries(tone_core PUBLIC ${DOPPLER_C_LIB})\n"
        "endif()\n"
    )
    _set_depends_on(dest, "tone", ["base", "extra"])
    _silent(apply_run, dest)
    text = cmake.read_text()
    assert "extra_core" in text  # dep reconciled
    assert "if(DOPPLER_C_LIB)" in text  # external block preserved


def test_apply_reconcile_preserves_component_include_dirs(tmp_path):
    dest = tmp_path / "p"
    _measure_module(dest)
    cfg = C.load(dest)
    cfg["tone"]["extra_include_dirs"] = ["${CMAKE_SOURCE_DIR}/vendor"]
    cfg["tone"]["depends_on"] = [
        {"name": "base", "link": True},
        {"name": "extra", "link": True},
    ]
    C.save(dest, cfg)
    _silent(apply_run, dest)
    text = (dest / "native/src/tone/CMakeLists.txt").read_text()
    assert "extra_core" in text
    assert "${CMAKE_SOURCE_DIR}/vendor" in text


def test_status_check_flags_stale_object_depends_on(tmp_path):
    # status --check ran the same skipped reconcile, so it reported "up to
    # date" for a per-object CMakeLists whose depends_on had drifted.
    from just_makeit import _status

    dest = tmp_path / "p"
    _measure_module(dest)
    # Manifest gains a dep but the per-object CMakeLists is left stale.
    _set_depends_on(dest, "tone", ["base", "extra"])
    cfg_only = C.load(dest)  # no apply — disk file still base-only
    assert (
        "extra_core"
        not in (dest / "native/src/tone/CMakeLists.txt").read_text()
    )
    assert C.depends_on(cfg_only, "tone") == ["base", "extra"]
    changed = _silent(_status.run, dest, check=True)
    assert changed > 0  # drift detected


def test_apply_reconcile_object_test_links_e2e(tmp_path):
    """The real regression: tone_core.c calls a sibling symbol after its
    depends_on is changed post-scaffold. Without the reconcile, test_tone_core
    fails with `undefined reference`. Build just that C test target (the module
    .so aggregation is out of scope here) to prove the symbol resolves."""
    if _SKIP:
        pytest.skip(_SKIP)
    dest = tmp_path / "p"
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "measure")
    _silent(
        object_run,
        dest,
        "extra",
        module="measure",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    # tone starts with NO dependency.
    _silent(
        object_run,
        dest,
        "tone",
        module="measure",
        state_vars=[("g", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    # tone_core.c now composes extra's symbol.
    h = dest / "native/inc/tone/tone_core.h"
    h.write_text(
        h.read_text()
        .replace(
            "#include <stddef.h>",
            '#include <stddef.h>\n#include "extra/extra_core.h"',
            1,
        )
        .replace(
            "return (float)x;",
            "return (float)x + extra_get_g((const extra_state_t *)0);",
        )
    )
    # Declare the dependency *after the fact* — the gh-271 scenario.
    _set_depends_on(dest, "tone", ["extra"])
    _silent(apply_run, dest)
    cfg = subprocess.run(
        ["cmake", "-S", str(dest), "-B", str(dest / "build")],
        capture_output=True,
        text=True,
    )
    assert cfg.returncode == 0, cfg.stderr
    bld = subprocess.run(
        [
            "cmake",
            "--build",
            str(dest / "build"),
            "--target",
            "test_tone_core",
        ],
        capture_output=True,
        text=True,
    )
    assert bld.returncode == 0, (
        "test_tone_core failed to link a depends_on added post-scaffold "
        f"(gh-271):\n{bld.stdout}\n{bld.stderr}"
    )


# ── gh-275: the reconcile must not clobber a hand-owned per-object CMakeLists ──


_HAND_OWNED_FFT = """\
# OBJECT library — pure C core, no Python dependency.
add_library(fft_core OBJECT fft_core.c pocketfft.c pocketfft_c99.c pffft.c)
target_include_directories(fft_core PUBLIC
    ${CMAKE_SOURCE_DIR}/native/inc ${CMAKE_SOURCE_DIR}/native/inc/fft)
set_source_files_properties(pffft.c PROPERTIES
    INCLUDE_DIRECTORIES ${CMAKE_SOURCE_DIR}/native/inc/pffft
    COMPILE_DEFINITIONS "_USE_MATH_DEFINES;_DEFAULT_SOURCE")
target_link_libraries(fft_core PUBLIC m)

add_executable(test_fft_core ${CMAKE_SOURCE_DIR}/native/tests/test_fft_core.c)
target_link_libraries(test_fft_core PRIVATE fft_core m)
add_test(NAME test_fft_core COMMAND test_fft_core)

add_executable(bench_fft_core ${CMAKE_SOURCE_DIR}/native/benchmarks/bench_fft_core.c)
target_link_libraries(bench_fft_core PRIVATE fft_core m)
"""


def _module_with_hand_owned_fft(dest):
    """Module `dsp` with a hand-owned `fft` (vendored pocketfft/PFFFT sources,
    per-source properties) plus a pure-jm `tone` — the doppler shape."""
    _silent(new_run, "p", dest)
    _silent(module_run, dest, "dsp")
    for obj in ("fft", "tone"):
        _silent(
            object_run,
            dest,
            obj,
            module="dsp",
            state_vars=[("g", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
    (dest / "native/src/fft/CMakeLists.txt").write_text(_HAND_OWNED_FFT)


def test_apply_preserves_hand_owned_object_cmake(tmp_path):
    dest = tmp_path / "p"
    _module_with_hand_owned_fft(dest)
    _silent(apply_run, dest)
    # Byte-for-byte intact: vendored sources, source properties, PUBLIC m.
    assert (
        dest / "native/src/fft/CMakeLists.txt"
    ).read_text() == _HAND_OWNED_FFT


def test_apply_hand_owned_survives_sibling_dep_change(tmp_path):
    # A reconcile triggered by another object's depends_on change must still
    # leave the hand-owned file untouched.
    dest = tmp_path / "p"
    _module_with_hand_owned_fft(dest)
    _set_depends_on(dest, "tone", ["fft"])
    _silent(apply_run, dest)
    assert (
        dest / "native/src/fft/CMakeLists.txt"
    ).read_text() == _HAND_OWNED_FFT
    # ...while the pure-jm sibling is still reconciled.
    assert "fft_core" in (dest / "native/src/tone/CMakeLists.txt").read_text()


def test_status_check_clean_for_hand_owned_object_cmake(tmp_path):
    from just_makeit import _status

    dest = tmp_path / "p"
    _module_with_hand_owned_fft(dest)
    assert _silent(_status.run, dest, check=True) == 0  # no phantom drift


def test_hand_owned_detection():
    from just_makeit._apply import _is_hand_owned_object_cmake

    # Pure-jm: only its own core, no bespoke rules.
    plain = "add_library(foo_core OBJECT foo_core.c)\n"
    assert not _is_hand_owned_object_cmake(plain, "foo")
    # Extra vendored source compiled into the core.
    assert _is_hand_owned_object_cmake(
        "add_library(foo_core OBJECT foo_core.c vendor.c)\n", "foo"
    )
    # Per-source build properties jm never emits.
    assert _is_hand_owned_object_cmake(
        plain + "set_source_files_properties(vendor.c PROPERTIES X Y)\n", "foo"
    )
    # Custom build step.
    assert _is_hand_owned_object_cmake(
        plain + "add_custom_command(OUTPUT gen.c COMMAND gen)\n", "foo"
    )
