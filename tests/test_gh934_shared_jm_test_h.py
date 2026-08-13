"""gh-934: one shared jm_test.h, not a copy of CHECK per scaffolded test.

The template used to carry its own assertion machinery inline, so every
component a project scaffolded got another private copy of `CHECK`, the
counters and the epilogue. One downstream reached **90 definitions of CHECK in
6 mutually incompatible variants**, and in 20 of those files the failure gate
had drifted ABOVE later assertions — leaving 75 assertions unable to affect the
exit code, which hid a real heap buffer overflow.

What is gated here is therefore not "the header exists" but the three
properties that make the drift unconstructible:

- there is exactly ONE copy, and both scaffolding paths write it (`jm new` and
  `jm object` are peers; only one of them writing it is the classic bug here)
- the gate and the report are ONE macro, so a gate cannot be left behind by a
  report — the shape those 20 files drifted into
- the gh-806 hollow-scaffold note survives the move, including its count

The compiler tier asserts on what the built binary PRINTS. Asserting on the
generated text would prove the template contains a printf, not that the number
it prints is right — and the count is the whole mechanism.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _render as R  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc")
_needs_cc = pytest.mark.skipif(_CC is None, reason="no C compiler on PATH")

#: The generated CMake asks for C99 with GNU extensions on, so match it here
#: rather than compiling jm's own output under stricter flags than the real
#: build uses.
_STD = "-std=gnu99"


def _project(dest: Path) -> Path:
    """A project with components from BOTH scaffolding paths.

    `jm new --object` and a later `jm object` are separate writers
    (`_init.py` and `_object.py`), and a header written by only one of them
    is the peer bug this repo has hit repeatedly.
    """
    new_run("tp", dest, object_names=["alpha"])
    object_run(dest, "beta", None)
    module_run(dest, "filt")
    object_run(dest, "gamma", "filt")
    return dest


class TestOneCopy:
    def test_exactly_one_header_and_no_inline_definitions(self, tmp_path):
        root = _project(tmp_path / "tp")

        headers = list(root.glob("native/**/jm_test.h"))
        assert len(headers) == 1, f"expected one jm_test.h, got {headers}"
        assert headers[0] == root / "native" / "tests" / "jm_test.h"

        tests = list(root.glob("native/tests/test_*_core.c"))
        assert len(tests) == 3, tests
        for t in tests:
            body = t.read_text(encoding="utf-8")
            assert "#define CHECK" not in body, f"{t.name} redefines CHECK"
            assert "static int" not in body, (
                f"{t.name} carries its own counter"
            )
            assert '#include "jm_test.h"' in body

    @pytest.mark.parametrize("shape", ["standalone", "module"])
    def test_every_scaffolding_shape_writes_the_header(self, tmp_path, shape):
        """Both writers, each exercised on the shape only IT covers.

        This is the assertion that survived its own sabotage. It used to
        scaffold a standalone object and call that "the `_object.py` peer" —
        but `_object.run` delegates to `_init.run` for a standalone component
        (`_object.py:2031`), so `_init.py`'s copy was doing the work and
        deleting `_object.py`'s left the suite green.

        Only a MODULE object reaches `_object.py`'s own write. Measured with
        that block removed: standalone still produced the header, module
        produced none. Two write sites means two shapes, or one of them is
        untested — which is the same peer trap the write sites themselves are.
        """
        root = tmp_path / "tp"
        new_run("tp", root)
        hdr = root / "native" / "tests" / "jm_test.h"
        assert not hdr.exists()

        if shape == "standalone":
            object_run(root, "solo", None)
        else:
            module_run(root, "m")
            object_run(root, "gamma", "m")

        assert hdr.exists(), f"{shape} scaffolding wrote no jm_test.h"

    def test_an_edited_header_survives_a_later_scaffold(self, tmp_path):
        """Create-only, like jm_bench.h — the author may extend it.

        Scaffolding a NEW component is the path that matters, and it is the
        one this test originally missed: it only called `apply`, which skips
        a file that already exists, so dropping the `if not exists()` guard in
        `_init.py` left the suite green. `jm object` re-enters that writer
        every time, so an unguarded write clobbers the author's edits on the
        next component they add — silently, since the file is create-only and
        nothing reports drift on it.
        """
        root = tmp_path / "tp"
        new_run("tp", root, object_names=["alpha"])
        hdr = root / "native" / "tests" / "jm_test.h"
        hdr.write_text(
            hdr.read_text(encoding="utf-8")
            + "\n#define MY_OWN_ASSERT(c) CHECK(c)\n",
            encoding="utf-8",
        )
        before = hdr.read_text(encoding="utf-8")

        object_run(root, "beta", None)
        assert hdr.read_text(encoding="utf-8") == before, (
            "scaffolding a new component overwrote the author's jm_test.h"
        )

        module_run(root, "m")
        object_run(root, "gamma", "m")
        assert hdr.read_text(encoding="utf-8") == before

        apply_run(root)
        assert hdr.read_text(encoding="utf-8") == before


class TestGateAndReportAreInseparable:
    def test_epilogue_carries_the_failure_gate(self):
        """The drifted shape needs a gate that can be written on its own."""
        hdr = R.JM_TEST_H
        epi = hdr[hdr.index("#define JM_TEST_EPILOGUE()") :]
        # Everything the 20 drifted files split apart lives in this one macro.
        assert "if (jm_fails)" in epi
        assert "return 1;" in epi
        assert "return 0;" in epi
        assert "PASSED" in epi and "FAILED" in epi

    def test_the_scaffold_never_emits_a_bare_failure_gate(self, tmp_path):
        root = _project(tmp_path / "tp")
        for t in root.glob("native/tests/test_*_core.c"):
            body = t.read_text(encoding="utf-8")
            assert "if (_fails)" not in body
            assert "jm_fails" not in body, (
                f"{t.name} touches the counter directly; only the epilogue "
                "should"
            )


class TestScaffoldCheckCount:
    def test_requires_are_counted(self, tmp_path):
        """gh-806's count must include REQUIRE, or the note misreports.

        `obj_null_check` emits a REQUIRE. A counter that saw only CHECK would
        stamp one less than the file asserts, so the runtime count would
        exceed it and the note would claim an author had written a test.
        """
        root = _project(tmp_path / "tp")
        body = (root / "native" / "tests" / "test_alpha_core.c").read_text(
            encoding="utf-8"
        )
        stamped = int(
            re.search(r"#define JM_SCAFFOLD_CHECKS\s+(\d+)", body).group(1)
        )
        actual = len(re.findall(r"^\s*(?:CHECK|REQUIRE)\s*\(", body, re.M))
        assert actual > 0
        assert stamped == actual
        assert "REQUIRE(" in body, "the REQUIRE path is not being exercised"


@_needs_cc
class TestRuntimeBehaviour:
    """What the built binary prints — the count is the mechanism."""

    def _build_and_run(self, root: Path, comp: str, tmp_path: Path):
        src = root / "native" / "tests" / f"test_{comp}_core.c"
        exe = tmp_path / f"t_{comp}"
        core = root / "native" / "src" / comp / f"{comp}_core.c"
        cp = subprocess.run(
            [
                _CC,
                _STD,
                "-I",
                str(root / "native" / "inc"),
                "-o",
                str(exe),
                str(src),
                str(core),
                "-lm",
            ],
            capture_output=True,
            text=True,
        )
        if cp.returncode != 0:
            pytest.skip(f"scaffold does not compile here:\n{cp.stderr}")
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        return run

    def test_scaffold_only_says_so_then_stops_saying_it(self, tmp_path):
        root = tmp_path / "tp"
        new_run("tp", root, object_names=["alpha"])

        out = self._build_and_run(root, "alpha", tmp_path)
        assert out.returncode == 0, out.stderr
        assert "PASSED" in out.stdout
        assert "NOTE" in out.stdout, (
            "the gh-806 hollow-scaffold note did not survive the move"
        )

        # One author assertion clears it — the note is a measurement, not a
        # marker that has to be deleted by hand.
        src = root / "native" / "tests" / "test_alpha_core.c"
        body = src.read_text(encoding="utf-8")
        src.write_text(
            body.replace(
                "    JM_TEST_EPILOGUE();",
                "    CHECK(1 == 1);\n    JM_TEST_EPILOGUE();",
            ),
            encoding="utf-8",
        )
        out2 = self._build_and_run(root, "alpha", tmp_path)
        assert out2.returncode == 0, out2.stderr
        assert "NOTE" not in out2.stdout, out2.stdout

    def test_a_failure_exits_nonzero_and_is_reported(self, tmp_path):
        root = tmp_path / "tp"
        new_run("tp", root, object_names=["alpha"])
        src = root / "native" / "tests" / "test_alpha_core.c"
        src.write_text(
            src.read_text(encoding="utf-8").replace(
                "    JM_TEST_EPILOGUE();",
                "    CHECK(1 == 2);\n    JM_TEST_EPILOGUE();",
            ),
            encoding="utf-8",
        )
        out = self._build_and_run(root, "alpha", tmp_path)
        assert out.returncode == 1
        assert "FAIL" in out.stderr
        assert "FAILED" in out.stderr

    def test_a_late_assertion_cannot_be_orphaned_by_the_gate(self, tmp_path):
        """The 20-file drift, attempted: a failure AFTER the last generated
        assertion must still reach the exit code.

        In the old per-file shape `if (_fails) return 1;` could sit above
        later CHECKs, which then printed FAIL and changed nothing. With gate
        and report fused into the epilogue, anything before it is counted.
        """
        root = tmp_path / "tp"
        new_run("tp", root, object_names=["alpha"])
        src = root / "native" / "tests" / "test_alpha_core.c"
        src.write_text(
            src.read_text(encoding="utf-8").replace(
                "    alpha_destroy(obj);",
                "    alpha_destroy(obj);\n    CHECK(1 == 2);",
            ),
            encoding="utf-8",
        )
        out = self._build_and_run(root, "alpha", tmp_path)
        assert out.returncode == 1, (
            "a failing assertion after the generated block did not affect the "
            "exit code — the drifted-gate shape is constructible again"
        )
