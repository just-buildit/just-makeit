"""gh-2292: every object jm's generated C allocates starts zeroed.

gh-2272 added ``jm_inflight_t _jm_inflight`` to each type that releases the
GIL, and relied on the object starting zeroed: ``n == 0`` and no ``idle`` lock.
Every type got that from ``tp_alloc`` / ``PyType_GenericNew`` except the
composer's ``stream()`` iterator, which was allocated with ``PyObject_New``.
That call returns UNINITIALISED memory, and the iterator named only its own
fields. So a composer with a ``realtime`` clock (the iterator that releases
the GIL around ``pace``) handed a garbage lock to ``PyThread_free_lock`` on
dealloc. Every doppler ``wfm_compose`` ``stream()`` test aborted on 0.105.1
and 0.105.2, on every platform.

Two checks, because each misses what the other catches:

* ``test_no_generator_allocates_unzeroed`` reads jm's own generators. It is
  registration-free, so a third iterator, or any new type, is held to the
  rule without anyone listing it. It covers BOTH allocation sites that held
  ``PyObject_New`` (the composer stream and an object's ``stream()``
  iterator). The second sets every field by hand today, so only this check
  can see it: no runtime test could fail for it.
* ``test_a_realtime_composer_stream_survives_dealloc`` builds the composer
  and drops a paced iterator under ``PYTHONMALLOC=debug``, which fills each
  fresh allocation with ``0xCD``. Uninitialised memory is then never zero by
  luck, which is how this shipped: a fresh heap read as zero often enough
  that jm's suite never saw it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli
from test_gh2283_composer_doc_faces import (
    _BACKINGS,
    _CC,
    _COMPOSER,
    _ENUM,
    _import,
    _layout,
)

_SRC = Path(__file__).resolve().parents[1] / "src" / "just_makeit"

#: The CPython allocators that return memory NOT zeroed. ``tp_alloc``
#: (``PyType_GenericAlloc``) and ``PyType_GenericNew`` zero the whole object;
#: these initialise only the header and leave every field as garbage.
_UNZEROED = re.compile(
    r"\bPyObject_(?:GC_)?(?:New|NEW|NewVar|NEW_VAR|Init|INIT|InitVar)\s*\("
)


def test_no_generator_allocates_unzeroed() -> None:
    """No generator or template spells a non-zeroing allocator."""
    hits = []
    for path in sorted(_SRC.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".c", ".h"}:
            continue
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if _UNZEROED.search(line):
                hits.append(f"{path.relative_to(_SRC)}:{n}: {line.strip()}")
    assert not hits, (
        "allocate through the type's tp_alloc, which zeroes the object, so a "
        "field nobody initialises (gh-2272's _jm_inflight) is not garbage "
        "(gh-2292):\n  " + "\n  ".join(hits)
    )


#: The fixture's partial realtime table, and the FULL one this test needs:
#: only all three clock functions give ``stream()`` a ``realtime=`` and the
#: iterator its ``_jm_inflight`` field.
_PARTIAL_RT = '[module.playlist.composer.realtime]\npace = "playlist_pace"\n'
_FULL_RT = """[module.playlist.composer.realtime]
clock_create = "playlist_clock_create"
pace = "playlist_clock_pace"
destroy = "playlist_clock_destroy"
header = "<<P>>playlist/playlist_clock.h"
"""

_CLOCK_H = """\
#ifndef PLAYLIST_CLOCK_H
#define PLAYLIST_CLOCK_H
#include <stddef.h>
void *playlist_clock_create(double fs, int flags);
void playlist_clock_pace(void *clk, size_t n);
void playlist_clock_destroy(void *clk);
#endif
"""

#: Appended to the fixture's backing: a clock that does not sleep.
_CLOCK_C = """
#include "<<P>>playlist/playlist_clock.h"
void *playlist_clock_create(double fs, int flags)
{
    (void)fs; (void)flags;
    return calloc(1, 1);
}
void playlist_clock_pace(void *clk, size_t n) { (void)clk; (void)n; }
void playlist_clock_destroy(void *clk) { free(clk); }
"""

#: Run in a fresh interpreter, so PYTHONMALLOC applies from its first
#: allocation. The iterator is dropped both unstarted and drained: dealloc is
#: where the garbage lock was freed.
_PROBE = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("playlist", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
mix = m.Mix(m.Track.sum(m.Clip(), dur=4))
for _ in range(64):
    it = mix.stream(block=2, realtime=1e9)
    del it
    list(mix.stream(block=2, realtime=1e9))
print("ok")
"""


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
def test_a_realtime_composer_stream_survives_dealloc(tmp_path) -> None:
    assert run_cli("new", "fc", "--no-c-prefix", cwd=tmp_path).returncode == 0
    proj = tmp_path / "fc"
    manifest = proj / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + _ENUM, encoding="utf-8"
    )
    assert _COMPOSER.count(_PARTIAL_RT) == 1
    (proj / "modules").mkdir(exist_ok=True)
    (proj / "modules" / "playlist.toml").write_text(
        _layout(_COMPOSER.replace(_PARTIAL_RT, _FULL_RT), proj),
        encoding="utf-8",
    )
    backings = dict(_BACKINGS)
    backings["native/inc/<<P>>playlist/playlist_clock.h"] = _CLOCK_H
    backings["native/src/playlist/playlist.c"] += _CLOCK_C
    for rel, text in backings.items():
        path = proj / _layout(rel, proj)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_layout(text, proj), encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    ext = (proj / "native/src/playlist/playlist_ext.c").read_text(
        encoding="utf-8"
    )
    # The shape that crashed: the iterator carries gh-2272's inflight field.
    (it_struct,) = re.findall(
        r"typedef struct \{[^}]*\} MixStreamObject;", ext
    )
    assert "jm_inflight_t _jm_inflight;" in it_struct, it_struct
    so = _import(proj).__file__
    r = subprocess.run(
        [sys.executable, "-c", _PROBE, so],
        env={**os.environ, "PYTHONMALLOC": "debug"},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0 and r.stdout.strip() == "ok", (
        f"exit {r.returncode}\n{r.stdout}\n{r.stderr}"
    )
