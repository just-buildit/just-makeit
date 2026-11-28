"""A composer Segment, Timeline or Composer read before ``__init__`` ran
does not crash (gh-2750).

The Segment type is a base type built by ``PyType_GenericNew``, so a
``Seg.__new__(Seg)`` -- or a subclass whose ``__init__`` never calls the
base's -- held a ``NULL`` ``sources`` until ``tp_init`` or ``sum`` set it.
``Seg_get_sources`` did ``Py_INCREF`` on it and the flat single-source
accessors ``PyList_GET_SIZE`` it: a segfault from pure Python. The Timeline
had the same shape over ``segments`` (its getter, ``len``, indexing,
iteration and ``add``).

Decision: the fix is where the object is made, not at each reader. Each
type has its own ``tp_new`` that allocates the empty list, so ``sources`` /
``segments`` is never ``NULL`` and no reader guards it. An un-initialised
Segment has no sources: ``sources`` reads ``[]`` and a flat accessor raises
the ``AttributeError`` a multi-source one does (it is "only on a
single-source Seg"); a Timeline is empty. The Composer's state is the
same shape (``segments``, ``repeat``, ``to_json`` and ``compose`` all
read ``self->state`` through NULL): it is born *closed*, so each refuses
with the ``RuntimeError`` every closed composer raises and ``__init__``
opens it -- a state a guard already covers, so no reader changed.

GATE: compiled, every reader of an un-initialised Segment, Timeline and
Composer (``__new__`` alone, and a subclass skipping ``__init__``) answers
instead of crashing; the rendered C has no ``PyType_GenericNew`` on any of
the three.
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
    """gh-2717's composer (flat single-source accessors) with a Timeline."""
    m = copy.deepcopy(flat._module())
    m["timeline"] = {"type_name": "Tl", "loop": ["once"]}
    return m


def _cfg() -> dict:
    return {"project": {"name": PKG}, "module": {"win": _module()}}


class TestRender:
    def test_a_segment_and_a_timeline_have_their_own_tp_new(self) -> None:
        seg = _composer.render_segment_type(_cfg(), "win")
        tl = _composer.render_timeline_type(_cfg(), "win")
        comp = _composer.render_composer_type(_cfg(), "win")
        assert ".tp_new       = Seg_new," in seg
        assert ".tp_new       = Tl_new," in tl
        assert ".tp_new       = Win_new," in comp
        assert "PyType_GenericNew" not in seg + tl + comp

    def test_the_new_allocates_the_list_before_anything_reads_it(
        self,
    ) -> None:
        seg = _composer.render_segment_type(_cfg(), "win")
        body = seg.split("Seg_new(", 1)[1].split("\n}\n", 1)[0]
        order = [
            "type->tp_alloc(type, 0)",
            "self->sources = PyList_New(0);",
            "Py_DECREF(self);",
            "return (PyObject *)self;",
        ]
        at = [body.index(s) for s in order]
        assert at == sorted(at), body


_DRIVER = """\
import sys
sys.path.insert(0, "src")
from winp.win.win import Src, Seg, Tl, Win


class S2(Seg):
    def __init__(self):
        pass


class T2(Tl):
    def __init__(self):
        pass


class W2(Win):
    def __init__(self):
        pass


def refused(call, exc):
    try:
        call()
    except exc:
        return
    raise AssertionError("not refused")


# Each claim is one reader of the un-initialised object.
for make in (lambda: Seg.__new__(Seg), S2):
    seg = make()
    assert seg.sources == [], seg.sources
    # A flat accessor is only on a single-source Seg: an AttributeError.
    refused(lambda: seg.bw, AttributeError)
    refused(lambda: seg.window, AttributeError)
    # The list is live: a source assigned in is read back.
    seg.sources.append(Src(bw=3e6))
    assert seg.bw == 3e6, seg.bw
    # Scalars a subclass skipped read their zero value, not a crash.
    seg.count
    seg.fs

for make in (lambda: Tl.__new__(Tl), T2):
    tl = make()
    assert tl.segments == [], tl.segments
    assert len(tl) == 0
    assert list(tl) == []
    refused(lambda: tl[0], IndexError)
    assert tl.add(Seg(bw=1e6)) is tl
    assert len(tl) == 1
# The Composer is a third reader: born closed, so every method and getter
# refuses with the RuntimeError a closed one does; `__init__` then opens it.
for make in (lambda: Win.__new__(Win), W2):
    w = make()
    for call in (
        lambda: w.segments,
        lambda: w.repeat,
        lambda: w.continuous,
        lambda: w.execute(4),
        lambda: w.to_json(),
        lambda: w.compose(),
    ):
        refused(call, RuntimeError)
    assert w.close() is None
w = Win.__new__(Win)
w.__init__([Seg(bw=1e6, count=8)])
w.execute(4)  # opened: no longer refused
assert len(w.segments) == 1
print("GH2750 OK")
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    if base._NO_TOOLCHAIN:
        pytest.skip("no cmake / C compiler")
    return flat.build(tmp_path_factory.mktemp("gh2750"), _module())


@pytest.mark.slow
@pytest.mark.skipif(base._NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_every_reader_of_an_uninitialised_object_answers(
    project: Path,
) -> None:
    driver = project / "drive_gh2750.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(driver)],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0 and "GH2750 OK" in r.stdout, (
        f"exit {r.returncode}\n" + r.stdout + r.stderr
    )
