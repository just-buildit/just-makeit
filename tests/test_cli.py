"""CLI dispatch tests for just-makeit."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"


def _cli(*args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c",
         "from just_makeit._cli import main; main()", *args],
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
        assert "init" in r.stdout
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


class TestInitCLI:
    def test_init_no_name_exits_1(self):
        r = _cli("init")
        assert r.returncode == 1
        assert "requires a component name" in r.stderr

    def test_init_creates_project(self, tmp_path):
        dest = tmp_path / "gain"
        r = _cli("init", "gain", str(dest))
        assert r.returncode == 0
        assert (dest / "CMakeLists.txt").exists()

    def test_init_prints_created_files(self, tmp_path):
        r = _cli("init", "gain", str(tmp_path / "gain"))
        assert r.returncode == 0
        assert "CMakeLists.txt" in r.stdout

    def test_init_success_message(self, tmp_path):
        r = _cli("init", "gain", str(tmp_path / "gain"))
        assert r.returncode == 0
        assert "Done!" in r.stdout

    def test_init_invalid_name(self, tmp_path):
        r = _cli("init", "my-filter", str(tmp_path / "my-filter"))
        assert r.returncode == 1

    def test_init_default_dest(self, tmp_path):
        r = _cli("init", "gain", cwd=tmp_path)
        assert r.returncode == 0
        assert (tmp_path / "gain" / "CMakeLists.txt").exists()


class TestInitStateCLI:
    def test_state_flag_creates_project(self, tmp_path):
        dest = tmp_path / "bpf"
        r = _cli("init", "bpf", str(dest), "--state", "gain:double")
        assert r.returncode == 0
        assert (dest / "CMakeLists.txt").exists()

    def test_state_flag_written_to_header(self, tmp_path):
        dest = tmp_path / "bpf"
        _cli("init", "bpf", str(dest), "--state", "cutoff:double")
        h = (dest / "native" / "inc" / "bpf" / "bpf_core.h").read_text()
        assert "double cutoff;" in h

    def test_state_flag_with_default(self, tmp_path):
        dest = tmp_path / "bpf"
        r = _cli("init", "bpf", str(dest), "--state", "gain:double:1.5")
        assert r.returncode == 0
        c = (dest / "native" / "src" / "bpf" / "bpf_core.c").read_text()
        assert "state->gain = 1.5;" in c

    def test_multi_state_flags(self, tmp_path):
        dest = tmp_path / "bpf"
        r = _cli(
            "init", "bpf", str(dest),
            "--state", "cutoff:double:440.0",
            "--state", "order:int:4",
        )
        assert r.returncode == 0
        h = (dest / "native" / "inc" / "bpf" / "bpf_core.h").read_text()
        assert "double cutoff;" in h
        assert "int order;" in h

    def test_default_uses_gain(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("init", "comp", str(dest))
        h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "double gain;" in h

    def test_invalid_type_exits_1(self, tmp_path):
        r = _cli("init", "bpf", str(tmp_path / "bpf"), "--state", "x:complex128")
        assert r.returncode == 1
        assert "unsupported type" in r.stderr

    def test_missing_colon_exits_1(self, tmp_path):
        r = _cli("init", "bpf", str(tmp_path / "bpf"), "--state", "nodcolon")
        assert r.returncode == 1
        assert "name:type" in r.stderr

    def test_state_flag_before_dir(self, tmp_path):
        dest = tmp_path / "bpf"
        r = _cli("init", "bpf", "--state", "gain:double", str(dest))
        assert r.returncode == 0
        assert (dest / "CMakeLists.txt").exists()


class TestAddCLI:
    def test_add_no_state_exits_1(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("init", "comp", str(dest))
        r = _cli("add", cwd=dest)
        assert r.returncode == 1
        assert "--state" in r.stderr

    def test_add_no_config_exits_1(self, tmp_path):
        r = _cli("add", "--state", "x:double", cwd=tmp_path)
        assert r.returncode == 1

    def test_add_creates_state_var_in_header(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("init", "comp", str(dest))
        r = _cli("add", "--state", "order:int:4", cwd=dest)
        assert r.returncode == 0
        h = (dest / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "int order;" in h

    def test_add_updates_config(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("init", "comp", str(dest))
        _cli("add", "--state", "order:int:4", cwd=dest)
        import tomllib
        with (dest / "just-makeit.toml").open("rb") as f:
            cfg = tomllib.load(f)
        names = [s["name"] for s in cfg["state"]]
        assert "order" in names

    def test_add_duplicate_exits_1(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("init", "comp", str(dest), "--state", "gain:double:1.0")
        r = _cli("add", "--state", "gain:double:2.0", cwd=dest)
        assert r.returncode == 1

    def test_add_invalid_type_exits_1(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("init", "comp", str(dest))
        r = _cli("add", "--state", "x:complex128", cwd=dest)
        assert r.returncode == 1

    def test_add_done_message(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("init", "comp", str(dest))
        r = _cli("add", "--state", "order:int", cwd=dest)
        assert r.returncode == 0
        assert "Done!" in r.stdout


class TestConfigCLI:
    def test_config_no_config_exits_1(self, tmp_path):
        r = _cli("config", cwd=tmp_path)
        assert r.returncode == 1

    def test_config_shows_component(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("init", "comp", str(dest))
        r = _cli("config", cwd=dest)
        assert r.returncode == 0
        assert "comp" in r.stdout

    def test_config_shows_state_vars(self, tmp_path):
        dest = tmp_path / "bpf"
        _cli("init", "bpf", str(dest), "--state", "cutoff:double:440.0")
        r = _cli("config", cwd=dest)
        assert r.returncode == 0
        assert "cutoff" in r.stdout

    def test_config_set_version(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("init", "comp", str(dest))
        r = _cli("config", "version", "0.2.0", cwd=dest)
        assert r.returncode == 0
        r2 = _cli("config", cwd=dest)
        assert "0.2.0" in r2.stdout

    def test_config_unknown_key_exits_1(self, tmp_path):
        dest = tmp_path / "comp"
        _cli("init", "comp", str(dest))
        r = _cli("config", "frobnicate", "value", cwd=dest)
        assert r.returncode == 1


class TestDryRunCLI:
    def test_dry_run_no_pyproject_exits_1(self, tmp_path):
        r = _cli("dry-run", cwd=tmp_path)
        assert r.returncode == 1
        assert "pyproject.toml" in r.stderr

    def test_dry_run_shows_sources(self, tmp_path):
        dest = tmp_path / "gain"
        _cli("init", "gain", str(dest))
        r = _cli("dry-run", cwd=dest)
        assert r.returncode == 0
        assert "gain_core.c" in r.stdout
        assert "gain_ext.c" in r.stdout

    def test_dry_run_shows_cmake_command(self, tmp_path):
        dest = tmp_path / "gain"
        _cli("init", "gain", str(dest))
        r = _cli("dry-run", cwd=dest)
        assert r.returncode == 0
        assert "cmake" in r.stdout
