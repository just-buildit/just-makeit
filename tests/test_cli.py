"""CLI dispatch tests for just-makeit."""

import os
import subprocess
import sys
from pathlib import Path


SRC = Path(__file__).parent.parent / "src"


def _cli(*args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from just_makeit._cli import main; main()", *args],
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


class TestNewStateCLI:
    def test_state_flag_written_to_header(self, tmp_path):
        dest = tmp_path / "bpf"
        _cli("new", "bpf", str(dest), "--object", "bpf", "--state", "cutoff:double")
        core = (dest / "native" / "inc" / "bpf" / "bpf_core.h").read_text(encoding="utf-8")
        assert "double cutoff;" in core

    def test_state_flag_with_default(self, tmp_path):
        dest = tmp_path / "bpf"
        r = _cli(
            "new", "bpf", str(dest), "--object", "bpf", "--state", "gain:double:1.5"
        )
        assert r.returncode == 0
        c = (dest / "native" / "src" / "bpf" / "bpf_core.c").read_text(encoding="utf-8")
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
        core = (dest / "native" / "inc" / "bpf" / "bpf_core.h").read_text(encoding="utf-8")
        assert "double cutoff;" in core
        assert "int order;" in core

    def test_default_uses_gain(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("new", "comp", str(dest), "--object", "comp")
        core = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text(encoding="utf-8")
        assert "double gain;" in core

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
        core = (dest / "native" / "inc" / "engine" / "engine_core.h").read_text(encoding="utf-8")
        assert "double rate;" in core


class TestVoidArgTypeCLI:
    """--arg-type void accepted by new and object commands."""

    def test_new_void_arg_type(self, tmp_path):
        r = _cli(
            "new", "gen", str(tmp_path / "gen"),
            "--object", "nco",
            "--arg-type", "void",
            "--return-type", "float",
        )
        assert r.returncode == 0

    def test_new_void_generates_no_input_step(self, tmp_path):
        dest = tmp_path / "gen"
        _cli(
            "new", "gen", str(dest),
            "--object", "nco",
            "--arg-type", "void",
            "--return-type", "float",
        )
        h = (dest / "native" / "inc" / "nco" / "nco_core.h").read_text(encoding="utf-8")
        assert "nco_step(const nco_state_t *state)" in h

    def test_object_void_arg_type(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli(
            "object", "osc",
            "--arg-type", "void",
            "--return-type", "float",
            cwd=dest,
        )
        assert r.returncode == 0

    def test_object_void_generates_no_input_step(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        _cli(
            "object", "osc",
            "--arg-type", "void",
            "--return-type", "float",
            cwd=dest,
        )
        h = (dest / "native" / "inc" / "osc" / "osc_core.h").read_text(encoding="utf-8")
        assert "osc_step(const osc_state_t *state)" in h


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
        core = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text(encoding="utf-8")
        assert "int order;" in core

    def test_add_updates_config(self, tmp_path):
        import tomllib

        dest = tmp_path / "comp"
        _cli("new", "comp", str(dest), "--object", "comp")
        _cli("add", "--state", "order:int:4", cwd=dest)
        with (dest / "just-makeit.toml").open("rb") as f:
            cfg = tomllib.load(f)
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
