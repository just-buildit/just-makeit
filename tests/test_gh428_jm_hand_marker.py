"""gh-428 (re-scoped) — `# jm:hand` marker: member-level `.pyi` merge with
zero manifest declaration required.

Field data from doppler (see gh-428/gh-441 discussion): `manual_stub = true`
(the original gh-428 fix) requires a manifest entry, which only covers a
method with no manifest-representable signature at all. The much more common
case is a manifest-derived member (a property, a regular method) whose
generated docstring/signature the user then hand-improves in place — that
member has a real manifest entry, but the *hand edit* itself has none, and
used to be silently clobbered on every regen once `status_allow` (gh-441)
stopped being the workaround (allowlisting the whole file also stops new
manifest-derived content from ever reaching it).

A `# jm:hand` comment directly above a class member opts it out of
regeneration without needing `manual_stub = true` or any other manifest
entry: it works for a manifest-derived member (hand edit replaces the fresh
render in place) and for a member with zero manifest backing (hand text is
appended after the class's last member, since jm never renders a placeholder
for it to land on).

Uses a module-owned object (`--module`), matching every real doppler
fixture named in gh-428/gh-440/gh-441 (dsss/track/etc. are all modules of
several objects, never standalone) -- the module-aggregated `.pyi` generator
(`_stubs.make_module_pyi`/`_obj_stub`) is also the one that already reflects
a newly added property immediately, unlike the standalone generator.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._module import run as module_run
from just_makeit._object import run as object_run
from just_makeit._property import run as property_run
from just_makeit._apply import run as apply_run
from just_makeit import _status


def _scaffold(tmp_path):
    dest = tmp_path / "dsp"
    new_run("dsp", dest)
    module_run(dest, "sig")
    object_run(dest, "fft", "sig")
    return dest


def _pyi_path(dest):
    return dest / "src" / "dsp" / "sig" / "sig.pyi"


class TestMarkedPropertySurvivesInPlace:
    def test_getter_and_setter_move_as_one_unit(self, tmp_path):
        dest = _scaffold(tmp_path)
        property_run(dest, "fft", "scale", "sig", "float", True)
        pyi_path = _pyi_path(dest)
        text = pyi_path.read_text(encoding="utf-8")
        assert "def scale(self) -> float:" in text

        hand = (
            "    # jm:hand\n"
            "    @property\n"
            "    def scale(self) -> float:\n"
            '        """Hand-improved: normalized gain, 0.0-1.0."""\n'
            "    @scale.setter\n"
            "    def scale(self, value: float) -> None:\n"
            "        # extra hand-written validation note\n"
            "        ...\n"
        )
        # Replace the generated getter+setter pair wholesale with the
        # hand-marked version above.
        m = re.search(
            r"    @property\n    def scale.*?"
            r"def scale\(self, value: float\) -> None: \.\.\.\n",
            text,
            re.DOTALL,
        )
        assert m, text
        text = text[: m.start()] + hand + text[m.end() :]
        pyi_path.write_text(text, encoding="utf-8")

        # Unrelated manifest change forces a real, whole-file regen.
        property_run(dest, "fft", "other", "sig", "size_t", False)
        apply_run(dest)

        after = pyi_path.read_text(encoding="utf-8")
        assert "Hand-improved: normalized gain" in after
        assert "extra hand-written validation note" in after
        assert "# jm:hand" in after
        # Marker survives -> a second apply is a no-op for this member.
        apply_run(dest)
        assert pyi_path.read_text(encoding="utf-8") == after


class TestMarkedZeroManifestMemberAppended:
    def test_hand_added_method_survives_and_not_dropped(
        self, tmp_path, capsys
    ):
        dest = _scaffold(tmp_path)
        pyi_path = _pyi_path(dest)
        text = pyi_path.read_text(encoding="utf-8")
        assert "execute_ci16" not in text

        # A hand-added overload with zero manifest representation -- the
        # exact gh-426 repro shape (Fft.execute_ci16), this time surviving
        # via the marker instead of a manual_stub manifest entry.
        added = (
            "\n"
            "    # jm:hand\n"
            "    def execute_ci16(self, x: object) -> object:\n"
            '        """Hand-written int16-complex overload."""\n'
            "        ...\n"
        )
        text = text.rstrip("\n") + "\n" + added
        pyi_path.write_text(text, encoding="utf-8")

        apply_run(dest)
        after = pyi_path.read_text(encoding="utf-8")
        assert "execute_ci16" in after
        assert "Hand-written int16-complex overload" in after

        # Idempotent: re-applying from the same manifest reproduces it
        # byte-for-byte rather than duplicating the appended block.
        apply_run(dest)
        assert pyi_path.read_text(encoding="utf-8") == after

        capsys.readouterr()
        rc = _status.run(dest)
        out = capsys.readouterr().out
        assert "DROPPED" not in out
        assert rc == 0


class TestUnmarkedMemberStillRegenerates:
    def test_unmarked_hand_edit_is_overwritten(self, tmp_path):
        # Sanity guard: the marker is opt-in. A hand edit with no `#
        # jm:hand` comment above it is NOT preserved -- confirms the new
        # member-merge machinery doesn't accidentally start preserving
        # everything.
        dest = _scaffold(tmp_path)
        property_run(dest, "fft", "scale", "sig", "float", True)
        pyi_path = _pyi_path(dest)
        text = pyi_path.read_text(encoding="utf-8")
        text = text.replace(
            '"""Scale."""', '"""Hand-edited but NOT marked."""'
        )
        pyi_path.write_text(text, encoding="utf-8")

        property_run(dest, "fft", "other", "sig", "size_t", False)
        apply_run(dest)

        after = pyi_path.read_text(encoding="utf-8")
        assert "Hand-edited but NOT marked" not in after
