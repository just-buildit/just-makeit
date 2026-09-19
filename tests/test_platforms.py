"""MinGW is retired; Windows is clang-cl, with no flag (gh-1368).

``[project] platforms = [..., "windows"]`` (gh-213) used to be how a project
"targeted Windows", and what it did was emit MinGW runtime-DLL CMake, behind
a GNU-compiler guard, into every component and module ``CMakeLists.txt``.
Under clang-cl -- the Windows compiler jm now builds and gates in CI -- that
guard is false, so the blocks were dead code (406 lines across 29 files in
the project that reported it), and the flag's name advertised support it did
not give.

So the key still loads, says once that it does nothing, and renders nothing;
``apply`` removes blocks an older jm wrote; ``jm new --windows`` says the
same and records nothing. The generated Makefile picks clang-cl + Ninja on
Windows itself, and the ``make`` backend refuses Windows outright.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
from pathlib import Path

import pytest

from just_makeit import _config as C
from just_makeit import _status
from just_makeit._apply import run as jm_apply
from just_makeit._module import run as jm_module
from just_makeit._new import run as jm_new
from just_makeit._object import run as jm_object

_WINDOWS = ["linux", "macos", "windows"]

#: What gh-213 emitted, for seeding an "older jm" project.
_OLD_COMPONENT_BLOCK = (
    'if(WIN32 AND CMAKE_C_COMPILER_ID STREQUAL "GNU")\n'
    "    target_link_options(eng PRIVATE -static-libgcc)\n"
    "endif()\n"
)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _scaffold(root, platforms=None):
    _quiet(jm_new, "p", root, platforms=platforms)
    _quiet(
        jm_object,
        root,
        "eng",
        module=None,
        state_vars=[("g", "double", "1.0")],
    )
    _quiet(jm_module, root, "mod")
    _quiet(
        jm_object,
        root,
        "fir",
        module="mod",
        state_vars=[("g", "double", "1.0")],
    )
    return root


def _cmakes(root: Path) -> "list[str]":
    return [
        p.read_text(encoding="utf-8") for p in root.rglob("CMakeLists.txt")
    ]


@pytest.fixture(autouse=True)
def _fresh_notice(monkeypatch):
    """The notice is once per process; each test starts unsaid."""
    monkeypatch.setattr(C, "_RETIRED_PLATFORMS_SAID", set())


class TestNothingIsEmitted:
    @pytest.mark.parametrize(
        "platforms", [None, _WINDOWS], ids=["default", "windows"]
    )
    def test_no_mingw_anywhere(self, tmp_path, platforms):
        root = _scaffold(tmp_path / "p", platforms)
        for text in _cmakes(root):
            assert "static-libgcc" not in text
            assert "libwinpthread" not in text
            assert 'CMAKE_C_COMPILER_ID STREQUAL "GNU"' not in text
        assert _quiet(_status.run, root, check=True) == 0

    def test_the_key_still_loads_and_says_so_once(self, tmp_path, capsys):
        root = _scaffold(tmp_path / "p", _WINDOWS)
        capsys.readouterr()
        C._RETIRED_PLATFORMS_SAID.clear()
        C.load(root)
        C.load(root)
        err = capsys.readouterr().err
        assert err.count("no longer does anything") == 1
        assert "gh-1368" in err

    def test_a_default_project_says_nothing(self, tmp_path, capsys):
        root = _scaffold(tmp_path / "p")
        capsys.readouterr()
        C.load(root)
        assert "no longer does anything" not in capsys.readouterr().err


class TestMigration:
    def test_apply_removes_blocks_an_older_jm_wrote(self, tmp_path):
        root = _scaffold(tmp_path / "p", _WINDOWS)
        cml = root / "native/src/eng/CMakeLists.txt"
        text = cml.read_text(encoding="utf-8")
        anchor = "set_target_properties(eng PROPERTIES"
        assert anchor in text
        cml.write_text(
            text.replace(anchor, _OLD_COMPONENT_BLOCK + anchor, 1),
            encoding="utf-8",
        )
        assert _quiet(_status.run, root, check=True) != 0
        _quiet(jm_apply, root)
        assert "static-libgcc" not in cml.read_text(encoding="utf-8")
        assert _quiet(_status.run, root, check=True) == 0


class TestTheCliFlag:
    def test_windows_flag_warns_and_records_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        from just_makeit import _cli_new

        monkeypatch.chdir(tmp_path)
        _cli_new.run(["p", "--windows"])
        assert "--windows` is retired" in capsys.readouterr().err
        manifest = (tmp_path / "p" / "just-makeit.toml").read_text(
            encoding="utf-8"
        )
        assert "platforms" not in manifest


_MAKE = shutil.which("make")


def _make_eval(
    makefile: Path, expr: str, *overrides: str
) -> subprocess.CompletedProcess:
    """Evaluate *expr* in *makefile* as GNU make would on Windows.

    A probe makefile ``include``s the generated one and adds its own target:
    no ``--eval``, which GNU make only grew in 3.82 -- macOS still ships 3.81.
    SHELL is pinned back to /bin/sh on the command line (it beats the file's
    ``SHELL = cmd.exe``) so the probe recipe can run on this host."""
    probe = makefile.parent / "jm-probe.mk"
    probe.write_text(
        f"include {makefile.name}\njm-probe:\n\t@echo '{expr}'\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [
            _MAKE,
            "--no-print-directory",
            "-f",
            probe.name,
            "OS=Windows_NT",
            "SHELL=/bin/sh",
            *overrides,
            "jm-probe",
        ],
        cwd=makefile.parent,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(not _MAKE, reason="needs GNU make")
class TestTheMakefiles:
    def test_cmake_backend_picks_clang_cl_and_ninja(self, tmp_path):
        root = _scaffold(tmp_path / "p")
        r = _make_eval(root / "Makefile", "$(CMAKE_GEN_FLAG)|$(CC)")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == '-G "Ninja"|clang-cl'

    def test_a_cc_you_set_wins(self, tmp_path):
        root = _scaffold(tmp_path / "p")
        r = _make_eval(root / "Makefile", "$(CC)", "CC=gcc-custom")
        assert r.stdout.strip() == "gcc-custom", r.stderr

    def test_make_backend_refuses_windows(self, tmp_path):
        root = tmp_path / "p"
        _quiet(jm_new, "p", root, build_system="make")
        r = _make_eval(root / "Makefile", "unreached")
        assert r.returncode != 0
        assert 'the "make" build backend is POSIX-only' in r.stderr
