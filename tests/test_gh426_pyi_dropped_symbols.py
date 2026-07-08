"""
gh-426 — `jm status` must loudly flag a `.pyi` symbol (class/method/function)
that exists on disk with zero manifest trace and silently vanishes in what
`apply` would regenerate. Unlike ordinary STALE drift, a drop is real
content loss, not routine reformatting, and must never be suppressed by
`--allow` / `[project] status_allow`.

Repro: a hand-written method (e.g. doppler's `Fft.execute_ci16`, added
directly to a sacred `_ext_<obj>.c` fragment with a matching hand-added
`.pyi` stub) has no `[[obj.methods]]` entry anywhere — jm has no way to
know it exists — so a regen drops the stub with no other signal.
"""

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit import _status
from just_makeit import _config as C


def _run(root, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _status.run(root, **kw)
    return rc, buf.getvalue()


@pytest.fixture()
def hand_dropped(tmp_path):
    """A .pyi with a hand-written method that has no manifest declaration."""
    root = tmp_path / "proj"
    new_run("proj", root, object_names=["fft"])
    pyi = root / "src/proj/fft.pyi"
    text = pyi.read_text(encoding="utf-8")
    # Splice a hand-written method into the class body -- valid Python,
    # exactly what a human editing the generated .pyi by hand would add.
    marker = "    def destroy(self) -> None:"
    assert marker in text
    text = text.replace(
        marker,
        "    def execute_ci16(self, x) -> None:\n"
        '        """Hand-written int16 overload; no manifest entry."""\n\n'
        + marker,
        1,
    )
    pyi.write_text(text, encoding="utf-8")
    return root


class TestDroppedSymbolDetection:
    def test_reports_dropped_section(self, hand_dropped):
        rc, out = _run(hand_dropped)
        assert "DROPPED (1)" in out
        assert "src/proj/fft.pyi" in out
        assert "Fft.execute_ci16" in out
        assert rc >= 1

    def test_not_suppressed_by_allow_flag(self, hand_dropped):
        rc, out = _run(hand_dropped, allow=("src/proj/fft.pyi",))
        # The file itself is allowed (routine drift suppressed)...
        assert "ALLOWED (1)" in out
        # ...but the drop is still loudly reported and still counted.
        assert "DROPPED (1)" in out
        assert "Fft.execute_ci16" in out
        assert rc >= 1

    def test_not_suppressed_by_manifest_status_allow(self, hand_dropped):
        cfg = C.load(hand_dropped)
        cfg.setdefault("project", {})["status_allow"] = ["src/proj/fft.pyi"]
        C.save(hand_dropped, cfg)
        rc, out = _run(hand_dropped)
        assert "DROPPED (1)" in out
        assert rc >= 1

    def test_dropped_section_survives_check_mode(self, hand_dropped):
        # --check collapses MISSING/STALE/ALLOWED to a one-line summary,
        # but DROPPED must stay visible -- it's the whole point of gh-426.
        rc, out = _run(hand_dropped, check=True)
        assert "STALE (" not in out
        assert "DROPPED (1)" in out
        assert "Fft.execute_ci16" in out
        assert rc >= 1

    def test_json_includes_dropped_symbols(self, hand_dropped):
        rc, out = _run(hand_dropped, as_json=True)
        data = json.loads(out)
        entry = next(
            e for e in data["entries"] if e["path"].endswith("fft.pyi")
        )
        assert entry["dropped_symbols"] == ["Fft.execute_ci16"]
        assert data["dropped_files"] == 1
        assert rc >= 1

    def test_json_dropped_survives_allow(self, hand_dropped):
        rc, out = _run(hand_dropped, allow=("src/proj/fft.pyi",), as_json=True)
        data = json.loads(out)
        entry = next(
            e for e in data["entries"] if e["path"].endswith("fft.pyi")
        )
        assert entry["allowed"] is True
        assert entry["dropped_symbols"] == ["Fft.execute_ci16"]
        assert data["drift"] >= 1


class TestNoFalsePositive:
    def test_ordinary_pyi_drift_has_no_dropped_symbols(self, tmp_path):
        # A plain hand-edit (e.g. a stray comment) must not be misreported
        # as a symbol drop -- only an actual vanished class/def counts.
        root = tmp_path / "proj"
        new_run("proj", root, object_names=["widget"])
        pyi = root / "src/proj/widget.pyi"
        pyi.write_text(
            pyi.read_text(encoding="utf-8") + "\n# hand drift\n",
            encoding="utf-8",
        )
        rc, out = _run(root)
        assert "STALE (1)" in out
        assert "DROPPED" not in out

    def test_clean_project_reports_ok(self, tmp_path):
        root = tmp_path / "proj"
        new_run("proj", root, object_names=["widget"])
        rc, out = _run(root)
        assert rc == 0
        assert "up to date" in out
        assert "DROPPED" not in out
