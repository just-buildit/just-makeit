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
from just_makeit._bind import parse_header, parse_reset_defaults, run as bind_run


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
        rendered = bind_run(root, "my_filter", write=False)
        existing = (
            root / "native" / "src" / "my_filter" / "my_filter_ext.c"
        ).read_text()
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
