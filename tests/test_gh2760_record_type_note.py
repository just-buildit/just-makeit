"""gh-2760: a record result over a struct nothing defines is never silent.

``jm method --return-type peak_t --result-field ...`` (and ``--single``,
and ``jm function`` with either) renders prototypes over the author's
struct. With no definition the scaffold did not compile, and jm said only
"Implement ser_peaks()". ``--record-dtype`` already printed gh-1319's NOTE
with the typedef to paste; these shapes printed nothing.

jm neither writes the struct (gh-1319 did, and reverted it: the author's
later definition collided) nor refuses (a system header's ``div_t`` is valid
and invisible to jm, and ``jm script`` replays before the struct exists).
It tells the author, and the typedef it prints is the one that builds.

"Defined" follows the header's quoted project includes, the way struct
member docs do, so a struct declared in a shared header draws no note.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

import pytest
from _compilers import default_cc
from _jmrun import run_cli

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

_PEAKS = [
    "--arg-type",
    "float[]",
    "--return-type",
    "peak_t",
    "--result-field",
    "idx:int",
    "--result-field",
    "val:float",
]


def _project(tmp_path):
    r = run_cli("new", "rec", "--no-c-prefix", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    root = tmp_path / "rec"
    assert run_cli("object", "ser", cwd=root).returncode == 0
    return root


def _header(root, name):
    return root / "native" / "inc" / "rec" / name / f"{name}_core.h"


def _typedef(out: str, record: str) -> str:
    m = re.search(
        rf"^typedef struct\n\{{\n.*?\n\}} {record};$", out, re.S | re.M
    )
    assert m is not None, out
    return m.group(0)


def _declare(header, text):
    src = header.read_text(encoding="utf-8")
    anchor = '#include "rec/clib_common.h"\n'
    assert src.count(anchor) == 1, src
    header.write_text(
        src.replace(anchor, anchor + text + "\n"), encoding="utf-8"
    )


class TestTheNoteNamesTheStruct:
    def test_a_list_of_records_method(self, tmp_path):
        root = _project(tmp_path)
        r = run_cli("method", "ser", "peaks", *_PEAKS, cwd=root)
        assert r.returncode == 0, r.stderr
        assert "nothing defines `peak_t`" in r.stdout, r.stdout
        assert "int idx;" in _typedef(r.stdout, "peak_t")
        # Last on screen: the Done! line comes before it.
        assert r.stdout.index("Done!") < r.stdout.index("NOTE:")

    def test_a_single_record_method(self, tmp_path):
        root = _project(tmp_path)
        r = run_cli(
            "method",
            "ser",
            "best",
            "--arg-type",
            "float[]",
            "--return-type",
            "best_t",
            "--single",
            "--result-field",
            "idx:int",
            cwd=root,
        )
        assert r.returncode == 0, r.stderr
        assert "nothing defines `best_t`" in r.stdout, r.stdout

    @pytest.mark.parametrize("single", [False, True], ids=["list", "single"])
    def test_a_record_function(self, tmp_path, single):
        root = _project(tmp_path)
        assert run_cli("module", "dsp", cwd=root).returncode == 0
        r = run_cli(
            "function",
            "rows",
            "--module",
            "dsp",
            "--param",
            "x:double",
            "--return-type",
            "row_t",
            "--result-field",
            "a:int",
            *(["--single"] if single else []),
            cwd=root,
        )
        assert r.returncode == 0, r.stderr
        assert "nothing defines `row_t`" in r.stdout, r.stdout

    def test_a_forward_typedef_alone_is_not_a_definition(self, tmp_path):
        """The binding takes sizeof, so an incomplete type does not build."""
        root = _project(tmp_path)
        _declare(_header(root, "ser"), "typedef struct peak peak_t;")
        r = run_cli("method", "ser", "peaks", *_PEAKS, cwd=root)
        assert "nothing defines `peak_t`" in r.stdout, r.stdout


class TestADefinedStructDrawsNoNote:
    def test_in_the_components_own_header(self, tmp_path):
        root = _project(tmp_path)
        _declare(
            _header(root, "ser"),
            "typedef struct { int idx; float val; } peak_t;",
        )
        r = run_cli("method", "ser", "peaks", *_PEAKS, cwd=root)
        assert r.returncode == 0, r.stderr
        assert "NOTE:" not in r.stdout, r.stdout

    def test_in_a_project_header_it_includes(self, tmp_path):
        root = _project(tmp_path)
        shared = root / "native" / "inc" / "rec" / "shared" / "types.h"
        shared.parent.mkdir(parents=True)
        shared.write_text(
            "#ifndef SHARED_TYPES_H\n#define SHARED_TYPES_H\n"
            "typedef struct { int idx; float val; } peak_t;\n#endif\n",
            encoding="utf-8",
        )
        _declare(_header(root, "ser"), '#include "rec/shared/types.h"')
        r = run_cli("method", "ser", "peaks", *_PEAKS, cwd=root)
        assert r.returncode == 0, r.stderr
        assert "NOTE:" not in r.stdout, r.stdout

    def test_in_a_sibling_header_it_includes(self, tmp_path):
        """gh-2779's spelling: beside the header, as the compiler finds it."""
        root = _project(tmp_path)
        header = _header(root, "ser")
        (header.parent / "peak.h").write_text(
            "#ifndef SER_PEAK_H\n#define SER_PEAK_H\n"
            "typedef struct { int idx; float val; } peak_t;\n#endif\n",
            encoding="utf-8",
        )
        _declare(header, '#include "peak.h"')
        r = run_cli("method", "ser", "peaks", *_PEAKS, cwd=root)
        assert r.returncode == 0, r.stderr
        assert "NOTE:" not in r.stdout, r.stdout


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_printed_typedef_is_the_one_that_builds(tmp_path):
    """The acceptance: follow the note verbatim, and the scaffold builds.

    The method is declared first, as gh-788's authors do; the typedef the
    note prints is pasted into the sacred header unchanged.
    """
    root = _project(tmp_path)
    r = run_cli("method", "ser", "peaks", *_PEAKS, cwd=root)
    assert r.returncode == 0, r.stderr
    _declare(_header(root, "ser"), _typedef(r.stdout, "peak_t"))
    build = root / "build"
    cfg = subprocess.run(
        # The binding is what reads the fields (offsetof, `result[i].idx`),
        # so it must be in the build: a typedef with the wrong columns
        # compiles fine without it.
        [
            "cmake",
            "-S",
            str(root),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        capture_output=True,
        text=True,
    )
    assert cfg.returncode == 0, cfg.stdout + cfg.stderr
    made = subprocess.run(
        ["cmake", "--build", str(build)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert made.returncode == 0, made.stdout + made.stderr
