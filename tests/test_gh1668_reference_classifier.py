"""gh-1668 / gh-1669: `jm upgrade` respells what REFERS to a derived name.

The `c_prefix` respell and `apply`'s existing-tree refusal matched every
identifier SPELLED like a derived name. That was wrong in both directions:

- too much (gh-1668): doppler's component `frame` has a method `bits`, so
  `frame_bits` is derived -- and a public struct field, a parameter and a
  local of the same spelling were renamed with it. The tree still compiled
  (the rename was consistent), so nothing noticed but Doxygen; reverting
  them by hand then tripped `apply`'s refusal, forcing the author to rename
  their own API.
- too little (gh-1669): an author macro that token-pastes a stem argument,
  ``pfx##_reset (s)``, makes that ARGUMENT a derived-name reference. The
  call kept the old stem, pasted a name that no longer existed, and the tree
  stopped building.

One classifier, `_csym.references` (plus `_csym.pasted_stems` for a pasted
stem), now answers "is this a reference?" for both the respell and the
refusal, so what one rewrites is what the other reports.

The project is built by running jm; the author edits are the ones a person
makes by hand. The upgraded tree is compiled, because gh-1669's failure
was a build failure and gh-1668's was a rename that still compiled.

GATE: after `c_prefix` + `jm upgrade`, a struct member, member access,
      designated initializer, parameter and local spelled like a derived
      name keep their spelling; a call and a stem pasted by an author macro
      move; `apply` passes; the tree builds and its C tests pass. A macro
      that pastes a stem into a derived AND an author name is refused by
      `upgrade`, naming the call.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _csym
from just_makeit import _textio
from just_makeit._new import run as new_run

P = "zz"

LAY_H = """\
#ifndef Q_LAY_H
#define Q_LAY_H
#include <stddef.h>
#include <stdint.h>
/** A public layout struct whose field shares a derived name. */
struct lay
{
    size_t frame_bits; /**< Bits per frame. */
};
/**
 * @param l          The layout.
 * @param frame_bits Per-frame bit counts.
 */
size_t lay_total (const struct lay *l, const uint8_t *frame_bits);
#endif
"""

#: Appended to the sacred `frame_core.c`: every shape gh-1668 measured,
#: and a gh-1669 author macro pasting the stem into a derived name.
AUTHOR_C = """
#include "q/lay.h"

#define FRAME_RESET(pfx, s) pfx##_reset (s)

size_t
lay_total (const struct lay *l, const uint8_t *frame_bits)
{
    return l->frame_bits * frame_bits[0];
}

/* No parameter or local spelled `frame_bits` here, so nothing shadows it:
   only the member rule keeps these. */
size_t
lay_sum (const struct lay *l)
{
    struct lay k = { .frame_bits = 2 };
    return l->frame_bits + k.frame_bits;
}

size_t
lay_local (void)
{
    size_t frame_bits = 3;
    frame_bits++;
    return frame_bits;
}

float
lay_call (frame_state_t *st)
{
    FRAME_RESET (frame, st);
    return frame_bits (st, 1.0f);
}
"""

#: What must keep its spelling after the upgrade (gh-1668).
KEPT = (
    "size_t frame_bits; /**< Bits per frame. */",
    "const uint8_t *frame_bits)",
    "l->frame_bits * frame_bits[0]",
    "{ .frame_bits = 2 }",
    "l->frame_bits + k.frame_bits;",
    "size_t frame_bits = 3;",
    "frame_bits++;",
    "return frame_bits;",
)

#: What must move (the call, and gh-1669's pasted stem).
MOVED = (
    f"FRAME_RESET ({P}_frame, st);",
    f"return {P}_frame_bits (st, 1.0f);",
)


def _ok(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)
    return r


def _build(where, author_c=AUTHOR_C):
    root = where / "q"
    new_run("q", root, c_prefix=None)
    _ok(
        "object",
        "frame",
        "--state",
        "gain:float:1.0",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    _ok(
        "method",
        "frame",
        "bits",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    _textio.write_text(root / "native" / "inc" / "q" / "lay.h", LAY_H)
    core = root / "native" / "src" / "frame" / "frame_core.c"
    _textio.write_text(core, core.read_text(encoding="utf-8") + author_c)
    toml = root / C.FILENAME
    text = toml.read_text(encoding="utf-8")
    _textio.write_text(
        toml, text.replace("[project]\n", f'[project]\nc_prefix = "{P}"\n', 1)
    )
    return root


def _read(root):
    return "".join(
        (root / rel).read_text(encoding="utf-8")
        for rel in ("native/inc/q/lay.h", "native/src/frame/frame_core.c")
    )


@pytest.fixture(scope="module")
def upgraded(tmp_path_factory):
    root = _build(tmp_path_factory.mktemp("g1668"))
    steps = []
    for verb in ("upgrade", "apply"):
        r = run_cli(verb, cwd=root)
        steps.append((verb, r.returncode, r.stdout + r.stderr))
    return root, steps


def _steps_ok(steps):
    for what, rc, out in steps:
        assert rc == 0, (what, out[-3000:])


def test_members_parameters_and_locals_keep_their_spelling(upgraded):
    root, steps = upgraded
    _steps_ok(steps)
    text = _read(root)
    missing = [k for k in KEPT if k not in text]
    assert not missing, (
        "the upgrade renamed a member, parameter or local spelled like a "
        f"derived name (gh-1668): {missing}"
    )


def test_a_call_and_a_pasted_stem_move(upgraded):
    root, steps = upgraded
    _steps_ok(steps)
    text = _read(root)
    missing = [m for m in MOVED if m not in text]
    assert not missing, (
        "a reference to a derived name kept its old spelling (a call, or a "
        f"stem an author macro pastes: gh-1669): {missing}"
    )


def test_the_upgraded_tree_builds_and_its_c_tests_pass(upgraded):
    root, steps = upgraded
    _steps_ok(steps)
    b = root / "b"
    for cmd in (
        ["cmake", "-S", ".", "-B", str(b), "-DBUILD_PYTHON=OFF"],
        ["cmake", "--build", str(b)],
        ["ctest", "--test-dir", str(b), "--output-on-failure"],
    ):
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        assert r.returncode == 0, (cmd[:2], (r.stdout + r.stderr)[-3000:])


def test_a_macro_pasting_a_derived_and_an_author_name_is_refused(
    tmp_path,
):
    """`FRAME_BOTH (frame, s)` pastes `frame_reset` (derived, renamed) and
    `frame_mine` (the author's, not): no one spelling of the argument names
    both, so the upgrade must stop and name the call rather than guess."""
    both = AUTHOR_C.replace(
        "#define FRAME_RESET(pfx, s) pfx##_reset (s)",
        "#define FRAME_RESET(pfx, s) pfx##_reset (s)\n"
        "#define FRAME_BOTH(pfx, s) (pfx##_reset (s), pfx##_mine (s))\n"
        "static void frame_mine (frame_state_t *s) { (void) s; }",
    ).replace(
        "    FRAME_RESET (frame, st);\n",
        "    FRAME_RESET (frame, st);\n    FRAME_BOTH (frame, st);\n",
    )
    assert both.count("FRAME_BOTH") == 2, "the refusal case did not land"
    root = _build(tmp_path, both)
    before = _read(root)
    r = run_cli("upgrade", cwd=root)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert re.search(
        r"frame_core\.c:\d+: `FRAME_BOTH\(frame, \.\.\.\)`", out
    ), out
    assert _read(root) == before, "a refused upgrade still rewrote C"


@pytest.mark.parametrize(
    "src, kept, moved",
    [
        ("s.frame_bits = 1;", "s.frame_bits", None),
        ("p->frame_bits = 1;", "p->frame_bits", None),
        ("struct lay k = { .frame_bits = 1 };", ".frame_bits", None),
        ("struct lay { int frame_bits; };", "int frame_bits;", None),
        ("int f (int frame_bits);", "int frame_bits)", None),
        (
            "int f (int frame_bits) { return frame_bits; }",
            "return frame_bits;",
            None,
        ),
        ("void g (void) { int frame_bits = 0; }", "int frame_bits =", None),
        ("void g (void) { x = frame_bits (s, 1); }", None, "zz_frame_bits ("),
        ("void g (void) { fp = frame_bits; }", None, "= zz_frame_bits;"),
        ("float frame_bits (frame_state_t *s, float x);", None, "float zz_"),
    ],
)
def test_the_respell_and_the_refusal_agree(src, kept, moved):
    """Unit half: one classifier, so what `respell_c` rewrites is exactly
    what `_old_in` (the refusal) reports -- per shape."""
    names = {"frame_bits": "zz_frame_bits"}
    out = _csym.respell_c(src, names, {})
    refused = _csym._old_in(src, names, {})
    if kept:
        assert kept in out and out == src, out
        assert refused == [], refused
    else:
        assert moved in out, out
        assert refused == ["frame_bits"], refused
