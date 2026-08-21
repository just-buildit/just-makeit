"""gh-1027: a leftover directory is not an example, and must not be reported.

Cutting an example removes every tracked file, but a previous
`jm example <name>` run leaves a `__pycache__` behind and `__pycache__` is
gitignored -- so the parent directory survives holding nothing else.
`copy_examples._example_dirs` counted any directory not starting with `_` or
`.`, so each shell counted as an example and the reconciler reported the one
thing certainly not wrong with it: its missing README. Six of those turned
`make test` red on any checkout old enough to have run the examples #575 cut.

Invisible in CI by construction -- a fresh clone never had the directories --
so it fires only on a long-lived checkout, which is a maintainer's machine and
exactly where `make test` is meant to be trusted before pushing. It has since
recurred twice more from an unmerged branch's leftovers.

The fix is a marker file rather than "any directory". Both halves are gated
here, because narrowing a gate is how you turn a false failure into a silent
pass: a directory with NO marker is ignored, and a directory with EITHER
marker is still reported when its README is missing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "copy_examples",
    Path(__file__).parent.parent / "scripts" / "copy_examples.py",
)
CE = importlib.util.module_from_spec(_SPEC)
sys.modules["copy_examples"] = CE
_SPEC.loader.exec_module(CE)


class TestWhatCountsAsAnExample:
    def test_a_pycache_only_shell_is_not_an_example(self, tmp_path):
        ghost = tmp_path / "cut_example"
        (ghost / "__pycache__").mkdir(parents=True)
        (ghost / "__pycache__" / "test.cpython-312.pyc").write_bytes(b"")
        assert not CE._is_example(ghost)

    def test_an_entirely_empty_directory_is_not_an_example(self, tmp_path):
        empty = tmp_path / "cut_example"
        empty.mkdir()
        assert not CE._is_example(empty)

    @pytest.mark.parametrize("marker", ["test.py", "assemble.py"])
    def test_either_marker_makes_it_one(self, tmp_path, marker):
        """The guard against over-narrowing.

        The reconciler's stated promise is that neither a new example nor a
        deleted one slips through silently. Keying on `test.py` alone would
        make a half-created example invisible instead of flagged -- trading a
        false failure for a false pass, which is the worse of the two.
        """
        d = tmp_path / "new_example"
        d.mkdir()
        (d / marker).write_text("", encoding="utf-8")
        assert CE._is_example(d)

    def test_underscore_and_dot_dirs_are_still_skipped(self, tmp_path):
        for name in ("_private", ".hidden"):
            d = tmp_path / name
            d.mkdir()
            (d / "test.py").write_text("", encoding="utf-8")
            assert not CE._is_example(d)


class TestTheReconcilerAgrees:
    """The behaviour the shells actually broke."""

    def test_a_ghost_produces_no_complaint(self):
        """A shell is absent from `dirs`, so nothing is said about it.

        `_reconcile` takes the mapping rather than reading the disk, so this
        is the real function under the real inputs.
        """
        dirs = {name: True for name in CE.GALLERY}
        dirs.update({name: False for name in CE.UNPUBLISHED})
        assert CE._reconcile(dirs) == []

    def test_a_real_example_without_a_readme_is_still_reported(self):
        """The half that must NOT be narrowed away."""
        dirs = {name: True for name in CE.GALLERY}
        dirs.update({name: False for name in CE.UNPUBLISHED})
        dirs["brand_new"] = False
        problems = CE._reconcile(dirs)
        assert any("brand_new" in p for p in problems), problems


def test_the_repo_itself_is_in_sync():
    """Never vacuous: the checkout this runs in must have no ghosts.

    If a shell is present here, `_example_dirs` reading the real tree fails
    even though every synthetic case above passes.
    """
    assert CE._reconcile(CE._example_dirs()) == []
