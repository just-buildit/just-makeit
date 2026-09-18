"""A directory's own CMake lives in `<dir>_extra.cmake`, which jm never writes.

gh-1351. A component's or module's ``native/src/<dir>/CMakeLists.txt`` is glue
-- ``jm apply`` re-renders it -- so a free-standing statement an author adds
there (``target_compile_definitions``, a compile option, a ``find_package``) is
dropped by the next apply, on a standalone object and a module object alike.
gh-275/gh-271/gh-1301 keep three recognised shapes in place, and the set of
CMake statements is open-ended.

So every CMakeLists jm renders under ``native/src/<dir>/`` ends with::

    include(${CMAKE_CURRENT_LIST_DIR}/<dir>_extra.cmake OPTIONAL)

``OPTIONAL`` makes it inert until the file exists. ``jm regenerate`` and
``jm remove`` keep the file (gh-1216's rule). And an apply that is about to
drop a statement jm itself never writes says so, naming the hook.

Registration-free where it can be: the scaffolded tree is walked, so a new
kind of generated CMakeLists that forgets the hook fails here without being
listed.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(SRC))

from test_capsule_codegen import _cfg as _capsule_cfg  # noqa: E402
from test_composer_codegen import _cfg as _composer_cfg  # noqa: E402
from test_handle_codegen import _writer_cfg as _handle_cfg  # noqa: E402

from just_makeit import _capsule, _composer, _handle  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_HAVE_TOOLCHAIN = bool(shutil.which("cmake")) and any(
    shutil.which(c) for c in ("cc", "gcc", "clang")
)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _include(dirname: str) -> str:
    return (
        f"include(${{CMAKE_CURRENT_LIST_DIR}}/{dirname}_extra.cmake OPTIONAL)"
    )


def _project(tmp_path: Path) -> Path:
    """A standalone object, a collocated module object and a separate one."""
    root = tmp_path / "p"
    _quiet(new_run, "p", root)
    _quiet(object_run, root, "o", None, state_vars=[("n", "int", "0")])
    _quiet(module_run, root, "m")
    for obj in ("m", "w"):  # `m` is collocated with its module; `w` is not
        _quiet(object_run, root, obj, "m", state_vars=[("n", "int", "0")])
    _quiet(apply_run, root)
    return root


class TestEveryGeneratedCMakeListsCarriesItsHook:
    def test_the_scaffolded_tree(self, tmp_path):
        root = _project(tmp_path)
        files = sorted((root / "native" / "src").glob("*/CMakeLists.txt"))
        assert {f.parent.name for f in files} >= {"o", "m", "w"}, files
        missing = [
            f.parent.name
            for f in files
            if f.read_text(encoding="utf-8").count(_include(f.parent.name))
            != 1
        ]
        # Exactly once: a collocated object shares its module's file, and a
        # second include would run the author's rules twice.
        assert not missing, missing

    @pytest.mark.parametrize(
        "render, cfg, module, cname",
        [
            (_capsule.render_cmake, _capsule_cfg, "ddc_fn", "ddc_fn"),
            (_handle.render_cmake, _handle_cfg, "wfm_writer", "wfm_writer"),
            (
                _composer.render_cmake,
                _composer_cfg,
                "wfm_compose",
                "wfm_compose",
            ),
        ],
        ids=["capsule", "handle", "composer"],
    )
    def test_the_module_kinds(self, render, cfg, module, cname):
        assert render(cfg(), module).count(_include(cname)) == 1


class TestTheHookSurvives:
    def test_apply_leaves_it_alone(self, tmp_path):
        root = _project(tmp_path)
        hook = root / "native" / "src" / "o" / "o_extra.cmake"
        hook.write_text("# mine\n", encoding="utf-8")
        _quiet(apply_run, root)
        assert hook.read_text(encoding="utf-8") == "# mine\n"

    def test_regenerate_keeps_it(self, tmp_path):
        """gh-1216's failure, one file type over: measured, it deleted it."""
        from just_makeit._regenerate import run as regenerate_run

        root = _project(tmp_path)
        hook = root / "native" / "src" / "o" / "o_extra.cmake"
        hook.write_text("# mine\n", encoding="utf-8")
        _quiet(regenerate_run, root, "o", force=True)
        assert hook.read_text(encoding="utf-8") == "# mine\n"


class TestADroppedStatementIsNamed:
    def _apply_with(self, root: Path, line: str) -> str:
        cml = root / "native" / "src" / "o" / "CMakeLists.txt"
        cml.write_text(cml.read_text(encoding="utf-8") + line)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
            apply_run(root)
        return err.getvalue()

    def test_a_statement_jm_never_writes(self, tmp_path):
        out = self._apply_with(
            _project(tmp_path),
            "\ntarget_compile_definitions(o_core PRIVATE X=1)\n",
        )
        assert "target_compile_definitions()" in out
        assert "o_extra.cmake" in out

    def test_not_for_a_command_jm_writes_itself(self, tmp_path):
        """A second `target_link_libraries` is regeneration's business."""
        out = self._apply_with(
            _project(tmp_path),
            "\ntarget_link_libraries(o_core PUBLIC m)\n",
        )
        assert "gh-1351" not in out


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from just_makeit._cli import main; main()"]
        + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
        timeout=900,
    )


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="needs cmake and a C compiler")
class TestItReachesTheBuild:
    """Text cannot show a rule took effect; the compiler can."""

    GUARD = (
        "\n#ifndef JM_EXTRA_OK\n"
        '#error "the _extra.cmake hook did not reach the build"\n'
        "#endif\n"
    )

    @pytest.mark.parametrize("obj", ["o", "w"], ids=["standalone", "module"])
    def test_a_compile_definition_arrives(self, tmp_path, obj):
        root = _project(tmp_path)
        d = root / "native" / "src" / obj
        (d / f"{obj}_extra.cmake").write_text(
            f"target_compile_definitions({obj}_core PRIVATE JM_EXTRA_OK=1)\n",
            encoding="utf-8",
        )
        core = d / f"{obj}_core.c"
        core.write_text(core.read_text(encoding="utf-8") + self.GUARD)
        r = _cli("test", cwd=root)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    def test_without_the_hook_it_does_not(self, tmp_path):
        root = _project(tmp_path)
        core = root / "native" / "src" / "o" / "o_core.c"
        core.write_text(core.read_text(encoding="utf-8") + self.GUARD)
        assert _cli("test", cwd=root).returncode != 0
