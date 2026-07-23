"""gh-557 — a `# jm:hand` member keeps the top-of-file import it references.

The `.pyi` splice engine (gh-428) preserves a `# jm:hand`-marked member's text
across a regen but never touched the top-of-file imports. A hand member using a
non-builtin name therefore lost the import that binds it — doppler's
`Sequence[int]` stub silently lost `from collections.abc import Sequence` on the
next `jm apply`, leaving an unresolved name with no error (exactly the silent
stub break the stub-conformance gate exists to catch, on the one path — a
hand-transplanted member — that the gate does not generate).

The fix is bounded on both sides: `_splice_manual_stub_bodies` reinstates an old
import only when (a) a transplanted hand member references a name it binds and
(b) the fresh render does not already emit it — so a genuinely-dropped import is
not resurrected, and jm's own imports are never duplicated.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._property import run as property_run


def _scaffold(tmp_path):
    dest = tmp_path / "dsp"
    new_run("dsp", dest)
    module_run(dest, "sig")
    object_run(dest, "fft", "sig", state_vars=[("gain", "double", "1.0")])
    return dest, dest / "src" / "dsp" / "sig" / "sig.pyi"


def _add_hand_member(pyi: Path, import_line: str, member: str) -> None:
    """Hand-add *import_line* at the top and a `# jm:hand` *member* to Fft."""
    text = pyi.read_text(encoding="utf-8")
    text = text.replace(
        "from typing import final",
        f"from typing import final\n{import_line}",
        1,
    )
    text = text.replace(
        "    def reset(self)",
        f"    # jm:hand\n    {member}\n\n    def reset(self)",
        1,
    )
    pyi.write_text(text, encoding="utf-8")


class TestReferencedImportSurvives:
    def test_import_preserved_across_apply(self, tmp_path):
        dest, pyi = _scaffold(tmp_path)
        _add_hand_member(
            pyi,
            "from collections.abc import Sequence",
            "def add_batch(self, xs: Sequence[int]) -> None: ...",
        )
        # A manifest change forces a real whole-file regen.
        property_run(dest, "fft", "other", "sig", "size_t", False)
        apply_run(dest)

        after = pyi.read_text(encoding="utf-8")
        assert "from collections.abc import Sequence" in after
        assert "def add_batch(self, xs: Sequence[int]) -> None: ..." in after
        # No unresolved name: the stub parses and Sequence is imported.
        ast.parse(after)

    def test_apply_is_idempotent(self, tmp_path):
        dest, pyi = _scaffold(tmp_path)
        _add_hand_member(
            pyi,
            "from collections.abc import Sequence",
            "def add_batch(self, xs: Sequence[int]) -> None: ...",
        )
        apply_run(dest)
        first = pyi.read_text(encoding="utf-8")
        apply_run(dest)
        assert pyi.read_text(encoding="utf-8") == first
        # exactly one copy of the reinstated import
        assert first.count("from collections.abc import Sequence") == 1

    def test_attribute_root_is_counted(self, tmp_path):
        """A member using `deque.something` keeps `from collections import
        deque` — the reference is the root of an attribute chain, not a bare
        name."""
        dest, pyi = _scaffold(tmp_path)
        _add_hand_member(
            pyi,
            "from collections import deque",
            "def buf(self) -> deque[int]: ...",
        )
        apply_run(dest)
        after = pyi.read_text(encoding="utf-8")
        assert "from collections import deque" in after


class TestBounded:
    def test_unreferenced_hand_import_is_not_resurrected(self, tmp_path):
        """An import no transplanted member uses is left to the fresh render
        (jm dropped it for a reason); the splicer must not bring it back."""
        dest, pyi = _scaffold(tmp_path)
        # A hand member that references nothing non-builtin, plus a stray
        # hand-added import it does not use.
        text = pyi.read_text(encoding="utf-8")
        text = text.replace(
            "from typing import final",
            "from typing import final\nimport os",
            1,
        )
        text = text.replace(
            "    def reset(self)",
            "    # jm:hand\n    def noop(self) -> None: ...\n\n    def reset(self)",
            1,
        )
        pyi.write_text(text, encoding="utf-8")
        apply_run(dest)
        after = pyi.read_text(encoding="utf-8")
        assert "def noop(self) -> None: ..." in after  # member kept
        assert "\nimport os" not in after  # unreferenced import dropped

    def test_import_jm_already_emits_is_not_duplicated(self, tmp_path):
        """A hand member referencing `np` must not add a second numpy import —
        the fresh render already emits `import numpy as np`."""
        dest, pyi = _scaffold(tmp_path)
        _add_hand_member(
            pyi,
            "",  # no extra import; np is jm's own
            "def scaled(self) -> np.float64: ...",
        )
        # remove the empty line the "" import left
        pyi.write_text(
            pyi.read_text().replace(
                "from typing import final\n\n", "from typing import final\n"
            )
        )
        apply_run(dest)
        after = pyi.read_text(encoding="utf-8")
        assert after.count("import numpy as np") == 1
