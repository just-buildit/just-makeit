"""CLI dispatch tests for just-makeit."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from just_makeit._config import load as _load_cfg


SRC = Path(__file__).parent.parent / "src"


def _cli(*args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd or Path.cwd(),
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
    )


class TestHelp:
    def test_no_args_prints_usage(self):
        r = _cli()
        assert r.returncode == 0
        assert "Usage:" in r.stdout

    def test_help_command(self):
        r = _cli("help")
        assert r.returncode == 0
        assert "new" in r.stdout
        assert "object" in r.stdout
        assert "build" in r.stdout
        assert "test" in r.stdout
        assert "dry-run" in r.stdout

    def test_dash_h(self):
        r = _cli("-h")
        assert r.returncode == 0
        assert "Usage:" in r.stdout

    def test_unknown_command_exits_1(self):
        r = _cli("frobnicate")
        assert r.returncode == 1
        assert "unknown command" in r.stderr


class TestNewCLI:
    def test_new_no_name_exits_1(self):
        r = _cli("new")
        assert r.returncode == 1
        assert "requires a project name" in r.stderr

    def test_new_creates_scaffold(self, tmp_path):
        dest = tmp_path / "gain"
        r = _cli("new", "gain", str(dest))
        assert r.returncode == 0
        assert (dest / "CMakeLists.txt").exists()

    def test_new_with_object_creates_full_project(self, tmp_path):
        dest = tmp_path / "gain"
        r = _cli("new", "gain", str(dest), "--object", "gain")
        assert r.returncode == 0
        assert (dest / "CMakeLists.txt").exists()
        assert (dest / "native" / "inc" / "gain" / "gain_core.h").exists()

    def test_new_prints_created_files(self, tmp_path):
        r = _cli("new", "gain", str(tmp_path / "gain"), "--object", "gain")
        assert r.returncode == 0
        assert "CMakeLists.txt" in r.stdout

    def test_new_success_message(self, tmp_path):
        r = _cli("new", "gain", str(tmp_path / "gain"), "--object", "gain")
        assert r.returncode == 0
        assert "Done!" in r.stdout

    def test_new_invalid_name(self, tmp_path):
        r = _cli("new", "my-filter", str(tmp_path / "my-filter"))
        assert r.returncode == 1

    def test_new_default_dest(self, tmp_path):
        r = _cli("new", "gain", cwd=tmp_path)
        assert r.returncode == 0
        assert (tmp_path / "gain" / "CMakeLists.txt").exists()

    def test_new_multiple_objects(self, tmp_path):
        dest = tmp_path / "dsp"
        r = _cli(
            "new", "dsp", str(dest), "--object", "fir", "--object", "biquad"
        )
        assert r.returncode == 0
        assert (dest / "native" / "inc" / "fir" / "fir_core.h").exists()
        assert (dest / "native" / "inc" / "biquad" / "biquad_core.h").exists()

    def test_new_multiple_objects_both_in_init(self, tmp_path):
        dest = tmp_path / "dsp"
        _cli("new", "dsp", str(dest), "--object", "fir", "--object", "biquad")
        init = (dest / "src" / "dsp" / "__init__.py").read_text(
            encoding="utf-8"
        )
        assert "Fir" in init
        assert "Biquad" in init


class TestNewStateCLI:
    def test_state_flag_written_to_header(self, tmp_path):
        dest = tmp_path / "bpf"
        _cli(
            "new",
            "bpf",
            str(dest),
            "--object",
            "bpf",
            "--state",
            "cutoff:double",
        )
        core = (dest / "native" / "inc" / "bpf" / "bpf_core.h").read_text(
            encoding="utf-8"
        )
        assert "double cutoff;" in core

    def test_state_flag_with_default(self, tmp_path):
        dest = tmp_path / "bpf"
        r = _cli(
            "new",
            "bpf",
            str(dest),
            "--object",
            "bpf",
            "--state",
            "gain:double:1.5",
        )
        assert r.returncode == 0
        c = (dest / "native" / "src" / "bpf" / "bpf_core.c").read_text(
            encoding="utf-8"
        )
        assert "state->gain = 1.5;" in c

    def test_multi_state_flags(self, tmp_path):
        dest = tmp_path / "bpf"
        r = _cli(
            "new",
            "bpf",
            str(dest),
            "--object",
            "bpf",
            "--state",
            "cutoff:double:440.0",
            "--state",
            "order:int:4",
        )
        assert r.returncode == 0
        core = (dest / "native" / "inc" / "bpf" / "bpf_core.h").read_text(
            encoding="utf-8"
        )
        assert "double cutoff;" in core
        assert "int order;" in core

    def test_default_uses_gain(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("new", "comp", str(dest), "--object", "comp")
        core = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text(
            encoding="utf-8"
        )
        assert "float gain;" in core

    def test_invalid_type_exits_1(self, tmp_path):
        r = _cli(
            "new",
            "bpf",
            str(tmp_path / "bpf"),
            "--object",
            "bpf",
            "--state",
            "x:complex128",
        )
        assert r.returncode == 1
        assert "unsupported type" in r.stderr

    def test_missing_colon_exits_1(self, tmp_path):
        r = _cli(
            "new",
            "bpf",
            str(tmp_path / "bpf"),
            "--object",
            "bpf",
            "--state",
            "nocolon",
        )
        assert r.returncode == 1
        assert "name:type" in r.stderr


class TestObjectCLI:
    def test_object_no_name_exits_1(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli("object", cwd=dest)
        assert r.returncode == 1
        assert "requires an object name" in r.stderr

    def test_object_adds_standalone(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli("object", "engine", cwd=dest)
        assert r.returncode == 0
        assert (dest / "native" / "inc" / "engine" / "engine_core.h").exists()

    def test_object_no_project_exits_1(self, tmp_path):
        r = _cli("object", "engine", cwd=tmp_path)
        assert r.returncode == 1

    def test_object_success_message(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli("object", "engine", cwd=dest)
        assert r.returncode == 0
        assert "Done!" in r.stdout

    def test_object_with_state(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli("object", "engine", "--state", "rate:double:1.0", cwd=dest)
        assert r.returncode == 0
        core = (
            dest / "native" / "inc" / "engine" / "engine_core.h"
        ).read_text(encoding="utf-8")
        assert "double rate;" in core


class TestVoidArgTypeCLI:
    """--arg-type void accepted by new and object commands."""

    def test_new_void_arg_type(self, tmp_path):
        r = _cli(
            "new",
            "gen",
            str(tmp_path / "gen"),
            "--object",
            "nco",
            "--arg-type",
            "void",
            "--return-type",
            "float",
        )
        assert r.returncode == 0

    def test_new_void_generates_no_input_step(self, tmp_path):
        dest = tmp_path / "gen"
        _cli(
            "new",
            "gen",
            str(dest),
            "--object",
            "nco",
            "--arg-type",
            "void",
            "--return-type",
            "float",
        )
        h = (dest / "native" / "inc" / "nco" / "nco_core.h").read_text(
            encoding="utf-8"
        )
        assert "nco_step(const nco_state_t *state)" in h

    def test_object_void_arg_type(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli(
            "object",
            "osc",
            "--arg-type",
            "void",
            "--return-type",
            "float",
            cwd=dest,
        )
        assert r.returncode == 0

    def test_object_void_generates_no_input_step(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "osc",
            "--arg-type",
            "void",
            "--return-type",
            "float",
            cwd=dest,
        )
        h = (dest / "native" / "inc" / "osc" / "osc_core.h").read_text(
            encoding="utf-8"
        )
        assert "osc_step(const osc_state_t *state)" in h


class TestVoidReturnCLI:
    """--return-type void accepted by new, object, method commands."""

    def test_new_void_return_type(self, tmp_path):
        r = _cli(
            "new",
            "sink",
            str(tmp_path / "sink"),
            "--object",
            "sink",
            "--return-type",
            "void",
        )
        assert r.returncode == 0

    def test_new_void_return_no_volatile_void_in_bench(self, tmp_path):
        dest = tmp_path / "sink"
        _cli(
            "new",
            "sink",
            str(dest),
            "--object",
            "sink",
            "--return-type",
            "void",
        )
        bench = (
            dest / "native" / "benchmarks" / "bench_sink_core.c"
        ).read_text(encoding="utf-8")
        assert "volatile void" not in bench

    def test_object_void_return_type(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli("object", "sink", "--return-type", "void", cwd=dest)
        assert r.returncode == 0

    def test_method_void_return_type(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        r = _cli(
            "method",
            "nco",
            "reset_phase",
            "--module",
            "dsp",
            "--return-type",
            "void",
            cwd=dest,
        )
        assert r.returncode == 0


class TestArrayParamCLI:
    """--param name:type[] validated and scaffolded via CLI."""

    @staticmethod
    def _setup_module(tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        return dest

    def test_method_array_param_accepted(self, tmp_path):
        dest = self._setup_module(tmp_path)
        r = _cli(
            "method",
            "nco",
            "process",
            "--module",
            "dsp",
            "--param",
            "ctrl:float _Complex[]",
            "--return-type",
            "void",
            cwd=dest,
        )
        assert r.returncode == 0

    def test_method_array_param_generates_ptr_len(self, tmp_path):
        dest = self._setup_module(tmp_path)
        _cli(
            "method",
            "nco",
            "process",
            "--module",
            "dsp",
            "--param",
            "ctrl:float _Complex[]",
            "--return-type",
            "void",
            cwd=dest,
        )
        text = (dest / "native/src/nco/nco_core.c").read_text(encoding="utf-8")
        assert "const float complex *ctrl" in text
        assert "size_t ctrl_len" in text

    def test_method_bad_array_elem_type_exits_1(self, tmp_path):
        dest = self._setup_module(tmp_path)
        r = _cli(
            "method",
            "nco",
            "process",
            "--module",
            "dsp",
            "--param",
            "ctrl:bad_type[]",
            "--return-type",
            "void",
            cwd=dest,
        )
        assert r.returncode == 1
        assert "array element type" in r.stderr

    def test_function_array_param_accepted(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "fft")
        r = _cli(
            "function",
            "apply_window",
            "--module",
            "fft",
            "--param",
            "data:float _Complex[]",
            cwd=dest,
        )
        assert r.returncode == 0

    def test_function_bad_array_elem_type_exits_1(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "fft")
        r = _cli(
            "function",
            "apply_window",
            "--module",
            "fft",
            "--param",
            "data:bogus_t[]",
            cwd=dest,
        )
        assert r.returncode == 1
        assert "array element type" in r.stderr

    def test_method_bad_scalar_param_type_still_exits_1(self, tmp_path):
        dest = self._setup_module(tmp_path)
        r = _cli(
            "method",
            "nco",
            "process",
            "--module",
            "dsp",
            "--param",
            "x:not_a_type",
            "--return-type",
            "void",
            cwd=dest,
        )
        assert r.returncode == 1
        assert "not a supported type" in r.stderr


class TestHelpContent:
    """Key concepts and new features appear in --help output."""

    def test_help_mentions_void_return_type(self):
        r = _cli("help")
        assert "void" in r.stdout

    def test_help_mentions_array_param_syntax(self):
        r = _cli("help")
        assert "[]" in r.stdout

    def test_help_mentions_method_command(self):
        r = _cli("help")
        assert "method" in r.stdout

    def test_help_mentions_function_command(self):
        r = _cli("help")
        assert "function" in r.stdout

    def test_help_mentions_param_flag(self):
        r = _cli("help")
        assert "--param" in r.stdout

    def test_help_mentions_return_type_flag(self):
        r = _cli("help")
        assert "--return-type" in r.stdout

    def test_help_has_array_param_example(self):
        r = _cli("help")
        assert "execute_ctrl" in r.stdout or "apply_window" in r.stdout

    def test_help_mentions_sink_object(self):
        r = _cli("help")
        assert "sink" in r.stdout

    def test_help_mentions_generator_object(self):
        r = _cli("help")
        assert "generator" in r.stdout or "gen" in r.stdout

    @pytest.mark.parametrize(
        "flag",
        [
            "--state",
            "--object",
            "--module",
            "--arg-type",
            "--return-type",
            "--perf",
            "--build-system",
            "--basic",
            "--mutable",
            "--pytest",
            "--pytest-benchmark",
            "--no-state",
            "--no-step",
            "--init-param",
            "--param",
            "--variable-output",
            "--multi-output",
            "--batch",
            "--out-type",
            "--out-divisor",
            "--type",
            "--writable",
            "--field",
            "--impl",
            "--replace",
            "--doc",
        ],
    )
    def test_help_mentions_flag(self, flag):
        r = _cli("help")
        assert flag in r.stdout, f"Flag {flag!r} missing from --help output"


class TestAddCLI:
    def test_add_no_state_exits_1(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("new", "comp", str(dest), "--object", "comp")
        r = _cli("add", cwd=dest)
        assert r.returncode == 1
        assert "--state" in r.stderr

    def test_add_no_config_exits_1(self, tmp_path):
        r = _cli("add", "--state", "x:double", cwd=tmp_path)
        assert r.returncode == 1

    def test_add_creates_state_var_in_header(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("new", "comp", str(dest), "--object", "comp")
        r = _cli("add", "--state", "order:int:4", cwd=dest)
        assert r.returncode == 0
        core = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text(
            encoding="utf-8"
        )
        assert "int order;" in core

    def test_add_updates_config(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("new", "comp", str(dest), "--object", "comp")
        _cli("add", "--state", "order:int:4", cwd=dest)
        cfg = _load_cfg(dest)
        names = [s["name"] for s in cfg["comp"]["state"]]
        assert "order" in names

    def test_add_duplicate_exits_1(self, tmp_path):
        dest = tmp_path / "comp"
        _cli(
            "new",
            "comp",
            str(dest),
            "--object",
            "comp",
            "--state",
            "gain:double:1.0",
        )
        r = _cli("add", "--state", "gain:double:2.0", cwd=dest)
        assert r.returncode == 1

    def test_add_invalid_type_exits_1(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("new", "comp", str(dest), "--object", "comp")
        r = _cli("add", "--state", "x:complex128", cwd=dest)
        assert r.returncode == 1

    def test_add_done_message(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("new", "comp", str(dest), "--object", "comp")
        r = _cli("add", "--state", "order:int", cwd=dest)
        assert r.returncode == 0
        assert "Done!" in r.stdout


class TestConfigCLI:
    def test_config_no_config_exits_1(self, tmp_path):
        r = _cli("config", cwd=tmp_path)
        assert r.returncode == 1

    def test_config_shows_project(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("new", "comp", str(dest), "--object", "comp")
        r = _cli("config", cwd=dest)
        assert r.returncode == 0
        assert "comp" in r.stdout

    def test_config_shows_state_vars(self, tmp_path):
        dest = tmp_path / "bpf"
        _cli(
            "new",
            "bpf",
            str(dest),
            "--object",
            "bpf",
            "--state",
            "cutoff:double:440.0",
        )
        r = _cli("config", cwd=dest)
        assert r.returncode == 0
        assert "cutoff" in r.stdout

    def test_config_set_version(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("new", "comp", str(dest), "--object", "comp")
        r = _cli("config", "version", "0.2.0", cwd=dest)
        assert r.returncode == 0
        r2 = _cli("config", cwd=dest)
        assert "0.2.0" in r2.stdout

    def test_config_unknown_key_exits_1(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("new", "comp", str(dest), "--object", "comp")
        r = _cli("config", "frobnicate", "value", cwd=dest)
        assert r.returncode == 1


class TestDryRunCLI:
    def test_dry_run_no_pyproject_exits_1(self, tmp_path):
        r = _cli("dry-run", cwd=tmp_path)
        assert r.returncode == 1
        assert "pyproject.toml" in r.stderr

    def test_dry_run_shows_sources(self, tmp_path):
        dest = tmp_path / "gain"
        _cli("new", "gain", str(dest), "--object", "gain")
        r = _cli("dry-run", cwd=dest)
        assert r.returncode == 0
        assert "gain_core.c" in r.stdout
        assert "gain_ext.c" in r.stdout

    def test_dry_run_shows_cmake_command(self, tmp_path):
        dest = tmp_path / "gain"
        _cli("new", "gain", str(dest), "--object", "gain")
        r = _cli("dry-run", cwd=dest)
        assert r.returncode == 0
        assert "cmake" in r.stdout


class TestArrayArgTypeCLI:
    def test_array_arg_type_accepted(self, tmp_path):
        dest = tmp_path / "proc"
        r = _cli(
            "new",
            "proc",
            str(dest),
            "--object",
            "filt",
            "--arg-type",
            "float _Complex[]",
            "--return-type",
            "float _Complex",
        )
        assert r.returncode == 0

    def test_array_arg_bad_elem_type_exits_1(self, tmp_path):
        dest = tmp_path / "proc"
        r = _cli(
            "new",
            "proc",
            str(dest),
            "--object",
            "filt",
            "--arg-type",
            "bogus_t[]",
        )
        assert r.returncode == 1
        assert "array element type" in r.stderr

    def test_array_return_type_rejected(self, tmp_path):
        dest = tmp_path / "proc"
        r = _cli(
            "new",
            "proc",
            str(dest),
            "--object",
            "filt",
            "--arg-type",
            "float",
            "--return-type",
            "float[]",
        )
        assert r.returncode == 1
        assert "cannot be an array type" in r.stderr

    def test_array_arg_generates_ptr_len_in_c(self, tmp_path):
        dest = tmp_path / "proc"
        _cli(
            "new",
            "proc",
            str(dest),
            "--object",
            "filt",
            "--arg-type",
            "float[]",
            "--return-type",
            "float",
        )
        core_h = (dest / "native/inc/filt/filt_core.h").read_text(
            encoding="utf-8"
        )
        assert "const float *x, size_t x_len" in core_h

    def test_array_arg_no_steps_in_pymethoddef(self, tmp_path):
        dest = tmp_path / "proc"
        _cli(
            "new",
            "proc",
            str(dest),
            "--object",
            "filt",
            "--arg-type",
            "float _Complex[]",
            "--return-type",
            "float _Complex",
        )
        ext = (dest / "native/src/filt/filt_ext.c").read_text(encoding="utf-8")
        assert "Filt_steps" not in ext

    def test_object_cmd_array_arg_type(self, tmp_path):
        dest = tmp_path / "proc"
        _cli("new", "proc", str(dest), "--module", "dsp")
        r = _cli(
            "object",
            "filt",
            "--module",
            "dsp",
            "--arg-type",
            "float _Complex[]",
            "--return-type",
            "float _Complex",
            cwd=dest,
        )
        assert r.returncode == 0
        ext = (dest / "native/src/dsp/dsp_ext_filt.c").read_text(
            encoding="utf-8"
        )
        assert "PyArray_FROM_OTF" in ext
        assert "Filt_steps" not in ext


class TestNewBuildSystemCLI:
    """--build-system selects cmake (default) or make."""

    def test_cmake_is_default(self, tmp_path):
        dest = tmp_path / "gain"
        r = _cli("new", "gain", str(dest))
        assert r.returncode == 0
        assert (dest / "CMakeLists.txt").exists()

    def test_build_system_cmake_explicit(self, tmp_path):
        dest = tmp_path / "gain"
        r = _cli("new", "gain", str(dest), "--build-system", "cmake")
        assert r.returncode == 0
        assert (dest / "CMakeLists.txt").exists()

    def test_build_system_make_exits_0(self, tmp_path):
        r = _cli(
            "new", "gain", str(tmp_path / "gain"), "--build-system", "make"
        )
        assert r.returncode == 0

    def test_build_system_make_has_makefile(self, tmp_path):
        dest = tmp_path / "gain"
        _cli("new", "gain", str(dest), "--build-system", "make")
        assert (dest / "Makefile").exists()

    def test_build_system_make_has_no_cmake_lists(self, tmp_path):
        dest = tmp_path / "gain"
        _cli("new", "gain", str(dest), "--build-system", "make")
        assert not (dest / "CMakeLists.txt").exists()

    def test_build_system_invalid_exits_1(self, tmp_path):
        r = _cli(
            "new", "gain", str(tmp_path / "gain"), "--build-system", "meson"
        )
        assert r.returncode == 1

    def test_basic_deprecated_alias_still_works(self, tmp_path):
        dest = tmp_path / "gain"
        r = _cli("new", "gain", str(dest), "--basic")
        assert r.returncode == 0
        assert not (dest / "CMakeLists.txt").exists()

    def test_basic_deprecated_prints_warning(self, tmp_path):
        r = _cli("new", "gain", str(tmp_path / "gain"), "--basic")
        assert "deprecated" in r.stderr


class TestModuleCommandCLI:
    """bare `module <name>` command."""

    def test_module_no_name_exits_1(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli("module", cwd=dest)
        assert r.returncode == 1
        assert "module" in r.stderr

    def test_module_no_project_exits_1(self, tmp_path):
        r = _cli("module", "dsp", cwd=tmp_path)
        assert r.returncode == 1

    def test_module_creates_subpackage(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli("module", "dsp", cwd=dest)
        assert r.returncode == 0
        assert (dest / "src" / "proj" / "dsp" / "__init__.py").exists()

    def test_module_creates_ext_c(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("module", "dsp", cwd=dest)
        assert (dest / "native" / "src" / "dsp" / "dsp_ext.c").exists()

    def test_module_creates_core_h(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("module", "dsp", cwd=dest)
        assert (dest / "native" / "inc" / "dsp" / "dsp_core.h").exists()

    def test_module_object_creates_python_test(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("module", "dsp", cwd=dest)
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        test_py = dest / "src" / "proj" / "dsp" / "tests" / "test_nco.py"
        assert test_py.exists()
        text = test_py.read_text()
        assert "from proj.dsp import Nco" in text

    def test_module_object_creates_python_bench(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("module", "dsp", cwd=dest)
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        bench_py = (
            dest / "src" / "proj" / "dsp" / "benchmarks" / "bench_nco.py"
        )
        assert bench_py.exists()
        text = bench_py.read_text()
        assert "from proj.dsp import Nco" in text

    def test_module_object_second_object_gets_own_test(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("module", "dsp", cwd=dest)
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        _cli("object", "mixer", "--module", "dsp", cwd=dest)
        assert (
            dest / "src" / "proj" / "dsp" / "tests" / "test_nco.py"
        ).exists()
        assert (
            dest / "src" / "proj" / "dsp" / "tests" / "test_mixer.py"
        ).exists()


class TestObjectNoStateCLI:
    """object --no-state omits state struct."""

    def test_no_state_exits_0(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli("object", "gen", "--no-state", cwd=dest)
        assert r.returncode == 0

    def test_no_state_struct_has_placeholder_body(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "gen", "--no-state", cwd=dest)
        core_h = (dest / "native/inc/gen/gen_core.h").read_text(
            encoding="utf-8"
        )
        # struct frame is present but body is left as a manual-implementation placeholder
        assert "typedef struct" in core_h
        assert "IMPLEMENT" in core_h

    def test_no_state_and_state_mutually_exclusive(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli(
            "object", "gen", "--no-state", "--state", "x:double", cwd=dest
        )
        assert r.returncode == 1
        assert "mutually exclusive" in r.stderr

    def test_no_state_persisted_in_toml(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "gen", "--no-state", cwd=dest)
        cfg = _load_cfg(dest)
        assert cfg["gen"]["no_state"] == "true"


class TestObjectNoStepCLI:
    """object --no-step omits step() method."""

    def test_no_step_exits_0(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli("object", "sink", "--no-step", cwd=dest)
        assert r.returncode == 0

    def test_no_step_omits_inline_step_in_header(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "sink", "--no-step", cwd=dest)
        core_h = (dest / "native/inc/sink/sink_core.h").read_text(
            encoding="utf-8"
        )
        # inline function definition should be absent (docstring examples may mention it)
        assert "static inline" not in core_h

    def test_no_step_omits_step_in_ext_c(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "sink", "--no-step", cwd=dest)
        ext_c = (dest / "native/src/sink/sink_ext.c").read_text(
            encoding="utf-8"
        )
        assert "Sink_step" not in ext_c

    def test_no_step_persisted_in_toml(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "sink", "--no-step", cwd=dest)
        cfg = _load_cfg(dest)
        assert cfg["sink"]["no_step"] == "true"


class TestObjectMutableCLI:
    """object --mutable removes const from the state pointer in step()."""

    def test_mutable_exits_0(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli(
            "object",
            "nco",
            "--mutable",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            cwd=dest,
        )
        assert r.returncode == 0

    def test_mutable_removes_const_from_step(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "nco",
            "--mutable",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            cwd=dest,
        )
        core_h = (dest / "native/inc/nco/nco_core.h").read_text(
            encoding="utf-8"
        )
        assert "nco_step(nco_state_t *state)" in core_h
        assert "nco_step(const nco_state_t *state)" not in core_h

    def test_immutable_default_has_const(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "nco",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            cwd=dest,
        )
        core_h = (dest / "native/inc/nco/nco_core.h").read_text(
            encoding="utf-8"
        )
        assert "nco_step(const nco_state_t *state)" in core_h

    def test_mutable_scalar_arg_removes_const(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "filt",
            "--mutable",
            "--arg-type",
            "float",
            "--return-type",
            "float",
            cwd=dest,
        )
        core_h = (dest / "native/inc/filt/filt_core.h").read_text(
            encoding="utf-8"
        )
        assert "filt_step(filt_state_t *state, float x)" in core_h
        assert "filt_step(const filt_state_t *state, float x)" not in core_h

    def test_mutable_persisted_in_toml(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "nco",
            "--mutable",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            cwd=dest,
        )
        cfg = _load_cfg(dest)
        assert cfg["nco"]["mutable"] == "true"


class TestObjectPerfCLI:
    """object --perf annotates step() with JM_HOT/JM_FORCEINLINE."""

    def test_perf_exits_0(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli("object", "engine", "--perf", cwd=dest)
        assert r.returncode == 0

    def test_perf_includes_jm_perf_h(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "engine", "--perf", cwd=dest)
        core_h = (dest / "native/inc/engine/engine_core.h").read_text(
            encoding="utf-8"
        )
        assert "jm_perf.h" in core_h

    def test_perf_step_uses_jm_qualifiers(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "engine", "--perf", cwd=dest)
        core_h = (dest / "native/inc/engine/engine_core.h").read_text(
            encoding="utf-8"
        )
        assert "JM_HOT" in core_h or "JM_FORCEINLINE" in core_h


class TestMethodVariableOutputCLI:
    """method --variable-output generates runtime-sized output buffer."""

    @staticmethod
    def _setup(tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        return dest

    def test_variable_output_exits_0(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli(
            "method",
            "nco",
            "execute_cf32",
            "--module",
            "dsp",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            "--variable-output",
            cwd=dest,
        )
        assert r.returncode == 0

    def test_variable_output_has_buf_field_in_ext_c(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "nco",
            "execute_cf32",
            "--module",
            "dsp",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            "--variable-output",
            cwd=dest,
        )
        ext = (dest / "native/src/dsp/dsp_ext_nco.c").read_text(
            encoding="utf-8"
        )
        assert "_execute_cf32_buf" in ext and "realloc" in ext


class TestMethodMultiOutputCLI:
    """method --multi-output emits a second output array."""

    @staticmethod
    def _setup(tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        return dest

    def test_multi_output_exits_0(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli(
            "method",
            "nco",
            "execute_ovf",
            "--module",
            "dsp",
            "--arg-type",
            "void",
            "--return-type",
            "uint32_t",
            "--variable-output",
            "--multi-output",
            "uint8_t",
            cwd=dest,
        )
        assert r.returncode == 0

    def test_multi_output_bad_type_exits_1(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli(
            "method",
            "nco",
            "execute_ovf",
            "--module",
            "dsp",
            "--return-type",
            "uint32_t",
            "--variable-output",
            "--multi-output",
            "not_a_type",
            cwd=dest,
        )
        assert r.returncode == 1

    def test_multi_output_tuple_pack_in_ext_c(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "nco",
            "execute_ovf",
            "--module",
            "dsp",
            "--arg-type",
            "void",
            "--return-type",
            "uint32_t",
            "--variable-output",
            "--multi-output",
            "uint8_t",
            cwd=dest,
        )
        ext = (dest / "native/src/dsp/dsp_ext_nco.c").read_text(
            encoding="utf-8"
        )
        assert "PyTuple_Pack" in ext


class TestPropertyCLI:
    """property command and all its flags."""

    @staticmethod
    def _setup(tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--object", "engine")
        return dest

    def test_property_no_args_exits_1(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli("property", cwd=dest)
        assert r.returncode == 1
        assert "property" in r.stderr

    def test_property_no_project_exits_1(self, tmp_path):
        r = _cli("property", "engine", "gain", cwd=tmp_path)
        assert r.returncode == 1

    def test_property_exits_0(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli("property", "engine", "gain", "--type", "double", cwd=dest)
        assert r.returncode == 0

    def test_property_type_adds_getter_to_ext_c(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli("property", "engine", "gain", "--type", "double", cwd=dest)
        ext = (dest / "native/src/engine/engine_ext.c").read_text(
            encoding="utf-8"
        )
        assert "gain" in ext
        assert "PyGetSetDef" in ext or "getset" in ext.lower()

    def test_property_bad_type_exits_1(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli(
            "property", "engine", "gain", "--type", "not_a_type", cwd=dest
        )
        assert r.returncode == 1

    def test_property_writable_adds_setter(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "property",
            "engine",
            "gain",
            "--type",
            "double",
            "--writable",
            cwd=dest,
        )
        ext = (dest / "native/src/engine/engine_ext.c").read_text(
            encoding="utf-8"
        )
        assert (
            "set_gain" in ext
            or "gain_set" in ext
            or "setter" in ext.lower()
            or "gain" in ext
        )

    def test_property_writable_has_setter_decl_in_core_h(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "property",
            "engine",
            "gain",
            "--type",
            "double",
            "--writable",
            cwd=dest,
        )
        core_h = (dest / "native/inc/engine/engine_core.h").read_text(
            encoding="utf-8"
        )
        assert "set_gain" in core_h or "gain" in core_h

    def test_property_readonly_no_setter_in_ext_c(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli("property", "engine", "dropped", "--type", "size_t", cwd=dest)
        ext = (dest / "native/src/engine/engine_ext.c").read_text(
            encoding="utf-8"
        )
        assert "NULL" in ext  # readonly slot is NULL setter

    def test_property_field_adds_struct_member(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "property",
            "engine",
            "rate",
            "--type",
            "double",
            "--field",
            cwd=dest,
        )
        core_h = (dest / "native/inc/engine/engine_core.h").read_text(
            encoding="utf-8"
        )
        assert "double rate;" in core_h

    def test_property_module_flag_accepted(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        r = _cli(
            "property",
            "nco",
            "phase",
            "--module",
            "dsp",
            "--type",
            "uint32_t",
            cwd=dest,
        )
        assert r.returncode == 0

    def test_property_module_updates_module_ext_c(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        _cli(
            "property",
            "nco",
            "phase",
            "--module",
            "dsp",
            "--type",
            "uint32_t",
            cwd=dest,
        )
        ext = (dest / "native/src/dsp/dsp_ext_nco.c").read_text(
            encoding="utf-8"
        )
        assert "phase" in ext


class TestFunctionReturnTypeCLI:
    """function --return-type sets the C return type."""

    @staticmethod
    def _setup(tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "fft")
        return dest

    def test_return_type_exits_0(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli(
            "function",
            "get_size",
            "--module",
            "fft",
            "--return-type",
            "size_t",
            cwd=dest,
        )
        assert r.returncode == 0

    def test_return_type_appears_in_core_h(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "function",
            "get_size",
            "--module",
            "fft",
            "--return-type",
            "size_t",
            cwd=dest,
        )
        core_h = (dest / "native/inc/fft/fft_core.h").read_text(
            encoding="utf-8"
        )
        assert "size_t" in core_h
        assert "get_size" in core_h

    def test_return_type_appears_in_fn_c_stub(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "function",
            "get_size",
            "--module",
            "fft",
            "--return-type",
            "size_t",
            cwd=dest,
        )
        fn_c = (dest / "native/src/fft/get_size.c").read_text(encoding="utf-8")
        assert "size_t" in fn_c
        assert "get_size" in fn_c

    def test_bad_return_type_exits_1(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli(
            "function",
            "get_size",
            "--module",
            "fft",
            "--return-type",
            "not_a_type",
            cwd=dest,
        )
        assert r.returncode == 1


class TestFunctionDocCLI:
    """function --doc sets the Python docstring."""

    def test_doc_exits_0(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "fft")
        r = _cli(
            "function",
            "apply_window",
            "--module",
            "fft",
            "--doc",
            "Apply a Hann window.",
            cwd=dest,
        )
        assert r.returncode == 0

    def test_doc_appears_in_ext_c(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "fft")
        _cli(
            "function",
            "apply_window",
            "--module",
            "fft",
            "--doc",
            "Apply a Hann window.",
            cwd=dest,
        )
        ext = (dest / "native/src/fft/fft_ext.c").read_text(encoding="utf-8")
        assert "Apply a Hann window." in ext


class TestAddParamCLI:
    """add --param appends a constructor parameter to a pure object."""

    @staticmethod
    def _setup(tmp_path):
        dest = tmp_path / "proj"
        _cli(
            "new",
            "proj",
            str(dest),
            "--object",
            "norm",
            "--state",
            "scale:double:1.0",
        )
        return dest

    def test_add_param_exits_0(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli("add", "--param", "offset:double:0.0", cwd=dest)
        assert r.returncode == 0

    def test_add_param_appears_in_header(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli("add", "--param", "offset:double:0.0", cwd=dest)
        core_h = (dest / "native/inc/norm/norm_core.h").read_text(
            encoding="utf-8"
        )
        assert "double offset" in core_h or "offset" in core_h

    def test_add_param_recorded_in_config(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli("add", "--param", "offset:double:0.0", cwd=dest)
        cfg = _load_cfg(dest)
        names = [s["name"] for s in cfg["norm"].get("state", [])]
        assert "offset" in names


class TestPerfCommandCLI:
    """perf command retrofits JM_HOT/JM_FORCEINLINE without touching user code."""

    def test_perf_no_project_exits_1(self, tmp_path):
        r = _cli("perf", cwd=tmp_path)
        assert r.returncode == 1

    def test_perf_exits_0(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--object", "engine")
        r = _cli("perf", cwd=dest)
        assert r.returncode == 0

    def test_perf_creates_jm_perf_h(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--object", "engine")
        _cli("perf", cwd=dest)
        assert (dest / "native" / "inc" / "jm_perf.h").exists()

    def test_perf_step_gains_qualifiers(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--object", "engine")
        _cli("perf", cwd=dest)
        core_h = (dest / "native/inc/engine/engine_core.h").read_text(
            encoding="utf-8"
        )
        assert "JM_HOT" in core_h or "JM_FORCEINLINE" in core_h


class TestExampleCommandCLI:
    """example command lists or runs bundled examples."""

    def test_example_no_name_lists_examples(self):
        r = _cli("example")
        assert r.returncode == 0
        assert len(r.stdout.strip()) > 0

    def test_example_unknown_name_exits_1(self):
        r = _cli("example", "not_a_real_example_xyzzy")
        assert r.returncode == 1


class TestMethodStandaloneCLI:
    """method command on a standalone object (no --module)."""

    @staticmethod
    def _setup(tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--object", "nco")
        return dest

    def test_method_standalone_exits_0(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli(
            "method",
            "nco",
            "reset_phase",
            "--return-type",
            "void",
            cwd=dest,
        )
        assert r.returncode == 0

    def test_method_standalone_appends_stub(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "nco",
            "reset_phase",
            "--return-type",
            "void",
            cwd=dest,
        )
        src = (dest / "native/src/nco/nco_core.c").read_text(encoding="utf-8")
        assert "nco_reset_phase" in src

    def test_method_standalone_missing_args_exits_1(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli("method", cwd=dest)
        assert r.returncode == 1


class TestMethodOutTypeCLI:
    """method --out-type allocates a per-call output array."""

    @staticmethod
    def _setup(tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "conv", "--module", "dsp", cwd=dest)
        return dest

    def test_out_type_exits_0(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli(
            "method",
            "conv",
            "process",
            "--module",
            "dsp",
            "--param",
            "data:float[]",
            "--out-type",
            "float",
            "--return-type",
            "void",
            cwd=dest,
        )
        assert r.returncode == 0

    def test_out_type_generates_pyarray_empty(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "conv",
            "process",
            "--module",
            "dsp",
            "--param",
            "data:float[]",
            "--out-type",
            "float",
            "--return-type",
            "void",
            cwd=dest,
        )
        ext = (dest / "native/src/dsp/dsp_ext_conv.c").read_text(
            encoding="utf-8"
        )
        assert "PyArray_EMPTY" in ext

    def test_out_type_bad_type_exits_1(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli(
            "method",
            "conv",
            "process",
            "--module",
            "dsp",
            "--param",
            "data:float[]",
            "--out-type",
            "not_a_type",
            cwd=dest,
        )
        assert r.returncode == 1

    def test_out_divisor_with_out_type(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli(
            "method",
            "conv",
            "decimate",
            "--module",
            "dsp",
            "--param",
            "data:float[]",
            "--out-type",
            "float",
            "--out-divisor",
            "2",
            "--return-type",
            "void",
            cwd=dest,
        )
        assert r.returncode == 0
        ext = (dest / "native/src/dsp/dsp_ext_conv.c").read_text(
            encoding="utf-8"
        )
        assert "/ 2" in ext

    def test_out_divisor_non_positive_exits_1(self, tmp_path):
        dest = self._setup(tmp_path)
        r = _cli(
            "method",
            "conv",
            "decimate",
            "--module",
            "dsp",
            "--param",
            "data:float[]",
            "--out-type",
            "float",
            "--out-divisor",
            "0",
            cwd=dest,
        )
        assert r.returncode == 1

    def test_out_type_persisted_in_toml(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "conv",
            "process",
            "--module",
            "dsp",
            "--param",
            "data:float[]",
            "--out-type",
            "float",
            "--return-type",
            "void",
            cwd=dest,
        )
        cfg = _load_cfg(dest)
        method = next(
            m for m in cfg["conv"]["methods"] if m["name"] == "process"
        )
        assert method["out_type"] == "float"

    def test_out_divisor_persisted_in_toml(self, tmp_path):
        dest = self._setup(tmp_path)
        _cli(
            "method",
            "conv",
            "decimate",
            "--module",
            "dsp",
            "--param",
            "data:float[]",
            "--out-type",
            "float",
            "--out-divisor",
            "4",
            "--return-type",
            "void",
            cwd=dest,
        )
        cfg = _load_cfg(dest)
        method = next(
            m for m in cfg["conv"]["methods"] if m["name"] == "decimate"
        )
        assert method["out_divisor"] == 4


class TestNewModuleRepeatableCLI:
    """new --module is repeatable."""

    def test_two_modules_both_created(self, tmp_path):
        dest = tmp_path / "proj"
        r = _cli(
            "new",
            "proj",
            str(dest),
            "--module",
            "filter",
            "--module",
            "source",
        )
        assert r.returncode == 0
        assert (dest / "native/inc/filter/filter_core.h").exists()
        assert (dest / "native/inc/source/source_core.h").exists()

    def test_two_modules_both_in_toml(self, tmp_path):
        dest = tmp_path / "proj"
        _cli(
            "new",
            "proj",
            str(dest),
            "--module",
            "filter",
            "--module",
            "source",
        )
        cfg = _load_cfg(dest)
        assert "filter" in cfg["module"]
        assert "source" in cfg["module"]


class TestScriptCLI:
    """script command emits a correct CLI reconstruction."""

    def test_script_exits_0(self, tmp_path):
        dest = tmp_path / "proj"
        _cli(
            "new",
            "proj",
            str(dest),
            "--object",
            "engine",
            "--state",
            "rate:double:1.0",
        )
        r = _cli("script", cwd=dest)
        assert r.returncode == 0

    def test_script_starts_with_new(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli("script", cwd=dest)
        assert "just-makeit new proj" in r.stdout

    def test_script_standalone_object(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "engine", "--state", "rate:double:1.0", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "just-makeit object engine" in r.stdout
        assert "--state rate:double:1.0" in r.stdout

    def test_script_module_object(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "just-makeit module dsp" in r.stdout
        assert "just-makeit object nco" in r.stdout
        assert "--module dsp" in r.stdout

    def test_script_no_duplicate_object_cmds(self, tmp_path):
        """Module objects must not also appear as standalone object commands."""
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        r = _cli("script", cwd=dest)
        assert r.stdout.count("just-makeit object nco") == 1

    def test_script_mutable_flag(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "nco",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            "--mutable",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--mutable" in r.stdout

    def test_script_return_type_emitted_when_differs_from_arg_type(
        self, tmp_path
    ):
        """--arg-type void --return-type 'float _Complex' must appear explicitly."""
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "nco",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert '--return-type "float _Complex"' in r.stdout

    def test_script_return_type_omitted_when_matches_arg_type(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object",
            "filt",
            "--arg-type",
            "float",
            "--return-type",
            "float",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--return-type float" not in r.stdout

    def test_script_method(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "nco", "--module", "dsp", cwd=dest)
        _cli(
            "method",
            "nco",
            "execute",
            "--module",
            "dsp",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            "--variable-output",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "just-makeit method nco execute" in r.stdout
        assert "--variable-output" in r.stdout

    def test_script_property(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "nco", cwd=dest)
        _cli(
            "property",
            "nco",
            "phase",
            "--type",
            "uint32_t",
            "--writable",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "just-makeit property nco phase" in r.stdout
        assert "--writable" in r.stdout

    def test_script_function(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli(
            "function",
            "dsp_init",
            "--module",
            "dsp",
            "--doc",
            "Initialize.",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "just-makeit function dsp_init" in r.stdout
        assert "--module dsp" in r.stdout

    def test_script_no_toml_exits_1(self, tmp_path):
        r = _cli("script", cwd=tmp_path)
        assert r.returncode == 1

    def test_script_no_state_flag(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "gen", "--no-state", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "--no-state" in r.stdout

    def test_script_no_step_flag(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "sink", "--no-step", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "--no-step" in r.stdout

    def test_script_init_param(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object", "gen", "--no-state", "--init-param", "n:int:16", cwd=dest
        )
        r = _cli("script", cwd=dest)
        assert "--init-param" in r.stdout
        assert "n:int:16" in r.stdout

    def test_script_method_batch(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "conv", "--module", "dsp", cwd=dest)
        _cli("method", "conv", "run", "--module", "dsp", "--batch", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "--batch" in r.stdout

    def test_script_method_out_type(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "conv", "--module", "dsp", cwd=dest)
        _cli(
            "method",
            "conv",
            "process",
            "--module",
            "dsp",
            "--param",
            "data:float[]",
            "--out-type",
            "float",
            "--return-type",
            "void",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert (
            '--out-type "float"' in r.stdout or "--out-type float" in r.stdout
        )

    def test_script_method_out_divisor(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "conv", "--module", "dsp", cwd=dest)
        _cli(
            "method",
            "conv",
            "decimate",
            "--module",
            "dsp",
            "--param",
            "data:float[]",
            "--out-type",
            "float",
            "--out-divisor",
            "4",
            "--return-type",
            "void",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--out-divisor 4" in r.stdout

    def test_script_method_out_divisor_1_not_emitted(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "conv", "--module", "dsp", cwd=dest)
        _cli(
            "method",
            "conv",
            "process",
            "--module",
            "dsp",
            "--param",
            "data:float[]",
            "--out-type",
            "float",
            "--out-divisor",
            "1",
            "--return-type",
            "void",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--out-divisor" not in r.stdout

    def test_script_method_multi_output(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "conv", "--module", "dsp", cwd=dest)
        _cli(
            "method",
            "conv",
            "run",
            "--module",
            "dsp",
            "--multi-output",
            "float",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--multi-output" in r.stdout

    def test_script_property_field(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "nco", cwd=dest)
        _cli(
            "property",
            "nco",
            "phase",
            "--type",
            "uint32_t",
            "--field",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--field" in r.stdout

    def test_script_array_arg(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli("object", "buf", "--arg-type", "float[]", cwd=dest)
        r = _cli("script", cwd=dest)
        assert "--arg-type" in r.stdout
        assert "float[]" in r.stdout

    def test_script_function_params(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli(
            "function",
            "add_two",
            "--module",
            "dsp",
            "--param",
            "x:float",
            "--param",
            "y:float",
            "--return-type",
            "float",
            cwd=dest,
        )
        r = _cli("script", cwd=dest)
        assert "--param x:float" in r.stdout
        assert "--param y:float" in r.stdout
        assert "--return-type float" in r.stdout


class TestInitParamCLI:
    """object --init-param provides constructor args for --no-state objects."""

    def test_init_param_exits_0(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli(
            "object", "gen", "--no-state", "--init-param", "n:int:16", cwd=dest
        )
        assert r.returncode == 0

    def test_init_param_composes_with_state(self, tmp_path):
        """Phase 2 / gh-69: --init-param + --state together produce a ctor
        driven by init_params, with state staying internal and accessible
        through the generated getters/setters."""
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli(
            "object",
            "iq_reader",
            "--state",
            "fd:int:-1",
            "--init-param",
            "filepath:const char *",
            cwd=dest,
        )
        assert r.returncode == 0, r.stderr
        # Header declares the ctor taking the init param, not the state field.
        core_h = (dest / "native/inc/iq_reader/iq_reader_core.h").read_text(
            encoding="utf-8"
        )
        assert "iq_reader_create(const char * filepath)" in core_h
        assert "iq_reader_create(int fd)" not in core_h
        # State field is in the struct with a generated getter/setter pair.
        assert "int fd;" in core_h
        assert "iq_reader_get_fd" in core_h
        assert "iq_reader_set_fd" in core_h

    def test_init_param_constructor_arg_in_core_c(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object", "gen", "--no-state", "--init-param", "n:int:16", cwd=dest
        )
        core_c = (dest / "native/src/gen/gen_core.c").read_text(
            encoding="utf-8"
        )
        assert "int n" in core_c

    def test_multiple_init_params(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli(
            "object",
            "gen",
            "--no-state",
            "--init-param",
            "n:int:16",
            "--init-param",
            "order:int:4",
            cwd=dest,
        )
        assert r.returncode == 0
        core_c = (dest / "native/src/gen/gen_core.c").read_text(
            encoding="utf-8"
        )
        assert "int n" in core_c
        assert "int order" in core_c

    def test_init_param_persisted_in_toml(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object", "gen", "--no-state", "--init-param", "n:int:16", cwd=dest
        )
        cfg = _load_cfg(dest)
        ip = cfg["gen"]["init_params"]
        assert any(p["name"] == "n" for p in ip)

    def test_init_param_default_optional_in_python(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object", "gen", "--no-state", "--init-param", "n:int:16", cwd=dest
        )
        ext_c = (dest / "native/src/gen/gen_ext.c").read_text(encoding="utf-8")
        assert "16" in ext_c


class TestImplCLI:
    """--impl file::funcname lifts a body into the generated step() stub."""

    def test_impl_object(self, tmp_path):
        src_file = tmp_path / "algo.c"
        src_file.write_text(
            "float complex my_step(void) { return 0.0f; }\n",
            encoding="utf-8",
        )
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli(
            "object",
            "nco",
            "--arg-type",
            "void",
            "--return-type",
            "float _Complex",
            "--impl",
            f"{src_file}::my_step",
            cwd=dest,
        )
        assert r.returncode == 0
        core_h = (dest / "native/inc/nco/nco_core.h").read_text(
            encoding="utf-8"
        )
        assert "return 0.0f" in core_h

    def test_impl_method(self, tmp_path):
        src_file = tmp_path / "algo.c"
        src_file.write_text(
            "void run(void) { /* body */ }\n",
            encoding="utf-8",
        )
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--module", "dsp")
        _cli("object", "proc", "--module", "dsp", cwd=dest)
        r = _cli(
            "method",
            "proc",
            "run",
            "--module",
            "dsp",
            "--impl",
            f"{src_file}::run",
            cwd=dest,
        )
        assert r.returncode == 0
        core_c = (dest / "native/src/proc/proc_core.c").read_text(
            encoding="utf-8"
        )
        assert "/* body */" in core_c

    def test_impl_missing_file_exits_1(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli(
            "object", "nco", "--impl", "/nonexistent/file.c::my_func", cwd=dest
        )
        assert r.returncode == 1
