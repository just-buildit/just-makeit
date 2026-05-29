"""
test_module_gaps.py — regression tests for the 6 module-scaffold bugs fixed
in v0.10.3.

Gap #1: __init__.py preservation — adding an object must merge exports, not
        destroy user-written wrapper classes/docstrings.
Gap #2: batch=True persisted in config — method regenerates as array input
        (METH_VARARGS), not METH_NOARGS.
Gap #3: C body preservation — regenerating module ext.c must not overwrite
        existing static PyObject * implementations with empty stubs.
Gap #4: no-step objects in a mixed module — no step/steps stubs emitted for
        objects scaffolded with --no-step.
Gap #5: Phantom module_core.h include — module ext.c must not include
        <module>_core.h when there are no module-level functions.
Gap #6: External lib CMake blocks — if(DOPPLER_C_LIB) … endif() blocks are
        copied from sibling CMakeLists to newly added object libraries.
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._method import run as method_run


# ---------------------------------------------------------------------------
# Gap #1 — __init__.py preservation
# ---------------------------------------------------------------------------


class TestInitPyPreservation:
    def test_user_content_survives_second_object(self, tmp_path):
        """Adding a second object must not remove hand-written wrapper code."""
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])

        init = root / "src" / "pkg" / "dsp" / "__init__.py"
        # Simulate user-written wrapper class sitting below the re-export.
        original = init.read_text(encoding="utf-8")
        user_block = "\n\nclass NcoHelper:\n    '''User-written wrapper.'''\n"
        init.write_text(original + user_block, encoding="utf-8")

        object_run(root, "mixer", "dsp", state_vars=[("gain", "float", "1.0f")])

        content = init.read_text(encoding="utf-8")
        assert "NcoHelper" in content, "__init__.py user class was wiped"
        assert "User-written wrapper" in content

    def test_both_exports_present_after_merge(self, tmp_path):
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        object_run(root, "mixer", "dsp", state_vars=[("gain", "float", "1.0f")])

        init = (root / "src" / "pkg" / "dsp" / "__init__.py").read_text(
            encoding="utf-8"
        )
        assert "Nco" in init
        assert "Mixer" in init

    def test_empty_import_resolved_when_first_object_added(self, tmp_path):
        """The initial __init__.py has an empty import; first object must fix it."""
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["filter"])

        init_path = root / "src" / "pkg" / "filter" / "__init__.py"
        before = init_path.read_text(encoding="utf-8")
        # Must be an empty or near-empty import at this stage.
        assert "import" in before

        object_run(root, "fir", "filter", state_vars=[])

        content = init_path.read_text(encoding="utf-8")
        # The import line must be syntactically valid Python.
        compile(content, str(init_path), "exec")
        assert "Fir" in content

    def test_parenthesized_import_does_not_corrupt(self, tmp_path):
        """A formatter-reflowed parenthesized multi-line import must not
        corrupt when a subsequent mutation regenerates the module init.

        Regression for gh#5/#6: ruff/black reformat
        ``from .dsp import A, B  # noqa: E402`` to
        ``from .dsp import (\\n    A,\\n    B,\\n)`` for long imports;
        the merge regex only matched the first line and treated ``(`` as
        a name, producing ``from .dsp import (, A, B`` (SyntaxError).
        """
        from just_makeit._object import _merge_module_init

        src = (
            "from .dsp import (  # noqa: E402\n"
            "    Ema,\n"
            "    Iad,\n"
            ")\n\n"
            '__all__ = ["Ema", "Iad"]\n'
        )
        merged = _merge_module_init(src, "dsp", ["Ema", "Iad", "Nco"])
        compile(merged, "__init__.py", "exec")
        assert "from .dsp import Ema, Iad, Nco  # noqa: E402" in merged
        assert "(," not in merged
        assert '"("' not in merged

    def test_fresh_module_init_py_is_valid_python(self, tmp_path):
        """A freshly-created module's __init__.py must be valid Python.

        It must not emit `from .<module> import` with an empty name list —
        that is a SyntaxError. The import line is added only once the first
        object/function is scaffolded.
        """
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])

        init_path = root / "src" / "pkg" / "dsp" / "__init__.py"
        content = init_path.read_text(encoding="utf-8")
        compile(content, str(init_path), "exec")
        assert "from .dsp import  " not in content
        assert "__all__ = []" in content


# ---------------------------------------------------------------------------
# Gap #2 — batch flag persistence
# ---------------------------------------------------------------------------


class TestBatchFlagPersistence:
    def test_batch_written_to_config(self, tmp_path):
        root = tmp_path / "pkg"
        new_run("pkg", root)
        object_run(root, "nco", None, state_vars=[("freq", "float", "0.0f")])
        method_run(
            root,
            "nco",
            "process",
            module=None,
            arg_type="float _Complex",
            return_type="float _Complex",
            variable_output=False,
            multi_output=[],
            batch=True,
        )

        cfg_text = (root / "just-makeit.toml").read_text(encoding="utf-8")
        assert "batch = true" in cfg_text

    def test_batch_method_uses_meth_varargs(self, tmp_path):
        root = tmp_path / "pkg"
        new_run("pkg", root)
        object_run(root, "nco", None, state_vars=[("freq", "float", "0.0f")])
        method_run(
            root,
            "nco",
            "process",
            module=None,
            arg_type="float _Complex",
            return_type="float _Complex",
            variable_output=False,
            multi_output=[],
            batch=True,
        )

        ext_c = (root / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )
        # A batch method takes an array argument; it cannot be METH_NOARGS.
        assert (
            "METH_NOARGS" not in ext_c
            or "process" not in ext_c.split("METH_NOARGS")[0].split("\n")[-1]
        )


# ---------------------------------------------------------------------------
# Gap #3 — C body preservation on regeneration
# ---------------------------------------------------------------------------


class TestCBodyPreservation:
    def test_existing_method_body_not_overwritten(self, tmp_path):
        """Regenerating module ext.c must keep existing static PyObject * bodies."""
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        # Use "compute" as the method name to avoid a naming collision with the
        # auto-generated "freq" property getter (both would resolve to the C
        # function name Nco_get_freq if we used "get_freq" as the method name).
        method_run(
            root,
            "nco",
            "compute",
            module="dsp",
            arg_type="void",
            return_type="float",
            variable_output=False,
            multi_output=[],
        )

        ext_path = root / "native" / "src" / "dsp" / "dsp_ext_nco.c"
        original = ext_path.read_text(encoding="utf-8")
        # Inject a sentinel specifically inside Nco_compute — the call
        # nco_compute(self->handle) is unique to that wrapper function.
        sentinel = "/* SENTINEL: user-edited body */"
        unique_call = "nco_compute(self->handle)"
        assert unique_call in original, (
            "test setup failed: nco_compute call not found in fragment"
        )
        patched = original.replace(
            unique_call,
            f"{sentinel}\n    float y = {unique_call}",
            1,
        )
        assert sentinel in patched, (
            "test setup failed: could not inject sentinel into Nco_compute"
        )
        ext_path.write_text(patched, encoding="utf-8")

        # Adding a second object triggers _regenerate_module.
        object_run(root, "mixer", "dsp", state_vars=[("gain", "float", "1.0f")])

        after = ext_path.read_text(encoding="utf-8")
        assert sentinel in after, "static PyObject * body was overwritten on regen"


# ---------------------------------------------------------------------------
# Gap #4 — no-step objects in a mixed module
# ---------------------------------------------------------------------------


class TestNoStepInModule:
    def test_no_step_object_has_no_step_wrappers(self, tmp_path):
        """An object added with --no-step must not generate step/steps wrappers."""
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        # Normal object with step.
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        # No-step object.
        object_run(root, "util", "dsp", state_vars=[], no_step=True)

        nco_ext = (root / "native" / "src" / "dsp" / "dsp_ext_nco.c").read_text(
            encoding="utf-8"
        )
        util_ext = (root / "native" / "src" / "dsp" / "dsp_ext_util.c").read_text(
            encoding="utf-8"
        )
        # The nco wrappers should be present.
        assert "Nco_step" in nco_ext
        # No step wrapper should be emitted for util.
        assert "Util_step" not in util_ext

    def test_no_step_object_has_no_step_in_core_c(self, tmp_path):
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "util", "dsp", state_vars=[], no_step=True)

        core_c = (root / "native" / "src" / "util" / "util_core.c").read_text(
            encoding="utf-8"
        )
        assert "util_step" not in core_c


# ---------------------------------------------------------------------------
# Gap #5 — phantom module_core.h include
# ---------------------------------------------------------------------------


class TestPhantomCoreHInclude:
    def test_no_include_without_functions(self, tmp_path):
        """module_ext.c must NOT include module_core.h if no module functions."""
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])

        ext_c = (root / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
        assert '#include "dsp/dsp_core.h"' not in ext_c

    def test_include_present_with_functions(self, tmp_path):
        """module_ext.c MUST include module_core.h when module functions exist."""
        from just_makeit._function import run as function_run

        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
        function_run(root, "global_setup", "dsp", doc="Global setup.")

        ext_c = (root / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
        assert '#include "dsp/dsp_core.h"' in ext_c


# ---------------------------------------------------------------------------
# Gap #6 — CMakeLists external lib block propagation
# ---------------------------------------------------------------------------


class TestCMakeExternalLibBlocks:
    def test_external_block_copied_to_new_object(self, tmp_path):
        """if(SOME_LIB) target_link_libraries() block must be copied to new obj."""
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])

        # Manually inject an external lib block into nco's CMakeLists.
        nco_cmake = root / "native" / "src" / "nco" / "CMakeLists.txt"
        ext_block = (
            "\nif(DOPPLER_C_LIB)\n"
            "    target_include_directories(nco PRIVATE"
            " ${DOPPLER_INCLUDE_DIRS})\n"
            "    target_link_libraries(nco PRIVATE ${DOPPLER_LIBRARIES})\n"
            "endif()\n"
        )
        nco_cmake.write_text(
            nco_cmake.read_text(encoding="utf-8") + ext_block,
            encoding="utf-8",
        )

        # Add a sibling object — _copy_external_cmake_blocks should fire.
        object_run(root, "mixer", "dsp", state_vars=[("gain", "float", "1.0f")])

        mixer_cmake = (root / "native" / "src" / "mixer" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert "DOPPLER_C_LIB" in mixer_cmake
        assert "mixer" in mixer_cmake  # placeholder replaced with new comp name
        assert "nco" not in mixer_cmake  # old comp name not left behind


# ---------------------------------------------------------------------------
# Gap #7 — _extra.c convention (issue #24)
# ---------------------------------------------------------------------------


class TestExtraFiles:
    """*_extra.c files are included in the aggregator and never modified."""

    def test_obj_extra_included_after_fragment(self, tmp_path):
        """Per-object extra is #included after its fragment when it exists."""
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])

        extra = root / "native" / "src" / "dsp" / "dsp_ext_nco_extra.c"
        extra.write_text("/* hand-written nco extras */\n", encoding="utf-8")

        # Trigger aggregator regen by adding a second object.
        object_run(root, "mixer", "dsp", state_vars=[("gain", "float", "1.0f")])

        agg = (root / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
        assert '#include "dsp_ext_nco_extra.c"' in agg
        # Extra must appear after its fragment, not before.
        assert agg.index("dsp_ext_nco.c") < agg.index("dsp_ext_nco_extra.c")

    def test_module_extra_included_after_all_fragments(self, tmp_path):
        """Per-module extra appears after all per-object fragments."""
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])

        extra = root / "native" / "src" / "dsp" / "dsp_ext_extra.c"
        extra.write_text("/* hand-written module extras */\n", encoding="utf-8")

        object_run(root, "mixer", "dsp", state_vars=[("gain", "float", "1.0f")])

        agg = (root / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
        assert '#include "dsp_ext_extra.c"' in agg
        # Module extra must come after all fragment includes.
        assert agg.index("dsp_ext_mixer.c") < agg.index("dsp_ext_extra.c")

    def test_extra_file_not_modified(self, tmp_path):
        """jm never overwrites an _extra.c file."""
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])

        sentinel = "/* SENTINEL: jm must not touch this */\n"
        extra = root / "native" / "src" / "dsp" / "dsp_ext_nco_extra.c"
        extra.write_text(sentinel, encoding="utf-8")

        object_run(root, "mixer", "dsp", state_vars=[("gain", "float", "1.0f")])

        assert extra.read_text(encoding="utf-8") == sentinel

    def test_no_include_when_extra_absent(self, tmp_path):
        """Aggregator contains no _extra.c includes when none exist."""
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])

        agg = (root / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
        assert "_extra.c" not in agg


# ---------------------------------------------------------------------------
# Gap #7 — extra_link_libs in module CMakeLists (gh-27)
# ---------------------------------------------------------------------------


class TestExtraLinkLibs:
    """extra_link_libs in just-makeit.toml appear in the module CMakeLists."""

    def test_extra_libs_appear_in_cmake(self, tmp_path):
        """Libs declared in [module.dsp].extra_link_libs land in CMakeLists."""
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])

        manifest = root / "just-makeit.toml"
        toml_text = manifest.read_text(encoding="utf-8")
        # Inject extra_link_libs into the existing [module.dsp] section.
        toml_text = toml_text.replace(
            "[module.dsp]",
            '[module.dsp]\nextra_link_libs = ["resamp_core", "m"]',
        )
        manifest.write_text(toml_text, encoding="utf-8")

        from just_makeit._apply import run as apply_run

        apply_run(root)

        cmake = (root / "native" / "src" / "dsp" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert "resamp_core" in cmake
        assert "m" in cmake

    def test_no_extra_libs_by_default(self, tmp_path):
        """Without extra_link_libs, CMakeLists contains only the standard libs."""
        root = tmp_path / "pkg"
        new_run("pkg", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])

        cmake = (root / "native" / "src" / "dsp" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        libs_line = [ln for ln in cmake.splitlines() if "target_link_libraries" in ln]
        assert libs_line
        assert "Python3::NumPy" in cmake
