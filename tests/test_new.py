"""Integration tests for `just-makeit new`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run


# Fixture: new project with a component (the typical one-shot path).
@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "my_filter"
    run("my_filter", dest, "my_filter")
    return dest


# Fixture: project scaffold only, no component.
@pytest.fixture()
def scaffold(tmp_path):
    dest = tmp_path / "my_proj"
    run("my_proj", dest)
    return dest


class TestNewProjectFiles:
    def test_cmake_lists_exists(self, project):
        assert (project / "CMakeLists.txt").exists()

    def test_makefile_exists(self, project):
        assert (project / "Makefile").exists()

    def test_pyproject_exists(self, project):
        assert (project / "pyproject.toml").exists()

    def test_gitignore_exists(self, project):
        assert (project / ".gitignore").exists()

    def test_clib_common_h(self, project):
        assert (project / "native" / "inc" / "clib_common.h").exists()

    def test_pyex_common_h(self, project):
        assert (project / "native" / "inc" / "pyex_common.h").exists()

    def test_just_makeit_toml_exists(self, project):
        assert (project / "just-makeit.toml").exists()

    def test_umbrella_header_exists(self, project):
        assert (project / "native" / "inc" / "my_filter.h").exists()

    def test_pc_in_exists(self, project):
        assert (project / "cmake" / "my-filter.pc.in").exists()


class TestNewComponentFiles:
    def test_component_header(self, project):
        assert (project / "native" / "inc" / "my_filter" / "my_filter_core.h").exists()

    def test_component_core_c(self, project):
        assert (project / "native" / "src" / "my_filter" / "my_filter_core.c").exists()

    def test_component_ext_c(self, project):
        assert (project / "native" / "src" / "my_filter" / "my_filter_ext.c").exists()

    def test_component_cmake(self, project):
        assert (project / "native" / "src" / "my_filter" / "CMakeLists.txt").exists()

    def test_c_test(self, project):
        assert (project / "native" / "tests" / "test_my_filter_core.c").exists()

    def test_python_init(self, project):
        assert (project / "src" / "my_filter" / "__init__.py").exists()

    def test_python_pyi(self, project):
        assert (project / "src" / "my_filter" / "my_filter.pyi").exists()

    def test_tests_init(self, project):
        assert (project / "src" / "my_filter" / "tests" / "__init__.py").exists()

    def test_python_test(self, project):
        assert (project / "src" / "my_filter" / "tests" / "test_my_filter.py").exists()

    def test_compile_commands_exists(self, project):
        assert (project / "compile_commands.json").exists()

    def test_compile_commands_json(self, project):
        import json

        data = json.loads((project / "compile_commands.json").read_text())
        assert isinstance(data, list)
        assert len(data) == 4
        files = {e["file"] for e in data}
        assert any("my_filter_core.c" in f for f in files)
        assert any("my_filter_ext.c" in f for f in files)
        assert any("bench_my_filter_core.c" in f for f in files)
        assert all("directory" in e for e in data)


class TestNewScaffoldOnly:
    def test_cmake_lists_exists(self, scaffold):
        assert (scaffold / "CMakeLists.txt").exists()

    def test_minimal_init_py(self, scaffold):
        init = (scaffold / "src" / "my_proj" / "__init__.py").read_text()
        assert "my_proj" in init

    def test_no_component_files(self, scaffold):
        src = scaffold / "native" / "src"
        if src.exists():
            # Only the lib stub should be present — no component subdirectories
            entries = [p for p in src.iterdir() if p.is_dir()]
            assert entries == [], f"unexpected component dirs: {entries}"

    def test_no_compile_commands(self, scaffold):
        assert not (scaffold / "compile_commands.json").exists()

    def test_toml_has_no_components(self, scaffold):
        from just_makeit._config import load, components

        cfg = load(scaffold)
        assert components(cfg) == []


class TestNewConfig:
    def test_config_has_project_name(self, project):
        import tomllib

        with (project / "just-makeit.toml").open("rb") as f:
            cfg = tomllib.load(f)
        assert cfg["project"]["name"] == "my_filter"

    def test_config_has_default_state(self, project):
        import tomllib

        with (project / "just-makeit.toml").open("rb") as f:
            cfg = tomllib.load(f)
        assert any(s["name"] == "gain" for s in cfg["my_filter"]["state"])

    def test_config_records_custom_state(self, tmp_path):
        dest = tmp_path / "comp"
        run(
            "comp", dest, "comp", [("cutoff", "double", "440.0"), ("order", "int", "4")]
        )
        import tomllib

        with (dest / "just-makeit.toml").open("rb") as f:
            cfg = tomllib.load(f)
        names = [s["name"] for s in cfg["comp"]["state"]]
        assert names == ["cutoff", "order"]


class TestNewContent:
    def test_no_unreplaced_placeholders(self, project):
        for path in project.rglob("*"):
            if path.is_file() and path.suffix in (
                ".py",
                ".c",
                ".h",
                ".toml",
                ".txt",
                ".md",
            ):
                text = path.read_text(encoding="utf-8")
                assert "<<" not in text, f"Unreplaced placeholder in {path}"

    def test_cmake_top_has_project_name(self, project):
        cmake = (project / "CMakeLists.txt").read_text()
        assert "project(my_filter" in cmake

    def test_cmake_top_has_python_package_dir(self, project):
        cmake = (project / "CMakeLists.txt").read_text()
        assert "PYTHON_PACKAGE_DIR" in cmake
        assert "src/my_filter" in cmake

    def test_cmake_top_has_add_subdirectory(self, project):
        cmake = (project / "CMakeLists.txt").read_text()
        assert "add_subdirectory(native/src/my_filter)" in cmake

    def test_component_cmake_has_python3_add_library(self, project):
        cmake = (
            project / "native" / "src" / "my_filter" / "CMakeLists.txt"
        ).read_text()
        assert "Python3_add_library(my_filter" in cmake

    def test_header_has_correct_typedef(self, project):
        h = (project / "native" / "inc" / "my_filter" / "my_filter_core.h").read_text()
        assert "my_filter_state_t" in h
        assert "my_filter_create" in h
        assert "my_filter_destroy" in h
        assert "my_filter_step" in h

    def test_ext_c_has_correct_class(self, project):
        ext = (project / "native" / "src" / "my_filter" / "my_filter_ext.c").read_text()
        assert "MyFilterObject" in ext
        assert "PyInit_my_filter" in ext

    def test_python_init_imports_class(self, project):
        init = (project / "src" / "my_filter" / "__init__.py").read_text()
        assert "from .my_filter import MyFilter" in init

    def test_pyproject_uses_just_buildit(self, project):
        toml = (project / "pyproject.toml").read_text()
        assert 'build-backend = "just_buildit"' in toml
        assert 'command = "make just-build"' in toml

    def test_pyproject_has_project_name(self, project):
        toml = (project / "pyproject.toml").read_text()
        assert 'name = "my-filter"' in toml

    def test_cmake_top_has_combined_lib(self, project):
        cmake = (project / "CMakeLists.txt").read_text()
        assert "add_library(my_filter_lib SHARED" in cmake

    def test_cmake_component_is_object_lib(self, project):
        cmake = (
            project / "native" / "src" / "my_filter" / "CMakeLists.txt"
        ).read_text()
        assert "add_library(my_filter_core OBJECT" in cmake

    def test_umbrella_header_content(self, project):
        h = (project / "native" / "inc" / "my_filter.h").read_text()
        assert "MY_FILTER_H" in h

    def test_umbrella_header_updated_by_init(self, tmp_path):
        from just_makeit._init import run as init_run
        dest = tmp_path / "my_pkg"
        run("my_pkg", dest)
        init_run(dest, "gain")
        umbrella = (dest / "native" / "inc" / "my_pkg.h").read_text()
        assert '#include "gain/gain_core.h"' in umbrella

    def test_pc_in_content(self, project):
        pc = (project / "cmake" / "my-filter.pc.in").read_text()
        assert "Name: my-filter" in pc
        assert "-lmy_filter" in pc


class TestNewStateVars:
    def test_default_uses_gain(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, "comp")
        h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "double gain;" in h
        assert "comp_get_gain" in h

    def test_custom_single_var(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, "comp", [("cutoff", "double", "0.0")])
        h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "double cutoff;" in h

    def test_multi_vars(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, "comp", [("gain", "double", "1.0"), ("order", "int", "4")])
        h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "double gain;" in h
        assert "int order;" in h

    def test_float_type(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, "comp", [("alpha", "float", "0.0f")])
        h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "float alpha;" in h

    def test_reset_uses_default_not_zero(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, "comp", [("gain", "double", "1.5")])
        c = (dest / "native" / "src" / "comp" / "comp_core.c").read_text()
        assert "state->gain = 1.5;" in c


class TestNewWithModules:
    def test_single_module_scaffolded(self, tmp_path):
        dest = tmp_path / "my_pkg"
        run("my_pkg", dest, modules=["audio"])
        assert (dest / "native" / "src" / "audio" / "audio_ext.c").exists()
        assert (dest / "src" / "my_pkg" / "audio" / "__init__.py").exists()

    def test_multiple_modules_scaffolded(self, tmp_path):
        dest = tmp_path / "my_pkg"
        run("my_pkg", dest, modules=["osc", "env"])
        assert (dest / "native" / "src" / "osc" / "osc_ext.c").exists()
        assert (dest / "native" / "src" / "env" / "env_ext.c").exists()

    def test_modules_recorded_in_toml(self, tmp_path):
        from just_makeit._config import load, modules as cfg_modules
        dest = tmp_path / "my_pkg"
        run("my_pkg", dest, modules=["osc", "env"])
        cfg = load(dest)
        assert set(cfg_modules(cfg)) == {"osc", "env"}

    def test_module_add_subdirectory_in_cmake(self, tmp_path):
        dest = tmp_path / "my_pkg"
        run("my_pkg", dest, modules=["audio"])
        cmake = (dest / "CMakeLists.txt").read_text()
        assert "add_subdirectory(native/src/audio)" in cmake


class TestNewValidation:
    def test_invalid_name_digit_start(self, tmp_path):
        with pytest.raises(SystemExit):
            run("1bad", tmp_path / "1bad")

    def test_invalid_name_hyphen(self, tmp_path):
        with pytest.raises(SystemExit):
            run("my-filter", tmp_path / "my-filter")

    def test_nonempty_dest_fails(self, tmp_path):
        dest = tmp_path / "gain"
        dest.mkdir()
        (dest / "existing.txt").write_text("data")
        with pytest.raises(SystemExit):
            run("gain", dest)

    def test_single_word_name(self, tmp_path):
        dest = tmp_path / "gain"
        run("gain", dest, "gain")
        cmake = (dest / "CMakeLists.txt").read_text()
        assert "project(gain" in cmake


class TestNewBuild:
    """Integration test: new → cmake configure + build → CTest + pytest.
    Skipped if cmake or a C compiler is not available.
    """

    @pytest.fixture(scope="class")
    def built_project(self, tmp_path_factory):
        import shutil

        if not shutil.which("cmake"):
            pytest.skip("cmake not found")
        if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
            pytest.skip("no C compiler found")
        try:
            import numpy  # noqa: F401
        except ImportError:
            pytest.skip("numpy not importable")

        root = tmp_path_factory.mktemp("built") / "gain"
        run("gain", root, "gain")

        import subprocess

        r = subprocess.run(
            [
                "cmake",
                "-B",
                "build",
                "-S",
                ".",
                "-DCMAKE_BUILD_TYPE=Release",
                f"-DPython3_EXECUTABLE={sys.executable}",
            ],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, f"cmake configure failed:\n{r.stderr}"

        r = subprocess.run(
            ["cmake", "--build", "build", "--parallel", "4"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, f"cmake build failed:\n{r.stderr}"

        return root

    def test_so_file_produced(self, built_project):
        so_files = list((built_project / "src").rglob("*.so"))
        assert so_files, "No .so file found in src/"

    def test_combined_lib_produced(self, built_project):
        import platform
        suffix = ".dylib" if platform.system() == "Darwin" else ".so"
        libs = list((built_project / "build").rglob(f"libgain{suffix}"))
        assert libs, f"Combined shared library libgain{suffix} not found in build/"

    def test_ctest_passes(self, built_project):
        import subprocess

        r = subprocess.run(
            ["ctest", "--test-dir", "build", "--output-on-failure"],
            cwd=built_project,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, f"CTest failed:\n{r.stdout}\n{r.stderr}"

    def test_pytest_passes(self, built_project):
        import subprocess

        r = subprocess.run(
            [sys.executable, "-m", "pytest", "src/", "-v"],
            cwd=built_project,
            env={**__import__("os").environ, "PYTHONPATH": str(built_project / "src")},
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, f"pytest failed:\n{r.stdout}\n{r.stderr}"
