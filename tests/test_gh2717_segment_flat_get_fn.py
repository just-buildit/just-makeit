"""A Segment's flat source-field read resolves at the segment's ``fs``
(gh-2717).

gh-2710 let a source field read through ``get_fn``, handed the source's
``fs``. A ``flat_sources`` Segment's flat getter delegated to the inner
source's attribute -- and the inner source holds the source type's default
``fs``, because the segment's ``fs`` keyword is the segment's. doppler's
``Segment(type="ofdm", fs=40e6, bandwidth=20e6).window`` therefore read
the unset sentinel while ``Synth(...)`` with the same keywords read 4.

The flat getter of a field naming ``get_fn`` now calls the source type's
``<Src>_read_<field>`` with the segment's own ``fs``, the rate ``Synth``'s
getter passes its own.

GATE: compiled, ``Seg(bw=..., fs=X).window == Src(bw=..., fs=X).window``
and differs from the inner source's own (default-``fs``) read; the stored
member, the record and a field without ``get_fn`` are unchanged.
"""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli

from just_makeit import _composer
from just_makeit import _config as C

sys.path.insert(0, str(Path(__file__).parent))
import test_gh2710_composer_field_get_fn as base  # noqa: E402
from test_gh1711_composer_owned_pointer import CJSON_C, CJSON_H  # noqa: E402

PKG = base.PKG


def _module() -> dict:
    """gh-2710's composer with ``flat_sources`` on, and a source field with
    no ``get_fn`` (``bw``) beside the one with it (``window``)."""
    m = copy.deepcopy(base._module())
    m["segment"]["flat_sources"] = True
    return m


class TestRender:
    def _seg(self, m: dict) -> str:
        cfg = {"project": {"name": PKG}, "module": {"win": m}}
        return _composer.render_segment_type(cfg, "win")

    def test_the_flat_read_is_handed_the_segments_fs(self) -> None:
        seg = self._seg(_module())
        assert "Src_read_window((SrcObject *)_it, self->fs)" in seg
        # The cast is behind a type check: `sources` is a live list.
        assert "if (PyObject_TypeCheck(_it, &SrcType))" in seg
        # A field with no get_fn still delegates to the source's getter.
        assert 'return PyObject_GetAttrString(_it, "bw");' in seg

    def test_no_segment_fs_reads_at_the_sources_own(self) -> None:
        m = _module()
        fields = m["segment"]["fields"]
        fields[:] = [f for f in fields if f["name"] not in ("fs", "span")]
        seg = self._seg(m)
        assert "Src_read_window" not in seg
        assert 'return PyObject_GetAttrString(_it, "window");' in seg


_DRIVER = """\
import sys
sys.path.insert(0, "src")
from winp.win.win import Src, Seg

SIZE_MAX = 2**64 - 1

# Each line is one claim of gh-2717.
src = Src(bw=10e6, fs=40e6)
seg = Seg(bw=10e6, fs=40e6, count=4000)
inner = seg.sources[0]
assert src.window == 8, src.window
assert seg.window == src.window, ("flat read at the segment's fs",
                                  seg.window, src.window)
# gh-2725: the inline source is built at the segment's fs, and a later
# `seg.fs` moves the segment only -- so the two rates now differ, and the
# flat read must follow the segment's, not the inner source's.
seg.fs = 80e6
assert inner.fs == 40e6, ("the inner source keeps its own fs", inner.fs)
assert seg.window == 16, ("the flat read follows the segment's fs",
                          seg.window)
assert seg.window != inner.window, ("not the inner source's fs",
                                    seg.window, inner.window)
# A stored value reads as itself; a field with no get_fn is unchanged.
inner.window = 5
assert seg.window == 5, seg.window
assert seg.bw == 10e6, seg.bw

# `sources` is the live list: a foreign object in slot 0 is read by
# attribute, never cast to a source -- an AttributeError, not a crash.
class Foreign:
    window = 7
seg.sources[0] = Foreign()
assert seg.window == 7, seg.window
seg.sources[0] = 5
try:
    seg.window
except AttributeError:
    pass
else:
    raise AssertionError("an int has no window")
print("FLAT GET_FN OK")
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    if base._NO_TOOLCHAIN:
        pytest.skip("no cmake / C compiler")
    return build(tmp_path_factory.mktemp("gh2717"), _module())


def build(
    tmp: Path,
    module: dict,
    header: str = base._HEADER,
    resolve: str = base._RESOLVE,
    enums: "list[dict] | None" = None,
) -> Path:
    """gh-2710's backing project with *module* as its composer, built
    under ``-Werror``; gh-2725 builds its own variant through this,
    gh-2747 its own *header* and *resolve* C as well, and gh-2753 the
    ``[[enum]]`` tables (*enums*) its fields name."""
    assert run_cli("new", PKG, cwd=tmp).returncode == 0
    proj = tmp / PKG
    inc = proj / "native" / "inc" / PKG / "win"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "win_core.h").write_text(header, encoding="utf-8")
    backing = proj / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    (backing / "win_core.c").write_text(base._SOURCE, encoding="utf-8")
    (backing / "win_resolve.c").write_text(resolve, encoding="utf-8")
    (backing / "cJSON.h").write_text(CJSON_H, encoding="utf-8")
    (backing / "cJSON.c").write_text(CJSON_C, encoding="utf-8")
    (backing / "CMakeLists.txt").write_text(
        base._BACKING_CMAKE, encoding="utf-8"
    )
    cfg = C.load(proj)
    cfg["project"]["c_deps"] = ["backing"]
    cfg["project"]["status_allow"] = ["CMakeLists.txt:backing_core"]
    cfg.setdefault("module", {})["win"] = module
    if enums:
        cfg["enum"] = enums
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    base._run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            f"-DPython3_EXECUTABLE={sys.executable}",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        proj,
    )
    base._run(["cmake", "--build", "build", "--parallel", "4"], proj)
    return proj


@pytest.mark.slow
@pytest.mark.skipif(base._NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_flat_read_resolves_at_the_segments_fs(project: Path) -> None:
    driver = project / "drive_gh2717.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(driver)],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0 and "FLAT GET_FN OK" in r.stdout, (
        r.stdout + r.stderr
    )
