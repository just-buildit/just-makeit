"""Integration tests for `just-makeit new`."""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run

_STRAY_PLACEHOLDER = re.compile(r"<<(?!IMPLEMENT:)")


# Fixture: new project with a component (the typical one-shot path).
@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "my_filter"
    run("my_filter", dest, ["my_filter"])
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

    def test_config_cmake_in_exists(self, project):
        assert (project / "cmake" / "my_filter-config.cmake.in").exists()


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

        data = json.loads(
            (project / "compile_commands.json").read_text(encoding="utf-8")
        )
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
        init = (scaffold / "src" / "my_proj" / "__init__.py").read_text(
            encoding="utf-8"
        )
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
            "comp",
            dest,
            ["comp"],
            [("cutoff", "double", "440.0"), ("order", "int", "4")],
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
        cmake = (project / "CMakeLists.txt").read_text(encoding="utf-8")
        assert "project(my_filter" in cmake

    def test_cmake_top_has_python_package_dir(self, project):
        cmake = (project / "CMakeLists.txt").read_text(encoding="utf-8")
        assert "PYTHON_PACKAGE_DIR" in cmake
        assert "src/my_filter" in cmake

    def test_cmake_top_has_add_subdirectory(self, project):
        cmake = (project / "CMakeLists.txt").read_text(encoding="utf-8")
        assert "add_subdirectory(native/src/my_filter)" in cmake

    def test_component_cmake_has_python3_add_library(self, project):
        cmake = (project / "native" / "src" / "my_filter" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert "Python3_add_library(my_filter" in cmake

    def test_header_has_correct_typedef(self, project):
        h = (project / "native" / "inc" / "my_filter" / "my_filter_core.h").read_text(
            encoding="utf-8"
        )
        assert "my_filter_state_t" in h
        assert "my_filter_create" in h
        assert "my_filter_destroy" in h
        assert "my_filter_step" in h

    def test_ext_c_has_correct_class(self, project):
        ext = (project / "native" / "src" / "my_filter" / "my_filter_ext.c").read_text(
            encoding="utf-8"
        )
        assert "MyFilterObject" in ext
        assert "PyInit_my_filter" in ext

    def test_python_init_imports_class(self, project):
        init = (project / "src" / "my_filter" / "__init__.py").read_text(
            encoding="utf-8"
        )
        assert "from .my_filter import MyFilter" in init

    def test_pyproject_uses_just_buildit(self, project):
        toml = (project / "pyproject.toml").read_text(encoding="utf-8")
        assert 'build-backend = "just_buildit"' in toml
        assert 'command = "make just-build"' in toml

    def test_pyproject_has_project_name(self, project):
        toml = (project / "pyproject.toml").read_text(encoding="utf-8")
        assert 'name = "my-filter"' in toml

    def test_cmake_top_has_combined_lib(self, project):
        cmake = (project / "CMakeLists.txt").read_text(encoding="utf-8")
        assert "add_library(my_filter_lib SHARED" in cmake
        assert "add_library(my_filter_lib_static STATIC" in cmake

    def test_cmake_component_is_object_lib(self, project):
        cmake = (project / "native" / "src" / "my_filter" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert "add_library(my_filter_core OBJECT" in cmake

    def test_umbrella_header_content(self, project):
        h = (project / "native" / "inc" / "my_filter.h").read_text(encoding="utf-8")
        assert "MY_FILTER_H" in h

    def test_umbrella_header_updated_by_init(self, tmp_path):
        from just_makeit._init import run as init_run

        dest = tmp_path / "my_pkg"
        run("my_pkg", dest)
        init_run(dest, "gain")
        umbrella = (dest / "native" / "inc" / "my_pkg.h").read_text(encoding="utf-8")
        assert '#include "gain/gain_core.h"' in umbrella

    def test_pc_in_content(self, project):
        pc = (project / "cmake" / "my-filter.pc.in").read_text(encoding="utf-8")
        assert "Name: my-filter" in pc
        assert "-lmy_filter" in pc
        assert "CMAKE_INSTALL_FULL_" not in pc, (
            "pc.in must use relative ${prefix}/... paths, not absolute @CMAKE_INSTALL_FULL_*@ vars"
        )

    def test_config_cmake_in_content(self, project):
        cfg = (project / "cmake" / "my_filter-config.cmake.in").read_text(
            encoding="utf-8"
        )
        assert "@PACKAGE_INIT@" in cfg
        assert "my_filter-targets.cmake" in cfg
        assert "check_required_components(my_filter)" in cfg


class TestMakeTestRunner:
    """Regression: make test must use unittest by default, pytest only with --pytest."""

    def _makefile(self, tmp_path, **kwargs) -> str:
        dest = tmp_path / "proj"
        run("proj", dest, **kwargs)
        return (dest / "Makefile").read_text(encoding="utf-8")

    def _makefile_simple(self, tmp_path, **kwargs) -> str:
        dest = tmp_path / "proj"
        run("proj", dest, build_system="make", **kwargs)
        return (dest / "Makefile").read_text(encoding="utf-8")

    # ── CMake Makefile (default) ───────────────────────────────────────────

    def test_cmake_default_uses_unittest(self, tmp_path):
        mk = self._makefile(tmp_path)
        assert "unittest discover" in mk

    def test_cmake_default_no_pytest_invocation(self, tmp_path):
        mk = self._makefile(tmp_path)
        # coverage always uses pytest-cov regardless of test style;
        # check only that the *test runner* doesn't invoke pytest
        assert "pytest src/ -v" not in mk

    def test_cmake_pytest_flag_uses_pytest(self, tmp_path):
        mk = self._makefile(tmp_path, pytest_=True)
        assert "pytest src/" in mk

    def test_cmake_pytest_flag_no_unittest(self, tmp_path):
        mk = self._makefile(tmp_path, pytest_=True)
        assert "unittest discover" not in mk

    # ── Plain Makefile (--build-system make) ──────────────────────────────

    def test_make_default_uses_unittest(self, tmp_path):
        mk = self._makefile_simple(tmp_path)
        assert "unittest discover" in mk

    def test_make_default_no_pytest_invocation(self, tmp_path):
        mk = self._makefile_simple(tmp_path)
        assert "pytest src/" not in mk

    def test_make_pytest_flag_uses_pytest(self, tmp_path):
        mk = self._makefile_simple(tmp_path, pytest_=True)
        assert "pytest src/" in mk

    def test_make_pytest_flag_no_unittest(self, tmp_path):
        mk = self._makefile_simple(tmp_path, pytest_=True)
        assert "unittest discover" not in mk

    # ── No double-tab orphan line ──────────────────────────────────────────

    def test_cmake_default_no_orphan_tab_line(self, tmp_path):
        """Empty ensure block must not leave a bare tab-only recipe line."""
        mk = self._makefile(tmp_path)
        assert "\n\t\n" not in mk

    def test_make_default_no_orphan_tab_line(self, tmp_path):
        mk = self._makefile_simple(tmp_path)
        assert "\n\t\n" not in mk


class TestNewStateVars:
    def test_default_uses_gain(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, ["comp"])
        core_h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text(
            encoding="utf-8"
        )
        assert "float gain;" in core_h
        assert "comp_get_gain" in core_h

    def test_custom_single_var(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, ["comp"], [("cutoff", "double", "0.0")])
        core_h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text(
            encoding="utf-8"
        )
        assert "double cutoff;" in core_h

    def test_multi_vars(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, ["comp"], [("gain", "double", "1.0"), ("order", "int", "4")])
        core_h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text(
            encoding="utf-8"
        )
        assert "double gain;" in core_h
        assert "int order;" in core_h

    def test_float_type(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, ["comp"], [("alpha", "float", "0.0f")])
        core_h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text(
            encoding="utf-8"
        )
        assert "float alpha;" in core_h

    def test_reset_uses_default_not_zero(self, tmp_path):
        dest = tmp_path / "comp"
        run("comp", dest, ["comp"], [("gain", "double", "1.5")])
        c = (dest / "native" / "src" / "comp" / "comp_core.c").read_text(
            encoding="utf-8"
        )
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
        cmake = (dest / "CMakeLists.txt").read_text(encoding="utf-8")
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
        run("gain", dest, ["gain"])
        cmake = (dest / "CMakeLists.txt").read_text(encoding="utf-8")
        assert "project(gain" in cmake


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Generated C99 complex types are not supported by MSVC; "
    "use MinGW or Clang on Windows to build generated projects.",
)
class TestNewBuild:
    """Integration test: new → cmake configure + build → CTest + pytest.
    Skipped if cmake or a C99-capable compiler is not available.
    Skipped entirely on Windows (MSVC rejects float complex).
    """

    @pytest.fixture(scope="class")
    def built_project(self, tmp_path_factory):
        import shutil

        if not shutil.which("cmake"):
            pytest.skip("cmake not found")
        if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
            pytest.skip("no C99 compiler found")
        try:
            import numpy  # noqa: F401
        except ImportError:
            pytest.skip("numpy not importable")

        root = tmp_path_factory.mktemp("built") / "gain"
        run("gain", root, ["gain"])

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


class TestVoidReturn:
    """--return-type void on jm new / jm object."""

    @pytest.fixture()
    def sink(self, tmp_path):
        dest = tmp_path / "audio"
        run(
            "audio",
            dest,
            ["sink"],
            state_vars=[("volume", "double", "1.0")],
            return_type="void",
        )
        return dest

    @pytest.fixture()
    def void_gen(self, tmp_path):
        dest = tmp_path / "audio"
        run(
            "audio",
            dest,
            ["ticker"],
            state_vars=[("phase", "double", "0.0")],
            arg_type="void",
            return_type="void",
        )
        return dest

    def test_step_returns_void_in_core_h(self, sink):
        h = (sink / "native/inc/sink/sink_core.h").read_text(encoding="utf-8")
        # step_impl_def spans two lines: "static inline void\nsink_step("
        assert "static inline void" in h
        assert "sink_step(" in h

    def test_no_volatile_void_in_bench(self, sink):
        bench = (sink / "native/benchmarks/bench_sink_core.c").read_text(
            encoding="utf-8"
        )
        assert "volatile void" not in bench

    def test_no_void_ptr_out_in_bench(self, sink):
        bench = (sink / "native/benchmarks/bench_sink_core.c").read_text(
            encoding="utf-8"
        )
        assert "void *out" not in bench
        assert "sizeof(void)" not in bench

    def test_bench_calls_step_without_assignment(self, sink):
        bench = (sink / "native/benchmarks/bench_sink_core.c").read_text(
            encoding="utf-8"
        )
        assert "sink_step(obj" in bench
        assert "_sink = sink_step" not in bench

    def test_steps_no_out_param_in_sink(self, sink):
        c = (sink / "native/src/sink/sink_core.c").read_text(encoding="utf-8")
        assert "void *output" not in c
        assert "sink_steps(state, input, n)" in c or (
            "const float complex    *input" in c and "size_t               n)" in c
        )

    def test_core_h_step_example_no_void_y(self, sink):
        h = (sink / "native/inc/sink/sink_core.h").read_text(encoding="utf-8")
        assert "void y = " not in h
        assert "sink_step(obj" in h

    def test_void_gen_no_volatile_void(self, void_gen):
        bench = (void_gen / "native/benchmarks/bench_ticker_core.c").read_text(
            encoding="utf-8"
        )
        assert "volatile void" not in bench

    def test_void_gen_steps_takes_only_n(self, void_gen):
        c = (void_gen / "native/src/ticker/ticker_core.c").read_text(encoding="utf-8")
        assert "ticker_steps(state, n)" in c or (
            "ticker_steps(\n    ticker_state_t *state,\n    size_t" in c
        )

    def test_no_stray_placeholders_sink(self, sink):
        for path in sink.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml", ".txt"):
                text = path.read_text(encoding="utf-8")
                m = _STRAY_PLACEHOLDER.search(text)
                assert m is None, f"Stray placeholder in {path}"

    def test_no_stray_placeholders_void_gen(self, void_gen):
        for path in void_gen.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml", ".txt"):
                text = path.read_text(encoding="utf-8")
                m = _STRAY_PLACEHOLDER.search(text)
                assert m is None, f"Stray placeholder in {path}"


# ── Array arg type (--arg-type "float _Complex[]") ────────────────────────────


class TestArrayArgType:
    @pytest.fixture()
    def arr_obj(self, tmp_path):
        dest = tmp_path / "proc"
        run(
            "proc",
            dest,
            ["filt"],
            arg_type="float _Complex[]",
            return_type="float _Complex",
        )
        return dest

    def test_core_h_step_has_ptr_len_params(self, arr_obj):
        h = (arr_obj / "native/inc/filt/filt_core.h").read_text(encoding="utf-8")
        assert "const float complex *x, size_t x_len" in h

    def test_core_h_step_returns_correct_type(self, arr_obj):
        h = (arr_obj / "native/inc/filt/filt_core.h").read_text(encoding="utf-8")
        assert "static inline float complex" in h

    def test_core_h_no_steps(self, arr_obj):
        h = (arr_obj / "native/inc/filt/filt_core.h").read_text(encoding="utf-8")
        assert "filt_steps" not in h

    def test_bench_no_inner_sample_loop(self, arr_obj):
        bench = (arr_obj / "native/benchmarks/bench_filt_core.c").read_text(
            encoding="utf-8"
        )
        # The step() call should pass the full buffer, not index in[i].
        assert "filt_step(obj, in, BENCH_N)" in bench
        # in[i] appears only in the input-init loop, not in any step() call.
        for line in bench.splitlines():
            if "filt_step" in line:
                assert "in[i]" not in line, line

    def test_bench_no_steps_timing(self, arr_obj):
        bench = (arr_obj / "native/benchmarks/bench_filt_core.c").read_text(
            encoding="utf-8"
        )
        assert "filt_steps" not in bench

    def test_ext_c_step_uses_pyarray(self, arr_obj):
        ext = (arr_obj / "native/src/filt/filt_ext.c").read_text(encoding="utf-8")
        assert "PyArray_FROM_OTF" in ext
        assert "PyArray_DATA" in ext
        assert "x_len" in ext

    def test_ext_c_no_steps_method(self, arr_obj):
        ext = (arr_obj / "native/src/filt/filt_ext.c").read_text(encoding="utf-8")
        assert "Filt_steps" not in ext

    def test_pyi_step_annotation(self, arr_obj):
        pyi = (arr_obj / "src/proc/filt.pyi").read_text(encoding="utf-8")
        assert "def step(self, x: NDArray[np.complex64]) -> complex:" in pyi

    def test_pyi_no_steps(self, arr_obj):
        pyi = (arr_obj / "src/proc/filt.pyi").read_text(encoding="utf-8")
        assert "def steps" not in pyi

    def test_no_stray_placeholders(self, arr_obj):
        for path in arr_obj.rglob("*"):
            if path.is_file() and path.suffix in (
                ".py",
                ".c",
                ".h",
                ".toml",
                ".txt",
                ".pyi",
            ):
                text = path.read_text(encoding="utf-8")
                m = _STRAY_PLACEHOLDER.search(text)
                assert m is None, f"Stray placeholder in {path}"
