"""`jm apply` must not append a body for a symbol the HEADER defines (gh-1328).

gh-1294 taught `apply` to splice a declared method's missing `_core.c` body.
`missing_core_definitions` asked `_core.c` and the component's sibling `.c`
files and **never the header** — so a function defined `static inline` in the
sacred `<comp>_core.h`, and absent from `_core.c` by design, read as missing.
`apply` then appended an empty placeholder into a file jm's contract says it
never writes.

**The build break is the lucky outcome.** C forbids redefinition within one
translation unit, so doppler's `cic_decimate` and `dp_tlm_set_now` failed to
compile. Move the real definition to another TU and the placeholder *links*,
and the decimator silently returns 0 for every call — in a file the author
owns, written by a tool that promised not to write there, with nothing in the
tree pointing at jm.

`_method.already_provides` has stated the rule since gh-994 — *"reads the
header as well as the source"* — and gh-1294 did not carry it across. The
sources are one list now (`component_core_sources`) so a third reader cannot
get it wrong either.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import (  # noqa: E402
    component_core_sources,
    missing_core_definitions,
)
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _project(root: Path):
    """A component whose declared method is defined INLINE IN THE HEADER.

    doppler's shape for `cic_decimate`: the author moved the body out of
    `_core.c` and into the sacred header, which is legal and which jm's own
    `step` does by default.
    """
    _silent(new_run, "p", root)
    _silent(
        object_run,
        root,
        "cic",
        None,
        state_vars=[("r", "uint32_t", "2")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    _silent(
        method_run,
        root,
        "cic",
        "decimate",
        None,
        "float _Complex[]",
        "float _Complex",
        True,
        [],
    )
    c = root / "native/src/cic/cic_core.c"
    t = c.read_text(encoding="utf-8")
    i = t.index("cic_decimate(")
    start = t.rindex("\n", 0, t.rindex("\n", 0, i)) + 1
    end = t.index("\n}\n", i) + 3
    body = t[start:end]
    c.write_text(t[:start] + t[end:], encoding="utf-8")
    h = root / "native/inc/cic/cic_core.h"
    s = h.read_text(encoding="utf-8")
    cut = s.rindex("#ifdef __cplusplus")
    h.write_text(
        s[:cut] + "static inline " + body.lstrip() + "\n" + s[cut:],
        encoding="utf-8",
    )
    # gh-1328 repro B. A SECOND component, whose constructor is named by
    # `create_fn` rather than derived as `<comp>_create`.
    #
    # This is the shape the sweep below could not see. Every component in
    # this fixture used the default name, so a sweep that walks every
    # `*_core.c` was registration-free over FILES and still blind to a whole
    # SHAPE -- it went green for a release while `apply` appended a phantom
    # `acq_create` into three of doppler's sacred files.
    _silent(
        object_run,
        root,
        "widget",
        None,
        state_vars=[("n", "size_t", "4")],
        create_fn="widget_open",
    )
    # ...and the constructor's name is then asserted into the sacred files by
    # HAND, not left as whatever jm rendered.
    #
    # This is the difference between a gate and a description. `_project`
    # builds its tree by running jm, so a fault in the renderer moves the
    # fixture and the reference render together and they still agree --
    # the checker's oracle comes from the pipeline under test, and it is
    # blind to every fault in it. Measured: reverting the fix leaves this
    # sweep GREEN while the scaffold is broken, because both halves moved.
    #
    # doppler's `acq` is the real shape and it is not self-consistent: the
    # author wrote `acq_create_continuous` themselves, so the sacred file
    # says one thing no matter what jm thinks. Writing the name in here is
    # what makes `apply`'s derived `<comp>_create` show up as the append it
    # is.
    for rel in (
        "native/src/widget/widget_core.c",
        "native/inc/widget/widget_core.h",
    ):
        f = root / rel
        s = f.read_text(encoding="utf-8")
        f.write_text(
            s.replace("widget_create", "widget_open"), encoding="utf-8"
        )
    assert "widget_open(" in (
        root / "native/src/widget/widget_core.c"
    ).read_text(encoding="utf-8"), "fixture failed to name the constructor"
    return root


class TestThePredicate:
    def test_a_header_definition_counts(self, tmp_path):
        root = _project(tmp_path / "p")
        rendered = (
            "size_t\ncic_decimate(cic_state_t *s)\n{\n    return 0;\n}\n"
        )
        core_c = (root / "native/src/cic/cic_core.c").read_text()
        assert not missing_core_definitions(
            rendered, core_c, component_core_sources(root, "cic")
        )

    def test_the_header_is_in_the_source_list(self, tmp_path):
        """One list, so `apply` and `status` cannot disagree about it."""
        root = _project(tmp_path / "p")
        assert any(
            "cic_decimate(" in src
            for src in component_core_sources(root, "cic")
        )

    def test_a_genuinely_missing_body_is_still_reported(self, tmp_path):
        """The fix must not blind the feature gh-1294 exists for."""
        root = _project(tmp_path / "p")
        rendered = "void\ncic_nowhere(cic_state_t *s)\n{\n}\n"
        core_c = (root / "native/src/cic/cic_core.c").read_text()
        assert missing_core_definitions(
            rendered, core_c, component_core_sources(root, "cic")
        ) == ["cic_nowhere"]


class TestApplyLeavesSacredCoreCAlone:
    def test_the_inline_case(self, tmp_path):
        root = _project(tmp_path / "p")
        path = root / "native/src/cic/cic_core.c"
        before = path.read_text(encoding="utf-8")
        _silent(apply_run, root)
        assert path.read_text(encoding="utf-8") == before

    def test_no_placeholder_definition_was_appended(self, tmp_path):
        """Named separately from the byte check: if the file ever legitimately
        gains something, this still says whether a DUPLICATE definition of the
        header's symbol is what it gained."""
        root = _project(tmp_path / "p")
        _silent(apply_run, root)
        text = (root / "native/src/cic/cic_core.c").read_text()
        assert "cic_decimate(cic_state_t" not in text, text

    def _sacred(self, root: Path) -> dict:
        return {
            p: p.read_text(encoding="utf-8")
            for p in sorted((root / "native/src").rglob("*_core.c"))
        }

    def test_the_first_apply_is_a_no_op_on_every_sacred_file(self, tmp_path):
        """The FIRST `apply`, measured from the scaffold jm just wrote.

        This is the one that catches repro B, and the one the original sweep
        could not: it settled with an `apply` before snapshotting, so what it
        compared was apply-2 against apply-1. The damage lands on apply-1 --
        the phantom constructor is appended once and every later run finds it
        present and stops. Idempotence is therefore satisfied BY the bug,
        and the laundering call was the test's own blind spot.

        jm's contract makes the stronger claim available for free: a freshly
        scaffolded project is already up to date, so `apply` has nothing to
        do and every sacred file must come out byte-identical.
        """
        root = _project(tmp_path / "p")
        before = self._sacred(root)
        assert before, "no sacred _core.c found — the sweep is not armed"
        assert any("widget" in p.name for p in before), (
            "the create_fn component is missing — the sweep is blind to "
            "repro B again"
        )
        _silent(apply_run, root)
        after = {p: p.read_text(encoding="utf-8") for p in before}
        changed = [p.name for p in before if before[p] != after[p]]
        assert not changed, f"apply rewrote sacred file(s): {changed}"

    def test_apply_never_invents_a_derived_constructor(self, tmp_path):
        """Named apart from the byte check, because this is the silent half.

        A phantom `<comp>_create` beside the author's real constructor is a
        NEW symbol: nothing collides, nothing fails to build, and dead code
        returning an uninitialised state sits in a hand-owned file. doppler
        only ever saw it through an unrelated bare-`calloc` ratchet.
        """
        root = _project(tmp_path / "p")
        _silent(apply_run, root)
        text = (root / "native/src/widget/widget_core.c").read_text()
        assert "widget_create(" not in text, text
        assert "widget_open(" in text, text

    def test_every_sacred_core_c_is_byte_identical(self, tmp_path):
        """Idempotence, kept as its own property: a second `apply` must also
        change nothing. Weaker than the first-apply check above and retained
        because it fails for causes that one does not -- a rewrite that only
        appears once the tree is settled.
        """
        root = _project(tmp_path / "p")
        _silent(apply_run, root)  # settle anything genuinely outstanding
        before = self._sacred(root)
        assert before, "no sacred _core.c found — the sweep is not armed"
        _silent(apply_run, root)
        after = {p: p.read_text(encoding="utf-8") for p in before}
        changed = [p.name for p in before if before[p] != after[p]]
        assert not changed, f"apply rewrote sacred file(s): {changed}"
