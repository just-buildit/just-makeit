"""Integration tests for --pytest and --pytest-benchmark flags.

Covers:
- _config.py: is_pytest(), is_pytest_benchmark(), from_new() persistence, _dump()
- Generated test file content (pure pytest functions, no unittest shim)
- Generated bench file content (pytest-benchmark fixtures)
- Module objects inherit project-level flags
- `add` regeneration preserves chosen framework
- Default (no flags) still produces legacy files (backward compat)
- Script round-trip: flags survive TOML → jm script → replay
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._add import run as add_run
from just_makeit._config import (
    _dump,
    from_new,
    is_pytest,
    is_pytest_benchmark,
    load,
    save,
)
from just_makeit._init import run as init_run
from just_makeit._new import run as new_run

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
        timeout=600,
    )


# ── _config helpers ───────────────────────────────────────────────────────────


class TestConfigFlags:
    def test_is_pytest_false_by_default(self):
        cfg = from_new("proj")
        assert not is_pytest(cfg)

    def test_is_pytest_benchmark_false_by_default(self):
        cfg = from_new("proj")
        assert not is_pytest_benchmark(cfg)

    def test_is_pytest_true_when_set(self):
        cfg = from_new("proj", pytest_=True)
        assert is_pytest(cfg)

    def test_is_pytest_benchmark_true_when_set(self):
        cfg = from_new("proj", pytest_benchmark_=True)
        assert is_pytest_benchmark(cfg)

    def test_both_flags_independent(self):
        cfg = from_new("proj", pytest_=True, pytest_benchmark_=True)
        assert is_pytest(cfg)
        assert is_pytest_benchmark(cfg)

    def test_pytest_written_to_toml(self):
        cfg = from_new("proj", pytest_=True)
        text = _dump(cfg)
        assert 'pytest = "true"' in text

    def test_pytest_benchmark_written_to_toml(self):
        cfg = from_new("proj", pytest_benchmark_=True)
        text = _dump(cfg)
        assert 'pytest_benchmark = "true"' in text

    def test_flags_default_false_in_toml_when_not_set(self):
        cfg = from_new("proj")
        text = _dump(cfg)
        assert 'pytest = "false"' in text
        assert 'pytest_benchmark = "false"' in text

    def test_round_trip_via_save_load(self, tmp_path):
        cfg = from_new("proj", pytest_=True, pytest_benchmark_=True)
        save(tmp_path, cfg)
        loaded = load(tmp_path)
        assert is_pytest(loaded)
        assert is_pytest_benchmark(loaded)


# ── standalone object: --pytest ───────────────────────────────────────────────


@pytest.fixture()
def pytest_project(tmp_path):
    dest = tmp_path / "myproj"
    new_run("myproj", dest, pytest_=True)
    init_run(dest, "engine", [("gain", "float", "0.0f")])
    return dest


class TestPytestTestFile:
    def test_no_unittest_import(self, pytest_project):
        test = (
            pytest_project / "src" / "myproj" / "tests" / "test_engine.py"
        ).read_text(encoding="utf-8")
        assert "import unittest" not in test

    def test_imports_pytest(self, pytest_project):
        test = (
            pytest_project / "src" / "myproj" / "tests" / "test_engine.py"
        ).read_text(encoding="utf-8")
        assert "import pytest" in test

    def test_no_compatibility_shim(self, pytest_project):
        test = (
            pytest_project / "src" / "myproj" / "tests" / "test_engine.py"
        ).read_text(encoding="utf-8")
        assert "compatibility shim" not in test
        assert "_approx" not in test
        assert "_raises" not in test

    def test_top_level_functions_not_class(self, pytest_project):
        test = (
            pytest_project / "src" / "myproj" / "tests" / "test_engine.py"
        ).read_text(encoding="utf-8")
        assert "class Test" not in test
        assert "def test_create():" in test
        assert "def test_getter_setter():" in test
        assert "def test_reset():" in test

    def test_uses_pytest_approx(self, pytest_project):
        test = (
            pytest_project / "src" / "myproj" / "tests" / "test_engine.py"
        ).read_text(encoding="utf-8")
        assert "pytest.approx" in test

    def test_uses_pytest_raises(self, pytest_project):
        test = (
            pytest_project / "src" / "myproj" / "tests" / "test_engine.py"
        ).read_text(encoding="utf-8")
        assert "pytest.raises" in test

    def test_plain_assert_not_self_assert(self, pytest_project):
        test = (
            pytest_project / "src" / "myproj" / "tests" / "test_engine.py"
        ).read_text(encoding="utf-8")
        assert "self.assert" not in test
        assert "self.assertEqual" not in test


# ── standalone object: --pytest-benchmark ────────────────────────────────────


@pytest.fixture()
def bench_project(tmp_path):
    dest = tmp_path / "myproj"
    new_run("myproj", dest, pytest_benchmark_=True)
    init_run(dest, "engine", [("gain", "float", "0.0f")])
    return dest


class TestPytestBenchFile:
    def test_no_perf_counter(self, bench_project):
        bench = (
            bench_project / "src" / "myproj" / "benchmarks" / "bench_engine.py"
        ).read_text(encoding="utf-8")
        assert "perf_counter" not in bench

    def test_imports_pytest(self, bench_project):
        bench = (
            bench_project / "src" / "myproj" / "benchmarks" / "bench_engine.py"
        ).read_text(encoding="utf-8")
        assert "import pytest" in bench

    def test_has_fixture(self, bench_project):
        bench = (
            bench_project / "src" / "myproj" / "benchmarks" / "bench_engine.py"
        ).read_text(encoding="utf-8")
        assert "@pytest.fixture" in bench
        assert "def obj():" in bench

    def test_benchmark_functions(self, bench_project):
        bench = (
            bench_project / "src" / "myproj" / "benchmarks" / "bench_engine.py"
        ).read_text(encoding="utf-8")
        assert "def test_bench_step(benchmark, obj):" in bench

    def test_benchmark_call(self, bench_project):
        bench = (
            bench_project / "src" / "myproj" / "benchmarks" / "bench_engine.py"
        ).read_text(encoding="utf-8")
        assert "benchmark(obj.step," in bench

    def test_no_main_function(self, bench_project):
        bench = (
            bench_project / "src" / "myproj" / "benchmarks" / "bench_engine.py"
        ).read_text(encoding="utf-8")
        assert "def main()" not in bench


# ── default (no flags) is unchanged ──────────────────────────────────────────


@pytest.fixture()
def default_project(tmp_path):
    dest = tmp_path / "myproj"
    new_run("myproj", dest)
    init_run(dest, "engine", [("gain", "float", "0.0f")])
    return dest


class TestDefaultUnchanged:
    def test_still_uses_unittest(self, default_project):
        test = (
            default_project / "src" / "myproj" / "tests" / "test_engine.py"
        ).read_text(encoding="utf-8")
        assert "import unittest" in test
        assert "class TestEngine" in test

    def test_bench_still_uses_perf_counter(self, default_project):
        bench = (
            default_project
            / "src"
            / "myproj"
            / "benchmarks"
            / "bench_engine.py"
        ).read_text(encoding="utf-8")
        assert "perf_counter" in bench
        assert "def main()" in bench


# ── different arg-types with --pytest ────────────────────────────────────────


class TestPytestArgTypes:
    def test_void_arg_generator(self, tmp_path):
        dest = tmp_path / "proj"
        new_run("proj", dest, pytest_=True)
        init_run(
            dest, "nco", [], arg_type="void", return_type="float _Complex"
        )
        test = (dest / "src" / "proj" / "tests" / "test_nco.py").read_text(
            encoding="utf-8"
        )
        assert "import unittest" not in test
        assert "def test_step_runs():" in test
        assert "obj.step()" in test

    def test_array_arg_no_steps_bench(self, tmp_path):
        dest = tmp_path / "proj"
        new_run("proj", dest, pytest_benchmark_=True)
        init_run(
            dest,
            "fir",
            [],
            arg_type="float _Complex[]",
            return_type="float _Complex",
        )
        bench = (
            dest / "src" / "proj" / "benchmarks" / "bench_fir.py"
        ).read_text(encoding="utf-8")
        assert "@pytest.fixture" in bench
        # array arg: step_1k/64k instead of steps_1k/64k
        assert "def test_bench_step_1k(benchmark, obj):" in bench
        assert "def test_bench_step_64k(benchmark, obj):" in bench
        assert "def test_bench_steps" not in bench

    def test_scalar_float_bench(self, tmp_path):
        dest = tmp_path / "proj"
        new_run("proj", dest, pytest_benchmark_=True)
        init_run(
            dest,
            "gain",
            [("level", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        bench = (
            dest / "src" / "proj" / "benchmarks" / "bench_gain.py"
        ).read_text(encoding="utf-8")
        assert "def test_bench_step(benchmark, obj):" in bench
        assert "def test_bench_steps_1k(benchmark, obj):" in bench
        assert "def test_bench_steps_64k(benchmark, obj):" in bench


# ── [project.bench] block_sizes (gh-390) ──────────────────────────────────────


class TestBenchBlockSizes:
    """`[project.bench] block_sizes` controls the generated bench suites."""

    def test_default_is_1k_and_64k(self):
        from just_makeit._config import project_bench_block_sizes

        assert project_bench_block_sizes({}) == [1024, 65536]

    def test_custom_dedup_sorted_filtered(self):
        from just_makeit._config import project_bench_block_sizes

        cfg = {"project": {"bench": {"block_sizes": [65536, 1024, 1024, -1]}}}
        assert project_bench_block_sizes(cfg) == [1024, 65536]

    def test_empty_falls_back_to_default(self):
        from just_makeit._config import project_bench_block_sizes

        cfg = {"project": {"bench": {"block_sizes": []}}}
        assert project_bench_block_sizes(cfg) == [1024, 65536]

    def test_block_sizes_round_trip_through_dump(self, tmp_path):
        # The `[project.bench]` sub-table must survive a config save/load
        # (jm rewrites just-makeit.toml on every mutating command).
        from just_makeit._config import project_bench_block_sizes

        dest = tmp_path / "proj"
        new_run("proj", dest, pytest_benchmark_=True)
        cfg = load(dest)
        cfg.setdefault("project", {})["bench"] = {"block_sizes": [65536]}
        save(dest, cfg)
        assert project_bench_block_sizes(load(dest)) == [65536]

    def test_scaffold_honors_single_block_size(self, tmp_path):
        # A project that benches only 64k blocks must not get the _1k suite
        # (or its now-unused BLOCK_1K constant) reintroduced on scaffold.
        dest = tmp_path / "proj"
        new_run("proj", dest, pytest_benchmark_=True)
        cfg = load(dest)
        cfg.setdefault("project", {})["bench"] = {"block_sizes": [65536]}
        save(dest, cfg)
        init_run(
            dest,
            "gain",
            [("level", "float", "1.0f")],
            arg_type="float",
            return_type="float",
        )
        bench = (
            dest / "src" / "proj" / "benchmarks" / "bench_gain.py"
        ).read_text(encoding="utf-8")
        assert "BLOCK_64K = 65_536" in bench
        assert "BLOCK_1K" not in bench
        assert "def test_bench_steps_64k(benchmark, obj):" in bench
        assert "def test_bench_steps_1k" not in bench

    def test_non_power_of_1024_label_is_literal(self):
        from just_makeit._context import make_sample_ctx

        ctx = make_sample_ctx("float", "float", [256, 4096])
        # 4096 collapses to a k-suffix; 256 keeps its literal label.
        assert "BLOCK_256 = 256" in ctx["bench_block_consts"]
        assert "BLOCK_4K" in ctx["bench_block_consts"]
        assert (
            "def test_bench_steps_256(benchmark, obj):" in ctx["bm_steps_py"]
        )
        assert "def test_bench_steps_4k(benchmark, obj):" in ctx["bm_steps_py"]


# ── module objects inherit project flags ─────────────────────────────────────


@pytest.fixture()
def module_pytest_project(tmp_path):
    dest = tmp_path / "myproj"
    new_run("myproj", dest, pytest_=True, pytest_benchmark_=True)
    from just_makeit._module import run as module_run
    from just_makeit._object import run as object_run

    module_run(dest, "dsp")
    object_run(dest, "fir", "dsp")
    return dest


class TestModuleInheritance:
    def test_module_test_pure_pytest(self, module_pytest_project):
        test = (
            module_pytest_project
            / "src"
            / "myproj"
            / "dsp"
            / "tests"
            / "test_fir.py"
        ).read_text(encoding="utf-8")
        assert "import unittest" not in test
        assert "import pytest" in test
        assert "def test_create():" in test
        assert "pytest.approx" in test

    def test_module_bench_pytest_benchmark(self, module_pytest_project):
        bench = (
            module_pytest_project
            / "src"
            / "myproj"
            / "dsp"
            / "benchmarks"
            / "bench_fir.py"
        ).read_text(encoding="utf-8")
        assert "perf_counter" not in bench
        assert "@pytest.fixture" in bench
        assert "def test_bench_step(benchmark, obj):" in bench

    def test_module_bench_imports_from_subpackage(self, module_pytest_project):
        bench = (
            module_pytest_project
            / "src"
            / "myproj"
            / "dsp"
            / "benchmarks"
            / "bench_fir.py"
        ).read_text(encoding="utf-8")
        assert "from myproj.dsp import Fir" in bench


# ── add regeneration preserves chosen framework ───────────────────────────────


class TestAddPreservesFramework:
    def test_add_keeps_pure_pytest(self, tmp_path):
        dest = tmp_path / "proj"
        new_run("proj", dest, pytest_=True)
        init_run(dest, "filt", [("gain", "float", "1.0f")])
        add_run(dest, "filt", [("order", "int", "4")], force=True)
        test = (dest / "src" / "proj" / "tests" / "test_filt.py").read_text(
            encoding="utf-8"
        )
        assert "import unittest" not in test
        assert "import pytest" in test
        assert "get_order" in test

    def test_add_keeps_pytest_benchmark(self, tmp_path):
        dest = tmp_path / "proj"
        new_run("proj", dest, pytest_benchmark_=True)
        init_run(dest, "filt", [("gain", "float", "1.0f")])
        add_run(dest, "filt", [("order", "int", "4")], force=True)
        bench = (
            dest / "src" / "proj" / "benchmarks" / "bench_filt.py"
        ).read_text(encoding="utf-8")
        assert "perf_counter" not in bench
        assert "@pytest.fixture" in bench

    def test_add_keeps_default_on_legacy_project(self, tmp_path):
        dest = tmp_path / "proj"
        new_run("proj", dest)
        init_run(dest, "filt", [("gain", "float", "1.0f")])
        add_run(dest, "filt", [("order", "int", "4")], force=True)
        test = (dest / "src" / "proj" / "tests" / "test_filt.py").read_text(
            encoding="utf-8"
        )
        assert "import unittest" in test


# ── TOML round-trip via jm script ─────────────────────────────────────────────


def _run_script_and_replay(source_dir, replay_base):
    r = _cli("script", cwd=source_dir)
    assert r.returncode == 0, f"jm script failed:\n{r.stderr}"
    script = r.stdout
    cd_match = re.search(r"^cd (\S+)$", script, re.MULTILINE)
    assert cd_match
    project_name = cd_match.group(1)
    joined = re.sub(r"\\\n\s*", " ", script)
    commands: list[list[str]] = []
    for line in joined.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("cd "):
            continue
        if line.startswith("just-makeit "):
            commands.append(shlex.split(line[len("just-makeit ") :]))
    replay_root = replay_base / project_name
    replay_root.mkdir(parents=True, exist_ok=True)
    assert commands[0][0] == "new"
    r2 = _cli(*commands[0], str(replay_root), cwd=replay_base)
    assert r2.returncode == 0, f"Replay 'new' failed:\n{r2.stderr}"
    for cmd in commands[1:]:
        r2 = _cli(*cmd, cwd=replay_root)
        assert r2.returncode == 0, (
            f"Replay failed: just-makeit {' '.join(cmd)}\n{r2.stderr}"
        )
    orig = (source_dir / "just-makeit.toml").read_text(encoding="utf-8")
    replay = (replay_root / "just-makeit.toml").read_text(encoding="utf-8")
    return orig, replay


class TestScriptRoundTrip:
    def test_pytest_flag_in_script_output(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--pytest")
        r = _cli("script", cwd=dest)
        assert "--pytest" in r.stdout

    def test_pytest_benchmark_flag_in_script_output(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--pytest-benchmark")
        r = _cli("script", cwd=dest)
        assert "--pytest-benchmark" in r.stdout

    def test_pytest_round_trip(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--pytest")
        _cli("object", "gain", cwd=dest)
        orig, replay = _run_script_and_replay(dest, tmp_path / "replay")
        assert orig == replay

    def test_pytest_benchmark_round_trip(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--pytest-benchmark")
        _cli("object", "gain", cwd=dest)
        orig, replay = _run_script_and_replay(dest, tmp_path / "replay")
        assert orig == replay

    def test_both_flags_round_trip(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--pytest", "--pytest-benchmark")
        _cli("object", "gain", cwd=dest)
        orig, replay = _run_script_and_replay(dest, tmp_path / "replay")
        assert orig == replay

    def test_no_flags_no_pytest_in_script(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest))
        r = _cli("script", cwd=dest)
        assert "--pytest" not in r.stdout


# ── CLI flag parsing ───────────────────────────────────────────────────────────


class TestCLIParsing:
    def test_new_accepts_pytest(self, tmp_path):
        dest = tmp_path / "proj"
        r = _cli("new", "proj", str(dest), "--pytest")
        assert r.returncode == 0

    def test_new_accepts_pytest_benchmark(self, tmp_path):
        dest = tmp_path / "proj"
        r = _cli("new", "proj", str(dest), "--pytest-benchmark")
        assert r.returncode == 0

    def test_new_accepts_both(self, tmp_path):
        dest = tmp_path / "proj"
        r = _cli("new", "proj", str(dest), "--pytest", "--pytest-benchmark")
        assert r.returncode == 0

    def test_toml_stores_pytest(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--pytest")
        toml = (dest / "just-makeit.toml").read_text(encoding="utf-8")
        assert 'pytest = "true"' in toml

    def test_toml_stores_pytest_benchmark(self, tmp_path):
        dest = tmp_path / "proj"
        _cli("new", "proj", str(dest), "--pytest-benchmark")
        toml = (dest / "just-makeit.toml").read_text(encoding="utf-8")
        assert 'pytest_benchmark = "true"' in toml
