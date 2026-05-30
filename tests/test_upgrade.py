"""Tests for _upgrade.py schema migration and _config.py schema helpers."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit import _upgrade as U


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_toml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _minimal_toml(name: str = "myproj", schema: int | None = None) -> str:
    schema_line = f'schema = "{schema}"\n' if schema is not None else ""
    return (
        "[project]\n"
        f'name = "{name}"\n'
        'version = "0.1.0"\n'
        'build = "cmake"\n'
        'perf = "false"\n'
        'pytest = "false"\n'
        'pytest_benchmark = "false"\n'
        f"{schema_line}"
    )


# ── _config schema helpers ────────────────────────────────────────────────────


class TestSchemaVersion:
    def test_defaults_to_1_when_absent(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml())
        cfg = C.load(tmp_path)
        assert C.schema_version(cfg) == 1

    def test_reads_explicit_schema(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml(schema=2))
        cfg = C.load(tmp_path)
        assert C.schema_version(cfg) == 2

    def test_returns_int(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml(schema=2))
        cfg = C.load(tmp_path)
        assert isinstance(C.schema_version(cfg), int)


class TestSetSchemaVersion:
    def test_sets_version_in_cfg(self):
        cfg = {"project": {"name": "x", "version": "0.1.0"}}
        C.set_schema_version(cfg, 3)
        assert cfg["project"]["schema"] == "3"

    def test_returns_same_cfg(self):
        cfg = {"project": {}}
        result = C.set_schema_version(cfg, 2)
        assert result is cfg

    def test_creates_project_section_if_missing(self):
        cfg = {}
        C.set_schema_version(cfg, 2)
        assert cfg["project"]["schema"] == "2"


class TestFromNew:
    def test_from_new_includes_current_schema(self):
        cfg = C.from_new("my_proj")
        assert "schema" in cfg["project"]
        assert int(cfg["project"]["schema"]) == C.CURRENT_SCHEMA


class TestCurrentSchemaConstant:
    def test_current_schema_is_int(self):
        assert isinstance(C.CURRENT_SCHEMA, int)

    def test_current_schema_matches_highest_migration(self):
        # Every schema N in MIGRATIONS must be < CURRENT_SCHEMA.
        for n in U.MIGRATIONS:
            assert n < C.CURRENT_SCHEMA


# ── migration step types ──────────────────────────────────────────────────────


class TestAddFileStep:
    def test_creates_file_when_absent(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("mypkg", schema=1))
        cfg = C.load(tmp_path)
        ctx = U._build_ctx(cfg)
        step = U.AddFile("zensical.toml", "ZENSICAL_TOML")
        U._apply_step(tmp_path, step, ctx)
        assert (tmp_path / "zensical.toml").exists()

    def test_skips_existing_file(self, tmp_path):
        sentinel = "# user edited\n"
        (tmp_path / "zensical.toml").write_text(sentinel, encoding="utf-8")
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("mypkg", schema=1))
        cfg = C.load(tmp_path)
        ctx = U._build_ctx(cfg)
        step = U.AddFile("zensical.toml", "ZENSICAL_TOML")
        U._apply_step(tmp_path, step, ctx)
        # Original content must be preserved.
        assert (tmp_path / "zensical.toml").read_text(
            encoding="utf-8"
        ) == sentinel

    def test_creates_parent_directories(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("mypkg", schema=1))
        cfg = C.load(tmp_path)
        ctx = U._build_ctx(cfg)
        step = U.AddFile("docs/index.md", "DOCS_INDEX_MD")
        U._apply_step(tmp_path, step, ctx)
        assert (tmp_path / "docs" / "index.md").exists()

    def test_rendered_content_substitutes_package(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("coolpkg", schema=1))
        cfg = C.load(tmp_path)
        ctx = U._build_ctx(cfg)
        step = U.AddFile("docs/api.md", "DOCS_API_MD")
        U._apply_step(tmp_path, step, ctx)
        content = (tmp_path / "docs" / "api.md").read_text(encoding="utf-8")
        assert "coolpkg" in content
        assert "<<package>>" not in content


class TestAddTomlKeyStep:
    def test_adds_missing_key(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("p", schema=1))
        step = U.AddTomlKey("project", "docs", "false")
        cfg_before = C.load(tmp_path)
        assert "docs" not in cfg_before.get("project", {})
        U._apply_step(tmp_path, step, {})
        cfg_after = C.load(tmp_path)
        assert cfg_after["project"]["docs"] == "false"

    def test_preserves_existing_key(self, tmp_path):
        toml = _minimal_toml("p", schema=1) + 'docs = "true"\n'
        _write_toml(tmp_path / C.FILENAME, toml)
        step = U.AddTomlKey("project", "docs", "false")
        U._apply_step(tmp_path, step, {})
        cfg = C.load(tmp_path)
        assert cfg["project"]["docs"] == "true"

    def test_step_is_idempotent(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("p", schema=1))
        step = U.AddTomlKey("project", "docs", "false")
        U._apply_step(tmp_path, step, {})
        U._apply_step(tmp_path, step, {})
        cfg = C.load(tmp_path)
        assert cfg["project"]["docs"] == "false"


# ── full migration run ────────────────────────────────────────────────────────


class TestUpgradeRun:
    def test_upgrades_schema_1_to_current(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("mypkg", schema=1))
        U.run(tmp_path)
        cfg = C.load(tmp_path)
        assert C.schema_version(cfg) == C.CURRENT_SCHEMA

    def test_creates_docs_files_on_upgrade(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("mypkg", schema=1))
        U.run(tmp_path)
        assert (tmp_path / "zensical.toml").exists()
        assert (tmp_path / "docs" / "index.md").exists()
        assert (tmp_path / "docs" / "api.md").exists()

    def test_creates_jm_bench_h_on_upgrade(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("mypkg", schema=3))
        U.run(tmp_path)
        assert (tmp_path / "native" / "benchmarks" / "jm_bench.h").exists()

    def test_jm_bench_h_skipped_if_exists(self, tmp_path):
        sentinel = "/* user-edited */\n"
        jm_h = tmp_path / "native" / "benchmarks" / "jm_bench.h"
        jm_h.parent.mkdir(parents=True)
        jm_h.write_text(sentinel, encoding="utf-8")
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("mypkg", schema=3))
        U.run(tmp_path)
        assert jm_h.read_text(encoding="utf-8") == sentinel

    def test_already_current_is_noop(self, tmp_path, capsys):
        _write_toml(
            tmp_path / C.FILENAME,
            _minimal_toml("mypkg", schema=C.CURRENT_SCHEMA),
        )
        U.run(tmp_path)
        out = capsys.readouterr().out
        assert "already up to date" in out
        # No new files should be created.
        assert not (tmp_path / "docs").exists()

    def test_idempotent_second_run(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("mypkg", schema=1))
        U.run(tmp_path)
        # Write sentinel into generated file to detect overwrites.
        sentinel = "# user edited\n"
        (tmp_path / "zensical.toml").write_text(sentinel, encoding="utf-8")
        U.run(tmp_path)
        assert (tmp_path / "zensical.toml").read_text(
            encoding="utf-8"
        ) == sentinel

    def test_exits_without_toml(self, tmp_path):
        with pytest.raises(SystemExit):
            U.run(tmp_path)

    def test_schema_bumped_in_toml_after_upgrade(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("mypkg", schema=1))
        U.run(tmp_path)
        # Reload from disk to verify persistence.
        cfg = C.load(tmp_path)
        assert C.schema_version(cfg) == C.CURRENT_SCHEMA

    def test_docs_content_has_project_name(self, tmp_path):
        _write_toml(
            tmp_path / C.FILENAME, _minimal_toml("awesomelib", schema=1)
        )
        U.run(tmp_path)
        content = (tmp_path / "docs" / "index.md").read_text(encoding="utf-8")
        assert "awesomelib" in content

    def test_no_unresolved_placeholders_in_generated_files(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("mypkg", schema=1))
        U.run(tmp_path)
        for path in [
            tmp_path / "zensical.toml",
            tmp_path / "docs" / "index.md",
            tmp_path / "docs" / "api.md",
        ]:
            content = path.read_text(encoding="utf-8")
            assert "<<" not in content, (
                f"unresolved placeholder in {path.name}"
            )


# ── RegenBench step ──────────────────────────────────────────────────────────


class TestRegenBenchStep:
    def _toml_with_method(self, name: str = "myproj") -> str:
        return (
            "[project]\n"
            f'name = "{name}"\n'
            'version = "0.1.0"\n'
            'build = "cmake"\n'
            'perf = "false"\n'
            'pytest = "false"\n'
            'pytest_benchmark = "false"\n'
            f'schema = "{C.CURRENT_SCHEMA}"\n'
            "\n"
            "[engine]\n"
            'arg_type = "float _Complex"\n'
            'return_type = "float _Complex"\n'
            'mutable = "false"\n'
            'no_state = "false"\n'
            'no_step = "false"\n'
            "\n"
            "[[engine.state]]\n"
            'name = "gain"\n'
            'type = "double"\n'
            'default = "1.0"\n'
            "\n"
            "[[engine.methods]]\n"
            'name = "configure"\n'
            'arg_type = "double"\n'
            'return_type = "void"\n'
        )

    def test_regenerates_bench_file_with_method_block(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, self._toml_with_method())
        bench = tmp_path / "native" / "benchmarks" / "bench_engine_core.c"
        bench.parent.mkdir(parents=True)
        bench.write_text(
            "/* old bench, no method blocks */\n", encoding="utf-8"
        )

        cfg = C.load(tmp_path)
        ctx = U._build_ctx(cfg)
        U._apply_step(tmp_path, U.RegenBench(), ctx)

        content = bench.read_text(encoding="utf-8")
        assert "configure" in content
        assert "bench: configure()" in content
        assert "<<" not in content

    def test_skips_missing_bench_file(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, self._toml_with_method())
        # No bench file created — step should be a silent no-op.
        cfg = C.load(tmp_path)
        ctx = U._build_ctx(cfg)
        U._apply_step(tmp_path, U.RegenBench(), ctx)  # must not raise

    def test_no_bench_method_excluded(self, tmp_path):
        toml = self._toml_with_method().replace(
            '[[engine.methods]]\nname = "configure"\narg_type = "double"\nreturn_type = "void"\n',
            '[[engine.methods]]\nname = "configure"\narg_type = "double"\nreturn_type = "void"\nbench = false\n',
        )
        _write_toml(tmp_path / C.FILENAME, toml)
        bench = tmp_path / "native" / "benchmarks" / "bench_engine_core.c"
        bench.parent.mkdir(parents=True)
        bench.write_text("/* old */\n", encoding="utf-8")

        cfg = C.load(tmp_path)
        ctx = U._build_ctx(cfg)
        U._apply_step(tmp_path, U.RegenBench(), ctx)

        content = bench.read_text(encoding="utf-8")
        assert "bench: configure()" not in content


# ── _build_ctx ────────────────────────────────────────────────────────────────


class TestBuildCtx:
    def test_package_key(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("my_lib", schema=1))
        cfg = C.load(tmp_path)
        ctx = U._build_ctx(cfg)
        assert ctx["package"] == "my_lib"

    def test_project_key_uses_hyphens(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("my_lib", schema=1))
        cfg = C.load(tmp_path)
        ctx = U._build_ctx(cfg)
        assert ctx["project"] == "my-lib"

    def test_version_key(self, tmp_path):
        _write_toml(tmp_path / C.FILENAME, _minimal_toml("p", schema=1))
        cfg = C.load(tmp_path)
        ctx = U._build_ctx(cfg)
        assert ctx["version"] == "0.1.0"
