"""gh-2380: a composer's setting and serializer, compiled and run.

The scan (`test_gh2380_order_scan_composer`) reads the generated C. This is
the truth anchor behind it, on the bundled ``composer_seams`` project: its
``playlist_destroy`` records the pointer it freed, so a backing call handed
that pointer afterwards says so on stderr; and the serializer reports being
called at all. An argument whose conversion runs Python (``__index__``)
closes the composer; the call must then raise ``RuntimeError`` without the
backing seeing the freed state.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli
from test_gh2346_composer_capsule import _cmake, _same_second_as_the_build

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, SRC.as_posix())

from just_makeit import _config as C  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

_DESTROY = "void\nplaylist_destroy (playlist_state_t *state)\n{\n"
_DESTROY_DECL = "void           playlist_destroy (playlist_state_t *state);"

_PROBE = """\
import sys
sys.path.insert(0, "src")
from studio.playlist.playlist import Mix, Track


class Idx:
    def __init__(self, m):
        self.m = m

    def __index__(self):
        self.m.close()          # a teardown during the conversion
        return 5


def attempt(label, call):
    m = Mix([Track(dur=4)])
    try:
        call(m)
        print(label, "returned", flush=True)
    except RuntimeError as e:
        print(label, "RuntimeError", e, flush=True)


attempt("gain", lambda m: setattr(m, "gain", Idx(m)))
attempt("ser", lambda m: m.ser(Idx(m)))
"""


def _build(tmp_path: Path) -> Path:
    sys.path.insert(
        0, str(SRC / "just_makeit" / "examples" / "composer_seams")
    )
    try:
        seams = importlib.import_module("test")
        seams.run(tmp_path)  # the bundled composer project, built
    finally:
        sys.path.pop(0)
        sys.modules.pop("test", None)
    proj = tmp_path / "studio"
    hdr = next(proj.glob("native/inc/**/playlist/playlist_core.h"))
    text = hdr.read_text(encoding="utf-8")
    assert text.count(_DESTROY_DECL) == 1
    hdr.write_text(
        text.replace(
            _DESTROY_DECL,
            _DESTROY_DECL + "\n"
            "void           playlist_set_gain (playlist_state_t *state, "
            "int v);\n"
            "int            playlist_gain (const playlist_state_t *state);\n"
            "char          *playlist_ser (int p, const track_t *segs, "
            "size_t n);",
            1,
        ),
        encoding="utf-8",
    )
    core = proj / "native" / "src" / "backing" / "playlist_core.c"
    c = core.read_text(encoding="utf-8")
    head = _DESTROY + "  if (!state)\n    return;\n"
    assert c.count(head) == 1
    c = c.replace(
        head,
        "static const void *jm_last_destroyed;\n"
        "void\nplaylist_set_gain (playlist_state_t *state, int v)\n{\n"
        "  if ((const void *) state == jm_last_destroyed)\n"
        '    fprintf (stderr, "USE_AFTER_DESTROY set_gain\\n");\n'
        "  (void) v;\n}\n"
        "int\nplaylist_gain (const playlist_state_t *state)\n{\n"
        "  (void) state;\n  return 0;\n}\n"
        "char *\nplaylist_ser (int p, const track_t *segs, size_t n)\n{\n"
        '  fprintf (stderr, "SER_CALLED\\n");\n'
        "  (void) p; (void) segs; (void) n;\n"
        '  return strdup ("{}");\n}\n\n'
        + head
        + "  jm_last_destroyed = state;\n",
        1,
    )
    c = "#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n" + c
    core.write_text(c, encoding="utf-8")
    cfg = C.load(proj)
    cfg["module"]["playlist"]["settings"] = [
        {
            "name": "gain",
            "setter_fn": "playlist_set_gain",
            "getter_fn": "playlist_gain",
            "type": "int",
        }
    ]
    cfg["module"]["playlist"]["serializers"] = [
        {
            "name": "ser",
            "fn": "playlist_ser",
            "params": [{"name": "p", "type": "int"}],
        }
    ]
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, (r.stdout, r.stderr)
    _same_second_as_the_build(proj)
    _cmake(proj, "--build", "build", "--parallel", "4", "--clean-first")
    return proj


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_close_in_conversion_is_an_error_not_a_use_after_free(tmp_path):
    proj = _build(tmp_path)
    (proj / "probe.py").write_text(_PROBE, encoding="utf-8")
    run = subprocess.run(
        [sys.executable, "probe.py"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    assert run.stdout.splitlines() == [
        "gain RuntimeError composer already closed",
        "ser RuntimeError composer already closed",
    ], run.stdout
    # the backing was never handed the freed state, and never called
    assert "USE_AFTER_DESTROY" not in run.stderr, run.stderr
    assert "SER_CALLED" not in run.stderr, run.stderr
