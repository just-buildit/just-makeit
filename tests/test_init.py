"""Integration tests for `just-makeit init` (add component to existing project)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._init import run as init_run
from just_makeit._config import load, components, state_vars


@pytest.fixture()
def project(tmp_path):
    """Project scaffold (no component yet)."""
    dest = tmp_path / "myproj"
    new_run("myproj", dest)
    return dest


@pytest.fixture()
def project_with_engine(tmp_path):
    """Project scaffold + engine component added via init."""
    dest = tmp_path / "myproj"
    new_run("myproj", dest)
    init_run(dest, "engine", [("rate", "double", "1.0")])
    return dest


class TestInitAddsFiles:
    def test_component_header(self, project_with_engine):
        assert (project_with_engine / "native" / "inc" / "engine" / "engine_core.h").exists()

    def test_component_core_c(self, project_with_engine):
        assert (project_with_engine / "native" / "src" / "engine" / "engine_core.c").exists()

    def test_component_ext_c(self, project_with_engine):
        assert (project_with_engine / "native" / "src" / "engine" / "engine_ext.c").exists()

    def test_component_cmake(self, project_with_engine):
        assert (project_with_engine / "native" / "src" / "engine" / "CMakeLists.txt").exists()

    def test_c_test(self, project_with_engine):
        assert (project_with_engine / "native" / "tests" / "test_engine_core.c").exists()

    def test_python_pyi(self, project_with_engine):
        assert (project_with_engine / "src" / "myproj" / "engine.pyi").exists()

    def test_python_test(self, project_with_engine):
        assert (project_with_engine / "src" / "myproj" / "tests" / "test_engine.py").exists()

    def test_compile_commands_created(self, project_with_engine):
        assert (project_with_engine / "compile_commands.json").exists()


class TestInitUpdatesCMake:
    def test_cmake_top_has_add_subdirectory(self, project_with_engine):
        cmake = (project_with_engine / "CMakeLists.txt").read_text()
        assert "add_subdirectory(native/src/engine)" in cmake

    def test_component_cmake_has_targets(self, project_with_engine):
        cmake = (project_with_engine / "native" / "src" / "engine" / "CMakeLists.txt").read_text()
        assert "Python3_add_library(engine" in cmake
        assert "engine_core" in cmake

    def test_second_component_appends(self, project):
        init_run(project, "engine", [("rate", "double", "1.0")])
        init_run(project, "parser", [("depth", "int", "8")])
        cmake = (project / "CMakeLists.txt").read_text()
        assert "add_subdirectory(native/src/engine)" in cmake
        assert "add_subdirectory(native/src/parser)" in cmake


class TestInitUpdatesConfig:
    def test_component_registered(self, project_with_engine):
        cfg = load(project_with_engine)
        assert "engine" in components(cfg)

    def test_state_vars_recorded(self, project_with_engine):
        cfg = load(project_with_engine)
        vars_ = state_vars(cfg, "engine")
        assert any(n == "rate" for n, _, __ in vars_)

    def test_multiple_components_in_config(self, project):
        init_run(project, "engine", [("rate", "double", "1.0")])
        init_run(project, "parser", [("depth", "int", "8")])
        cfg = load(project)
        assert set(components(cfg)) == {"engine", "parser"}


class TestInitContent:
    def test_no_unreplaced_placeholders(self, project_with_engine):
        for path in project_with_engine.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml", ".txt"):
                text = path.read_text(encoding="utf-8")
                assert "<<" not in text, f"Unreplaced placeholder in {path}"

    def test_header_has_correct_typedef(self, project_with_engine):
        h = (project_with_engine / "native" / "inc" / "engine" / "engine_core.h").read_text()
        assert "engine_state_t" in h
        assert "engine_create" in h

    def test_ext_c_has_correct_class(self, project_with_engine):
        ext = (project_with_engine / "native" / "src" / "engine" / "engine_ext.c").read_text()
        assert "EngineObject" in ext
        assert "PyInit_engine" in ext

    def test_pyi_uses_package_dir(self, project_with_engine):
        # .pyi lives under src/myproj/ (the project package), not src/engine/
        assert (project_with_engine / "src" / "myproj" / "engine.pyi").exists()

    def test_test_file_uses_package_dir(self, project_with_engine):
        assert (project_with_engine / "src" / "myproj" / "tests" / "test_engine.py").exists()

    def test_second_component_no_placeholder(self, project):
        init_run(project, "engine")
        init_run(project, "parser", [("depth", "int", "8")])
        for path in project.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml", ".txt"):
                text = path.read_text(encoding="utf-8")
                assert "<<" not in text, f"Unreplaced placeholder in {path}"


class TestInitPreservesInit:
    def test_first_component_writes_init_py(self, project):
        init_run(project, "engine")
        assert (project / "src" / "myproj" / "__init__.py").exists()

    def test_second_component_does_not_overwrite_init_py(self, project):
        init_run(project, "engine")
        init_path = project / "src" / "myproj" / "__init__.py"
        original = init_path.read_text()
        init_run(project, "parser")
        assert init_path.read_text() == original


class TestInitValidation:
    def test_no_config_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            init_run(tmp_path, "engine")

    def test_invalid_name_digit_start(self, project):
        with pytest.raises(SystemExit):
            init_run(project, "1bad")

    def test_invalid_name_hyphen(self, project):
        with pytest.raises(SystemExit):
            init_run(project, "my-comp")

    def test_duplicate_component_exits(self, project):
        init_run(project, "engine")
        with pytest.raises(SystemExit):
            init_run(project, "engine")
