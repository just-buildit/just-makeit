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
from just_makeit._keys import PROPERTY_KEYS
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
    # gh-543 -- the keys added after this guard went in, which is exactly the
    # case it exists to cover.
    CONTAINER_PROP = {
        "name": "keywords",
        "type": "dict",
        "doc": "Extended header.",
        "value_type": "object",
        "count_fn": "rdr_num_keywords",
        "key_fn": "rdr_keyword_tag",
        "value_fn": "rdr_keyword_value",
        "entry_fn": "rdr_keyword_at",
        "entry_type": "rdr_kw_t",
        "type_field": "tag",
        "value_field": "val",
        "count_field": "n_kw",
        "codec": "blue_keyword",
    }
    # gh-788 gap 4 / gh-1242. The key this guard was blind to for five
    # releases: the dumper dropped it, `jm split-objects` left a property
    # still typed `capsule` that published nothing, and after gh-1224 every
    # `object` reference to that component stopped resolving.
    CAPSULE_PROP = {
        "name": "_capsule",
        "type": "capsule",
        "doc": "The backing pointer.",
        "capsule": "dsp.nco.state",
        # gh-1235, added the release after this shape was. It failed the
        # union assertion above on the commit that introduced it, which is
        # the whole point of having one.
        "capsule_type": "const dp_nco_desc_t *",
        "expr": "&self->handle->d",
    }
    SCALAR_PROP = {
        "name": "gain",
        "type": "double",
        "doc": "Linear gain.",
        "field": True,
        "writable": True,
        "mutable": True,
        "default": "1.0",
        "out": "g",
        "ctype": "double",
    }

    SHAPES = {
        "enum": ENUM_PROP,
        "buf_field": BUF_PROP,
        "expr": EXPR_PROP,
        "container": CONTAINER_PROP,
        "capsule": CAPSULE_PROP,
        "scalar": SCALAR_PROP,
    }

    @pytest.mark.parametrize("prop", list(SHAPES.values()), ids=list(SHAPES))
    def test_no_key_is_lost(self, prop):
        assert _round_trip(prop) == prop

    def test_the_shapes_between_them_cover_the_whole_vocabulary(self):
        """gh-1242: the assertion this guard was always missing.

        Its docstring promised "any key the dumper forgets fails it, including
        one that does not exist yet". That was true of the ASSERTION and false
        of the FIXTURE: "fully-populated" meant four hand-written dicts, so it
        covered the keys someone remembered to add to a sample. gh-788 gap 4
        added `capsule` afterwards, no sample gained it, and the guard passed
        while `jm split-objects` destroyed the property.

        The shapes cannot be merged into one -- `enum` and `buf_field` are
        mutually exclusive (gh-519) -- so what has to hold is that their UNION
        covers `PROPERTY_KEYS`. A key added to the vocabulary with no
        representative fails here rather than being silently uncovered, which
        is the `_a_sweep_is_only_as_good_as_its_tree` fix: repair the fixture,
        not the assertion.
        """
        covered = set()
        for prop in self.SHAPES.values():
            covered |= set(prop)
        missing = sorted(PROPERTY_KEYS - covered)
        assert not missing, (
            f"no probe property carries {missing}, so the dumper could drop "
            "them and this file would still pass -- add each to whichever "
            "shape it is legal on (or a new shape if it is exclusive with "
            "every existing one)"
        )

    def test_enum_specifically_survives(self):
        """The reported symptom, kept as a named case for the changelog."""
        assert _round_trip(self.ENUM_PROP)["enum"] == "ftype"

    def test_capsule_specifically_survives(self):
        """gh-1242's symptom, kept as a named case for the same reason."""
        assert _round_trip(self.CAPSULE_PROP)["capsule"] == "dsp.nco.state"


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


class TestSplitObjectsPreservesTheCapsule:
    """gh-1242, end-to-end: the path that actually reached users.

    The unit round-trip above is the mechanism; this is the command. gh-549
    paired them for `enum` and the pairing is what makes the fix credible --
    a dumper test alone cannot show that `split-objects` is the caller that
    loses it.
    """

    @pytest.fixture()
    def project(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, ["nco"], [("cap", "size_t", "16")])
        property_run(
            dest,
            "nco",
            "_capsule",
            None,
            "capsule",
            False,
            capsule="dsp.nco.state",
        )
        return dest

    def test_the_producer_still_publishes_after_a_split(self, project):
        split_run(project)
        cfg = C.load(project)
        assert C.object_ref_capsule(cfg, "nco") == "dsp.nco.state", (
            "split-objects dropped the property's `capsule` -- gh-788 gap 4's "
            "producer publishes nothing, and every gh-1224 `object` reference "
            "to this component stops resolving"
        )

    def test_the_fragment_text_carries_it(self, project):
        split_run(project)
        frag = (project / "objects" / "nco.toml").read_text()
        assert 'capsule = "dsp.nco.state"' in frag
