"""gh-609: `jm apply` silently overwrote a hand-edited generated ``_step``
body with no explanation, because the header gave no hint that its body was
actually a build product of the manifest's ``impl``/``impl_file`` key.

Fixes, all in `_patch_step_impls` / `_impl_marker` (`_apply.py`):

1. The injected body always carries a one-line provenance comment
   (`_impl_marker`) naming the file and manifest key it came from.
2. `_impl_marker` names the FILE THAT ACTUALLY OWNS ``[comp]`` — the
   top-level manifest for a flat project, or ``objects/<comp>.toml`` once
   `jm split-objects` has moved the section — via `C._provenance`, the same
   owner-tracking `save()` uses, rather than a hardcoded generic phrase.
3. When the on-disk body about to be overwritten differs in CONTENT (marker
   line ignored) from what the manifest now says AND isn't the untouched
   fresh-scaffold TODO stub (`_remove._STUB_MARKER`), `apply` prints a
   warning identifying the file and component before overwriting it —
   instead of silently reverting a hand-edit (or reapplying a changed
   `impl`) with no explanation. Comparing CONTENT rather than marker-prefixed
   text matters: the marker is new/changed text on its own (first adoption,
   or a reworded owner path after `split-objects`), so a naive text compare
   would warn once, falsely, on every already-in-sync component.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import _impl_marker
from just_makeit._apply import _MARKER_LINE_RE
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
def scaler_project(tmp_path):
    """A scaffolded project + fragment, NOT yet applied.

    Kept separate from `project_with_impl` (below) so a test that needs to
    observe the FIRST `apply_run` under an already-active `capsys` — e.g.
    "no warning on first materialization" — can call `apply_run` itself,
    inside the test body, rather than relying on a fixture that ran (and
    printed to the real, uncaptured stderr) before `capsys` started
    capturing."""
    from just_makeit._new import run as new_run

    root = tmp_path / "proj"
    new_run("proj", root)
    frag = tmp_path / "scaler.toml"
    frag.write_text(_OBJECT_FRAGMENT)
    return root, frag


@pytest.fixture()
def project_with_impl(scaler_project):
    root, frag = scaler_project
    apply_run(root, fragment=frag)
    return root


@pytest.fixture()
def scaler_project_no_impl(tmp_path):
    """A scaffolded, impl-less ``scaler`` object: its ``_step`` body is still
    the fresh-scaffold TODO stub (`_remove._STUB_MARKER`). This is the actual
    state the stub guard in `_patch_step_impls` exists for — `impl` gets
    added to the manifest well after the object was first scaffolded (the
    common real-world order: scaffold now, write the DSP body later), so the
    very first `apply` that patches it in is filling an empty stub, not
    reverting a hand-edit. (`project_with_impl`, by contrast, composes a
    fragment that already carries `impl` — `_replay` bakes the real body in
    immediately, so the on-disk header there never passes through a stub
    state at all, and can't exercise this guard.)"""
    from just_makeit._new import run as new_run
    from just_makeit._object import run as object_run

    root = tmp_path / "proj"
    new_run("proj", root)
    object_run(root, "scaler", None, state_vars=[("gain", "float", "1.0f")])
    return root


def _core_h(root: Path) -> Path:
    return root / "native" / "inc" / "scaler" / "scaler_core.h"


class TestMarkerComment:
    def test_marker_present_after_first_apply(self, project_with_impl, capsys):
        text = _core_h(project_with_impl).read_text(encoding="utf-8")
        assert _impl_marker("scaler", project_with_impl) in text

    def test_no_warning_on_first_materialization(
        self, scaler_project_no_impl, capsys
    ):
        # gh-609 review: the original version of this test took
        # `project_with_impl` (which itself calls `apply_run`) as a fixture
        # parameter listed BEFORE `capsys` — fixtures are resolved in
        # declaration order, so `project_with_impl`'s `apply_run` call, and
        # everything it printed, happened before `capsys` started capturing.
        # `captured.err` therefore read back `""` regardless of whether the
        # warning-suppression logic even ran. Worse, that fixture's fragment
        # already carries `impl`, so `_replay` bakes the real body in before
        # the on-disk header is ever written — the stub state this guard
        # exists for never even occurs on that path, so the test could not
        # have exercised the guard either way. Deleting the `_STUB_MARKER`
        # guard in `_patch_step_impls` left the original test green either
        # way (confirmed by the red/green check accompanying this fix).
        #
        # This version calls `apply_run` in the test body, with `capsys`
        # already active, against a project whose object was scaffolded
        # WITHOUT `impl` — so its on-disk `_step` body genuinely is the
        # untouched TODO stub when `impl` is added and applied for the first
        # time. That is the guard's actual job: filling in a stub is routine
        # first-time materialization, not overwriting someone's work.
        from just_makeit._remove import _STUB_MARKER

        root = scaler_project_no_impl
        h_path = _core_h(root)
        assert _STUB_MARKER in h_path.read_text(encoding="utf-8")

        cfg = C.load(root)
        cfg["scaler"]["impl"] = "return (float _Complex)(state->gain * x);\n"
        C.save(root, cfg)
        capsys.readouterr()  # discard the scaffold/save's own stdout

        apply_run(root)

        captured = capsys.readouterr()
        assert "warning" not in captured.err
        assert _STUB_MARKER not in h_path.read_text(encoding="utf-8")

    def test_reapply_is_idempotent_and_quiet(self, project_with_impl, capsys):
        before = _core_h(project_with_impl).read_text(encoding="utf-8")
        capsys.readouterr()  # discard the first apply's output
        apply_run(project_with_impl)
        after = _core_h(project_with_impl).read_text(encoding="utf-8")
        assert before == after
        captured = capsys.readouterr()
        assert "warning" not in captured.err


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
        assert "warning" in captured.err
        assert "scaler_core.h" in captured.err
        assert "[scaler] impl/impl_file" in captured.err

        # The manifest is the source of truth: the hand-edit is gone.
        reverted = h_path.read_text(encoding="utf-8")
        assert "oops" not in reverted
        assert "state->gain * x" in reverted
        assert _impl_marker("scaler", project_with_impl) in reverted

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
        assert "warning" in captured.err
        text = _core_h(project_with_impl).read_text(encoding="utf-8")
        assert "3.0f * state->gain * x" in text

    def test_marker_upgrade_matches_content_does_not_warn(
        self, project_with_impl, capsys
    ):
        # gh-609 review: a project that already had a hand-authored `impl`
        # body BEFORE this feature existed adopts it the first time apply
        # runs after an upgrade — the on-disk body gains the marker, but its
        # CODE was already exactly what the manifest says. That is not a
        # divergence to warn about; only the marker line itself is new. The
        # naive fix (comparing marker-prefixed text) warns here regardless,
        # once, on every such component — this is the regression test for
        # that false positive.
        h_path = _core_h(project_with_impl)
        marker = _impl_marker("scaler", project_with_impl)
        text = h_path.read_text(encoding="utf-8")
        assert marker in text
        # Simulate "no marker yet, but the code already matches" by
        # stripping the marker line the first apply added, leaving the
        # underlying `return (float _Complex)(state->gain * x);` untouched.
        # `_MARKER_LINE_RE` (not a plain string replace) is what
        # `_patch_step_impls` itself uses to strip the marker before
        # comparing content, so it is also what correctly removes the
        # marker's own leading indentation here without disturbing the
        # following line.
        unmarked = _MARKER_LINE_RE.sub("", text, count=1)
        assert marker not in unmarked
        assert "state->gain * x" in unmarked
        h_path.write_text(unmarked, encoding="utf-8")
        capsys.readouterr()  # discard the first apply's own output

        apply_run(project_with_impl)

        captured = capsys.readouterr()
        assert "warning" not in captured.err
        # The marker is still (re-)added even though nothing warned.
        after = h_path.read_text(encoding="utf-8")
        assert marker in after


@pytest.fixture()
def flat_project_with_impl(tmp_path):
    """A project with ``[scaler]`` written directly into the top-level
    manifest — no `include`, no `objects/` fragment. `apply_run(root,
    fragment=...)` (used by `project_with_impl` above) always composes via
    `_compose_fragment`, which copies straight into `objects/` and adds the
    `include` glob — so that fixture is already split-layout by construction
    and cannot exercise the flat-manifest case at all. This fixture writes
    the section into `just-makeit.toml` itself, matching how a project looks
    before anyone has ever run `jm split-objects`."""
    from just_makeit._new import run as new_run

    root = tmp_path / "proj"
    new_run("proj", root)
    cfg = C.load(root)
    cfg["scaler"] = {
        "arg_type": "float _Complex",
        "return_type": "float _Complex",
        "mutable": "false",
        "no_state": "false",
        "no_step": "false",
        "impl": "return (float _Complex)(state->gain * x);\n",
        "state": [{"name": "gain", "type": "float", "default": "1.0f"}],
    }
    C.save(root, cfg)
    apply_run(root)
    return root


class TestMarkerNamesOwningFile:
    """gh-609 review: `_impl_marker` must name whichever file actually holds
    ``[comp]`` — the flat manifest, or (once `jm split-objects` has run)
    ``objects/<comp>.toml`` — not a hardcoded generic phrase. A hardcoded
    phrase is exactly the discoverability gap gh-609 was filed over: it
    points a reader at a file that, in the split layout, doesn't contain the
    `impl`/`impl_file` key at all."""

    def test_marker_names_flat_manifest(self, flat_project_with_impl):
        marker = _impl_marker("scaler", flat_project_with_impl)
        assert "just-makeit.toml" in marker
        assert "objects/" not in marker

    def test_marker_names_split_layout_fragment(self, flat_project_with_impl):
        from just_makeit._split_objects import run as split_run

        split_run(flat_project_with_impl)
        marker = _impl_marker("scaler", flat_project_with_impl)
        assert "objects/scaler.toml" in marker

    def test_reapply_after_split_names_fragment_in_warning(
        self, flat_project_with_impl, capsys
    ):
        from just_makeit._split_objects import run as split_run

        split_run(flat_project_with_impl)
        h_path = _core_h(flat_project_with_impl)
        original = h_path.read_text(encoding="utf-8")
        capsys.readouterr()  # discard split's own output, if any
        hand_edited = original.replace(
            "return (float _Complex)(state->gain * x);",
            "return (float _Complex)(2.0f * state->gain * x); /* oops */",
        )
        h_path.write_text(hand_edited, encoding="utf-8")

        apply_run(flat_project_with_impl)

        captured = capsys.readouterr()
        assert "warning" in captured.err
        assert "objects/scaler.toml" in captured.err


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
        assert "warning" not in captured.err
        assert drift_count >= 1
