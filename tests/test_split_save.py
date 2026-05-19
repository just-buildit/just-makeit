"""Phase 2 tests: mutations on a split-TOML project route writes to the
fragment file that owns the section; the manifest and siblings stay
untouched. New objects on a split project go to a new fragment file;
removing the last section of a fragment deletes the fragment file."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._method import run as method_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._remove import run as remove_run


_AGC_FRAGMENT = """\
[agc]
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "false"
no_state = "false"
no_step = "false"

[[agc.state]]
name = "gain"
type = "double"
default = "1.0"
"""


@pytest.fixture()
def split_project(tmp_path):
    """A project with one fragment (agc) materialized via apply."""
    root = tmp_path / "proj"
    new_run("proj", root)
    fragment = tmp_path / "agc.toml"
    fragment.write_text(_AGC_FRAGMENT)
    apply_run(root, fragment=fragment)
    return root


class TestMutationRoutesToFragment:
    def test_method_addition_lands_in_fragment(self, split_project):
        before_manifest = (split_project / C.FILENAME).read_bytes()
        method_run(
            split_project,
            "agc",
            "tune",
            None,
            "void",
            "float _Complex",
            False,
            [],
            params=[("freq", "double")],
        )
        # Fragment now carries the method.
        frag = (split_project / "objects" / "agc.toml").read_text(
            encoding="utf-8"
        )
        assert "[[agc.methods]]" in frag
        assert "tune" in frag
        # Manifest is byte-for-byte unchanged.
        assert (split_project / C.FILENAME).read_bytes() == before_manifest

    def test_manifest_has_no_object_section(self, split_project):
        method_run(
            split_project,
            "agc",
            "tune",
            None,
            "void",
            "float _Complex",
            False,
            [],
            params=[("freq", "double")],
        )
        manifest = (split_project / C.FILENAME).read_text(encoding="utf-8")
        assert "[agc]" not in manifest
        assert "[[agc." not in manifest


class TestNewObjectInSplitProject:
    def test_new_standalone_object_creates_new_fragment(self, split_project):
        before_manifest = (split_project / C.FILENAME).read_bytes()
        object_run(
            split_project,
            "widget",
            None,
            state_vars=[("g", "float", "1.0f")],
        )
        new_frag = split_project / "objects" / "widget.toml"
        assert new_frag.exists()
        text = new_frag.read_text(encoding="utf-8")
        assert "[widget]" in text
        # Manifest is unchanged.
        assert (split_project / C.FILENAME).read_bytes() == before_manifest


class TestRemovalEmptiesFragment:
    def test_remove_object_deletes_empty_fragment(self, split_project):
        frag = split_project / "objects" / "agc.toml"
        assert frag.exists()
        remove_run(split_project, "object", "agc", force=True)
        assert not frag.exists()


class TestSingleFileUnchanged:
    def test_save_on_single_file_project_writes_only_manifest(self, tmp_path):
        root = tmp_path / "proj"
        new_run("proj", root)
        object_run(
            root,
            "widget",
            None,
            state_vars=[("g", "float", "1.0f")],
        )
        # No fragments created.
        assert not (root / "objects").exists()
        # Object lives in the manifest.
        manifest = (root / C.FILENAME).read_text(encoding="utf-8")
        assert "[widget]" in manifest
