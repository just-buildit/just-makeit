"""Unit tests for _config.py (just-makeit.toml read/write)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._config import (
    FILENAME,
    _dump,
    add_component,
    add_method,
    add_property,
    components,
    from_new,
    init_params,
    is_mutable,
    is_no_state,
    is_no_step,
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
        text = (tmp_path / FILENAME).read_text(encoding="utf-8")
        assert "[[" not in text

    def test_multiple_state_entries(self, tmp_path):
        cfg = from_new("p")
        add_component(cfg, "g", [("a", "double", "0.0"), ("b", "int", "0")])
        save(tmp_path, cfg)
        text = (tmp_path / FILENAME).read_text(encoding="utf-8")
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


class TestAddComponentFlags:
    def test_no_state_stored_as_string_true(self):
        cfg = from_new("p")
        add_component(cfg, "gen", [], no_state_=True)
        assert cfg["gen"]["no_state"] == "true"

    def test_no_step_stored_as_string_true(self):
        cfg = from_new("p")
        add_component(cfg, "sink", [], no_step_=True)
        assert cfg["sink"]["no_step"] == "true"

    def test_mutable_stored_as_string_true(self):
        cfg = from_new("p")
        add_component(cfg, "nco", [], mutable_=True)
        assert cfg["nco"]["mutable"] == "true"

    def test_is_no_state_reader(self):
        cfg = from_new("p")
        add_component(cfg, "gen", [], no_state_=True)
        assert is_no_state(cfg, "gen") is True
        assert is_no_state(cfg, "missing") is False

    def test_is_no_step_reader(self):
        cfg = from_new("p")
        add_component(cfg, "sink", [], no_step_=True)
        assert is_no_step(cfg, "sink") is True
        assert is_no_step(cfg, "missing") is False

    def test_is_mutable_reader(self):
        cfg = from_new("p")
        add_component(cfg, "nco", [], mutable_=True)
        assert is_mutable(cfg, "nco") is True
        assert is_mutable(cfg, "missing") is False

    def test_arg_type_non_default_stored(self):
        cfg = from_new("p")
        add_component(cfg, "nco", [], arg_type_="void")
        assert cfg["nco"]["arg_type"] == "void"

    def test_arg_type_default_stored(self):
        cfg = from_new("p")
        add_component(cfg, "gain", [], arg_type_="float _Complex")
        assert cfg["gain"]["arg_type"] == "float _Complex"

    def test_return_type_void_stored(self):
        cfg = from_new("p")
        add_component(cfg, "sink", [], return_type_="void")
        assert cfg["sink"]["return_type"] == "void"

    def test_return_type_default_stored(self):
        cfg = from_new("p")
        add_component(cfg, "gain", [], return_type_="float _Complex")
        assert cfg["gain"]["return_type"] == "float _Complex"

    def test_init_params_stored_and_read_back(self):
        cfg = from_new("p")
        add_component(cfg, "gen", [], no_state_=True,
                      init_params_=[("n", "int", "16"), ("order", "int", "4")])
        result = init_params(cfg, "gen")
        assert result == [("n", "int", "16"), ("order", "int", "4")]


class TestDumpFlagFields:
    def test_dump_no_state(self):
        cfg = from_new("p")
        add_component(cfg, "gen", [], no_state_=True)
        assert 'no_state = "true"' in _dump(cfg)

    def test_dump_no_step(self):
        cfg = from_new("p")
        add_component(cfg, "sink", [], no_step_=True)
        assert 'no_step = "true"' in _dump(cfg)

    def test_dump_mutable(self):
        cfg = from_new("p")
        add_component(cfg, "nco", [], mutable_=True)
        assert 'mutable = "true"' in _dump(cfg)

    def test_dump_init_params_section(self):
        cfg = from_new("p")
        add_component(cfg, "gen", [], no_state_=True,
                      init_params_=[("n", "int", "16")])
        text = _dump(cfg)
        assert "[[gen.init_params]]" in text
        assert 'name = "n"' in text
        assert 'type = "int"' in text
        assert 'default = "16"' in text

    def test_dump_method_batch(self):
        cfg = from_new("p")
        add_component(cfg, "nco", [])
        add_method(cfg, "nco", {"name": "run", "batch": True})
        assert "batch = true" in _dump(cfg)

    def test_dump_method_out_type(self):
        cfg = from_new("p")
        add_component(cfg, "conv", [])
        add_method(cfg, "conv", {"name": "proc", "out_type": "float"})
        assert 'out_type = "float"' in _dump(cfg)

    def test_dump_method_out_divisor(self):
        cfg = from_new("p")
        add_component(cfg, "conv", [])
        add_method(cfg, "conv", {"name": "proc", "out_divisor": 4})
        assert "out_divisor = 4" in _dump(cfg)

    def test_dump_method_out_divisor_1_not_written(self):
        cfg = from_new("p")
        add_component(cfg, "conv", [])
        add_method(cfg, "conv", {"name": "proc", "out_divisor": 1})
        assert "out_divisor" not in _dump(cfg)

    def test_dump_property_type(self):
        cfg = from_new("p")
        add_component(cfg, "nco", [])
        add_property(cfg, "nco", {"name": "phase", "type": "uint32_t"})
        assert 'type = "uint32_t"' in _dump(cfg)

    def test_dump_property_field(self):
        cfg = from_new("p")
        add_component(cfg, "nco", [])
        add_property(cfg, "nco", {"name": "phase", "type": "uint32_t", "field": True})
        assert "field = true" in _dump(cfg)

    def test_dump_property_writable(self):
        cfg = from_new("p")
        add_component(cfg, "nco", [])
        add_property(cfg, "nco", {"name": "phase", "type": "uint32_t", "writable": True})
        assert "writable = true" in _dump(cfg)

    def test_dump_array_arg_type(self):
        cfg = from_new("p")
        add_component(cfg, "fir", [], array_args_=[("h", "float32")])
        assert 'type = "float32"' in _dump(cfg)


class TestBackwardCompat:
    """Old TOML keys (ctype, dtype) must still load correctly."""

    def test_property_ctype_key_loads(self, tmp_path):
        (tmp_path / FILENAME).write_text(
            '[project]\nname = "p"\nversion = "0.1.0"\n'
            'build = "cmake"\nperf = "false"\n'
            'pytest = "false"\npytest_benchmark = "false"\n\n'
            '[nco]\narg_type = "float _Complex"\nreturn_type = "float _Complex"\n'
            'mutable = "false"\nno_state = "false"\nno_step = "false"\n\n'
            '[[nco.properties]]\nname = "phase"\nctype = "uint32_t"\n',
            encoding="utf-8",
        )
        cfg = load(tmp_path)
        props = cfg["nco"]["properties"]
        assert props[0].get("ctype") == "uint32_t"
        # dump must re-emit under the new 'type' key
        text = _dump(cfg)
        assert 'type = "uint32_t"' in text

    def test_array_arg_dtype_key_loads(self, tmp_path):
        (tmp_path / FILENAME).write_text(
            '[project]\nname = "p"\nversion = "0.1.0"\n'
            'build = "cmake"\nperf = "false"\n'
            'pytest = "false"\npytest_benchmark = "false"\n\n'
            '[fir]\narg_type = "float _Complex"\nreturn_type = "float _Complex"\n'
            'mutable = "false"\nno_state = "false"\nno_step = "false"\n\n'
            '[[fir.array_args]]\nname = "h"\ndtype = "float32"\n',
            encoding="utf-8",
        )
        cfg = load(tmp_path)
        from just_makeit._config import array_args
        assert array_args(cfg, "fir") == [("h", "float32")]
        text = _dump(cfg)
        assert 'type = "float32"' in text

    def test_dump_build_make(self):
        cfg = from_new("p", build_system="make")
        assert 'build = "make"' in _dump(cfg)

    def test_dump_build_cmake_written(self):
        cfg = from_new("p")
        assert 'build = "cmake"' in _dump(cfg)
