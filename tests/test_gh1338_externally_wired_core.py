"""jm must not re-wire a core the project already wires itself (gh-1338).

A component may declare its core inside a platform guard and fold it into
both combined libraries there::

    if(NOT WIN32)                      # POSIX clock_*
        add_library(timing_core OBJECT timing/timing_core.c)
        target_sources(p_lib        PRIVATE $<TARGET_OBJECTS:timing_core>)
        target_sources(p_lib_static PRIVATE $<TARGET_OBJECTS:timing_core>)
    endif()

doppler does exactly this, for a POSIX-only timing core, *specifically* to
keep the conditional out of the jm-managed block. Another module then
declares it as a dependency with ``link = true``, and jm emitted its own
**unguarded** pair into the root:

- **redundant** where the guard holds — the objects are folded in twice;
- **fatal** where it does not. cmake resolves ``$<TARGET_OBJECTS:>`` at
  CONFIGURE time, so a missing target kills generate before anything
  compiles::

      CMake Error at CMakeLists.txt:63 (target_sources):
        Error evaluating generator expression

Neither workaround is available. Hand-guarding jm's pair drifts: the next
`apply` re-emits the canonical unguarded pair and relocates the hand-written
one, leaving both, and `status` reports the file STALE until it is reverted.
Dropping `link = true` does not remove the emission either, and in doppler
that key is load-bearing for `process_global`.

**The asymmetry was the bug.** `unwired` has read wiring from any CMakeLists
since gh-988 -- its docstring says outright that "a project may wire a core
from the component's own CMakeLists, and doppler deliberately does" -- while
the emitter looked only at the root. `_libwiring` exists to hold the writer
and the reader together, so the two disagreeing is the defect, and the fix is
to give the writer the view the reader already had.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _libwiring as L  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._status import run as status_run  # noqa: E402

GUARDED = "timing_core"
PLAIN = "clockc_core"


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _project(tmp_path, *, guard: str = "if(NOT WIN32)") -> Path:
    """doppler's shape, and every part of it is load-bearing.

    - a `no_generate` module, because `native/src/*/CMakeLists.txt` is
      RECONCILED for a normal component -- `apply` regenerates it, so an
      author simply cannot put a conditional there. A first attempt at this
      fixture did, `apply` deleted the hand-written wiring, and the repro
      measured something else entirely.
    - **aligned** argument spacing, because that is how a hand-written block
      is written, and the generous reader matched only jm's own
      single-space spelling.
    - a second component with `depends_on link = true`, which is what puts
      the core into the root at all. Without it nothing is emitted and the
      bug does not appear -- the second thing this fixture got wrong.
    """
    root = tmp_path / "p"
    _silent(new_run, "p", root)
    _silent(
        object_run, root, "clockc", None, state_vars=[("n", "size_t", "4")]
    )

    cfg = root / "just-makeit.toml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + "\n[module.timing]\nno_generate = true\nobjects = []\n"
        + '\n[[clockc.depends_on]]\nname = "timing"\nlink = true\n',
        encoding="utf-8",
    )
    d = root / "native/src/timing"
    d.mkdir(parents=True, exist_ok=True)
    (d / "timing_core.c").write_text(
        "int timing_ping(void){return 1;}\n", encoding="utf-8"
    )
    (d / "CMakeLists.txt").write_text(
        f"{guard}\n"
        "    add_library(timing_core OBJECT timing_core.c)\n"
        "    set_target_properties(timing_core PROPERTIES"
        " POSITION_INDEPENDENT_CODE ON)\n"
        "    target_sources(p_lib        PRIVATE"
        " $<TARGET_OBJECTS:timing_core>)\n"
        "    target_sources(p_lib_static PRIVATE"
        " $<TARGET_OBJECTS:timing_core>)\n"
        "endif()\n",
        encoding="utf-8",
    )
    return root


def _root_wiring(root: Path, core: str) -> int:
    text = (root / "CMakeLists.txt").read_text(encoding="utf-8")
    return text.count(f"$<TARGET_OBJECTS:{core}>")


class TestTheReaderSeesHandWrittenWiring:
    def test_aligned_spacing_is_matched(self):
        """The generous reader exists to see hand-written wiring, and a
        hand-written block aligns its arguments."""
        for line in (
            "target_sources(p_lib PRIVATE $<TARGET_OBJECTS:x_core>)",
            "target_sources(p_lib        PRIVATE $<TARGET_OBJECTS:x_core>)",
            "    target_sources(d_lib  PRIVATE $<TARGET_OBJECTS:t_core>)",
        ):
            assert L._WIRING_ANY.findall(line), line

    def test_the_guarded_core_reads_as_externally_wired(self, tmp_path):
        root = _project(tmp_path)
        assert GUARDED in L.externally_wired(root)

    def test_a_core_wired_only_in_the_root_is_not(self, tmp_path):
        """`externally_wired` must mean *somewhere other than the root*.
        Were it "wired anywhere", jm would stop re-emitting its own line and
        `apply` could never restore one that was deleted."""
        root = _project(tmp_path)
        _silent(apply_run, root)
        assert PLAIN not in L.externally_wired(root), (
            "a core jm wires in the root is jm's to own"
        )


class TestApplyLeavesItAlone:
    def test_no_root_wiring_is_emitted_for_it(self, tmp_path):
        root = _project(tmp_path)
        _silent(apply_run, root)
        assert _root_wiring(root, GUARDED) == 0, (
            root / "CMakeLists.txt"
        ).read_text(encoding="utf-8")

    def test_a_normal_core_is_still_wired(self, tmp_path):
        """The asymmetry is the whole point: this must not become 'jm stops
        wiring cores'."""
        root = _project(tmp_path)
        _silent(apply_run, root)
        assert _root_wiring(root, PLAIN) == 2, (
            root / "CMakeLists.txt"
        ).read_text(encoding="utf-8")

    def test_the_hand_written_block_survives(self, tmp_path):
        root = _project(tmp_path)
        _silent(apply_run, root)
        body = (root / "native/src/timing/CMakeLists.txt").read_text(
            encoding="utf-8"
        )
        assert "if(NOT WIN32)" in body, body
        assert body.count("$<TARGET_OBJECTS:timing_core>") == 2, body

    def test_a_second_apply_changes_nothing(self, tmp_path):
        """Re-emitting on the second pass is how the hand-guarded workaround
        ended up with two copies."""
        root = _project(tmp_path)
        _silent(apply_run, root)
        before = (root / "CMakeLists.txt").read_text(encoding="utf-8")
        _silent(apply_run, root)
        assert (root / "CMakeLists.txt").read_text(encoding="utf-8") == before

    def test_status_reports_neither_stale_nor_unwired(self, tmp_path):
        root = _project(tmp_path)
        _silent(apply_run, root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with contextlib.suppress(SystemExit):
                status_run(root, check=True)
        out = buf.getvalue()
        assert "unwired" not in out, out
        assert "STALE" not in out, out


@pytest.mark.skipif(not shutil.which("cmake"), reason="cmake not available")
class TestItConfiguresWhenTheGuardIsFalse:
    """The failure this exists to prevent, end to end.

    A dangling `$<TARGET_OBJECTS:>` is a GENERATE error, so the whole
    configure dies before compiling anything -- which is why a link-time
    check would not see it.
    """

    def test_configure_succeeds_with_the_core_absent(self, tmp_path):
        root = _project(tmp_path, guard="if(FALSE)  # the guard is false here")
        _silent(apply_run, root)
        r = subprocess.run(
            ["cmake", "-B", "build", "-S", "."],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, (
            "configure died on a dangling generator expression:\n"
            + r.stdout
            + r.stderr
        )
