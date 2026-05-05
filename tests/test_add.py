"""Integration tests for `just-makeit add`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._init import run as init_run
from just_makeit._add import run as add_run
from just_makeit._config import load


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "comp"
    init_run("comp", dest, [("gain", "double", "1.0")])
    return dest


class TestAddStateVar:
    def test_add_single_var_header(self, project):
        add_run(project, [("order", "int", "4")])
        h = (project / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "double gain;" in h
        assert "int order;" in h

    def test_add_single_var_core_c(self, project):
        add_run(project, [("order", "int", "4")])
        c = (project / "native" / "src" / "comp" / "comp_core.c").read_text()
        assert "comp_get_gain" in c
        assert "comp_get_order" in c

    def test_add_single_var_ext_c(self, project):
        add_run(project, [("order", "int", "4")])
        ext = (project / "native" / "src" / "comp" / "comp_ext.c").read_text()
        assert '"gain"' in ext
        assert '"order"' in ext

    def test_add_single_var_pyi(self, project):
        add_run(project, [("order", "int", "4")])
        pyi = (project / "src" / "comp" / "comp.pyi").read_text()
        assert "gain: float = 1.0" in pyi
        assert "order: int = 4" in pyi

    def test_add_single_var_pytest(self, project):
        add_run(project, [("order", "int", "4")])
        test = (project / "src" / "comp" / "tests" / "test_comp.py").read_text()
        assert "get_order" in test

    def test_add_single_var_ctest(self, project):
        add_run(project, [("order", "int", "4")])
        ct = (project / "native" / "tests" / "test_comp_core.c").read_text()
        assert "comp_get_order" in ct

    def test_add_multiple_vars_at_once(self, project):
        add_run(project, [("bandwidth", "double", "200.0"), ("poles", "int", "2")])
        h = (project / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "double bandwidth;" in h
        assert "int poles;" in h

    def test_no_unreplaced_placeholders(self, project):
        add_run(project, [("order", "int", "4")])
        for path in project.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml", ".txt"):
                text = path.read_text(encoding="utf-8")
                assert "<<" not in text, f"Unreplaced placeholder in {path}"


class TestAddUpdatesConfig:
    def test_config_has_new_var(self, project):
        add_run(project, [("order", "int", "4")])
        cfg = load(project)
        names = [s["name"] for s in cfg["state"]]
        assert "gain" in names
        assert "order" in names

    def test_config_preserves_original_var(self, project):
        add_run(project, [("order", "int", "4")])
        cfg = load(project)
        gain = next(s for s in cfg["state"] if s["name"] == "gain")
        assert gain["type"] == "double"
        assert gain["default"] == "1.0"

    def test_config_state_count(self, project):
        add_run(project, [("a", "double", "0.0"), ("b", "int", "0")])
        cfg = load(project)
        assert len(cfg["state"]) == 3  # gain + a + b


class TestAddValidation:
    def test_no_config_exits_1(self, tmp_path):
        with pytest.raises(SystemExit):
            add_run(tmp_path, [("x", "double", "0.0")])

    def test_duplicate_name_exits_1(self, project):
        with pytest.raises(SystemExit):
            add_run(project, [("gain", "double", "2.0")])


class TestAddBackupRestore:
    def test_backup_restores_on_write_failure(self, project):
        original_h = (project / "native" / "inc" / "comp" / "comp_core.h").read_text()

        # Make the .pyi path a directory so write_text fails there
        pyi = project / "src" / "comp" / "comp.pyi"
        pyi.unlink()
        pyi.mkdir()

        with pytest.raises((IsADirectoryError, OSError)):
            add_run(project, [("order", "int", "4")])

        # Header should be restored to original
        restored = (project / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert restored == original_h

    def test_config_not_written_on_failure(self, project):
        cfg_before = load(project)

        pyi = project / "src" / "comp" / "comp.pyi"
        pyi.unlink()
        pyi.mkdir()

        with pytest.raises((IsADirectoryError, OSError)):
            add_run(project, [("order", "int", "4")])

        cfg_after = load(project)
        assert len(cfg_after["state"]) == len(cfg_before["state"])
