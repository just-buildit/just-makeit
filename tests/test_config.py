"""Unit tests for _config.py (just-makeit.toml read/write)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._keys import INIT_PARAM_FIELDS
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
        add_component(
            cfg, "engine", [("rate", "double", "1.0"), ("order", "int", "4")]
        )
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
            "engine": {
                "state": [{"name": "gain", "type": "double", "default": "1.0"}]
            }
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
        assert state_vars(cfg, "engine") == [
            ("a", "double", "0.0"),
            ("b", "int", "4"),
        ]


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

    def test_truthy_flag_accepts_boolean_true(self):
        """gh-71: ``no_step = true`` (TOML boolean) must be honoured the same
        as ``no_step = "true"`` (the canonical string form jm writes).
        Hand-authored fragments commonly use the boolean form."""
        cfg = from_new("p")
        cfg["sink"] = {"no_step": True, "no_state": True, "mutable": True}
        assert is_no_step(cfg, "sink") is True
        assert is_no_state(cfg, "sink") is True
        assert is_mutable(cfg, "sink") is True

    def test_truthy_flag_rejects_boolean_false(self):
        cfg = from_new("p")
        cfg["sink"] = {"no_step": False, "no_state": False, "mutable": False}
        assert is_no_step(cfg, "sink") is False
        assert is_no_state(cfg, "sink") is False
        assert is_mutable(cfg, "sink") is False

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
        add_component(
            cfg,
            "gen",
            [],
            no_state_=True,
            init_params_=[("n", "int", "16"), ("order", "int", "4")],
        )
        result = init_params(cfg, "gen")
        # The arity is DERIVED, not written down. This comment said "12
        # fields since gh-790" while the tuples below carried 13, because
        # gh-900 appended `derived` and the prose stayed put — the same drift
        # `_keys.INIT_PARAM_FIELDS` exists to prevent for the serializer.
        # That registry's docstring claims it "mirrors the fields
        # `_project_init_params` reads back"; this is the mechanism behind
        # the claim.
        #
        # gh-1224 put a gap in that one-to-one mapping, so the invariant is
        # stated with the gap NAMED rather than as a looser comparison: the
        # tuple now carries two slots that are RESOLVED, not authored --
        # `object_class` and `object_import`, both derived from `object`.
        # They are not manifest keys and must never become writable ones, or
        # a save would bake a consumer's resolution of someone else's capsule
        # back into its own declaration.
        _DERIVED_SLOTS = 2
        assert len(result[0]) == len(INIT_PARAM_FIELDS) + _DERIVED_SLOTS
        # ...and the derived slots really are the trailing ones, so a key
        # appended later cannot silently land inside the gap.
        assert result[0][-_DERIVED_SLOTS:] == ("", "")
        assert result == [
            (
                "n",
                "int",
                "16",
                "",
                "",
                "",
                False,
                "",
                False,
                "",
                "",
                "",
                "",
                "",
                "",
                # gh-1224: (object, object_class, object_import) — empty for
                # every param that does not name another generated class: the
                # declared reference, the class it resolves to, and the `.pyi`
                # import line that makes the annotation resolvable.
                "",
                "",
                "",
            ),
            (
                "order",
                "int",
                "4",
                "",
                "",
                "",
                False,
                "",
                False,
                "",
                "",
                "",
                "",
                "",
                "",
                # gh-1224: (object, object_class, object_import) — empty for
                # every param that does not name another generated class: the
                # declared reference, the class it resolves to, and the `.pyi`
                # import line that makes the annotation resolvable.
                "",
                "",
                "",
            ),
        ]


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
        add_component(
            cfg, "gen", [], no_state_=True, init_params_=[("n", "int", "16")]
        )
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
        add_property(
            cfg, "nco", {"name": "phase", "type": "uint32_t", "field": True}
        )
        assert "field = true" in _dump(cfg)

    def test_dump_property_writable(self):
        cfg = from_new("p")
        add_component(cfg, "nco", [])
        add_property(
            cfg, "nco", {"name": "phase", "type": "uint32_t", "writable": True}
        )
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


tomlkit = pytest.importorskip("tomlkit", reason="tomlkit not installed")


class TestCommentPreservation:
    """save() must preserve user comments in [project] and [module.X]
    sections across load → mutate → save round-trips.  Component sections
    (repeated tables) are rebuilt from _dump() and do not preserve comments
    — this is documented behaviour.

    Skipped when tomlkit is not installed (just-buildit does not propagate
    [project].dependencies to the wheel, so tomlkit may be absent in
    tool-installed environments; comment preservation is a quality-of-life
    feature and its tests must not block CI)."""

    def _write(self, path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")

    def test_file_level_comment_survives(self, tmp_path):
        """A comment at the top of just-makeit.toml survives a save()."""
        toml = tmp_path / "just-makeit.toml"
        self._write(
            toml,
            "# top-level project comment\n"
            '[project]\nname = "p"\nversion = "0.1.0"\n'
            'build = "cmake"\nperf = "false"\npytest = "false"\n'
            'pytest_benchmark = "false"\nschema = "6"\n',
        )
        cfg = load(tmp_path)
        cfg["project"]["version"] = "0.2.0"
        save(tmp_path, cfg)
        result = toml.read_text(encoding="utf-8")
        assert "# top-level project comment" in result
        assert '0.2.0"' in result

    def test_project_inline_comment_survives(self, tmp_path):
        """An inline comment on a [project] key survives a save()."""
        toml = tmp_path / "just-makeit.toml"
        self._write(
            toml,
            '[project]\nname = "p"  # package name\nversion = "0.1.0"\n'
            'build = "cmake"\nperf = "false"\npytest = "false"\n'
            'pytest_benchmark = "false"\nschema = "6"\n',
        )
        cfg = load(tmp_path)
        cfg["project"]["perf"] = "true"
        save(tmp_path, cfg)
        result = toml.read_text(encoding="utf-8")
        assert "# package name" in result

    def test_project_section_comment_survives(self, tmp_path):
        """A comment block before [project] survives a save()."""
        toml = tmp_path / "just-makeit.toml"
        self._write(
            toml,
            "# === project config ===\n"
            '[project]\nname = "p"\nversion = "0.1.0"\n'
            'build = "cmake"\nperf = "false"\npytest = "false"\n'
            'pytest_benchmark = "false"\nschema = "6"\n',
        )
        cfg = load(tmp_path)
        add_component(cfg, "engine", [("gain", "float", "1.0f")])
        save(tmp_path, cfg)
        result = toml.read_text(encoding="utf-8")
        assert "# === project config ===" in result
        assert "[engine]" in result

    def test_module_section_comment_survives(self, tmp_path):
        """A comment on a [module.X] section survives a save()."""
        toml = tmp_path / "just-makeit.toml"
        self._write(
            toml,
            '[project]\nname = "p"\nversion = "0.1.0"\n'
            'build = "cmake"\nperf = "false"\npytest = "false"\n'
            'pytest_benchmark = "false"\nschema = "6"\n\n'
            "# DSP filter bank\n"
            "[module.dsp]\nobjects = []\n",
        )
        cfg = load(tmp_path)
        cfg.setdefault("module", {}).setdefault("dsp", {}).setdefault(
            "objects", []
        ).append("fir")
        save(tmp_path, cfg)
        result = toml.read_text(encoding="utf-8")
        assert "# DSP filter bank" in result
        assert '"fir"' in result

    def test_new_file_created_without_error(self, tmp_path):
        """Writing to a non-existent file still works (no tomlkit round-trip)."""
        cfg = from_new("brand_new")
        save(tmp_path, cfg)
        assert (tmp_path / "just-makeit.toml").exists()

    def test_unchanged_project_keys_intact(self, tmp_path):
        """Keys not touched by the mutation survive verbatim."""
        toml = tmp_path / "just-makeit.toml"
        self._write(
            toml,
            '[project]\nname = "p"\nversion = "0.1.0"\n'
            'build = "make"\nperf = "false"\npytest = "false"\n'
            'pytest_benchmark = "false"\nschema = "6"\n',
        )
        cfg = load(tmp_path)
        cfg["project"]["perf"] = "true"
        save(tmp_path, cfg)
        result = toml.read_text(encoding="utf-8")
        assert 'build = "make"' in result  # untouched key preserved


def test_dump_preserves_unknown_scalar_method_keys():
    # gh-257: the method serializer must round-trip manifest-authored keys it
    # does not explicitly know (record_name + any future scalar), so the write
    # pass stops silently stripping them. Transient `_`-prefixed and list/table
    # keys are not re-emitted generically.
    cfg = {
        "tm": {
            "arg_type": "void",
            "return_type": "void",
            "state": [],
            "methods": [
                {
                    "name": "analyze",
                    "return_type": "tone_meas_t",
                    "single": True,
                    "record_name": "ToneMetrics",  # unknown -> round-trips
                    "future_flag": True,  # unknown bool
                    "future_count": 7,  # unknown int
                    "_doc_blocks": {"x": 1},  # transient -> skipped
                }
            ],
        }
    }
    out = _dump(cfg)
    assert 'record_name = "ToneMetrics"' in out
    assert "future_flag = true" in out
    assert "future_count = 7" in out
    assert "_doc_blocks" not in out
    assert out.count("single = true") == 1  # known key not duplicated
