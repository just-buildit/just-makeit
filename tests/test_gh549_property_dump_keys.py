"""gh-549: ``_property_dump_lines`` silently dropped a property's ``enum``.

The dumper emitted every property key except ``enum``. That was invisible on an
ordinary save, because ``C.save`` -> ``_write_doc`` round-trips an *existing*
file through tomlkit and so preserves unknown keys generically. ``_dump`` is
reached only for brand-new files and by ``jm split-objects`` / ``jm migrate``,
which rewrite a section from the parsed dict.

The consequence was a silent public-API break: splitting a project reverted an
enum property's Python face from its ordered string back to a raw ``int`` and
discarded the gh-521 bounds check, with no error and no warning — exactly what
gh-519 fixed, reintroduced through a different door.

The regression guard is deliberately *structural*. A test that asserted
``enum`` specifically would have been satisfied by the one-line fix and left
the next key just as exposed, so ``TestEveryKeyRoundTrips`` instead dumps a
fully-populated property, reloads it, and demands equality. Any key the dumper
forgets fails it, including one that does not exist yet.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._new import run as new_run
from just_makeit._property import run as property_run
from just_makeit._split_objects import run as split_run

# tomllib is stdlib only on 3.11+; jm supports down to 3.9 and guards this in
# _config.py, so borrow its already-resolved reader rather than importing it
# here (a bare `import tomllib` is a collection error on 3.9/3.10).
tomllib = C.tomllib


def _round_trip(prop: dict) -> dict:
    """Dump *prop* and parse it back, as ``jm split-objects`` effectively does."""
    text = "\n".join(C._property_dump_lines(prop, "[[o.properties]]"))
    return tomllib.loads(text)["o"]["properties"][0]


class TestEveryKeyRoundTrips:
    """The dumper must not lose a key. Pins the set, not any one member."""

    # Every key `_property.run` can write into an entry, in two mutually
    # compatible groups -- `enum` and `buf_field` are exclusive (gh-519), so a
    # single property cannot carry all of them at once.
    ENUM_PROP = {
        "name": "file_type",
        "type": "int",
        "doc": "Container format.",
        "enum": "ftype",
        "writable": True,
        "field": True,
    }
    BUF_PROP = {
        "name": "taps",
        "type": "double[]",
        "doc": "Filter taps.",
        "buf_field": "h",
        "len_field": "ntaps",
        "valid_field": "ready",
    }
    EXPR_PROP = {
        "name": "span",
        "type": "size_t",
        "doc": "Window span.",
        "expr": "state->hi - state->lo",
    }

    @pytest.mark.parametrize(
        "prop",
        [ENUM_PROP, BUF_PROP, EXPR_PROP],
        ids=["enum", "buf_field", "expr"],
    )
    def test_no_key_is_lost(self, prop):
        assert _round_trip(prop) == prop

    def test_enum_specifically_survives(self):
        """The reported symptom, kept as a named case for the changelog."""
        assert _round_trip(self.ENUM_PROP)["enum"] == "ftype"


class TestSplitObjectsPreservesEnum:
    """End-to-end: the path that actually reached users."""

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, ["nco"], [("cap", "size_t", "16")])
        cfg = C.load(dest)
        cfg.setdefault("enum", []).append(
            {"name": "ftype", "values": ["raw", "wav"]}
        )
        C.save(dest, cfg)
        property_run(
            dest, "nco", "ft", None, "int", False, field=True, enum="ftype"
        )
        return dest

    def test_manifest_keeps_enum_after_split(self, project):
        split_run(project)
        cfg = C.load(project)
        (prop,) = C.properties(cfg, "nco")
        assert prop.get("enum") == "ftype", (
            "split-objects dropped `enum` -- the property's Python face "
            "silently reverts to a raw int on the next regeneration"
        )

    def test_fragment_text_carries_enum(self, project):
        split_run(project)
        frag = (project / "objects" / "nco.toml").read_text()
        assert 'enum = "ftype"' in frag
