"""A `record_dtype` scaffold must BUILD untouched (gh-1319).

`record_dtype` renders prototypes in the author's struct type, and nothing
defined it:

    native/inc/ring/ring_core.h:103:1: error: unknown type name 'iq_pair_t'
    native/src/ring/ring_core.c:48:1:  error: unknown type name 'iq_pair_t'

from a declaration jm accepted without a word. **Both record paths**, so
`borrow` inherited it from `variable_output` rather than introducing it — this
predates gh-1310.

It breaks the rule gh-1311's own changelog states as the reason a header-only
component carries definitions rather than prototypes: *the untouched scaffold
builds and passes before the author has written a line of it.*

**Why the suite did not catch it.** gh-1310's oracle test injects a
hand-written struct into the header before compiling — correctly, because it
is testing that the numpy dtype follows the compiler's real layout, padding
and all. Every compiled assertion there therefore starts from a tree the test
fixed up. Nothing asserted that a *plain* declaration produces a building one.
That is the gate here, and it is a sibling of the oracle test rather than a
replacement for it.

**jm reports rather than writes, and that is a deliberate carve-out.**
Scaffolding the typedef into the sacred header was implemented first and
reverted: the record struct is routinely added AFTER the method is declared —
gh-788's own fixtures do exactly that — so a scaffolded definition collides
with the author's (`conflicting types for 'dp_tlm_rec_t'`), and jm would have
created a compile error in a sacred file to avoid a different one. A scaffold
that does not build beats a file jm broke, which this repo has now learned
twice (gh-1328).

So *"the untouched scaffold builds"* is **not** met for a record method. That
gap is filed, not papered over. What is fixed is the silence.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

FIELDS = [
    {"name": "i", "type": "int16_t"},
    {"name": "q", "type": "int16_t"},
]


def _no_toolchain():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _no_toolchain()


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _declare(root: Path, *, borrow: bool) -> str:
    """Declare a record method; return what jm printed."""
    _silent(new_run, "p", root)
    _silent(
        object_run, root, "ring", None, state_vars=[("cap", "size_t", "8")]
    )
    kw = dict(params=[("n", "size_t")], borrow=True) if borrow else {}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        method_run(
            root,
            "ring",
            "wait" if borrow else "pull",
            None,
            "void" if borrow else "float _Complex[]",
            "float _Complex",
            False if borrow else True,
            [],
            record_dtype="iq_pair_t",
            result_fields=FIELDS,
            **kw,
        )
    return buf.getvalue()


class TestTheAuthorIsTold:
    @pytest.mark.parametrize("borrow", [True, False], ids=["borrow", "varout"])
    def test_the_declaration_names_the_missing_type(self, tmp_path, borrow):
        out = _declare(tmp_path / "p", borrow=borrow)
        assert "iq_pair_t" in out and "nothing defines" in out, out

    @pytest.mark.parametrize("borrow", [True, False], ids=["borrow", "varout"])
    def test_it_hands_over_a_pasteable_typedef(self, tmp_path, borrow):
        """Naming the type without its fields leaves the author to re-derive
        what jm already knows -- and getting the field ORDER wrong is a
        silently wrong dtype, not a compile error."""
        out = _declare(tmp_path / "p", borrow=borrow)
        assert "typedef struct" in out, out
        assert "int16_t i;" in out and "int16_t q;" in out, out
        assert "} iq_pair_t;" in out, out

    def test_it_says_which_file(self, tmp_path):
        out = _declare(tmp_path / "p", borrow=True)
        assert "ring_core.h" in out, out

    def test_it_says_the_layout_is_the_authors(self, tmp_path):
        """The author must know jm reads the layout back rather than assuming
        it, or they will not trust themselves to pad it."""
        out = _declare(tmp_path / "p", borrow=True)
        assert "offsetof" in out, out


class TestItStaysQuietWhenTheTypeExists:
    """No advice once the type is there -- a note that never goes away is a
    note nobody reads."""

    def test_silent_when_the_author_already_declared_it(self, tmp_path):
        root = tmp_path / "p"
        _silent(new_run, "p", root)
        _silent(
            object_run,
            root,
            "ring",
            None,
            state_vars=[("cap", "size_t", "8")],
        )
        h = root / "native/inc/ring/ring_core.h"
        t = h.read_text()
        cut = t.index("#ifdef __cplusplus")
        h.write_text(
            t[:cut] + "typedef struct { int16_t i; int32_t big; int16_t q; }"
            " iq_pair_t;\n\n" + t[cut:]
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            method_run(
                root,
                "ring",
                "wait",
                None,
                "void",
                "float _Complex",
                False,
                [],
                params=[("n", "size_t")],
                borrow=True,
                record_dtype="iq_pair_t",
                result_fields=FIELDS,
            )
        assert "nothing defines" not in buf.getvalue(), buf.getvalue()

    def test_the_authors_type_is_never_touched(self, tmp_path):
        """The reason this reports instead of writing."""
        root = tmp_path / "p"
        _silent(new_run, "p", root)
        _silent(
            object_run,
            root,
            "ring",
            None,
            state_vars=[("cap", "size_t", "8")],
        )
        h = root / "native/inc/ring/ring_core.h"
        t = h.read_text()
        cut = t.index("#ifdef __cplusplus")
        h.write_text(
            t[:cut] + "typedef struct { int16_t i; int32_t big; int16_t q; }"
            " iq_pair_t;\n\n" + t[cut:]
        )
        _silent(
            method_run,
            root,
            "ring",
            "wait",
            None,
            "void",
            "float _Complex",
            False,
            [],
            params=[("n", "size_t")],
            borrow=True,
            record_dtype="iq_pair_t",
            result_fields=FIELDS,
        )
        text = h.read_text()
        assert text.count("iq_pair_t;") == 1, text
        assert "int32_t big" in text, "the author's padding was lost"
