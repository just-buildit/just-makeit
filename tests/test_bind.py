"""Integration tests for the ``jm bind`` prototype.

The MVP only handles the *filter* template shape. The test contract is:
scaffold a project with ``jm`` (the canonical source of truth), delete
the generated ``_ext.c``, run ``_bind.run``, and assert the regenerated
binding is byte-identical to the original.

Byte-identity is the strongest possible bind-is-correct signal — it
proves the parser-driven path and the TOML-driven path agree on every
character of the output, including doctest defaults sourced from the
``<comp>_core.c`` reset body.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._bind import (
    parse_header,
    parse_reset_defaults,
    run as bind_run,
)


def _scaffold_filter(root: Path) -> Path:
    """Scaffold a default filter; return the path to <comp>_ext.c."""
    new_run("my_dsp", root, ["my_filter"], [("gain", "float", "1.0f")])
    return root / "native" / "src" / "my_filter" / "my_filter_ext.c"


class TestBindByteIdenticalToScaffold:
    """The single bar this prototype must clear."""

    def test_default_filter_roundtrips(self, tmp_path):
        root = tmp_path / "proj"
        ext_c = _scaffold_filter(root)
        original = ext_c.read_text(encoding="utf-8")

        ext_c.unlink()
        bind_run(root, "my_filter")

        assert ext_c.read_text(encoding="utf-8") == original

    def test_multi_field_state_roundtrips(self, tmp_path):
        """Multiple scalar state fields of different types must still round-trip."""
        root = tmp_path / "proj"
        new_run("my_dsp", root, [], [])
        object_run(
            root,
            "accumulator",
            None,
            arg_type="double",
            return_type="double",
            state_vars=[
                ("sum", "double", "0.0"),
                ("count", "uint64_t", "0"),
            ],
        )
        ext_c = root / "native" / "src" / "accumulator" / "accumulator_ext.c"
        original = ext_c.read_text(encoding="utf-8")

        ext_c.unlink()
        bind_run(root, "accumulator")

        assert ext_c.read_text(encoding="utf-8") == original

    def test_check_mode_passes_for_freshly_scaffolded(self, tmp_path):
        root = tmp_path / "proj"
        _scaffold_filter(root)
        # write=False mode: render and compare against on-disk version.
        # Explicit utf-8 is required: the rendered string is utf-8 (it
        # carries em-dashes from the template) but on Windows ``read_text``
        # without an encoding defaults to cp1252 and mis-decodes them.
        rendered = bind_run(root, "my_filter", write=False)
        existing = (
            root / "native" / "src" / "my_filter" / "my_filter_ext.c"
        ).read_text(encoding="utf-8")
        assert rendered == existing


class TestParser:
    def test_parses_state_struct_fields(self, tmp_path):
        root = tmp_path / "proj"
        _scaffold_filter(root)
        header = root / "native" / "inc" / "my_filter" / "my_filter_core.h"
        parsed = parse_header(header)
        assert parsed["component"] == "my_filter"
        assert parsed["fields"] == [("gain", "float")]

    def test_parses_step_signature(self, tmp_path):
        root = tmp_path / "proj"
        _scaffold_filter(root)
        header = root / "native" / "inc" / "my_filter" / "my_filter_core.h"
        parsed = parse_header(header)
        assert parsed["arg_type"] == "float _Complex"
        assert parsed["return_type"] == "float _Complex"

    def test_parses_mutable_state_step(self, tmp_path):
        """Step bodies that mutate state through the pointer drop ``const``;
        the parser must accept both forms.  The running_stats example
        patches the Welford accumulator inline and lands in this shape."""
        h = tmp_path / "foo_core.h"
        h.write_text(
            "typedef struct { double sum; } foo_state_t;\n"
            "static inline double foo_step(foo_state_t *state, double x) {\n"
            "    state->sum += x;\n"
            "    return state->sum;\n"
            "}\n"
        )
        parsed = parse_header(h)
        assert parsed["arg_type"] == "double"
        assert parsed["return_type"] == "double"

    def test_parses_reset_defaults(self, tmp_path):
        root = tmp_path / "proj"
        _scaffold_filter(root)
        core_c = root / "native" / "src" / "my_filter" / "my_filter_core.c"
        defaults = parse_reset_defaults(core_c)
        assert defaults == {"gain": "1.0f"}


class TestParseFailures:
    """The parser must fail loudly when the header doesn't fit the
    filter shape. Falling back silently to the wrong shape would
    silently produce a broken binding — much worse than a clear
    ``ValueError``."""

    def test_missing_state_struct_raises(self, tmp_path):
        bad = tmp_path / "core.h"
        bad.write_text("/* no state struct here */\n")
        with pytest.raises(ValueError, match="state_t"):
            parse_header(bad)

    def test_missing_step_raises(self, tmp_path):
        bad = tmp_path / "core.h"
        bad.write_text("typedef struct {\n    float gain;\n} foo_state_t;\n")
        with pytest.raises(ValueError, match="step"):
            parse_header(bad)


# ── Phase 3b: new shape parsing ───────────────────────────────────────────────


_PROP_HEADER = """\
typedef struct {{
    {field_type} {field_name};
}} comp_state_t;

{field_type} comp_get_{field_name}(const comp_state_t *state);
void comp_set_{field_name}(comp_state_t *state, {field_type} val);

static inline float comp_step(const comp_state_t *state, float x) {{
    return x;
}}
"""

_METHOD_HEADER = (
    "typedef struct {\n    float gain;\n} foo_state_t;\n\n"
    "float foo_get_gain(const foo_state_t *state);\n"
    "void foo_set_gain(foo_state_t *state, float val);\n\n"
    "float foo_scale(const foo_state_t *state, float x);\n"
    "float foo_offset(const foo_state_t *state);\n\n"
    "size_t foo_collect_max_out(const foo_state_t *state);\n"
    "size_t foo_collect(foo_state_t *state, float x, float *out);\n\n"
    "static inline float foo_step(const foo_state_t *state, float x) {\n"
    "    return x * state->gain;\n}\n"
)

_OPAQUE_HEADER = (
    "typedef struct foo_state_t foo_state_t;\n\n"
    "static inline float foo_step(foo_state_t *state, float x) {\n"
    "    return x;\n}\n"
)


class TestPhase3bParser:
    """Property, method, variable-output, and opaque-state parsing."""

    def test_property_getter_only(self, tmp_path):
        h = tmp_path / "comp_core.h"
        h.write_text(
            _PROP_HEADER.format(field_type="double", field_name="alpha")
        )
        parsed = parse_header(h)
        # 'alpha' is a state field AND has a getter — state fields must be
        # excluded from properties (make_state_ctx already handles them).
        assert parsed["properties"] == []

    def test_property_non_state_field(self, tmp_path):
        h = tmp_path / "comp_core.h"
        h.write_text(
            "typedef struct { float gain; } comp_state_t;\n"
            "size_t comp_get_sample_count(const comp_state_t *state);\n"
            "static inline float comp_step(const comp_state_t *state, float x) "
            "{ return x; }\n"
        )
        parsed = parse_header(h)
        # sample_count is not a state field → it becomes a read-only property.
        assert len(parsed["properties"]) == 1
        assert parsed["properties"][0]["name"] == "sample_count"
        assert parsed["properties"][0]["writable"] is False

    def test_property_writable(self, tmp_path):
        h = tmp_path / "comp_core.h"
        h.write_text(
            "typedef struct { float rate; } comp_state_t;\n"
            "double comp_get_smooth(const comp_state_t *state);\n"
            "void comp_set_smooth(comp_state_t *state, double val);\n"
            "static inline float comp_step(const comp_state_t *state, float x) "
            "{ return x; }\n"
        )
        parsed = parse_header(h)
        props = parsed["properties"]
        assert len(props) == 1
        assert props[0]["name"] == "smooth"
        assert props[0]["writable"] is True

    def test_custom_method_scalar_in_scalar_out(self, tmp_path):
        h = tmp_path / "foo_core.h"
        h.write_text(_METHOD_HEADER)
        parsed = parse_header(h)
        method_names = [m["name"] for m in parsed["methods"]]
        assert "scale" in method_names
        scale = next(m for m in parsed["methods"] if m["name"] == "scale")
        assert scale["arg_type"] == "float"
        assert scale["return_type"] == "float"
        assert not scale.get("variable_output", False)

    def test_custom_method_void_in(self, tmp_path):
        h = tmp_path / "foo_core.h"
        h.write_text(_METHOD_HEADER)
        parsed = parse_header(h)
        method_names = [m["name"] for m in parsed["methods"]]
        assert "offset" in method_names

    def test_lifecycle_verbs_excluded_from_methods(self, tmp_path):
        h = tmp_path / "foo_core.h"
        h.write_text(_METHOD_HEADER)
        parsed = parse_header(h)
        method_names = [m["name"] for m in parsed["methods"]]
        for excluded in ("create", "destroy", "reset", "step", "steps"):
            assert excluded not in method_names

    def test_variable_output_detection(self, tmp_path):
        h = tmp_path / "foo_core.h"
        h.write_text(_METHOD_HEADER)
        parsed = parse_header(h)
        method_names = [m["name"] for m in parsed["methods"]]
        # collect has a _max_out sibling → variable_output=True
        assert "collect" in method_names
        collect = next(m for m in parsed["methods"] if m["name"] == "collect")
        assert collect.get("variable_output", False)
        # collect_max_out itself must NOT appear as a method
        assert "collect_max_out" not in method_names

    def test_opaque_state_forward_decl(self, tmp_path):
        h = tmp_path / "foo_core.h"
        h.write_text(_OPAQUE_HEADER)
        parsed = parse_header(h)
        assert parsed["is_opaque"] is True
        assert parsed["fields"] == []
        assert parsed["component"] == "foo"

    def test_opaque_step_still_parsed(self, tmp_path):
        h = tmp_path / "foo_core.h"
        h.write_text(_OPAQUE_HEADER)
        parsed = parse_header(h)
        assert parsed["arg_type"] == "float"
        assert parsed["return_type"] == "float"

    def test_init_params_from_extra_ctor_args(self, tmp_path):
        h = tmp_path / "comp_core.h"
        h.write_text(
            "typedef struct { float gain; } comp_state_t;\n"
            "comp_state_t *comp_create(float gain, float sample_rate);\n"
            "static inline float comp_step(const comp_state_t *state, float x) "
            "{ return x; }\n"
        )
        parsed = parse_header(h)
        # gain is a state field; sample_rate is not → init_param
        ip_names = [p[0] for p in parsed["init_params"]]
        assert "gain" not in ip_names
        assert "sample_rate" in ip_names
