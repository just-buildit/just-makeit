"""gh-2264: a composer segment with no fields builds under ``-Werror``.

A ``kind = "composer"`` module's segment type renders ``<Seg>_init`` and
``<Seg>_sum`` with a ``fail:`` tail that each field's parse jumps to. A
segment that declares no ``fields`` has no such jump, so the label was
unreached: ``-Wunused-label`` under ``-Wall``, a hard error under
``-Werror``, in glue ``jm apply`` regenerates and the author cannot fix.
Nothing caught it because the warning sweep builds no composer (gh-1767).

Now each function carries the tail only when its own extraction code can
reach it (``_composer`` segment rendering), as gh-1863 does for a static
helper nothing calls.

GATE: gh-2283's compiled composer fixture, its segment stripped of every
field, compiles with ``-Wall -Wextra -Werror``; and with its field kept,
so the shape that does jump keeps the label. A second check needs no
compiler: every label in the rendered ``_ext.c`` of either shape has a
``goto`` -- read from the text, so a label added later is covered too.
"""

from __future__ import annotations

import re
import subprocess
import sysconfig
from pathlib import Path

import pytest
from _jminc import INC_DIR
from _jmrun import run_cli
from test_gh2283_composer_doc_faces import (
    _BACKINGS,
    _CC,
    _COMPOSER,
    _ENUM,
    _layout,
)

#: The fixture's one segment field; removing it is the issue's shape.
_SEG_FIELD = """\
[[module.playlist.segment.fields]]
name = "dur"
type = "size_t"
default = "8"
"""

_EXT = "native/src/playlist/playlist_ext.c"


def _composer(empty: bool) -> str:
    """The gh-2283 composer, its segment with or without its field."""
    assert _COMPOSER.count(_SEG_FIELD) == 1, "fixture segment field moved"
    return _COMPOSER.replace(_SEG_FIELD, "") if empty else _COMPOSER


def _project(root: Path, empty: bool) -> Path:
    """Scaffold the composer through ``jm apply`` and return the project."""
    assert run_cli("new", "fc", "--no-c-prefix", cwd=root).returncode == 0
    proj = root / "fc"
    manifest = proj / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + _ENUM, encoding="utf-8"
    )
    (proj / "modules").mkdir(exist_ok=True)
    (proj / "modules" / "playlist.toml").write_text(
        _layout(_composer(empty), proj), encoding="utf-8"
    )
    for rel, text in _BACKINGS.items():
        path = proj / _layout(rel, proj)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_layout(text, proj), encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    return proj


@pytest.fixture(scope="module", params=[True, False], ids=["empty", "field"])
def proj(request, tmp_path_factory) -> Path:
    return _project(tmp_path_factory.mktemp("gh2264"), request.param)


def test_every_label_is_reached(proj):
    text = (proj / _EXT).read_text(encoding="utf-8")
    assert "Track_sum(" in text, "fixture renders no segment"
    # A label's scope is its function, and many functions here name one
    # `fail`, so each top-level body (closed by a column-0 brace) is read
    # on its own.
    unreached = [
        f"{fn}: {lab}"
        for body in text.split("\n}\n")
        for fn in re.findall(r"^(\w+)\(", body, re.M)[-1:]
        for lab in re.findall(r"^([A-Za-z_]\w*):$", body, re.M)
        if not re.search(rf"\bgoto {lab};", body)
    ]
    assert not unreached, f"labels no goto reaches: {unreached}"


@pytest.mark.skipif(_CC is None, reason="no C compiler available")
def test_ext_builds_warning_clean(proj):
    import numpy as np

    r = subprocess.run(
        [_CC, "-c", "-fPIC", "-std=c11", "-Wall", "-Wextra", "-Werror",
         "-I", str(proj / INC_DIR),
         "-I", sysconfig.get_path("include"),
         "-I", np.get_include(),
         str(proj / _EXT), "-o", str(proj / "playlist_ext.o")],
        capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
