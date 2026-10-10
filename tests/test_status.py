"""Integration tests for `just-makeit status`."""

from _jminc import INC_ROOT  # noqa: E402
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
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
    def test_clean_project_is_up_to_date(self, project, capsys):
        # A freshly scaffolded project is exactly what `apply` would
        # produce, so status must report zero work — no false positive on
        # merged files like the package __init__.py (the v0.14 fix).
        count = status_run(project)
        out = capsys.readouterr().out
        assert count == 0
        assert "up to date" in out

    def test_reports_missing_after_deletion(self, project, capsys):
        header = project / INC_ROOT / "my_filter" / "my_filter_core.h"
        assert header.exists()
        header.unlink()
        count = status_run(project)
        out = capsys.readouterr().out
        assert "MISSING" in out
        assert "my_filter_core.h" in out
        assert count >= 1

    def test_sacred_core_c_edit_not_flagged(self, project, capsys):
        # _core.c is sacred — `apply` never rewrites it, so a hand-edited
        # algorithm body must NOT show up as actionable drift.
        core_c = project / "native" / "src" / "my_filter" / "my_filter_core.c"
        core_c.write_text(
            core_c.read_text(encoding="utf-8") + "\n/* my algorithm */\n",
            encoding="utf-8",
        )
        count = status_run(project)
        out = capsys.readouterr().out
        assert count == 0
        assert "up to date" in out

    def test_glue_edit_is_stale(self, project, capsys):
        # A hand-edited glue file (the binding) IS something `apply` would
        # regenerate, so status flags it STALE.
        ext_c = project / "native" / "src" / "my_filter" / "my_filter_ext.c"
        ext_c.write_text(
            ext_c.read_text(encoding="utf-8") + "\n/* stray edit */\n",
            encoding="utf-8",
        )
        count = status_run(project)
        out = capsys.readouterr().out
        assert "STALE" in out
        assert "my_filter_ext.c" in out
        assert count >= 1

    def test_errors_outside_a_project(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            status_run(tmp_path)
        err = capsys.readouterr().err
        assert "just-makeit.toml" in err

    def test_empty_manifest_is_checked(self, tmp_path, capsys):
        """gh-2076: a manifest declaring no component still owns the
        project's own files -- the root CMakeLists, the version copies --
        so `status` checks them and `apply` writes them. Both stopped early
        on one, and a bare project read clean because nothing was looked
        at."""
        dest = tmp_path / "empty"
        new_run("empty", dest)
        capsys.readouterr()
        # Clean on its merits: files were compared, not skipped.
        assert status_run(dest) == 0
        out = capsys.readouterr().out
        assert re.search(r"up to date; [1-9]\d* manifest-owned", out), out

        # A [project] key whose wiring `apply` writes is drift `status`
        # sees and `apply` clears, with no component declared.
        cfg = C.load(dest)
        cfg["project"]["c_deps"] = ["vend"]
        C.save(dest, cfg)
        assert status_run(dest) == 1
        assert "~ CMakeLists.txt" in capsys.readouterr().out
        apply_run(dest)
        cmake = (dest / "CMakeLists.txt").read_text(encoding="utf-8")
        assert "add_subdirectory(native/src/vend)\n" in cmake
        capsys.readouterr()
        assert status_run(dest) == 0

        # And a version copy that disagrees with the manifest gates.
        pyproject = dest / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text(encoding="utf-8").replace(
                'version = "0.1.0"', 'version = "0.2.0"', 1
            ),
            encoding="utf-8",
        )
        assert status_run(dest) == 1
        assert "! pyproject.toml" in capsys.readouterr().out
