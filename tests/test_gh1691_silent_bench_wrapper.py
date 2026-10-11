"""gh-1691: a benchmark recording through a helper is not "silent".

`_hollow.silent_benches` called a ``bench_<comp>_core.c`` silent when its
source held no line opening with ``jm_bench_add(``. doppler records through
``dp_bench_record(&_bench, ...)``, a helper in a project header that calls
``jm_bench_add`` itself, so `jm status` / `jm apply` reported every such
benchmark as measuring nothing while the binary wrote 4-7 entries. A false
"silent" trains readers to skip the true ones.

The fix narrows what the source scan looks at rather than teaching it to read
the author's call graph: the accumulator the file hands to
``jm_bench_write_json``. Silent means that accumulator is declared here and
touched by nothing but the write. And `jm bench`, which actually has the JSON,
now says ``silent`` from the artifact.

Each case below is a shape, and the assertion pairs the scan's verdict with
the thing it claims -- for the doppler shape, by compiling the file and
reading the JSON it writes.
"""

from __future__ import annotations

import contextlib
import io
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _bench as B  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _hollow  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_CC = default_cc()


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        fn(*a, **kw)


@pytest.fixture()
def silent_project(tmp_path) -> Path:
    """A `no_step` object whose only method has no benchable shape: jm
    scaffolds a benchmark it cannot populate, so SILENT is TRUE here."""
    root = tmp_path / "proj"
    _quiet(new_run, "proj", root, c_prefix=None)
    _quiet(
        object_run,
        root,
        "tlm",
        None,
        state_vars=[("gain", "float", "0.0f")],
        no_step=True,
    )
    _quiet(method_run, root, "tlm", "read", None, "void", "uint64_t", True, [])
    return root


def _bench_dir(root: Path) -> Path:
    return root / "native" / "benchmarks"


def _silent(root: Path) -> list[str]:
    return [s.component for s in _hollow.silent_benches(root, C.load(root))]


#: doppler's shape, reduced: the helper lives in a project HEADER the scan
#: does not read, and calls `jm_bench_add` there.
_HEADER_HELPER = """\
#include "jm_bench.h"
static void
my_bench_record (jm_bench_t *b, const char *n, const double *t, int r)
{
  jm_bench_add (b, n, t, r, 1);
}
"""

_THROUGH_HEADER = """\
#include "jm_bench.h"
#include "my_bench.h"
int
main (void)
{
  jm_bench_t _bench = { 0 };
  double t[3] = { 1e-6, 2e-6, 3e-6 };
  my_bench_record (&_bench, "read", t, 3);
  jm_bench_write_json (&_bench, "tlm");
  return 0;
}
"""

_THROUGH_LOCAL_WRAPPER = """\
#include "jm_bench.h"
static jm_bench_t acc = { 0 };
static void
rec (const char *n, const double *t, int r)
{
  jm_bench_add (&acc, n, t, r, 1);
}
int
main (void)
{
  double t[1] = { 1e-6 };
  rec ("read", t, 1);
  jm_bench_write_json (&acc, "tlm");
  return 0;
}
"""

_THROUGH_A_POINTER = """\
#include "jm_bench.h"
#include "my_bench.h"
int
main (void)
{
  jm_bench_t _bench = { 0 };
  jm_bench_t *bp = &_bench;
  double t[1] = { 1e-6 };
  my_bench_record (bp, "read", t, 1);
  jm_bench_write_json (&_bench, "tlm");
  return 0;
}
"""


class TestTheReportedFalsePositive:
    def test_the_scaffold_is_still_silent(self, silent_project):
        """The true case the detector exists for keeps firing."""
        assert _silent(silent_project) == ["tlm"]

    def test_a_header_helper_is_not_silent(self, silent_project):
        bench = _bench_dir(silent_project)
        (bench / "my_bench.h").write_text(_HEADER_HELPER, encoding="utf-8")
        (bench / "bench_tlm_core.c").write_text(
            _THROUGH_HEADER, encoding="utf-8"
        )
        assert _silent(silent_project) == []

    @pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")
    def test_and_the_artifact_agrees(self, silent_project, tmp_path):
        """The verdict is checked against what the binary writes, so the
        test cannot pass on a shape that in truth records nothing."""
        bench = _bench_dir(silent_project)
        (bench / "my_bench.h").write_text(_HEADER_HELPER, encoding="utf-8")
        (bench / "bench_tlm_core.c").write_text(
            _THROUGH_HEADER, encoding="utf-8"
        )
        exe = tmp_path / "bench_tlm_core"
        subprocess.run(
            [
                _CC,
                "-std=gnu99",
                "-I",
                str(bench),
                str(bench / "bench_tlm_core.c"),
                "-o",
                str(exe),
                "-lm",
            ],
            check=True,
        )
        subprocess.run(
            [str(exe)], cwd=tmp_path, check=True, capture_output=True
        )
        data = json.loads(
            (tmp_path / "bench_tlm_core.json").read_text(encoding="utf-8")
        )
        assert [b["name"] for b in data["benchmarks"]] == ["read"]


class TestTheShapesTheScanStaysQuietOn:
    """Each hands the accumulator to code jm does not follow."""

    def test_a_wrapper_defined_in_the_same_file(self, silent_project):
        (_bench_dir(silent_project) / "bench_tlm_core.c").write_text(
            _THROUGH_LOCAL_WRAPPER, encoding="utf-8"
        )
        assert _silent(silent_project) == []

    def test_a_pointer_taken_to_the_accumulator(self, silent_project):
        (_bench_dir(silent_project) / "bench_tlm_core.c").write_text(
            _THROUGH_A_POINTER, encoding="utf-8"
        )
        assert _silent(silent_project) == []

    def test_an_accumulator_declared_elsewhere(self, silent_project):
        (_bench_dir(silent_project) / "bench_tlm_core.c").write_text(
            '#include "my_bench.h"\n'
            "int main (void) {\n"
            '  run_all ();\n  jm_bench_write_json (&g_bench, "tlm");\n'
            "  return 0;\n}\n",
            encoding="utf-8",
        )
        assert _silent(silent_project) == []

    def test_no_write_jm_can_see(self, silent_project):
        (_bench_dir(silent_project) / "bench_tlm_core.c").write_text(
            '#include "my_bench.h"\nint main (void) { return run_all (); }\n',
            encoding="utf-8",
        )
        assert _silent(silent_project) == []


class TestTheScanIsNotFooledTheOtherWay:
    """Mentions that are not code must not count as recording."""

    def test_a_call_only_in_a_comment_or_string(self, silent_project):
        (_bench_dir(silent_project) / "bench_tlm_core.c").write_text(
            '#include "jm_bench.h"\n#include <stdio.h>\n'
            "int main (void) {\n"
            "  jm_bench_t _bench = { 0 };\n"
            "  // my_bench_record (&_bench, ...);\n"
            '  puts ("jm_bench_add(&_bench, ...)");\n'
            '  jm_bench_write_json (&_bench, "tlm");\n'
            "  return 0;\n}\n",
            encoding="utf-8",
        )
        assert _silent(silent_project) == ["tlm"]


@pytest.mark.skipif(sys.platform == "win32", reason="sh stand-in binary")
def test_jm_bench_reports_silent_from_the_json(tmp_path, monkeypatch, capsys):
    """The authoritative verdict: a binary that ran and wrote an empty
    `benchmarks` array is named, whatever its source looks like."""
    exe = tmp_path / "bench_tlm_core"
    exe.write_text(
        "#!/bin/sh\necho '{\"benchmarks\": []}' > bench_tlm_core.json\n",
        encoding="utf-8",
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(B, "_build_bench_target", lambda *a, **k: True)
    monkeypatch.setattr(B, "_find_bench_binary", lambda *_a: exe)
    assert B._collect_c(tmp_path, tmp_path, ["tlm"]) is None
    assert "silent     bench_tlm_core" in capsys.readouterr().out
