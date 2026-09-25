"""A bundled example run standalone builds into `_example.scratch_dir()`.

v0.90.0's pre-publish Windows smoke failed on `nco_tone` AFTER it printed
PASSED: its `__main__` imported the extension it built, and
`tempfile.TemporaryDirectory`'s cleanup could not delete a loaded ``.pyd``
(Windows keeps it locked until the process exits). pytest's ``tmp_path``
never deletes eagerly, so the per-PR examples job could not see it.

GATE: no example's test.py builds into `tempfile.TemporaryDirectory`, and
      `scratch_dir` survives a cleanup the OS refuses.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from just_makeit._example import scratch_dir

EXAMPLES = (
    Path(__file__).resolve().parent.parent / "src" / "just_makeit" / "examples"
)


def test_no_example_builds_into_a_temporary_directory():
    offenders = sorted(
        p.relative_to(EXAMPLES).as_posix()
        for p in EXAMPLES.glob("*/test.py")
        if "TemporaryDirectory(" in p.read_text(encoding="utf-8")
    )
    assert offenders == [], (
        "an example's standalone run builds into `_example.scratch_dir()`: "
        "TemporaryDirectory's cleanup raises on Windows when the example "
        f"has imported what it built: {offenders}"
    )


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="a read-only directory refuses deletion only for a non-root "
    "POSIX user",
)
def test_a_cleanup_the_os_refuses_does_not_fail_the_example():
    """A directory whose entries cannot be removed stands in for Windows'
    locked ``.pyd``: the context exits cleanly, and what was refused is left
    behind for the temp reaper."""
    with scratch_dir() as d:
        locked = Path(d) / "locked"
        locked.mkdir()
        (locked / "tone.pyd").write_text("x")
        locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert (locked / "tone.pyd").exists()
    finally:
        locked.chmod(stat.S_IRWXU)
        import shutil

        shutil.rmtree(d, ignore_errors=True)
