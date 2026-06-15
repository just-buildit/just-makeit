"""gh-265: optional house-style pass over generated C (`[project] c_style`).

A project may declare ``c_style = "clang-format"`` so jm reformats the generated
``native/**`` C/H to the committed ``.clang-format`` after every mutating
command — removing the manual ``clang-format`` step doppler documents. Off by
default, output is byte-identical, so existing projects are untouched.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _cfmt
from just_makeit import _config as C
from just_makeit._new import run as new_run


SRC = Path(__file__).parent.parent / "src"
_HAS_CF = shutil.which("clang-format") is not None
_cf_only = pytest.mark.skipif(not _HAS_CF, reason="clang-format not installed")


def _cli(*args, cwd):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
    )


class TestConfigGetter:
    def test_default_is_empty(self):
        assert C.c_style({}) == ""
        assert C.c_style({"project": {}}) == ""

    def test_reads_value(self):
        cfg = {"project": {"c_style": "clang-format"}}
        assert C.c_style(cfg) == "clang-format"

    def test_round_trips_through_dump(self, tmp_path):
        # [project] scalar keys round-trip generically; assert c_style survives
        # a save -> load cycle so the post-command hook keeps firing.
        new_run("p", tmp_path / "p", c_style="clang-format")
        cfg = C.load(tmp_path / "p")
        assert C.c_style(cfg) == "clang-format"
        text = (tmp_path / "p" / C.FILENAME).read_text()
        assert 'c_style = "clang-format"' in text


class TestNewScaffold:
    def test_seeds_clang_format(self, tmp_path):
        new_run("p", tmp_path / "p", c_style="clang-format")
        cf = tmp_path / "p" / ".clang-format"
        assert cf.exists()
        assert "BasedOnStyle: GNU" in cf.read_text()

    def test_no_clang_format_without_opt_in(self, tmp_path):
        new_run("p", tmp_path / "p")
        assert not (tmp_path / "p" / ".clang-format").exists()
        assert C.c_style(C.load(tmp_path / "p")) == ""

    @_cf_only
    def test_generated_c_is_gnu_styled(self, tmp_path):
        new_run(
            "p",
            tmp_path / "p",
            object_names=["widget"],
            c_style="clang-format",
        )
        core = (tmp_path / "p" / "native/src/widget/widget_core.c").read_text()
        # GNU style: a space before the call paren in a definition.
        assert "widget_create (float gain)" in core
        assert "widget_create(float gain)" not in core

    def test_default_keeps_jm_style(self, tmp_path):
        # Without c_style the canonical 4-space output is preserved verbatim.
        new_run("p", tmp_path / "p", object_names=["widget"])
        core = (tmp_path / "p" / "native/src/widget/widget_core.c").read_text()
        assert "widget_create(float gain)" in core
        assert "widget_create (float gain)" not in core


class TestFormatProject:
    def test_noop_when_unset(self, tmp_path):
        # No c_style -> returns immediately, never shells out to clang-format.
        new_run("p", tmp_path / "p", object_names=["widget"])
        cfg = C.load(tmp_path / "p")
        before = (
            tmp_path / "p" / "native/src/widget/widget_core.c"
        ).read_text()
        _cfmt.format_project(tmp_path / "p", cfg)
        after = (
            tmp_path / "p" / "native/src/widget/widget_core.c"
        ).read_text()
        assert before == after

    def test_missing_binary_is_soft_failure(
        self, tmp_path, monkeypatch, capsys
    ):
        # clang-format absent -> a warning, no exception, command still wins.
        new_run("p", tmp_path / "p", object_names=["widget"])
        cfg = {"project": {"c_style": "clang-format"}}
        monkeypatch.setattr(_cfmt.shutil, "which", lambda _name: None)
        _cfmt.format_project(tmp_path / "p", cfg)  # must not raise
        err = capsys.readouterr().err
        assert "clang-format was not found" in err

    def test_collects_inc_and_src(self, tmp_path):
        new_run("p", tmp_path / "p", object_names=["widget"])
        files = _cfmt._generated_c_files(tmp_path / "p")
        names = {f.name for f in files}
        assert "widget_core.c" in names
        assert "widget_core.h" in names
        assert files == sorted(files)


class TestPerCommandHook:
    @_cf_only
    def test_method_triggers_reformat(self, tmp_path):
        # The CLI post-dispatch hook reformats after a mutating command on a
        # c_style project (here `method`, which regenerates _ext.c).
        root = tmp_path / "p"
        new_run("p", root, object_names=["widget"], c_style="clang-format")
        r = _cli(
            "method",
            "widget",
            "execute_ctrl",
            "--arg-type",
            "float _Complex",
            "--return-type",
            "float _Complex",
            cwd=root,
        )
        assert r.returncode == 0, r.stderr
        ext = (root / "native/src/widget/widget_ext.c").read_text()
        # GNU style applied to the regenerated binding glue.
        assert "Py_TYPE (self)" in ext

    @_cf_only
    def test_default_project_not_reformatted(self, tmp_path):
        root = tmp_path / "p"
        new_run("p", root, object_names=["widget"])
        r = _cli(
            "method",
            "widget",
            "execute_ctrl",
            "--arg-type",
            "float _Complex",
            "--return-type",
            "float _Complex",
            cwd=root,
        )
        assert r.returncode == 0, r.stderr
        assert "format" not in r.stdout
        ext = (root / "native/src/widget/widget_ext.c").read_text()
        assert "Py_TYPE(self)" in ext


@pytest.mark.skipif(
    not (
        _HAS_CF
        and shutil.which("cmake")
        and any(shutil.which(c) for c in ("cc", "gcc", "clang"))
    ),
    reason="needs clang-format + cmake + C compiler",
)
def test_formatted_project_builds(tmp_path):
    """End-to-end: a GNU-reformatted scaffold still compiles (the style pass
    must not corrupt the generated C)."""
    root = tmp_path / "p"
    new_run("p", root, object_names=["widget"], c_style="clang-format")
    cfg = subprocess.run(
        ["cmake", "-S", str(root), "-B", str(root / "build")],
        capture_output=True,
        text=True,
    )
    assert cfg.returncode == 0, cfg.stderr
    bld = subprocess.run(
        ["cmake", "--build", str(root / "build")],
        capture_output=True,
        text=True,
    )
    assert bld.returncode == 0, f"{bld.stdout}\n{bld.stderr}"
