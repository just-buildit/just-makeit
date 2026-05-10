"""Integration tests for `just-makeit add`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._add import run as add_run
from just_makeit._config import load, state_vars


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "comp"
    new_run("comp", dest, "comp", [("gain", "double", "1.0")])
    return dest


class TestAddStateVar:
    def test_add_single_var_header(self, project):
        add_run(project, None, [("order", "int", "4")])
        core = (project / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "double gain;" in core
        assert "int order;" in core

    def test_add_single_var_core_c(self, project):
        add_run(project, None, [("order", "int", "4")])
        c = (project / "native" / "src" / "comp" / "comp_core.c").read_text()
        assert "comp_get_gain" in c
        assert "comp_get_order" in c

    def test_add_single_var_ext_c(self, project):
        add_run(project, None, [("order", "int", "4")])
        ext = (project / "native" / "src" / "comp" / "comp_ext.c").read_text()
        assert '"gain"' in ext
        assert '"order"' in ext

    def test_add_single_var_pyi(self, project):
        add_run(project, None, [("order", "int", "4")])
        pyi = (project / "src" / "comp" / "comp.pyi").read_text()
        assert "gain: np.float64 = 1.0" in pyi
        assert "order: np.int32 = 4" in pyi

    def test_add_single_var_pytest(self, project):
        add_run(project, None, [("order", "int", "4")])
        test = (project / "src" / "comp" / "tests" / "test_comp.py").read_text()
        assert "get_order" in test

    def test_add_single_var_ctest(self, project):
        add_run(project, None, [("order", "int", "4")])
        ct = (project / "native" / "tests" / "test_comp_core.c").read_text()
        assert "comp_get_order" in ct

    def test_add_multiple_vars_at_once(self, project):
        add_run(
            project, None, [("bandwidth", "double", "200.0"), ("poles", "int", "2")]
        )
        core = (project / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert "double bandwidth;" in core
        assert "int poles;" in core

    def test_no_unreplaced_placeholders(self, project):
        add_run(project, None, [("order", "int", "4")])
        for path in project.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".c", ".h", ".toml", ".txt"):
                text = path.read_text(encoding="utf-8")
                assert "<<" not in text, f"Unreplaced placeholder in {path}"


class TestAddUpdatesConfig:
    def test_config_has_new_var(self, project):
        add_run(project, None, [("order", "int", "4")])
        cfg = load(project)
        names = [n for n, _, __ in state_vars(cfg, "comp")]
        assert "gain" in names
        assert "order" in names

    def test_config_preserves_original_var(self, project):
        add_run(project, None, [("order", "int", "4")])
        cfg = load(project)
        vars_ = {n: (t, d) for n, t, d in state_vars(cfg, "comp")}
        assert vars_["gain"] == ("double", "1.0")

    def test_config_state_count(self, project):
        add_run(project, None, [("a", "double", "0.0"), ("b", "int", "0")])
        cfg = load(project)
        assert len(state_vars(cfg, "comp")) == 3  # gain + a + b


class TestAddValidation:
    def test_no_config_exits_1(self, tmp_path):
        with pytest.raises(SystemExit):
            add_run(tmp_path, None, [("x", "double", "0.0")])

    def test_duplicate_name_exits_1(self, project):
        with pytest.raises(SystemExit):
            add_run(project, None, [("gain", "double", "2.0")])

    def test_explicit_component_works(self, project):
        add_run(project, "comp", [("order", "int", "4")])
        cfg = load(project)
        names = [n for n, _, __ in state_vars(cfg, "comp")]
        assert "order" in names

    def test_wrong_component_exits(self, project):
        with pytest.raises(SystemExit):
            add_run(project, "nonexistent", [("x", "double", "0.0")])


class TestAddBackupRestore:
    def test_backup_restores_on_write_failure(self, project):
        original_h = (project / "native" / "inc" / "comp" / "comp_core.h").read_text()

        # Make the .pyi path a directory so write_text fails there
        pyi = project / "src" / "comp" / "comp.pyi"
        pyi.unlink()
        pyi.mkdir()

        with pytest.raises((IsADirectoryError, OSError)):
            add_run(project, None, [("order", "int", "4")])

        restored = (project / "native" / "inc" / "comp" / "comp_core.h").read_text()
        assert restored == original_h

    def test_config_not_written_on_failure(self, project):
        cfg_before = load(project)
        before_count = len(state_vars(cfg_before, "comp"))

        pyi = project / "src" / "comp" / "comp.pyi"
        pyi.unlink()
        pyi.mkdir()

        with pytest.raises((IsADirectoryError, OSError)):
            add_run(project, None, [("order", "int", "4")])

        cfg_after = load(project)
        assert len(state_vars(cfg_after, "comp")) == before_count
