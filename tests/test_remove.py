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
from just_makeit._remove import run as remove_run
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
