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
        timeout=600,
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
    def test_regenerated_ext_c_is_gnu_styled(self, tmp_path):
        # The wholesale-regenerated binding IS reformatted to house style.
        new_run(
            "p",
            tmp_path / "p",
            object_names=["widget"],
            c_style="clang-format",
        )
        ext = (tmp_path / "p" / "native/src/widget/widget_ext.c").read_text()
        assert "Py_TYPE (self)" in ext  # GNU: space before the call paren
        assert "Py_TYPE(self)" not in ext

    @_cf_only
    def test_sacred_core_c_is_left_alone(self, tmp_path):
        # gh-493: the sacred algorithm source is NOT reformatted, even with
        # c_style on — its style is the project formatter's business, and
        # reformatting it broke apply convergence.
        new_run(
            "p",
            tmp_path / "p",
            object_names=["widget"],
            c_style="clang-format",
        )
        core = (tmp_path / "p" / "native/src/widget/widget_core.c").read_text()
        assert "widget_create(float gain)" in core  # jm's own 4-space style
        assert "widget_create (float gain)" not in core

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

    def test_collects_only_regenerated_ext_c(self, tmp_path):
        # gh-493: only the wholesale-regenerated *_ext.c glue is a format
        # target. Sacred sources (_core.c, native/inc/** headers) are excluded
        # so reformatting them can't flap the splice-patch detection.
        new_run("p", tmp_path / "p", object_names=["widget"])
        files = _cfmt._generated_c_files(tmp_path / "p")
        names = {f.name for f in files}
        assert "widget_ext.c" in names
        assert "widget_core.c" not in names
        assert "widget_core.h" not in names
        assert not any(
            "native/inc" in str(f.relative_to(tmp_path / "p")) for f in files
        )
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


class TestConvergence:
    """gh-493: with c_style on, repeated passes must not churn sacred files.

    The bug: format_project reformatted the splice-patched `_core.h` (and
    `_core.c`), and jm's declaration-injection is whitespace-sensitive, so a
    later `jm apply`/`status --check` believed the header had drifted and
    re-patched it — `apply` never converged and `status --check` flapped on an
    unchanged manifest. These pin the fix at the point it broke.
    """

    @_cf_only
    def test_repeated_format_leaves_sacred_files_byte_identical(
        self, tmp_path
    ):
        root = tmp_path / "p"
        new_run("p", root, object_names=["widget"], c_style="clang-format")
        cfg = C.load(root)
        sacred = [
            root / "native/src/widget/widget_core.c",
            root / "native/inc/widget/widget_core.h",
            root / "native/inc/p.h",
        ]
        sacred = [p for p in sacred if p.exists()]
        assert sacred, "expected sacred sources to exist"
        before = {p: p.read_bytes() for p in sacred}
        # Every mutating command re-runs the format hook; simulate a few.
        for _ in range(3):
            _cfmt.format_project(root, cfg)
        for p, blob in before.items():
            assert p.read_bytes() == blob, (
                f"{p.relative_to(root)} was reformatted by c_style — this is "
                f"what broke apply convergence (gh-493)"
            )

    @_cf_only
    def test_status_check_stays_clean_across_apply(self, tmp_path):
        # The end-to-end symptom: apply on a c_style project, then status
        # --check must report clean on the still-unchanged manifest — twice.
        root = tmp_path / "p"
        new_run("p", root, object_names=["widget"], c_style="clang-format")
        assert _cli("apply", cwd=root).returncode == 0
        first = _cli("status", "--check", cwd=root)
        assert first.returncode == 0, f"first status --check: {first.stdout}"
        # A second apply with an unchanged manifest must be a no-op, and
        # status --check must still pass — i.e. it converged.
        assert _cli("apply", cwd=root).returncode == 0
        second = _cli("status", "--check", cwd=root)
        assert second.returncode == 0, (
            f"status --check flapped after a second apply on an unchanged "
            f"manifest — apply did not converge (gh-493): {second.stdout}"
        )


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
        timeout=600,
    )
    assert cfg.returncode == 0, cfg.stderr
    bld = subprocess.run(
        ["cmake", "--build", str(root / "build")],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert bld.returncode == 0, f"{bld.stdout}\n{bld.stderr}"
