"""Tests for `just-makeit split-objects` — migrate a single-file project
to the split per-object TOML layout."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._split_objects import run as split_run


@pytest.fixture()
def single_file_project(tmp_path):
    """A project with standalone + module objects, all in the manifest."""
    root = tmp_path / "proj"
    new_run("proj", root, ["widget"], [("gain", "float", "0.0f")])
    object_run(root, "gadget", None, state_vars=[("g", "float", "1.0f")])
    module_run(root, "dsp")
    object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
    return root


class TestSplitMigration:
    def test_creates_fragment_per_object(self, single_file_project):
        split_run(single_file_project)
        for comp in ("widget", "gadget", "nco"):
            frag = single_file_project / "objects" / f"{comp}.toml"
            assert frag.exists(), comp
            assert f"[{comp}]" in frag.read_text(encoding="utf-8")

    def test_manifest_keeps_project_and_module(self, single_file_project):
        split_run(single_file_project)
        manifest = (single_file_project / C.FILENAME).read_text(
            encoding="utf-8"
        )
        assert "[project]" in manifest
        assert "[module.dsp]" in manifest
        # Object sections are gone from the manifest.
        for comp in ("widget", "gadget", "nco"):
            assert f"[{comp}]" not in manifest
            assert f"[[{comp}." not in manifest

    def test_manifest_gains_include_glob(self, single_file_project):
        split_run(single_file_project)
        manifest_doc = C.load_manifest(single_file_project)
        assert manifest_doc.get("include") == ["objects/*.toml"]

    def test_merged_cfg_unchanged_after_split(self, single_file_project):
        before = C.load(single_file_project)
        split_run(single_file_project)
        after = C.load(single_file_project)
        assert after == before

    def test_module_membership_preserved(self, single_file_project):
        split_run(single_file_project)
        cfg = C.load(single_file_project)
        assert C.module_objects(cfg, "dsp") == ["nco"]
        # nco is a top-level object section, served from its fragment.
        assert "nco" in C.components(cfg)


class TestSplitIsIdempotent:
    def test_rerun_is_noop(self, single_file_project):
        split_run(single_file_project)
        manifest_before = (single_file_project / C.FILENAME).read_bytes()
        fragments_before = {
            p.name: p.read_bytes()
            for p in (single_file_project / "objects").iterdir()
        }
        split_run(single_file_project)
        assert (
            single_file_project / C.FILENAME
        ).read_bytes() == manifest_before
        assert {
            p.name: p.read_bytes()
            for p in (single_file_project / "objects").iterdir()
        } == fragments_before


class TestSplitEdgeCases:
    def test_empty_project_is_noop(self, tmp_path):
        root = tmp_path / "proj"
        new_run("proj", root)
        # No standalone objects yet.
        before = (root / C.FILENAME).read_bytes()
        split_run(root)
        assert (root / C.FILENAME).read_bytes() == before
        assert not (root / "objects").exists()

    def test_missing_manifest_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            split_run(tmp_path)
