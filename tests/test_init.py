"""Integration tests for `just-makeit init`."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._init import run


@pytest.fixture()
def project(tmp_path):
    """Run init for 'my_filter' into a temp directory and return the root."""
    dest = tmp_path / "my_filter"
    run("my_filter", dest)
    return dest


class TestInitFiles:
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

    def test_component_header(self, project):
        assert (project / "native" / "inc" / "my_filter" / "my_filter_core.h").exists()

    def test_component_core_c(self, project):
        assert (project / "native" / "src" / "my_filter" / "my_filter_core.c").exists()

    def test_component_ext_c(self, project):
        assert (project / "native" / "src" / "my_filter" / "my_filter_ext.c").exists()

    def test_c_test(self, project):
        assert (project / "native" / "tests" / "test_my_filter_core.c").exists()

    def test_python_init(self, project):
        assert (project / "src" / "my_filter" / "__init__.py").exists()

    def test_python_pyi(self, project):
        assert (project / "src" / "my_filter" / "my_filter.pyi").exists()

    def test_tests_init(self, project):
        assert (project / "src" / "my_filter" / "tests" / "__init__.py").exists()

    def test_just_makeit_toml_exists(self, project):
        assert (project / "just-makeit.toml").exists()

    def test_compile_commands_exists(self, project):
        assert (project / "compile_commands.json").exists()

    def test_compile_commands_json(self, project):
        import json
        data = json.loads((project / "compile_commands.json").read_text())
        assert isinstance(data, list)
        assert len(data) == 3
        files = {e["file"] for e in data}
        assert any("my_filter_core.c" in f for f in files)
        assert any("my_filter_ext.c" in f for f in files)
        assert all("directory" in e for e in data)

    def test_python_test(self, project):
        assert (project / "src" / "my_filter" / "tests" / "test_my_filter.py").exists()


class TestInitConfig:
    def test_config_has_component_name(self, project):
        import tomllib
        with (project / "just-makeit.toml").open("rb") as f:
            cfg = tomllib.load(f)
        assert cfg["component"]["name"] == "my_filter"

    def test_config_has_default_state(self, project):
        import tomllib
        with (project / "just-makeit.toml").open("rb") as f:
            cfg = tomllib.load(f)
        assert any(s["name"] == "gain" for s in cfg["state"])

    def test_config_records_custom_state(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, [("cutoff", "double", "440.0"), ("order", "int", "4")])
        import tomllib
        with (dest / "just-makeit.toml").open("rb") as f:
            cfg = tomllib.load(f)
        names = [s["name"] for s in cfg["state"]]
        assert names == ["cutoff", "order"]

    def test_config_default_values_preserved(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, [("gain", "double", "1.5")])
        import tomllib
        with (dest / "just-makeit.toml").open("rb") as f:
            cfg = tomllib.load(f)
        gain = next(s for s in cfg["state"] if s["name"] == "gain")
        assert gain["default"] == "1.5"


class TestInitContent:
    def test_no_unreplaced_placeholders(self, project):
        """All <<...>> placeholders must be replaced in every generated file."""
        for path in project.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml", ".txt", ".md"):
                text = path.read_text(encoding="utf-8")
                assert "<<" not in text, f"Unreplaced placeholder in {path}"

    def test_cmake_has_correct_project_name(self, project):
        cmake = (project / "CMakeLists.txt").read_text()
        assert "project(my_filter" in cmake

    def test_cmake_has_python3_add_library(self, project):
        cmake = (project / "CMakeLists.txt").read_text()
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


class TestInitStateVars:
    def test_default_uses_gain(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest)
        h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "double gain;" in h
        assert "comp_get_gain" in h

    def test_custom_single_var(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, [("cutoff", "double", "0.0")])
        h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "double cutoff;" in h
        assert "comp_get_cutoff" in h

    def test_multi_vars(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, [("gain", "double", "1.0"), ("order", "int", "4")])
        h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "double gain;" in h
        assert "int order;" in h
        c = (dest / "native" / "src" / "comp" / "comp_core.c").read_text()
        assert "comp_get_gain" in c
        assert "comp_get_order" in c

    def test_float_type(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, [("alpha", "float", "0.0f")])
        h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "float alpha;" in h

    def test_pyi_has_correct_params_with_defaults(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, [("gain", "double", "1.0"), ("order", "int", "4")])
        pyi = (dest / "src" / "comp" / "comp.pyi").read_text()
        assert "gain: float = 1.0" in pyi
        assert "order: int = 4" in pyi

    def test_ext_c_has_correct_kwlist(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, [("gain", "double", "1.0"), ("order", "int", "4")])
        ext = (dest / "native" / "src" / "comp" / "comp_ext.c").read_text()
        assert '"gain"' in ext
        assert '"order"' in ext

    def test_ext_c_init_params_optional(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, [("gain", "double", "1.0")])
        ext = (dest / "native" / "src" / "comp" / "comp_ext.c").read_text()
        assert '"|d"' in ext

    def test_reset_uses_default_not_zero(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, [("gain", "double", "1.5")])
        c = (dest / "native" / "src" / "comp" / "comp_core.c").read_text()
        assert "state->gain = 1.5;" in c

    def test_no_unreplaced_placeholders_custom_vars(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, [("cutoff", "double", "0.0"), ("poles", "int", "2")])
        for path in dest.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml", ".txt"):
                text = path.read_text(encoding="utf-8")
                assert "<<" not in text, f"Unreplaced placeholder in {path}"


class TestInitValidation:
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
        run("gain", dest)
        assert (dest / "CMakeLists.txt").exists()
        cmake = (dest / "CMakeLists.txt").read_text()
        assert "project(gain" in cmake


class TestInitBuild:
    """Integration test: init → cmake configure + build → CTest + pytest.
    Skipped if cmake or a C compiler is not available.
    """

    @pytest.fixture(scope="class")
    def built_project(self, tmp_path_factory):
        import shutil
        if not shutil.which("cmake"):
            pytest.skip("cmake not found")
        if not shutil.which("cc") and not shutil.which("gcc") and not shutil.which("clang"):
            pytest.skip("no C compiler found")
        try:
            import numpy  # noqa: F401
        except ImportError:
            pytest.skip("numpy not importable in this environment")

        root = tmp_path_factory.mktemp("built") / "gain"
        run("gain", root)

        import subprocess
        r = subprocess.run(
            [
                "cmake", "-B", "build", "-S", ".",
                "-DCMAKE_BUILD_TYPE=Release",
                f"-DPython3_EXECUTABLE={sys.executable}",
            ],
            cwd=root, capture_output=True, text=True,
        )
        assert r.returncode == 0, f"cmake configure failed:\n{r.stderr}"

        r = subprocess.run(
            ["cmake", "--build", "build", "--parallel", "4"],
            cwd=root, capture_output=True, text=True,
        )
        assert r.returncode == 0, f"cmake build failed:\n{r.stderr}"

        return root

    def test_so_file_produced(self, built_project):
        so_files = list((built_project / "src").rglob("*.so"))
        assert so_files, "No .so file found in src/"

    def test_ctest_passes(self, built_project):
        import subprocess
        r = subprocess.run(
            ["ctest", "--test-dir", "build", "--output-on-failure"],
            cwd=built_project, capture_output=True, text=True,
        )
        assert r.returncode == 0, f"CTest failed:\n{r.stdout}\n{r.stderr}"

    def test_pytest_passes(self, built_project):
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "src/", "-v"],
            cwd=built_project,
            env={**__import__("os").environ, "PYTHONPATH": str(built_project / "src")},
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"pytest failed:\n{r.stdout}\n{r.stderr}"
