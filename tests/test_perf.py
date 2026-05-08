"""Tests for the --perf scaffold and `just-makeit perf` upgrade command."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._init import run as init_run
from just_makeit._perf import run as perf_run
from just_makeit._config import load, is_perf


@pytest.fixture()
def perf_project(tmp_path):
    dest = tmp_path / "myproj"
    new_run("myproj", dest, "mycomp", perf=True)
    return dest


@pytest.fixture()
def plain_project(tmp_path):
    dest = tmp_path / "myproj"
    new_run("myproj", dest, "mycomp")
    return dest


# ── File presence ─────────────────────────────────────────────────────────────

class TestPerfFilePresence:
    def test_jm_perf_h_generated(self, perf_project):
        assert (perf_project / "native" / "inc" / "jm_perf.h").exists()

    def test_jm_perf_h_absent_without_flag(self, plain_project):
        assert not (plain_project / "native" / "inc" / "jm_perf.h").exists()


# ── Config ────────────────────────────────────────────────────────────────────

class TestPerfConfig:
    def test_perf_recorded_in_toml(self, perf_project):
        assert is_perf(load(perf_project))

    def test_perf_absent_from_plain_toml(self, plain_project):
        assert not is_perf(load(plain_project))


# ── Generated C content ───────────────────────────────────────────────────────

class TestPerfCoreHeader:
    def test_includes_jm_perf_h(self, perf_project):
        h = (perf_project / "native" / "inc" / "mycomp" / "mycomp_core.h").read_text()
        assert '#include "jm_perf.h"' in h

    def test_step_uses_jm_qualifiers(self, perf_project):
        h = (perf_project / "native" / "inc" / "mycomp" / "mycomp_core.h").read_text()
        assert "JM_FORCEINLINE JM_HOT" in h

    def test_plain_uses_static_inline(self, plain_project):
        h = (plain_project / "native" / "inc" / "mycomp" / "mycomp_core.h").read_text()
        assert "static inline" in h
        assert "JM_FORCEINLINE" not in h

    def test_plain_does_not_include_jm_perf_h(self, plain_project):
        h = (plain_project / "native" / "inc" / "mycomp" / "mycomp_core.h").read_text()
        assert "jm_perf.h" not in h


class TestPerfCoreC:
    def test_omp_simd_hint_present(self, perf_project):
        c = (perf_project / "native" / "src" / "mycomp" / "mycomp_core.c").read_text()
        assert "/* #pragma omp simd */" in c

    def test_omp_simd_hint_absent_without_flag(self, plain_project):
        c = (plain_project / "native" / "src" / "mycomp" / "mycomp_core.c").read_text()
        assert "/* #pragma omp simd */" not in c


# ── jm_perf.h content ─────────────────────────────────────────────────────────

class TestJmPerfHContent:
    def test_has_all_public_macros(self, perf_project):
        h = (perf_project / "native" / "inc" / "jm_perf.h").read_text()
        for macro in ("JM_LIKELY", "JM_UNLIKELY", "JM_RESTRICT", "JM_FORCEINLINE",
                      "JM_ALIGNED", "JM_HOT"):
            assert macro in h, f"{macro} missing from jm_perf.h"

    def test_has_three_compiler_paths(self, perf_project):
        h = (perf_project / "native" / "inc" / "jm_perf.h").read_text()
        assert "__GNUC__" in h
        assert "_MSC_VER" in h
        assert "#else" in h

    def test_gnuc_uses_builtin_expect(self, perf_project):
        h = (perf_project / "native" / "inc" / "jm_perf.h").read_text()
        assert "__builtin_expect" in h

    def test_no_unreplaced_placeholders(self, perf_project):
        h = (perf_project / "native" / "inc" / "jm_perf.h").read_text()
        assert "<<" not in h


# ── CMake SIMD option (always present) ───────────────────────────────────────

class TestSimdCmakeOption:
    def test_simd_option_in_plain_cmake(self, plain_project):
        cmake = (plain_project / "CMakeLists.txt").read_text()
        assert "ENABLE_SIMD" in cmake

    def test_simd_option_in_perf_cmake(self, perf_project):
        cmake = (perf_project / "CMakeLists.txt").read_text()
        assert "ENABLE_SIMD" in cmake

    def test_simd_off_by_default(self, plain_project):
        cmake = (plain_project / "CMakeLists.txt").read_text()
        assert 'ENABLE_SIMD "' in cmake and "OFF" in cmake


# ── Inheritance: init on a perf project picks up perf automatically ───────────

class TestPerfInheritedByInit:
    def test_second_component_gets_jm_qualifiers(self, perf_project):
        init_run(perf_project, "engine", [("rate", "double", "1.0")])
        h = (perf_project / "native" / "inc" / "engine" / "engine_core.h").read_text()
        assert "JM_FORCEINLINE JM_HOT" in h

    def test_second_component_includes_jm_perf_h(self, perf_project):
        init_run(perf_project, "engine", [("rate", "double", "1.0")])
        h = (perf_project / "native" / "inc" / "engine" / "engine_core.h").read_text()
        assert '#include "jm_perf.h"' in h

    def test_jm_perf_h_not_duplicated(self, perf_project):
        init_run(perf_project, "engine", [("rate", "double", "1.0")])
        perf_h_files = list(perf_project.rglob("jm_perf.h"))
        assert len(perf_h_files) == 1


# ── Enabling perf via init on a plain project ─────────────────────────────────

class TestPerfEnabledViaInit:
    def test_init_perf_writes_jm_perf_h(self, plain_project):
        init_run(plain_project, "engine", perf=True)
        assert (plain_project / "native" / "inc" / "jm_perf.h").exists()

    def test_init_perf_updates_config(self, plain_project):
        init_run(plain_project, "engine", perf=True)
        assert is_perf(load(plain_project))

    def test_init_perf_component_gets_qualifiers(self, plain_project):
        init_run(plain_project, "engine", perf=True)
        h = (plain_project / "native" / "inc" / "engine" / "engine_core.h").read_text()
        assert "JM_FORCEINLINE JM_HOT" in h


# ── `just-makeit perf` upgrade command ───────────────────────────────────────

class TestPerfUpgrade:
    @pytest.fixture()
    def upgraded(self, tmp_path):
        dest = tmp_path / "myproj"
        new_run("myproj", dest, "mycomp")
        perf_run(dest)
        return dest

    def test_writes_jm_perf_h(self, upgraded):
        assert (upgraded / "native" / "inc" / "jm_perf.h").exists()

    def test_updates_toml(self, upgraded):
        assert is_perf(load(upgraded))

    def test_header_includes_jm_perf_h(self, upgraded):
        h = (upgraded / "native" / "inc" / "mycomp" / "mycomp_core.h").read_text()
        assert '#include "jm_perf.h"' in h

    def test_step_qualifier_upgraded(self, upgraded):
        h = (upgraded / "native" / "inc" / "mycomp" / "mycomp_core.h").read_text()
        assert "JM_FORCEINLINE JM_HOT" in h
        assert "static inline" not in h

    def test_step_body_preserved(self, upgraded):
        """User implementation survives the upgrade."""
        header = upgraded / "native" / "inc" / "mycomp" / "mycomp_core.h"
        # Write a custom implementation, then upgrade a second component
        text = header.read_text()
        text = text.replace("(void)state; /* TODO: implement DSP using state variables */\n    return x;",
                            "return x * 2.0f;")
        header.write_text(text)
        # Re-run perf (idempotent) — body must survive
        perf_run(upgraded)
        assert "return x * 2.0f;" in header.read_text()

    def test_idempotent(self, upgraded):
        """Running perf_run twice produces the same result."""
        h_before = (upgraded / "native" / "inc" / "mycomp" / "mycomp_core.h").read_text()
        perf_run(upgraded)
        h_after = (upgraded / "native" / "inc" / "mycomp" / "mycomp_core.h").read_text()
        assert h_before == h_after

    def test_multi_component(self, tmp_path):
        dest = tmp_path / "multi"
        new_run("multi", dest, "alpha")
        init_run(dest, "beta")
        perf_run(dest)
        for comp in ("alpha", "beta"):
            h = (dest / "native" / "inc" / comp / f"{comp}_core.h").read_text()
            assert "JM_FORCEINLINE JM_HOT" in h

    def test_already_perf_is_noop(self, tmp_path, capsys):
        dest = tmp_path / "already"
        new_run("already", dest, "mycomp", perf=True)
        perf_run(dest)
        out = capsys.readouterr().out
        assert "already enabled" in out
