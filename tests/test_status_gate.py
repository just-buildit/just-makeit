"""gh-140: `jm status` as a CI drift gate — --allow / --json / --diff / --check.

`_status.run` already builds a throwaway apply and diffs it against the tree;
these options surface that result for CI: an allowlist of known-accepted
deviations (CLI + `[project] status_allow`), a machine-readable report, and a
unified diff per stale file. The return value counts only non-allowed drift so
a pipeline fails exactly on real drift.
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


@pytest.fixture()
def drifted(tmp_path):
    """A project whose .pyi glue file has been hand-edited (apply would fix it)."""
    root = tmp_path / "proj"
    new_run("proj", root, object_names=["widget"])
    pyi = root / "src/proj/widget.pyi"
    pyi.write_text(pyi.read_text() + "\n# hand drift\n", encoding="utf-8")
    return root


def _run(root, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _status.run(root, **kw)
    return rc, buf.getvalue()


def test_drift_detected_by_default(drifted):
    rc, out = _run(drifted)
    assert rc == 1
    assert "STALE (1)" in out
    assert "src/proj/widget.pyi" in out


def test_allow_flag_excludes_from_count(drifted):
    rc, out = _run(drifted, allow=("src/proj/widget.pyi",))
    assert rc == 0
    assert "ALLOWED (1)" in out
    assert "1 allowed" in out


def test_allow_glob(drifted):
    rc, _ = _run(drifted, allow=("src/proj/*.pyi",))
    assert rc == 0


def test_project_status_allow_from_manifest(drifted):
    cfg = C.load(drifted)
    cfg.setdefault("project", {})["status_allow"] = ["src/proj/widget.pyi"]
    C.save(drifted, cfg)
    rc, out = _run(drifted)
    assert rc == 0
    assert "ALLOWED (1)" in out


def test_json_report(drifted):
    rc, out = _run(drifted, as_json=True)
    assert rc == 1
    data = json.loads(out)
    assert data["drift"] == 1
    entry = next(
        e for e in data["entries"] if e["path"].endswith("widget.pyi")
    )
    assert entry["state"] == "stale"
    assert entry["allowed"] is False


def test_json_marks_allowed(drifted):
    rc, out = _run(drifted, allow=("src/proj/widget.pyi",), as_json=True)
    assert rc == 0
    data = json.loads(out)
    assert data["drift"] == 0
    assert data["entries"][0]["allowed"] is True


def test_diff_shows_unified_diff(drifted):
    _, out = _run(drifted, show_diff=True)
    assert "--- a/src/proj/widget.pyi" in out
    assert "+++ b/src/proj/widget.pyi" in out
    assert "# hand drift" in out


def test_check_suppresses_listing(drifted):
    _, out = _run(drifted, check=True)
    assert "STALE (" not in out
    assert "summary:" in out  # one-line summary still printed


def test_clean_project_returns_zero(tmp_path):
    root = tmp_path / "clean"
    new_run("clean", root, object_names=["widget"])
    rc, out = _run(root)
    assert rc == 0
    assert "OK — up to date" in out
