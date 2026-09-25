"""gh-1447 ask 3: `status` told the author to delete a working feature.

ACTIONABLE printed one piece of advice for every entry -- "the manifest
moved and these did not ... Delete the file and re-run `jm apply`" -- and
listed, among them, doppler's

    Resampler.execute_ctrl: binding "OO|O" vs manifest "OO"
    Farrow.delay:           binding "Od|O" vs manifest "Od"

There the BINDING is the superset: it accepts an `out=` the manifest never
declared. Following the advice deletes the `out=`. The comparison already
knew which side was bigger -- `_adopt.binding_ahead` refuses a flip on
exactly this -- and the advice never asked it.

The fix reads that one predicate, not a second copy of it: a fragment with
any member ahead leaves ACTIONABLE for BINDING AHEAD, which says the
manifest is behind and how to declare what the file accepts.

GATE: `status` never advises deleting a binding fragment that accepts
      arguments the manifest does not declare.
"""

from __future__ import annotations

from pathlib import Path

from _statusfaces import assert_status_faces_agree
from _jmrun import run_cli

FRAG = Path("native") / "src" / "dsp" / "dsp_ext_blk.c"


def _module_project(tmp_path: Path) -> Path:
    """A module object whose `execute` renders `"O|O"` (input, `out=`)."""
    root = tmp_path / "w"
    root.mkdir()
    assert run_cli("new", "demo", cwd=root).returncode == 0
    proj = root / "demo"
    assert run_cli("module", "dsp", cwd=proj).returncode == 0
    assert (
        run_cli(
            "object",
            "blk",
            "--module",
            "dsp",
            "--no-state",
            "--no-step",
            "--init-param",
            "n:size_t:16",
            cwd=proj,
        ).returncode
        == 0
    )
    assert (
        run_cli(
            "method",
            "blk",
            "execute",
            "--module",
            "dsp",
            "--arg-type",
            "float _Complex",
            "--return-type",
            "float _Complex",
            "--variable-output",
            cwd=proj,
        ).returncode
        == 0
    )
    assert run_cli("apply", cwd=proj).returncode == 0
    return proj


def _set_execute_format(proj: Path, fmt: str) -> None:
    """Rewrite the `execute` wrapper's PyArg format on disk.

    Anchored on the rendered format with its call, and required to match
    exactly once, so a codegen change that moves it fails here loudly
    rather than leaving the fixture unedited.
    """
    frag = proj / FRAG
    s = frag.read_text()
    old = 'PyArg_ParseTupleAndKeywords(args, kwds, "O|O"'
    assert s.count(old) == 1, "fixture no longer renders execute as O|O"
    frag.write_text(
        s.replace(old, f'PyArg_ParseTupleAndKeywords(args, kwds, "{fmt}"')
    )


def _unreconciled(out: str) -> str:
    start = out.index("UNRECONCILED")
    return out[start : out.index("What apply will not do", start)]


class TestABindingAheadIsNotDeleted:
    def test_no_delete_advice_for_a_superset_binding(self, tmp_path):
        """doppler's `execute_ctrl`: the file takes one more optional."""
        proj = _module_project(tmp_path)
        _set_execute_format(proj, "O|OO")
        block = _unreconciled(run_cli("status", cwd=proj).stdout)
        assert "Delete the file" not in block, block
        assert "ACTIONABLE" not in block, block
        assert "BINDING AHEAD (1)" in block, block
        assert "MANIFEST is behind" in block, block
        assert str(FRAG.as_posix()) in block, block
        # ...and it says WHICH member, with the evidence.
        assert '"O|OO"' in block and "(binding ahead)" in block, block
        assert_status_faces_agree(proj, "binding_ahead")


class TestTheManifestAheadStillSaysDelete:
    def test_a_subset_binding_is_still_actionable(self, tmp_path):
        """The converse is an ordinary undelivered fix.

        Without this, "never say delete" would pass the test above too.
        """
        proj = _module_project(tmp_path)
        _set_execute_format(proj, "O")
        block = _unreconciled(run_cli("status", cwd=proj).stdout)
        assert "ACTIONABLE (1)" in block, block
        assert "Delete the file" in block, block
        assert "BINDING AHEAD" not in block, block
        assert_status_faces_agree(proj, "actionable")
