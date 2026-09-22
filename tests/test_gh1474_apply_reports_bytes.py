"""gh-1474: `apply` reports a file only when this run changed its bytes.

Each write site used to report what it WROTE, and three things made that
differ from what CHANGED. Measured on a copy of doppler, whose second `apply`
announced nine `update`s and changed no file:

* the gh-917 formatter pass rewrites a render back to the project's style
  after the write site has already counted it, so a `c_format_command`
  project hears about seven binding fragments it will never see move;
* one file is handed back by two sites -- five doppler modules share
  ``package = "wfm"`` -- once relative and once under the root, so it was
  listed twice;
* an owned fragment (gh-1448) is rewritten from its render on every apply
  and reported as ``create`` even when it already matched.

`_apply._changed_report` decides from digests taken before the run and bytes
read after it. The end-to-end half of this is `stale_project`'s golden, whose
last `apply` used to print ``create`` for a byte-identical fragment.
"""

from __future__ import annotations

from pathlib import Path

from just_makeit import _apply


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.c").write_text("int a;\n")
    (root / "src" / "b.c").write_text("int b;\n")
    return root


def test_a_write_that_ends_where_it_started_is_not_reported(tmp_path):
    root = _tree(tmp_path)
    before = _apply._tree_digests(root)
    # Written, then formatted back -- the gh-917 shape.
    (root / "src" / "a.c").write_text("int  a;\n")
    (root / "src" / "a.c").write_text("int a;\n")
    assert _apply._changed_report(root, before, [root / "src" / "a.c"]) == []


def test_one_file_named_twice_is_reported_once(tmp_path):
    root = _tree(tmp_path)
    before = _apply._tree_digests(root)
    (root / "src" / "a.c").write_text("int a = 1;\n")
    got = _apply._changed_report(
        root, before, [Path("src/a.c"), root / "src" / "a.c"]
    )
    assert got == [("update", "src/a.c")]


def test_new_and_changed_files_are_reported_project_relative(tmp_path):
    root = _tree(tmp_path)
    before = _apply._tree_digests(root)
    (root / "src" / "c.c").write_text("int c;\n")
    (root / "src" / "b.c").write_text("int b = 2;\n")
    got = _apply._changed_report(
        root, before, [Path("src/c.c"), root / "src" / "b.c"]
    )
    assert got == [("create", "src/c.c"), ("update", "src/b.c")]


def test_an_overwritten_file_that_already_matched_is_not_a_create(tmp_path):
    """The owned-fragment shape: `_sync_missing` rewrites it and lists it
    among the files it created, though it existed with these bytes."""
    root = _tree(tmp_path)
    before = _apply._tree_digests(root)
    (root / "src" / "a.c").write_bytes((root / "src" / "a.c").read_bytes())
    assert _apply._changed_report(root, before, [Path("src/a.c")]) == []


def test_the_snapshot_does_not_descend_into_a_build_tree(tmp_path):
    root = _tree(tmp_path)
    b = root / "cmake-build-debug"
    b.mkdir()
    (b / "CMakeCache.txt").write_text("")
    (b / "big.o").write_bytes(b"\0" * 10)
    assert set(_apply._tree_digests(root)) == {"src/a.c", "src/b.c"}
