"""gh-1860: the per-object type section is written once.

The CPython glue for one object (its struct, ``tp_new``/``tp_init``/
``tp_dealloc``, ``destroy``, ``__enter__``/``__exit__``, the ``PyMethodDef``
table and the ``PyTypeObject``) used to exist twice: in the standalone
``component_ext.c`` template and as ``_render.COMPONENT_TYPE_SECTION``, a
string literal rendered for every module object. gh-1856 had to fix both by
hand, and a fix landing in one copy builds and passes for every project
shaped like that copy.

Now ``templates/c/src/component_type.c`` is the one copy. The standalone
template splices it in at import, and every module path renders it through
``_render.render_type_section``.

The gate reads jm's shipped source rather than a list of files, so a copy
re-pasted anywhere fails it. It compares LINES, not runs of lines: a copy
is dangerous exactly because it drifts, and a drifted copy (an older
``tp_new`` without gh-1856's casts) shares no run of consecutive lines with
the section, so an exact-run match goes green on it -- measured, by
sabotage, before this was written. What a drifted copy keeps is the section's
slot-bearing lines (``<<ComponentW>>_new(PyTypeObject *type, ...)``). When
this was written, no other file under ``src/just_makeit`` shared even one of
the section's 36; a file sharing ``SHARED`` or more is a copy. Slot-free
lines such as ``Py_INCREF(self);`` are what every generator writes and prove
nothing. The frozen ``stale_project`` tree is the one exclusion: it is a
0.33.14 project's generated output, kept verbatim on purpose.

GATE: no file in jm's source repeats the per-object type section.
"""

from __future__ import annotations

import re
from pathlib import Path

from just_makeit import _render as R

SRC = Path(R.__file__).parent
SECTION = SRC / "templates" / "c" / "src" / "component_type.c"
#: Generated output frozen on purpose (gh-1860's docstring says why).
FROZEN = SRC / "examples" / "stale_project" / "tree"
#: Shared slot-bearing lines that make a copy. Two, so a template that
#: merely reuses one bare slot line (``<<enum_tables>>``) is not one.
SHARED = 2

_WRAPPED = re.compile(r"/\*<<(\w+)>>\*/")


def _norm(text: str) -> "list[str]":
    """Lines with the spelling differences between copies removed.

    A template file spells a slot ``/*<<x>>*/`` and a Python literal
    ``<<x>>``; an f-string doubles its braces. Whitespace is a formatter's
    choice. None of that makes a copy any less of a copy.
    """
    text = _WRAPPED.sub(r"<<\1>>", text)
    text = text.replace("{{", "{").replace("}}", "}")
    return [" ".join(ln.split()) for ln in text.splitlines() if ln.strip()]


def _slot_lines(text: str) -> "set[str]":
    return {ln for ln in _norm(text) if "<<" in ln}


def test_no_second_copy_of_the_type_section():
    signature = _slot_lines(SECTION.read_text(encoding="utf-8"))
    assert len(signature) > SHARED, "the section yielded no slot lines"
    copies = []
    for path in sorted(SRC.rglob("*")):
        if not path.is_file() or path == SECTION:
            continue
        if FROZEN in path.parents or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits = signature & _slot_lines(text)
        if len(hits) >= SHARED:
            copies.append(
                f"{path.relative_to(SRC)}: {len(hits)} section lines, e.g. "
                f"{sorted(hits)[0]!r}"
            )
    assert not copies, (
        "the per-object type section is repeated outside "
        f"{SECTION.relative_to(SRC)}; render it from there instead "
        "(COMPONENT_EXT_C splices it in, render_type_section renders it "
        "for a module):\n  " + "\n  ".join(copies)
    )


def test_standalone_ext_carries_the_section_once_and_no_seam():
    """The splice landed, once, and left no seam slot behind."""
    ext = R.COMPONENT_EXT_C
    assert ext.count("_new(PyTypeObject *type, PyObject *args") == 1
    assert "component_type_section" not in ext
    assert "type_core_include" not in ext


def test_module_section_includes_its_own_core_header():
    """A module object's section opens with that object's header, once."""
    ctx = {
        t: f"@{t}@"
        for t in set(re.findall(r"<<(\w+)>>", R.COMPONENT_TYPE_SECTION))
        - {"type_core_include"}
    }
    ctx.update(inc_prefix="pkg/", component="gain")
    out = R.render_type_section(ctx)
    include = '#include "pkg/gain/gain_core.h"'
    assert out.count(include) == 1
    banner_end = out.index("/* ===", out.index("Object")) + 1
    assert banner_end < out.index(include) < out.index("typedef struct {")
