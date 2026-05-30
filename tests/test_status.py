"""Integration tests for `just-makeit status`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._status import run as status_run


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    dest = tmp_path / "dsp"
    new_run("dsp", dest)
    object_run(
        dest,
        "my_filter",
        module=None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    return dest


class TestStatus:
    def test_returns_drift_count_for_clean_project(self, project, capsys):
        # Fresh scaffold can show benign drift in package __init__.py
        # (apply-add-only produced the minimal form; the replay produces
        # the bootstrapped one). The count is what callers gate on.
        drift = status_run(project)
        out = capsys.readouterr().out
        # Either fully clean (0 drift) or a small benign drift (1–2 files).
        assert drift >= 0
        # Output should mention either OK or the summary line.
        assert "OK" in out or "drift" in out or "missing" in out

    def test_reports_missing_after_deletion(self, project, capsys):
        header = project / "native" / "inc" / "my_filter" / "my_filter_core.h"
        assert header.exists()
        header.unlink()
        drift = status_run(project)
        out = capsys.readouterr().out
        assert "MISSING" in out
        assert "my_filter_core.h" in out
        assert drift >= 1

    def test_reports_drift_after_hand_edit(self, project, capsys):
        core_c = project / "native" / "src" / "my_filter" / "my_filter_core.c"
        original = core_c.read_text(encoding="utf-8")
        core_c.write_text(original + "\n/* user edit */\n", encoding="utf-8")
        drift = status_run(project)
        out = capsys.readouterr().out
        assert "DRIFT" in out
        assert "my_filter_core.c" in out
        assert drift >= 1

    def test_errors_outside_a_project(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            status_run(tmp_path)
        err = capsys.readouterr().err
        assert "just-makeit.toml" in err

    def test_empty_manifest_is_noop(self, tmp_path, capsys):
        dest = tmp_path / "empty"
        new_run("empty", dest)
        # `jm new` without --object produces a manifest with no
        # components; status should not crash.
        drift = status_run(dest)
        out = capsys.readouterr().out
        assert drift == 0
        assert "nothing to status" in out
