"""A template's C family: one hand-written macro, N header-only cores (gh-1310
part 2).

Part 1 made the MANIFESTS of a family unable to diverge. This makes the C
unable to either. Each instance is ``header_only`` and names a family macro:

- ``core_macro`` / ``core_args`` / ``core_header`` -- all three or none, and
  only on a ``header_only`` component, refused at ``load`` otherwise.
- The instance header holds DECLARATIONS only, each ``static inline`` and
  each under its doc comment, then ``#include`` of the family header and one
  ``DECLARE_...(...)`` line. jm writes no body there, from any route --
  scaffold, or ``apply`` replaying a member the template declares.
- ``apply`` keeps that one line in sync with the instance row, through the
  same writer that keeps the prototypes in sync (``_refresh_core_h_decls``),
  for standalone and module instances alike.
- The family header is the author's. ``apply`` refuses, before writing
  anything, when it does not exist.
"""

# gh-1591: this file's hand-written C and expectations spell jm's bare
# derived names, so its projects opt out of the prefix `jm new` now
# defaults to; the default is gated by tests/test_gh1591_*.py.

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402
from just_makeit import _incpath as INC  # noqa: E402

import contextlib
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _status  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402

_HAVE_TOOLCHAIN = bool(shutil.which("cmake")) and any(
    shutil.which(c) for c in ("cc", "gcc", "clang")
)

# `sat` reaches only `core_args`, so changing it moves the invocation line and
# nothing else -- no kwarg, no signature.
TEMPLATE = """
[template.f32_to_int]
params = ["elem", "sat"]
<<module>>header_only = "true"
arg_type = "float"
return_type = "{elem}"
core_macro = "DECLARE_F32_TO_INT"
core_args = ["{id}", "{elem}", "{sat}"]
core_header = "<<P>>cvt/f32_to_int.h"

[[template.f32_to_int.state]]
name = "scale"
type = "float"
default = "1.0f"

[[template.f32_to_int.instances]]
id = "f32_to_i16"
elem = "int16_t"
sat = "32767.0f"

[[template.f32_to_int.instances]]
id = "f32_to_i8"
elem = "int8_t"
sat = "127.0f"
"""

#: Every function one member has, defined once for the whole family.
FAMILY_H = r"""#ifndef F32_TO_INT_H
#define F32_TO_INT_H
#include <math.h>
#include <stdlib.h>
#define DECLARE_F32_TO_INT(id, elem, SAT)                                     \
  static inline elem id##_step (const id##_state_t *s, float x)              \
  {                                                                          \
    float y = roundf (x * s->scale);                                         \
    return (elem)(y > SAT ? SAT : y < -SAT ? -SAT : y);                      \
  }                                                                          \
  static inline id##_state_t *id##_create (float scale)                      \
  {                                                                          \
    id##_state_t *o = calloc (1, sizeof *o);                                 \
    if (o)                                                                   \
      o->scale = scale;                                                      \
    return o;                                                                \
  }                                                                          \
  static inline void id##_destroy (id##_state_t *s) { free (s); }            \
  static inline void id##_reset (id##_state_t *s) { s->scale = 1.0f; }       \
  static inline void id##_steps (id##_state_t *s, const float *in,           \
                                 elem *out, size_t n)                        \
  {                                                                          \
    for (size_t i = 0; i < n; i++)                                           \
      out[i] = id##_step (s, in[i]);                                         \
  }                                                                          \
  static inline float id##_get_scale (const id##_state_t *s)                 \
  {                                                                          \
    return s->scale;                                                         \
  }                                                                          \
  static inline void id##_set_scale (id##_state_t *s, float v)               \
  {                                                                          \
    s->scale = v;                                                            \
  }
#endif
"""


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            return fn(*a, **kw)


def _project(
    tmp_path: Path,
    *,
    module: bool = True,
    template: str = TEMPLATE,
    family: "str | None" = FAMILY_H,
) -> Path:
    root = tmp_path / "p"
    _quiet(new_run, "p", root, c_prefix=None)
    if module:
        _quiet(module_run, root, "cvt")
    toml = root / C.FILENAME
    toml.write_text(
        toml.read_text(encoding="utf-8")
        + template.replace(
            "<<module>>", 'module = "cvt"\n' if module else ""
        ).replace("<<P>>", INC.prefix(root)),
        "utf-8",
    )
    if family is not None:
        fh = root / INC_ROOT / "cvt" / "f32_to_int.h"
        fh.parent.mkdir(parents=True, exist_ok=True)
        fh.write_text(family, "utf-8")
        _quiet(apply_run, root)
    return root


def _header(root: Path, iid: str = "f32_to_i16") -> Path:
    return root / INC_ROOT / iid / f"{iid}_core.h"


def _invocations(text: str) -> "list[str]":
    return re.findall(r"^DECLARE_F32_TO_INT .*$", text, re.MULTILINE)


# ── the header ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("module", [True, False], ids=["module", "standalone"])
def test_the_header_declares_and_never_defines(tmp_path, module):
    root = _project(tmp_path, module=module)
    text = _header(root).read_text()
    # An independent oracle for "no body": a function body is the only `{`
    # that follows a `)`. The struct's `{` follows `struct`.
    assert not re.search(r"\)\s*\{", text), text
    protos = re.findall(r"^[a-z].*\bf32_to_i16_\w+\(.*\);$", text, re.M)
    assert protos, "the fixture must declare something to check"
    assert all(p.startswith("static inline ") for p in protos), protos
    for fn in ("create", "destroy", "reset", "step", "steps", "get_scale"):
        assert re.search(rf"\bf32_to_i16_{fn}\(", text), fn
    assert _invocations(text) == [
        "DECLARE_F32_TO_INT (f32_to_i16, int16_t, 32767.0f)"
    ]
    assert f'#include "{INC.include("cvt/f32_to_int.h", root)}"' in text


def test_each_declaration_keeps_its_doc(tmp_path):
    """The point of declarations over the macro alone: Doxygen and the
    derived Python docstrings read them."""
    text = _header(_project(tmp_path)).read_text()
    assert re.search(
        r"\* @brief Process one input sample\.[^/]*\*/\s*\n"
        r"static inline int16_t f32_to_i16_step\(",
        text,
    ), text


def test_a_second_apply_changes_nothing(tmp_path):
    root = _project(tmp_path)
    before = _header(root).read_text()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        apply_run(root)
    assert "nothing to do" in out.getvalue()
    assert _header(root).read_text() == before
    assert _quiet(_status.run, root, check=True) == 0


# ── keeping the invocation in sync ──────────────────────────────────────────


def _set_sat(root: Path, old: str, new: str) -> None:
    toml = root / C.FILENAME
    text = toml.read_text(encoding="utf-8")
    assert text.count(f'sat = "{old}"') == 1
    toml.write_text(text.replace(f'sat = "{old}"', f'sat = "{new}"'), "utf-8")


@pytest.mark.parametrize("module", [True, False], ids=["module", "standalone"])
def test_a_changed_arg_is_drift_and_apply_rewrites_the_line(tmp_path, module):
    root = _project(tmp_path, module=module)
    _set_sat(root, "32767.0f", "16383.0f")
    assert _quiet(_status.run, root, check=True) != 0
    _quiet(apply_run, root)
    text = _header(root).read_text()
    assert _invocations(text) == [
        "DECLARE_F32_TO_INT (f32_to_i16, int16_t, 16383.0f)"
    ]
    assert _quiet(_status.run, root, check=True) == 0


def test_a_changed_type_reaches_the_prototypes(tmp_path):
    """The gh-133 guard skipped a name the header declared `static inline`,
    taking the declaration for a definition -- so the invocation moved to
    `int32_t` and every prototype stayed `int16_t`."""
    root = _project(tmp_path)
    toml = root / C.FILENAME
    toml.write_text(
        toml.read_text().replace('elem = "int16_t"', 'elem = "int32_t"'),
        "utf-8",
    )
    _quiet(apply_run, root)
    text = _header(root).read_text()
    assert "static inline int32_t f32_to_i16_step(" in text
    assert "int32_t *output" in text
    assert "int16_t f32_to_i16_step(" not in text


def test_a_removed_line_comes_back(tmp_path):
    root = _project(tmp_path)
    h = _header(root)
    h.write_text(
        "\n".join(
            ln for ln in h.read_text().splitlines() if not _invocations(ln)
        )
        + "\n"
    )
    _quiet(apply_run, root)
    assert len(_invocations(h.read_text())) == 1


def test_an_invocation_in_a_comment_is_not_the_line(tmp_path):
    """`@code` examples show the macro; the one jm keeps is in code."""
    root = _project(tmp_path)
    h = _header(root)
    text = h.read_text()
    doc = "/*\nDECLARE_F32_TO_INT (example, int16_t, 1.0f)\n*/\n"
    h.write_text(text.replace("typedef struct", doc + "typedef struct", 1))
    _set_sat(root, "32767.0f", "16383.0f")
    _quiet(apply_run, root)
    after = h.read_text()
    assert "DECLARE_F32_TO_INT (example, int16_t, 1.0f)" in after
    assert "DECLARE_F32_TO_INT (f32_to_i16, int16_t, 16383.0f)" in after


def test_two_invocations_are_left_alone_and_reported(tmp_path):
    root = _project(tmp_path)
    h = _header(root)
    line = _invocations(h.read_text())[0]
    h.write_text(h.read_text().replace(line, f"{line}\n{line}"))
    _set_sat(root, "32767.0f", "16383.0f")
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(err):
            apply_run(root)
    assert _invocations(h.read_text()) == [line, line]
    assert "invokes DECLARE_F32_TO_INT 2 times" in err.getvalue()


# ── no body from any route ──────────────────────────────────────────────────


def test_a_member_the_template_gains_gets_a_declaration_only(tmp_path):
    """`apply` replays a template's properties onto each instance in its temp
    tree, through the verb that appends a header-only body (gh-1303). For a
    family member that body would be a second definition."""
    root = _project(tmp_path)
    toml = root / C.FILENAME
    toml.write_text(
        toml.read_text().replace(
            "[[template.f32_to_int.instances]]",
            "[[template.f32_to_int.properties]]\n"
            'name = "level"\ntype = "double"\n\n'
            "[[template.f32_to_int.instances]]",
            1,
        ),
        "utf-8",
    )
    _quiet(apply_run, root)
    text = _header(root).read_text()
    assert "static inline double f32_to_i16_get_level(" in text
    assert not re.search(r"\)\s*\{", text), text


# ── refusals ────────────────────────────────────────────────────────────────


def test_apply_refuses_a_missing_family_header_before_writing(tmp_path):
    root = _project(tmp_path, family=None)
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(err), pytest.raises(SystemExit):
            apply_run(root)
    rel = INC.rel("cvt/f32_to_int.h", root)
    assert f"{rel} does not exist" in err.getvalue()
    assert not _header(root).exists()


@pytest.mark.parametrize(
    "edit, says",
    [
        (
            ('core_header = "<<P>>cvt/f32_to_int.h"\n', ""),
            "but not core_header",
        ),
        (('header_only = "true"\n', ""), 'without `header_only = "true"`'),
        (('"DECLARE_F32_TO_INT"', '"DECLARE-X"'), "is not a C identifier"),
        (('["{id}", "{elem}", "{sat}"]', "[]"), "non-empty list of strings"),
    ],
    ids=["missing-key", "not-header-only", "bad-macro", "empty-args"],
)
def test_a_bad_family_is_refused_at_load(tmp_path, edit, says):
    root = _project(tmp_path, template=TEMPLATE.replace(*edit), family=None)
    err = io.StringIO()
    with contextlib.redirect_stderr(err), pytest.raises(SystemExit):
        C.load(root)
    assert says in err.getvalue()


def test_the_temp_manifest_round_trips_the_family(tmp_path):
    """apply's replay rewrites each instance as a plain component through
    `add_component` and `_dump`; both enumerate keys, so an unnamed one is
    silently absent -- and then the replayed header has bodies again."""
    fam = C.CoreFamily("DECLARE_Q", ("q", '"a \\"b\\""'), "fam/q.h")
    cfg: dict = {"project": {"name": "p", "version": "0.1.0"}}
    C.add_component(cfg, "q", [], header_only_=True, core_family_=fam)
    back = C.tomllib.loads(C._dump(cfg))
    assert C.core_family(back, "q") == fam


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="needs cmake + a C compiler")
@pytest.mark.parametrize("module", [True, False], ids=["module", "standalone"])
def test_it_builds_and_passes(tmp_path, module):
    root = _project(tmp_path, module=module)
    r = subprocess.run(
        ["make", "test"], cwd=root, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="needs cmake + a C compiler")
def test_a_member_the_family_forgets_fails_the_build_by_name(tmp_path):
    """jm writes no body for a family member, so nothing fills the gap when
    the template declares a property the family header does not define. The
    gh-1361 address-taking test is what turns that into a build failure that
    names the function, rather than a binding that happens to link."""
    root = _project(tmp_path)
    toml = root / C.FILENAME
    toml.write_text(
        toml.read_text().replace(
            "[[template.f32_to_int.instances]]",
            "[[template.f32_to_int.properties]]\n"
            'name = "level"\ntype = "double"\n\n'
            "[[template.f32_to_int.instances]]",
            1,
        ),
        "utf-8",
    )
    _quiet(apply_run, root)
    r = subprocess.run(
        ["make", "test"], cwd=root, capture_output=True, text=True
    )
    assert r.returncode != 0
    assert "f32_to_i16_get_level" in r.stdout + r.stderr


def test_an_unclosed_invocation_is_left_alone(tmp_path):
    """Scanning for its `)` would otherwise run to EOF, and the rewrite would
    take the rest of the header with it."""
    root = _project(tmp_path)
    h = _header(root)
    line = _invocations(h.read_text())[0]
    broken = h.read_text().replace(line, line.rstrip(")"))
    h.write_text(broken)
    _set_sat(root, "32767.0f", "16383.0f")
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(err):
            apply_run(root)
    assert h.read_text() == broken
    assert "never closes" in err.getvalue()
