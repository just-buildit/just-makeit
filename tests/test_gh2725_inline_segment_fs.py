"""An inline-built Segment builds its source at the segment's ``fs``
(gh-2725).

``Seg(type=..., fs=40e6, ...)`` consumed ``fs`` as the segment's own and
never handed it to the source it built, so ``seg.sources[0].fs`` was the
source type's default and any read through the source object --
``seg.sources[0].window`` for a ``get_fn`` field -- resolved at a rate no
segment had. Every other way a Segment gets its sources already agreed:
the composer's read-back sets each rebuilt source's ``fs`` to its
segment's.

Decision: the inline face builds its source at the segment's ``fs``,
given or defaulted. Afterwards they are separate objects -- a later
``seg.fs = x`` moves the segment only, and a ``Segment.sum`` source keeps
the ``fs`` it was built with.

GATE: compiled, with the segment's ``fs`` default (2e6) unlike the
source type's (1e6): ``seg.sources[0].fs == seg.fs`` both given and
defaulted, the inner ``get_fn`` read agrees with the flat one, a ``sum``
source is untouched, and a segment with no ``fs`` field forwards nothing.
"""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest

from just_makeit import _composer

sys.path.insert(0, str(Path(__file__).parent))
import test_gh2710_composer_field_get_fn as base  # noqa: E402
import test_gh2717_segment_flat_get_fn as flat  # noqa: E402

PKG = base.PKG


def _module() -> dict:
    """gh-2717's flat composer, its segment ``fs`` defaulting to 2e6 so a
    forwarded default is told apart from the source type's 1e6."""
    m = copy.deepcopy(flat._module())
    for f in m["segment"]["fields"]:
        if f["name"] == "fs":
            f["default"] = "2e6"
    return m


class TestRender:
    def _seg(self, m: dict) -> str:
        cfg = {"project": {"name": PKG}, "module": {"win": m}}
        return _composer.render_segment_type(cfg, "win")

    def test_init_hands_the_source_the_segments_fs(self) -> None:
        init = self._seg(_module()).split("Seg_init(", 1)[1]
        init = init.split("\n}\n", 1)[0]
        put = init.index('PyDict_SetItemString(kw, "fs", _fs)')
        assert "PyFloat_FromDouble((double)self->fs)" in init
        # After the extraction that removes the segment's own keyword, and
        # before the source is built from what is left.
        assert init.index('PyDict_DelItemString(kw, "fs")') < put
        assert put < init.index("PyObject_Call(")

    def test_no_segment_fs_forwards_nothing(self) -> None:
        m = _module()
        fields = m["segment"]["fields"]
        fields[:] = [f for f in fields if f["name"] not in ("fs", "span")]
        assert '"fs", _fs' not in self._seg(m)


_DRIVER = """\
import sys
sys.path.insert(0, "src")
from winp.win.win import Src, Seg

# Each line is one claim of gh-2725.
assert Src(bw=10e6).fs == 1e6, "the source type's own default"

seg = Seg(bw=10e6, fs=40e6)
assert seg.sources[0].fs == seg.fs == 40e6, ("given fs reaches the source",
                                             seg.sources[0].fs)
assert seg.sources[0].window == seg.window == 8, (
    "a read through the source agrees with the flat one",
    seg.sources[0].window, seg.window)

seg = Seg(bw=10e6)
assert seg.sources[0].fs == seg.fs == 2e6, ("the segment's DEFAULT fs "
                                            "reaches the source",
                                            seg.sources[0].fs)

# Separate objects afterwards: the setter moves the segment only.
seg.fs = 80e6
assert seg.sources[0].fs == 2e6, seg.sources[0].fs

# A summed source keeps the fs it was built with.
s = Src(bw=10e6, fs=4e6)
assert Seg.sum(s, fs=40e6).sources[0].fs == 4e6, s.fs
print("INLINE FS OK")
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    if base._NO_TOOLCHAIN:
        pytest.skip("no cmake / C compiler")
    return flat.build(tmp_path_factory.mktemp("gh2725"), _module())


@pytest.mark.slow
@pytest.mark.skipif(base._NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_inline_source_is_built_at_the_segments_fs(project: Path) -> None:
    driver = project / "drive_gh2725.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(driver)],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0 and "INLINE FS OK" in r.stdout, (
        r.stdout + r.stderr
    )
