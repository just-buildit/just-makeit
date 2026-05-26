"""Integration tests for `just-makeit remove`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._module import run as module_run
from just_makeit._method import run as method_run
from just_makeit._property import run as property_run
from just_makeit._function import run as function_run
from just_makeit._remove import (
    run as remove_run,
    _is_implemented,
    _STUB_MARKER,
    _core_c_is_implemented,
    _CORE_C_STUB_MARKER,
)
from just_makeit._config import load, components, modules, module_objects


@pytest.fixture()
def project(tmp_path):
    """A project with a standalone object plus a module of two objects."""
    root = tmp_path / "proj"
    new_run("proj", root, ["widget"], [("gain", "float", "0.0f")])
    object_run(root, "gadget", None, state_vars=[("g", "float", "1.0f")])
    module_run(root, "dsp")
    object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
    object_run(root, "mixer", "dsp", state_vars=[("lo", "float", "0.0f")])
    return root


class TestRemoveObjectStandalone:
    def test_files_deleted(self, project):
        remove_run(project, "object", "gadget", force=True)
        assert not (project / "native" / "inc" / "gadget").exists()
        assert not (project / "native" / "src" / "gadget").exists()
        assert not (project / "src" / "proj" / "gadget.pyi").exists()
        assert not (
            project / "native" / "tests" / "test_gadget_core.c"
        ).exists()

    def test_toml_section_dropped(self, project):
        remove_run(project, "object", "gadget", force=True)
        assert "gadget" not in components(load(project))

    def test_cmake_and_umbrella_stripped(self, project):
        remove_run(project, "object", "gadget", force=True)
        cmake = (project / "CMakeLists.txt").read_text(encoding="utf-8")
        assert "native/src/gadget" not in cmake
        assert "gadget_core>" not in cmake
        umbrella = (project / "native" / "inc" / "proj.h").read_text(
            encoding="utf-8"
        )
        assert "gadget/gadget_core.h" not in umbrella

    def test_pkg_init_import_removed(self, project):
        remove_run(project, "object", "gadget", force=True)
        init = (project / "src" / "proj" / "__init__.py").read_text(
            encoding="utf-8"
        )
        assert "Gadget" not in init
        compile(init, "__init__.py", "exec")

    def test_unknown_object_exits(self, project):
        with pytest.raises(SystemExit):
            remove_run(project, "object", "nonexistent", force=True)


class TestRemoveObjectInModule:
    def test_files_deleted_and_module_membership_updated(self, project):
        remove_run(project, "object", "mixer", force=True)
        assert not (project / "native" / "inc" / "mixer").exists()
        cfg = load(project)
        assert "mixer" not in components(cfg)
        assert module_objects(cfg, "dsp") == ["nco"]

    def test_sibling_object_survives(self, project):
        remove_run(project, "object", "mixer", force=True)
        assert (project / "native" / "inc" / "nco").exists()
        ext = (project / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
        assert "mixer" not in ext.lower()


class TestRemoveModule:
    def test_module_and_objects_deleted(self, project):
        remove_run(project, "module", "dsp", force=True)
        for gone in (
            "native/inc/dsp",
            "native/src/dsp",
            "src/proj/dsp",
            "native/inc/nco",
            "native/inc/mixer",
        ):
            assert not (project / gone).exists(), gone
        cfg = load(project)
        assert "dsp" not in modules(cfg)
        assert "nco" not in components(cfg)
        assert "mixer" not in components(cfg)

    def test_standalone_object_untouched(self, project):
        remove_run(project, "module", "dsp", force=True)
        assert (project / "native" / "inc" / "widget").exists()
        assert "widget" in components(load(project))

    def test_unknown_module_exits(self, project):
        with pytest.raises(SystemExit):
            remove_run(project, "module", "nope", force=True)


class TestRemoveMethodPropertyFunction:
    def test_remove_method_drops_toml_entry(self, project):
        method_run(
            project,
            "nco",
            "tune",
            "dsp",
            "void",
            "float _Complex",
            False,
            [],
            params=[("freq", "double")],
        )
        remove_run(project, "method", "tune", object_name="nco", force=True)
        cfg = load(project)
        assert not cfg["nco"].get("methods")

    def test_remove_property_drops_toml_entry(self, project):
        property_run(
            project, "nco", "locked", "dsp", "uint8_t", True, field=True
        )
        remove_run(
            project, "property", "locked", object_name="nco", force=True
        )
        cfg = load(project)
        assert not cfg["nco"].get("properties")

    def test_remove_function_drops_toml_entry(self, project):
        function_run(project, "dsp_reset", "dsp")
        remove_run(project, "function", "dsp_reset", module="dsp", force=True)
        cfg = load(project)
        assert not cfg["module"]["dsp"].get("functions")

    def test_remove_method_requires_object(self, project):
        with pytest.raises(SystemExit):
            remove_run(project, "method", "tune", force=True)

    def test_unknown_method_exits(self, project):
        with pytest.raises(SystemExit):
            remove_run(
                project, "method", "ghost", object_name="nco", force=True
            )


class TestIsImplemented:
    def test_stub_not_implemented(self, project):
        core_h = project / "native" / "inc" / "gadget" / "gadget_core.h"
        assert core_h.exists()
        assert not _is_implemented(core_h)

    def test_missing_file_not_implemented(self, tmp_path):
        assert not _is_implemented(tmp_path / "nonexistent.h")

    def test_implemented_after_marker_removed(self, project):
        core_h = project / "native" / "inc" / "gadget" / "gadget_core.h"
        text = core_h.read_text(encoding="utf-8")
        # gadget has state vars so it uses the longer form of the marker
        assert "/* TODO: implement using state variables */" in text
        assert _STUB_MARKER in text  # prefix check
        core_h.write_text(
            text.replace(
                "(void)state; /* TODO: implement using state variables */",
                "state->g = 0.5f;",
            ),
            encoding="utf-8",
        )
        assert _is_implemented(core_h)


class TestRemoveWarnsOnImplemented:
    def _implement(self, project, obj):
        """Replace the TODO stub in obj's _core.h to simulate user code."""
        core_h = project / "native" / "inc" / obj / f"{obj}_core.h"
        text = core_h.read_text(encoding="utf-8")
        # Strip every variant of the TODO marker (with or without state suffix)
        import re
        new_text = re.sub(
            r"\(void\)state; /\* TODO: implement[^*]* \*/",
            "state->phase += state->freq; /* user algorithm */",
            text,
        )
        core_h.write_text(new_text, encoding="utf-8")

    def test_force_prints_warning_to_stderr(self, project, capsys):
        self._implement(project, "gadget")
        remove_run(project, "object", "gadget", force=True)
        err = capsys.readouterr().err
        assert "hand-written code" in err
        assert "gadget_core.h" in err

    def test_stub_only_no_stderr_warning(self, project, capsys):
        # gadget _core.h is still a fresh stub — no warning expected
        remove_run(project, "object", "gadget", force=True)
        err = capsys.readouterr().err
        assert "hand-written code" not in err

    def test_module_force_warns_for_implemented_objects(self, project, capsys):
        self._implement(project, "nco")
        remove_run(project, "module", "dsp", force=True)
        err = capsys.readouterr().err
        assert "hand-written code" in err
        assert "nco_core.h" in err

    def test_module_force_no_warning_for_stubs(self, project, capsys):
        # neither nco nor mixer has been implemented
        remove_run(project, "module", "dsp", force=True)
        err = capsys.readouterr().err
        assert "hand-written code" not in err


@pytest.fixture()
def no_step_project(tmp_path):
    """Project with two no_step objects: one no_state and one with state."""
    root = tmp_path / "nsp"
    new_run("nsp", root, ["sink"], [("buf", "float", "0.0f")])
    # no_step + no_state: lifecycle stubs in _core.c carry <<IMPLEMENT markers
    object_run(root, "chunker", None, no_step=True, no_state=True)
    # no_step + has_state + one method: method body in _core.c carries <<IMPLEMENT
    object_run(
        root,
        "detector",
        None,
        state_vars=[("thr", "float", "0.5f")],
        no_step=True,
    )
    method_run(
        root,
        "detector",
        "push",
        None,
        "float _Complex",
        "void",
        False,
        [],
    )
    return root


class TestCoreIsImplemented:
    def test_no_state_fresh_stub_not_implemented(self, no_step_project):
        core_c = (
            no_step_project / "native" / "src" / "chunker" / "chunker_core.c"
        )
        assert core_c.exists()
        assert _CORE_C_STUB_MARKER in core_c.read_text(encoding="utf-8")
        assert not _core_c_is_implemented(core_c, is_no_state=True, has_methods=False)

    def test_no_state_implemented_after_marker_removed(self, no_step_project):
        core_c = (
            no_step_project / "native" / "src" / "chunker" / "chunker_core.c"
        )
        text = core_c.read_text(encoding="utf-8")
        core_c.write_text(text.replace("/* <<IMPLEMENT:", "/* done:"), encoding="utf-8")
        assert not _CORE_C_STUB_MARKER in core_c.read_text(encoding="utf-8")
        assert _core_c_is_implemented(core_c, is_no_state=True, has_methods=False)

    def test_has_state_with_method_fresh_stub_not_implemented(self, no_step_project):
        core_c = (
            no_step_project / "native" / "src" / "detector" / "detector_core.c"
        )
        assert core_c.exists()
        assert _CORE_C_STUB_MARKER in core_c.read_text(encoding="utf-8")
        assert not _core_c_is_implemented(core_c, is_no_state=False, has_methods=True)

    def test_has_state_with_method_implemented_after_marker_removed(
        self, no_step_project
    ):
        core_c = (
            no_step_project / "native" / "src" / "detector" / "detector_core.c"
        )
        text = core_c.read_text(encoding="utf-8")
        core_c.write_text(text.replace("/* <<IMPLEMENT:", "/* done:"), encoding="utf-8")
        assert _core_c_is_implemented(core_c, is_no_state=False, has_methods=True)

    def test_has_state_no_methods_always_false(self, no_step_project):
        # no_step + has_state + no methods: generated file never had markers;
        # can't distinguish fresh from modified, so always returns False
        core_c = (
            no_step_project / "native" / "src" / "detector" / "detector_core.c"
        )
        # even if markers are absent, returns False when has_methods=False
        assert not _core_c_is_implemented(core_c, is_no_state=False, has_methods=False)

    def test_missing_file_not_implemented(self, tmp_path):
        assert not _core_c_is_implemented(
            tmp_path / "nonexistent.c", is_no_state=True, has_methods=False
        )


class TestRemoveWarnsOnNoStepImplemented:
    def _implement_core_c(self, root, obj):
        core_c = root / "native" / "src" / obj / f"{obj}_core.c"
        text = core_c.read_text(encoding="utf-8")
        core_c.write_text(
            text.replace("/* <<IMPLEMENT:", "/* done:"), encoding="utf-8"
        )

    def test_no_state_force_warns_to_stderr(self, no_step_project, capsys):
        self._implement_core_c(no_step_project, "chunker")
        remove_run(no_step_project, "object", "chunker", force=True)
        err = capsys.readouterr().err
        assert "hand-written code" in err
        assert "chunker_core.c" in err

    def test_no_state_fresh_no_warning(self, no_step_project, capsys):
        remove_run(no_step_project, "object", "chunker", force=True)
        err = capsys.readouterr().err
        assert "hand-written code" not in err

    def test_has_state_with_method_force_warns(self, no_step_project, capsys):
        self._implement_core_c(no_step_project, "detector")
        remove_run(no_step_project, "object", "detector", force=True)
        err = capsys.readouterr().err
        assert "hand-written code" in err
        assert "detector_core.c" in err

    def test_has_state_with_method_fresh_no_warning(self, no_step_project, capsys):
        remove_run(no_step_project, "object", "detector", force=True)
        err = capsys.readouterr().err
        assert "hand-written code" not in err

    def test_standard_step_object_not_affected(self, no_step_project, capsys):
        # 'sink' is a normal object with step() — its _core.h is checked, not _core.c
        remove_run(no_step_project, "object", "sink", force=True)
        err = capsys.readouterr().err
        assert "hand-written code" not in err
