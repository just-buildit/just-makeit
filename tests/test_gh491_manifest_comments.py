"""gh-491: a mutating command must not destroy the manifest's prose.

Every mutating command calls `C.save()`. That rebuilt component sections from
`_dump()`, and since tomllib preserves no comments, **one `jm warning` stripped
every comment from all 49 of doppler's `objects/*.toml`** and collapsed
multi-line arrays to one line — as a side effect of declaring one warning on
one object.

The manifest is jm's single source of truth and the surface users are told to
author. A project that documents *why* a component links what it links had no
usable CLI: the damage was invisible unless you read a 2800-line diff.

It also explains a mechanic we documented without knowing its cause. gh-481
noted doppler's migration path is "edit the manifest, *delete* the fragment,
`jm apply`". That is not taste — it is the only path that preserves comments,
because `_apply` writes `C.save` to a *temp* root and never rewrites the
project's manifest. We had shipped a CLI whose sanctioned usage was "don't use
the CLI".

The fix syncs the document in place with tomlkit and touches only keys whose
values actually changed. These tests pin the properties, not the mechanism:
prose survives, a no-op save is a no-op, and fresh output is unchanged.
"""

import re
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._new import run as new_run
from just_makeit._property import run as property_run
from just_makeit._warning import run as warning_run

_PROSE = """
# ── Construction: physics in, grid out ───────────────────────────────────────
# The user states the waveform, front end, and operating point in real units;
# the engine derives the whole search grid (coherent depth, threshold, looks).
"""


def _comments(text: str) -> int:
    return len(re.findall(r"^\s*#", text, re.M))


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "dsp"
    new_run("dsp", dest, ["acq"], [("underpowered", "int", "0")])
    manifest = dest / "just-makeit.toml"
    # Author the manifest the way a real project does: prose above a component,
    # and a hand-formatted multi-line array.
    text = manifest.read_text(encoding="utf-8")
    text = text.replace("[acq]", _PROSE.strip() + "\n[acq]")
    text += (
        "\n# OBJECT-library object files do not propagate transitively, so\n"
        "# fft2d/fft must be linked explicitly.\n"
        "depends_on = [\n"
        '    { name = "corr2d", link = true },\n'
        '    { name = "fft", link = true },\n'
        "]\n"
    )
    manifest.write_text(text, encoding="utf-8")
    return dest


def _manifest(project) -> str:
    return (project / "just-makeit.toml").read_text(encoding="utf-8")


class TestProseSurvivesMutation:
    def test_jm_warning_keeps_every_comment(self, project):
        before = _manifest(project)
        warning_run(project, "acq", "underpowered", "best effort only")
        after = _manifest(project)
        assert _comments(after) == _comments(before), (
            "a mutating command stripped comments from the manifest — gh-491"
        )
        assert "physics in, grid out" in after
        assert "propagate transitively" in after

    def test_jm_warning_only_adds(self, project):
        before = _manifest(project).splitlines()
        warning_run(project, "acq", "underpowered", "best effort only")
        after = _manifest(project).splitlines()
        removed = [ln for ln in before if ln not in after]
        assert removed == [], f"lines were lost: {removed[:5]}"

    def test_multiline_array_layout_survives(self, project):
        warning_run(project, "acq", "underpowered", "best effort only")
        after = _manifest(project)
        assert "depends_on = [\n" in after, (
            "hand-formatted array was collapsed to one line"
        )

    def test_jm_property_keeps_comments_too(self, project):
        # Not warning-specific: every mutating command shares C.save().
        before = _comments(_manifest(project))
        property_run(project, "acq", "dropped", None, "size_t", False)
        assert _comments(_manifest(project)) == before


class TestNoOpSaveIsNoOp:
    """The strongest property: saving unchanged config changes nothing."""

    def test_byte_identical(self, project):
        path = project / "just-makeit.toml"
        original = path.read_text(encoding="utf-8")
        C.save(project, C.load(project))
        assert path.read_text(encoding="utf-8") == original

    def test_repeated_saves_are_stable(self, project):
        warning_run(project, "acq", "underpowered", "best effort only")
        once = _manifest(project)
        C.save(project, C.load(project))
        assert _manifest(project) == once


class TestUnchangedComponentsUntouched:
    def test_declaring_on_one_object_leaves_siblings_byte_identical(
        self, tmp_path
    ):
        # The doppler shape: 2 objects, declare on one, the other must not move.
        dest = tmp_path / "dsp"
        new_run("dsp", dest, ["acq", "nco"], [("underpowered", "int", "0")])
        manifest = dest / "just-makeit.toml"
        text = manifest.read_text(encoding="utf-8")
        text = text.replace("[nco]", "# nco's own prose, must survive\n[nco]")
        manifest.write_text(text, encoding="utf-8")

        warning_run(dest, "acq", "underpowered", "best effort")
        after = manifest.read_text(encoding="utf-8")
        assert "# nco's own prose, must survive" in after


class TestFreshOutputUnchanged:
    """A brand-new file has nothing to preserve; _dump() still owns its text."""

    def test_new_project_manifest_matches_dump(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, ["acq"])
        text = (dest / "just-makeit.toml").read_text(encoding="utf-8")
        assert text == C._dump(C.load(dest))


class TestSyncEdgeCases:
    def test_transient_underscore_keys_are_not_written(self, project):
        # _object._regenerate_module stashes _doc_blocks (DoxyBlock objects) on
        # the cfg; they are in-memory state, and tomlkit cannot serialise them.
        cfg = C.load(project)
        cfg["acq"]["_doc_blocks"] = {"acq_get_x": object()}
        C.save(project, cfg)
        assert "_doc_blocks" not in _manifest(project)

    def test_none_values_are_omitted_not_nulled(self, project):
        # TOML has no null: None means absent.
        cfg = C.load(project)
        cfg["acq"]["class_name"] = None
        C.save(project, cfg)
        text = _manifest(project)
        assert "class_name" not in text
        assert "None" not in text

    def test_new_table_array_renders_as_repeated_table(self, project):
        # Assigning a list of dicts naively yields an inline array of inline
        # tables; the manifest uses [[x.y]] everywhere.
        warning_run(project, "acq", "underpowered", "best effort")
        text = _manifest(project)
        assert "[[acq.warnings]]" in text
        assert "warnings = [{" not in text

    def test_removing_a_key_removes_it_from_the_file(self, project):
        warning_run(project, "acq", "underpowered", "best effort")
        assert "[[acq.warnings]]" in _manifest(project)
        cfg = C.load(project)
        del cfg["acq"]["warnings"]
        C.save(project, cfg)
        assert "[[acq.warnings]]" not in _manifest(project)

    def test_changed_scalar_is_updated(self, project):
        cfg = C.load(project)
        cfg["acq"]["arg_type"] = "double"
        C.save(project, cfg)
        assert C.load(project)["acq"]["arg_type"] == "double"
        # ...and the prose is still there.
        assert "physics in, grid out" in _manifest(project)


class TestSplitLayoutFragments:
    """Fragments are where the prose actually lives in doppler."""

    def test_comments_in_objects_fragment_survive(self, tmp_path):
        dest = tmp_path / "dsp"
        new_run("dsp", dest, ["acq"], [("underpowered", "int", "0")])
        from just_makeit._split_objects import run as split_run

        split_run(dest)
        frag = dest / "objects" / "acq.toml"
        if not frag.exists():
            pytest.skip("split layout not produced for this shape")
        frag.write_text(
            "# why acq links what it links\n"
            + frag.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        warning_run(dest, "acq", "underpowered", "best effort")
        after = frag.read_text(encoding="utf-8")
        assert "# why acq links what it links" in after
        assert "[[acq.warnings]]" in after
        assert tomllib.loads(after)["acq"]["warnings"][0]["condition"] == (
            "underpowered"
        )
