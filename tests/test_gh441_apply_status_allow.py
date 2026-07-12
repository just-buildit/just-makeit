"""gh-441: `jm apply` must never clobber a `[project] status_allow` file.

`jm status --check` already treats a status_allow-matched file as allowed
drift rather than failing the gate, but `apply`'s reconcile step re-renders
every glue file unconditionally, so a bare `jm apply` was silently
overwriting hand-maintained content the manifest explicitly marks as
excluded from the templated shape. `jm status` still needs to see the
*real* diff internally (its throwaway replay classifies ALLOWED vs STALE
by comparing genuine before/after content), so the skip only applies to
apply's real, on-disk write path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _status  # noqa: E402


def _hand_edit(pyi: Path) -> str:
    text = pyi.read_text(encoding="utf-8") + "\n# hand-maintained note\n"
    pyi.write_text(text, encoding="utf-8")
    return text


def test_apply_skips_status_allow_file(tmp_path):
    root = tmp_path / "proj"
    new_run("proj", root, object_names=["widget"])
    cfg = C.load(root)
    cfg.setdefault("project", {})["status_allow"] = ["src/proj/widget.pyi"]
    C.save(root, cfg)

    pyi = root / "src/proj/widget.pyi"
    hand_text = _hand_edit(pyi)

    apply_run(root)

    assert pyi.read_text(encoding="utf-8") == hand_text


def test_apply_still_overwrites_non_allowed_file(tmp_path):
    root = tmp_path / "proj"
    new_run("proj", root, object_names=["widget"])
    pyi = root / "src/proj/widget.pyi"
    _hand_edit(pyi)

    apply_run(root)

    assert "# hand-maintained note" not in pyi.read_text(encoding="utf-8")


def test_status_still_classifies_allowed_via_genuine_diff(tmp_path):
    root = tmp_path / "proj"
    new_run("proj", root, object_names=["widget"])
    cfg = C.load(root)
    cfg.setdefault("project", {})["status_allow"] = ["src/proj/widget.pyi"]
    C.save(root, cfg)
    _hand_edit(root / "src/proj/widget.pyi")

    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _status.run(root)
    assert rc == 0
    assert "ALLOWED (1)" in buf.getvalue()
