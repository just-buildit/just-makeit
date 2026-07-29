"""gh-609: `jm apply` silently overwrote a hand-edited generated ``_step``
body with no explanation, because the header gave no hint that its body was
actually a build product of the manifest's ``impl``/``impl_file`` key.

Two fixes, both in `_patch_step_impls` (`_apply.py`):

1. The injected body always carries a one-line provenance comment
   (`_impl_marker`) naming the manifest key it came from.
2. When the on-disk body about to be overwritten differs from what the
   manifest now says AND isn't the untouched fresh-scaffold TODO stub
   (`_remove._STUB_MARKER`), `apply` prints a warning identifying the file
   and component before overwriting it — instead of silently reverting a
   hand-edit (or reapplying a changed `impl`) with no explanation.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import _impl_marker
from just_makeit._apply import run as apply_run


_OBJECT_FRAGMENT = """\
[scaler]
arg_type = "float _Complex"
return_type = "float _Complex"
mutable = "false"
no_state = "false"
no_step = "false"

impl = '''
return (float _Complex)(state->gain * x);
'''

[[scaler.state]]
name = "gain"
type = "float"
default = "1.0f"
"""


@pytest.fixture()
def project_with_impl(tmp_path):
    from just_makeit._new import run as new_run

    root = tmp_path / "proj"
    new_run("proj", root)
    frag = tmp_path / "scaler.toml"
    frag.write_text(_OBJECT_FRAGMENT)
    apply_run(root, fragment=frag)
    return root


def _core_h(root: Path) -> Path:
    return root / "native" / "inc" / "scaler" / "scaler_core.h"


class TestMarkerComment:
    def test_marker_present_after_first_apply(self, project_with_impl, capsys):
        text = _core_h(project_with_impl).read_text(encoding="utf-8")
        assert _impl_marker("scaler") in text

    def test_no_warning_on_first_materialization(
        self, project_with_impl, capsys
    ):
        # The fresh-scaffold TODO stub existed before this apply patched it —
        # that's routine first-time materialization, not an overwrite of
        # someone's work, so no warning should have printed.
        captured = capsys.readouterr()
        assert "warning:" not in captured.err

    def test_reapply_is_idempotent_and_quiet(self, project_with_impl, capsys):
        before = _core_h(project_with_impl).read_text(encoding="utf-8")
        capsys.readouterr()  # discard the first apply's output
        apply_run(project_with_impl)
        after = _core_h(project_with_impl).read_text(encoding="utf-8")
        assert before == after
        captured = capsys.readouterr()
        assert "warning:" not in captured.err


class TestOverwriteWarning:
    def test_hand_edit_triggers_warning_and_is_reverted(
        self, project_with_impl, capsys
    ):
        h_path = _core_h(project_with_impl)
        original = h_path.read_text(encoding="utf-8")
        capsys.readouterr()  # discard first-apply output
        hand_edited = original.replace(
            "return (float _Complex)(state->gain * x);",
            "return (float _Complex)(2.0f * state->gain * x); /* oops */",
        )
        assert hand_edited != original
        h_path.write_text(hand_edited, encoding="utf-8")

        apply_run(project_with_impl)

        captured = capsys.readouterr()
        assert "warning:" in captured.err
        assert "scaler_core.h" in captured.err
        assert "[scaler] impl/impl_file" in captured.err

        # The manifest is the source of truth: the hand-edit is gone.
        reverted = h_path.read_text(encoding="utf-8")
        assert "oops" not in reverted
        assert "state->gain * x" in reverted
        assert _impl_marker("scaler") in reverted

    def test_manifest_impl_change_also_warns(self, project_with_impl, capsys):
        # Same heuristic fires for a deliberate impl edit + reapply — the
        # function can't distinguish "you edited the header" from "you
        # edited the manifest", and the issue's own proposed fix accepts
        # that (warn whenever a non-stub body is about to change).
        cfg = C.load(project_with_impl)
        cfg["scaler"]["impl"] = (
            "return (float _Complex)(3.0f * state->gain * x);\n"
        )
        C.save(project_with_impl, cfg)
        capsys.readouterr()

        apply_run(project_with_impl)

        captured = capsys.readouterr()
        assert "warning:" in captured.err
        text = _core_h(project_with_impl).read_text(encoding="utf-8")
        assert "3.0f * state->gain * x" in text


class TestStatusCheckDoesNotLeakWarning:
    """gh-609 follow-up: `status --check` replays `apply` against a throwaway
    scratch copy to compute drift. That replay used to leak this warning
    straight to the user's stderr — misleadingly, since `status` never
    touches the real project. It should stay silent and let its own STALE
    report carry the signal instead."""

    def test_hand_edit_reported_as_stale_with_no_warning_leak(
        self, project_with_impl, capsys
    ):
        from just_makeit import _status

        h_path = _core_h(project_with_impl)
        text = h_path.read_text(encoding="utf-8")
        h_path.write_text(
            text.replace(
                "return (float _Complex)(state->gain * x);",
                "return (float _Complex)(2.0f * state->gain * x);",
            ),
            encoding="utf-8",
        )
        capsys.readouterr()  # discard the first apply's own output

        drift_count = _status.run(project_with_impl, check=True)

        captured = capsys.readouterr()
        assert "warning:" not in captured.err
        assert drift_count >= 1
