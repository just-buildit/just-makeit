"""Unit tests for _config.py (just-makeit.toml read/write)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._config import (
    FILENAME,
    _dump,
    add_component,
    components,
    from_new,
    load,
    project_name,
    project_version,
    save,
    state_vars,
)


class TestLoad:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load(tmp_path) == {}

    def test_load_project_name(self, tmp_path):
        (tmp_path / FILENAME).write_text(
            '[project]\nname = "my_proj"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        cfg = load(tmp_path)
        assert cfg["project"]["name"] == "my_proj"

    def test_load_component_state(self, tmp_path):
        (tmp_path / FILENAME).write_text(
            '[project]\nname = "p"\nversion = "0.1.0"\n\n'
            '[[gain.state]]\nname = "gain"\ntype = "double"\ndefault = "1.0"\n',
            encoding="utf-8",
        )
        cfg = load(tmp_path)
        assert cfg["gain"]["state"][0]["name"] == "gain"
        assert cfg["gain"]["state"][0]["type"] == "double"
        assert cfg["gain"]["state"][0]["default"] == "1.0"


class TestSave:
    def test_save_creates_file(self, tmp_path):
        cfg = from_new("gain")
        save(tmp_path, cfg)
        assert (tmp_path / FILENAME).exists()

    def test_round_trip_project(self, tmp_path):
        cfg = from_new("my_proj", "0.2.0")
        save(tmp_path, cfg)
        loaded = load(tmp_path)
        assert loaded["project"]["name"] == "my_proj"
        assert loaded["project"]["version"] == "0.2.0"

    def test_round_trip_with_component(self, tmp_path):
        cfg = from_new("p")
        add_component(cfg, "engine", [("rate", "double", "1.0"), ("order", "int", "4")])
        save(tmp_path, cfg)
        loaded = load(tmp_path)
        assert loaded["engine"]["state"][0]["name"] == "rate"
        assert loaded["engine"]["state"][1]["name"] == "order"

    def test_no_component_no_state_section(self, tmp_path):
        cfg = from_new("p")
        save(tmp_path, cfg)
        text = (tmp_path / FILENAME).read_text()
        assert "[[" not in text

    def test_multiple_state_entries(self, tmp_path):
        cfg = from_new("p")
        add_component(cfg, "g", [("a", "double", "0.0"), ("b", "int", "0")])
        save(tmp_path, cfg)
        text = (tmp_path / FILENAME).read_text()
        assert text.count("[[g.state]]") == 2


class TestComponents:
    def test_empty_config(self):
        assert components({}) == []

    def test_project_only(self):
        assert components({"project": {"name": "p", "version": "0.1.0"}}) == []

    def test_single_component(self):
        cfg = {"project": {}, "engine": {"state": []}}
        assert components(cfg) == ["engine"]

    def test_multiple_components(self):
        cfg = {"project": {}, "engine": {"state": []}, "parser": {"state": []}}
        result = components(cfg)
        assert set(result) == {"engine", "parser"}


class TestStateVars:
    def test_empty_returns_empty(self):
        assert state_vars({}, "engine") == []

    def test_missing_component_returns_empty(self):
        cfg = {"project": {}, "engine": {"state": []}}
        assert state_vars(cfg, "parser") == []

    def test_single_var(self):
        cfg = {
            "engine": {"state": [{"name": "gain", "type": "double", "default": "1.0"}]}
        }
        assert state_vars(cfg, "engine") == [("gain", "double", "1.0")]

    def test_multi_vars(self):
        cfg = {
            "engine": {
                "state": [
                    {"name": "a", "type": "double", "default": "0.0"},
                    {"name": "b", "type": "int", "default": "4"},
                ]
            }
        }
        assert state_vars(cfg, "engine") == [("a", "double", "0.0"), ("b", "int", "4")]


class TestProjectHelpers:
    def test_project_name(self):
        assert project_name({"project": {"name": "my_proj"}}) == "my_proj"

    def test_project_name_missing(self):
        assert project_name({}) == ""

    def test_project_version(self):
        assert project_version({"project": {"version": "0.3.0"}}) == "0.3.0"

    def test_project_version_default(self):
        assert project_version({}) == "0.1.0"


class TestFromNew:
    def test_has_project_section(self):
        cfg = from_new("my_proj")
        assert cfg["project"]["name"] == "my_proj"

    def test_default_version(self):
        cfg = from_new("p")
        assert cfg["project"]["version"] == "0.1.0"

    def test_custom_version(self):
        cfg = from_new("p", "0.3.0")
        assert cfg["project"]["version"] == "0.3.0"

    def test_no_components(self):
        cfg = from_new("p")
        assert components(cfg) == []


class TestAddComponent:
    def test_adds_component(self):
        cfg = from_new("p")
        add_component(cfg, "engine", [("rate", "double", "1.0")])
        assert "engine" in cfg
        assert cfg["engine"]["state"][0]["name"] == "rate"

    def test_multiple_components(self):
        cfg = from_new("p")
        add_component(cfg, "engine", [("rate", "double", "1.0")])
        add_component(cfg, "parser", [("depth", "int", "8")])
        assert set(components(cfg)) == {"engine", "parser"}


class TestDump:
    def test_project_section(self):
        text = _dump({"project": {"name": "p", "version": "0.1.0"}})
        assert "[project]" in text
        assert 'name = "p"' in text
        assert 'version = "0.1.0"' in text

    def test_component_state_section(self):
        cfg = from_new("p")
        add_component(cfg, "engine", [("rate", "double", "1.0")])
        text = _dump(cfg)
        assert "[[engine.state]]" in text
        assert 'name = "rate"' in text
        assert 'type = "double"' in text
        assert 'default = "1.0"' in text
