"""Generated C reaches complex math only through ``clib_common.h`` (gh-1368).

Under clang-cl the platform ``<complex.h>`` is the UCRT's, where ``crealf``
takes an ``_Fcomplex`` struct and ``I`` is one, so jm's own ``crealf(v)`` and
``x * I`` were 154 compile errors on the first Windows run. ``clib_common.h``
maps the C99 names onto clang builtins there. That only works if nothing jm
writes includes ``<complex.h>`` directly: a direct include skips the mapping
in whichever file does it.

Two checks. The source scan is registration-free over every template and
generator module, so a new emitter is covered without being listed. The
generated-tree check runs the real scaffolding and reads what it wrote, so a
path the scan misreads (a template assembled from pieces, say) still fails.
Bundled examples are excluded: their C is user code.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))
PKG = SRC / "just_makeit"

from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

#: A preprocessor line, anchored, so prose that mentions the header does not
#: count and a real include cannot hide behind leading whitespace.
_DIRECT = re.compile(r"^[ \t]*#[ \t]*include[ \t]*<complex\.h>", re.MULTILINE)

#: The one file allowed to include it: it is what does the mapping.
_OWNER = "clib_common.h"


def _sources() -> "list[Path]":
    out = []
    for p in sorted(PKG.rglob("*")):
        rel = p.relative_to(PKG).parts
        if not p.is_file() or "examples" in rel or p.name == _OWNER:
            continue
        if p.suffix in (".py", ".c", ".h") or "templates" in rel:
            out.append(p)
    return out


def test_the_scan_sees_the_generators():
    names = {p.name for p in _sources()}
    assert {
        "_render.py",
        "_handle.py",
        "component_ext.c",
        "jm_test.h",
    } <= names


def test_no_generator_includes_complex_h_directly():
    bad = [
        f"{p.relative_to(PKG)}:{p.read_text(encoding='utf-8')[: m.start()].count(chr(10)) + 1}"
        for p in _sources()
        for m in _DIRECT.finditer(p.read_text(encoding="utf-8"))
    ]
    assert not bad, (
        'include "clib_common.h", not <complex.h> (gh-1368):\n  '
        + "\n  ".join(bad)
    )


def test_a_generated_complex_project_includes_it_once(tmp_path):
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root)
        module_run(root, "dsp")
        object_run(
            root,
            "rot",
            "dsp",
            state_vars=[("phase", "float _Complex", "1.0f")],
            arg_type="float _Complex",
            return_type="float _Complex",
        )
        object_run(
            root,
            "gain",
            None,
            state_vars=[("g", "double _Complex", "1.0")],
            arg_type="double _Complex",
            return_type="double _Complex",
        )
    files = [
        p for p in root.rglob("*") if p.suffix in (".c", ".h") and p.is_file()
    ]
    assert len(files) > 10, "the fixture must generate C to check"
    direct = [
        str(p.relative_to(root))
        for p in files
        if p.name != _OWNER and _DIRECT.search(p.read_text(encoding="utf-8"))
    ]
    assert direct == []
    owners = [p for p in files if p.name == _OWNER]
    assert len(owners) == 1
    assert "#define crealf __builtin_crealf" in owners[0].read_text(
        encoding="utf-8"
    )
