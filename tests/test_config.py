"""Unit tests for _config.py (just-makeit.toml read/write)."""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._config import (
    FILENAME,
    _dump,
    from_init,
    load,
    save,
    state_vars,
)


class TestLoad:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load(tmp_path) == {}

    def test_load_component_name(self, tmp_path):
        (tmp_path / FILENAME).write_text(
            '[component]\nname = "gain"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        cfg = load(tmp_path)
        assert cfg["component"]["name"] == "gain"

    def test_load_state_vars(self, tmp_path):
        (tmp_path / FILENAME).write_text(
            '[component]\nname = "g"\nversion = "0.1.0"\n\n'
            '[[state]]\nname = "gain"\ntype = "double"\ndefault = "1.0"\n',
            encoding="utf-8",
        )
        cfg = load(tmp_path)
        assert cfg["state"][0]["name"] == "gain"
        assert cfg["state"][0]["type"] == "double"
        assert cfg["state"][0]["default"] == "1.0"


class TestSave:
    def test_save_creates_file(self, tmp_path):
        cfg = from_init("gain", "0.1.0", [("gain", "double", "0.0")])
        save(tmp_path, cfg)
        assert (tmp_path / FILENAME).exists()

    def test_round_trip(self, tmp_path):
        cfg = from_init(
            "my_f", "0.2.0", [("gain", "double", "1.5"), ("order", "int", "4")]
        )
        save(tmp_path, cfg)
        loaded = load(tmp_path)
        assert loaded["component"]["name"] == "my_f"
        assert loaded["component"]["version"] == "0.2.0"
        assert len(loaded["state"]) == 2
        assert loaded["state"][0]["name"] == "gain"
        assert loaded["state"][1]["name"] == "order"

    def test_empty_state_no_state_section(self, tmp_path):
        cfg = from_init("gain", "0.1.0", [])
        save(tmp_path, cfg)
        text = (tmp_path / FILENAME).read_text()
        assert "[[state]]" not in text

    def test_multiple_state_sections(self, tmp_path):
        cfg = from_init("g", "0.1.0", [("a", "double", "0.0"), ("b", "int", "0")])
        save(tmp_path, cfg)
        text = (tmp_path / FILENAME).read_text()
        assert text.count("[[state]]") == 2


class TestStateVars:
    def test_empty_returns_empty(self):
        assert state_vars({}) == []

    def test_single_var(self):
        cfg = {"state": [{"name": "gain", "type": "double", "default": "1.0"}]}
        assert state_vars(cfg) == [("gain", "double", "1.0")]

    def test_multi_vars(self):
        cfg = {
            "state": [
                {"name": "a", "type": "double", "default": "0.0"},
                {"name": "b", "type": "int", "default": "4"},
            ]
        }
        result = state_vars(cfg)
        assert result == [("a", "double", "0.0"), ("b", "int", "4")]


class TestFromInit:
    def test_component_fields(self):
        cfg = from_init("gain", "0.3.0", [])
        assert cfg["component"]["name"] == "gain"
        assert cfg["component"]["version"] == "0.3.0"

    def test_state_list(self):
        cfg = from_init("g", "0.1.0", [("x", "float", "0.5f")])
        assert cfg["state"] == [{"name": "x", "type": "float", "default": "0.5f"}]


class TestDump:
    def test_component_section(self):
        text = _dump({"component": {"name": "g", "version": "0.1.0"}})
        assert "[component]" in text
        assert 'name = "g"' in text
        assert 'version = "0.1.0"' in text

    def test_state_section(self):
        text = _dump(
            {
                "component": {"name": "g", "version": "0.1.0"},
                "state": [{"name": "gain", "type": "double", "default": "1.0"}],
            }
        )
        assert "[[state]]" in text
        assert 'name = "gain"' in text
        assert 'type = "double"' in text
        assert 'default = "1.0"' in text
