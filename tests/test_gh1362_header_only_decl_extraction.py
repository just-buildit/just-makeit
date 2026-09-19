"""apply must not read a header-only component's statements as declarations.

gh-1362. `_init._core_h_decl_lines` pulls prototypes out of jm's rendered
header so `apply` can inject the ones the author's header lacks. It accepted
any line holding ``(`` and ending in ``);`` -- and ``free(state);`` is such a
line. A header-only component's render (gh-1311) is full of ``static inline``
bodies, so their statements came back as "declarations".

Untouched, each already appeared in the real header (inside its body), so
nothing was injected and the bug hid. Once an author moved the bodies out of
the header -- the family-macro design of gh-1310 does exactly that -- `apply`
wrote ``q_state_t *obj = calloc(1, sizeof(*obj));`` and ``free(state);`` at
file scope, the header stopped compiling, and `status --check` still exited
0, because it replays the same injection on both sides.

The unit oracle below is independent of the function under test: a C
prototype names its function AFTER at least a return type, and contains no
``=``. A statement fails one or the other.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._init import _core_h_decl_lines  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402

from _jmrun import JmRun, run_cli

_HAVE_TOOLCHAIN = bool(shutil.which("cmake")) and any(
    shutil.which(c) for c in ("cc", "gcc", "clang")
)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _header_only(tmp_path: Path) -> Path:
    """A header-only `q`: state, a method and a computed property."""
    root = tmp_path / "p"
    _quiet(new_run, "p", root)
    _quiet(
        object_run,
        root,
        "q",
        None,
        state_vars=[("scale", "float", "1.0")],
        arg_type="float",
        return_type="int16_t",
        header_only=True,
    )
    _quiet(method_run, root, "q", "gain", None, "float", "float", False, [])
    _quiet(property_run, root, "q", "level", None, "double", False)
    return root


def _is_prototype(line: str) -> bool:
    head = line[: line.index("(")]
    return "=" not in line and (len(head.split()) >= 2 or "*" in head)


def test_nothing_from_inside_a_body_is_a_declaration(tmp_path):
    root = _header_only(tmp_path)
    text = (root / "native" / "inc" / "q" / "q_core.h").read_text()
    # The fixture must have bodies to be misread, or this passes vacuously.
    assert "free(state);" in text and "calloc(" in text
    decls = _core_h_decl_lines(text)
    assert all(_is_prototype(d) for d in decls), decls


def test_a_body_moved_out_of_the_header_is_not_injected_back(tmp_path):
    """The reported repro: the header is byte-identical after `apply`."""
    root = _header_only(tmp_path)
    h = root / "native" / "inc" / "q" / "q_core.h"
    _move_bodies_to_family(root, h)
    before = h.read_text(encoding="utf-8")
    _quiet(apply_run, root)
    assert h.read_text(encoding="utf-8") == before


# ── the family-macro shape gh-1310 builds on ────────────────────────────────

_FAMILY = r"""#ifndef FAM_H
#define FAM_H
#include <stdlib.h>
#define DECLARE_Q(id, elem) \
static inline elem id##_step(id##_state_t *s, float x) \
{ return (elem)(x * s->scale); } \
static inline id##_state_t *id##_create(float scale) \
{ id##_state_t *o = calloc(1, sizeof *o); if (o) o->scale = scale; \
  return o; } \
static inline void id##_destroy(id##_state_t *s) { free(s); } \
static inline void id##_reset(id##_state_t *s) { s->scale = 1.0f; } \
static inline void id##_steps(id##_state_t *s, const float *in, elem *out, \
                              size_t n) \
{ for (size_t i = 0; i < n; i++) out[i] = id##_step(s, in[i]); } \
static inline float id##_get_scale(const id##_state_t *s) \
{ return s->scale; } \
static inline void id##_set_scale(id##_state_t *s, float v) { s->scale = v; } \
static inline float id##_gain(id##_state_t *s, float x) { (void)s; return x; } \
static inline double id##_get_level(const id##_state_t *s) \
{ (void)s; return 0.0; }
#endif
"""


def _move_bodies_to_family(root: Path, header: Path) -> None:
    """Every static inline body in *header* becomes a declaration; the
    definitions come from one family macro."""
    text = header.read_text(encoding="utf-8")
    start = text.index("static inline")
    end = text.index("#ifdef __cplusplus\n}")
    decls = (
        "static inline int16_t q_step(q_state_t *state, float x);\n"
        "static inline q_state_t *q_create(float scale);\n"
        "static inline void q_destroy(q_state_t *state);\n"
        "static inline void q_reset(q_state_t *state);\n"
        "static inline void q_steps(q_state_t *state, const float *input,"
        " int16_t *output, size_t n);\n"
        "static inline float q_get_scale(const q_state_t *state);\n"
        "static inline void q_set_scale(q_state_t *state, float val);\n"
        "static inline float q_gain(q_state_t *state, float x);\n"
        "static inline double q_get_level(const q_state_t *state);\n"
        '\n#include "fam/fam.h"\nDECLARE_Q(q, int16_t)\n'
    )
    header.write_text(text[:start] + decls + text[end:], encoding="utf-8")
    fam = root / "native" / "inc" / "fam"
    fam.mkdir()
    (fam / "fam.h").write_text(_FAMILY, encoding="utf-8")


def _cli(*args, cwd) -> JmRun:
    # gh-1374: in THIS process -- the child bought isolation only.
    return run_cli(*args, cwd=cwd)


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="needs cmake and a C compiler")
def test_the_family_macro_shape_still_builds_after_apply(tmp_path):
    """Compiled: the shape gh-1310 needs survives `apply` and passes."""
    root = _header_only(tmp_path)
    _move_bodies_to_family(root, root / "native" / "inc" / "q" / "q_core.h")
    _quiet(apply_run, root)
    r = _cli("test", cwd=root)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]


def test_the_oracle_rejects_what_was_injected():
    """The oracle must be able to fail: the two lines #1362 injected."""
    assert not _is_prototype("q_state_t *obj = calloc(1, sizeof(*obj));")
    assert not _is_prototype("free(state);")
    assert _is_prototype("void q_destroy(q_state_t *state);")
    assert _is_prototype("q_state_t *q_create(float scale);")
