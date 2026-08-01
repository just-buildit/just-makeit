"""gh-676/gh-644: the built-ins derive from the header on every face and kind.

`create`, `reset`, `step` and `steps` are generated, but their *documentation*
is the author's: the sacred `<obj>_core.h` is the single source of truth. Four
of them times two object kinds times two faces is sixteen cells, and **eight**
were hard-coded literals that ignored the header entirely:

| `.pyi`     | create  | reset   | step    | steps   |
| ---------- | ------- | ------- | ------- | ------- |
| standalone | derived | LITERAL | LITERAL | LITERAL |
| module     | derived | derived | derived | derived |

| runtime    | create  | reset   | step    | steps   |
| ---------- | ------- | ------- | ------- | ------- |
| standalone | LITERAL | LITERAL | LITERAL | LITERAL |
| module     | derived | LITERAL | derived | derived |

That shape is why #676 and #644 read as contradicting each other: #676
described the standalone `.pyi` column and #644 the module runtime column, and
both were exactly right about the cell they looked at.

The causes were four separate missing lookups, not one, so the guard here is
the whole matrix rather than any single path: `_glue` never passed `doc_blocks`
to `make_state_ctx`/`make_step_ctx`; `_apply` re-rendered only the temp `.pyi`
from the real header; the standalone `_ext.c` template hard-coded `tp_doc`; and
`_object` never passed `doc_blocks` to `make_state_ctx` either.

The second class of test here is idempotence, which is what makes this safe:
a *scaffold* brief must still derive nothing, or a freshly created project
reports STALE against itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docstring import (  # noqa: E402
    authored_class_brief,
    is_scaffold_doc,
    parse_doxygen_block,
)
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._status import run as status_run  # noqa: E402

_MARKS = {
    "@brief Create": "@brief CTORMARK Create",
    "@brief Reset": "@brief RESETMARK Reset",
    "@brief Process one input sample.": "@brief STEPMARK one sample.",
    "@brief Process a block of samples.": "@brief STEPSMARK a block.",
}


def _scaffold(tmp_path: Path, *, module: bool) -> Path:
    root = tmp_path / "demo"
    new_run("demo", root)
    if module:
        module_run(root, "filt")
    object_run(
        root,
        "widget",
        "filt" if module else None,
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    return root


def _author(root: Path) -> None:
    """Give every derivable built-in a unique, non-scaffold @brief."""
    h = root / "native" / "inc" / "widget" / "widget_core.h"
    text = h.read_text(encoding="utf-8")
    for old, new in _MARKS.items():
        assert old in text, f"scaffold no longer writes {old!r}"
        text = text.replace(old, new)
    h.write_text(text, encoding="utf-8")


def _faces(root: Path) -> tuple[str, str]:
    pyi = next(root.rglob("*.pyi")).read_text(encoding="utf-8")
    c = "".join(
        p.read_text(encoding="utf-8") for p in (root / "native").rglob("*.c")
    )
    return pyi, c


@pytest.fixture(params=[False, True], ids=["standalone", "module"])
def authored(request, tmp_path):
    root = _scaffold(tmp_path, module=request.param)
    _author(root)
    apply_run(root)
    return root


class TestEveryCellDerives:
    """All sixteen. Parametrised over object kind; four marks x two faces."""

    @pytest.mark.parametrize(
        "mark", ["CTORMARK", "RESETMARK", "STEPMARK", "STEPSMARK"]
    )
    def test_stub_face(self, authored, mark):
        pyi, _c = _faces(authored)
        assert mark in pyi, f"{mark} never reached the .pyi"

    @pytest.mark.parametrize(
        "mark", ["CTORMARK", "RESETMARK", "STEPMARK", "STEPSMARK"]
    )
    def test_runtime_face(self, authored, mark):
        _pyi, c = _faces(authored)
        assert mark in c, f"{mark} never reached the runtime __doc__"


class TestScaffoldStillDerivesNothing:
    """The idempotence contract -- the reason this can ship at all."""

    @pytest.mark.parametrize(
        "module", [False, True], ids=["standalone", "mod"]
    )
    def test_fresh_scaffold_is_not_stale_against_itself(
        self, tmp_path, module
    ):
        # `jm object` renders the binding and stub without doc blocks while
        # `jm apply` renders them with. If a scaffold @brief derived, the two
        # would disagree and a brand-new project would report STALE the moment
        # it was created.
        root = _scaffold(tmp_path, module=module)
        assert status_run(root) == 0

    def test_reset_scaffold_brief_is_filtered(self):
        # This is the one that bit: the scaffold writes the CLASS name here
        # ("Reset MyFilter to ...") while create/destroy write the component
        # id ("Create a my_filter instance."), so a separator-sensitive
        # comparison matched one spelling and missed the other.
        blk = parse_doxygen_block(
            "@brief Reset MyFilter to its post-create state.\n"
            "@param state  Must be non-NULL."
        )
        assert is_scaffold_doc(blk, "reset", "my_filter")

    def test_authored_reset_still_derives(self):
        blk = parse_doxygen_block("@brief Reset the accumulator to zero.")
        assert not is_scaffold_doc(blk, "reset", "my_filter")


class TestClassBriefPrecedence:
    """One definition of the tp_doc rule, shared by apply, glue and bind."""

    def test_manifest_doc_outranks_the_header(self):
        blk = parse_doxygen_block("@brief From the header.")
        assert (
            authored_class_brief({"a_create": blk}, "a_create", "From TOML.")
            == "From TOML."
        )

    def test_header_used_when_no_manifest_doc(self):
        blk = parse_doxygen_block("@brief From the header.")
        assert (
            authored_class_brief({"a_create": blk}, "a_create")
            == "From the header."
        )

    def test_empty_when_nothing_authored(self):
        # Returning "" rather than a fallback is what lets each caller keep
        # its own seeded default -- and what keeps a fresh scaffold clean.
        assert authored_class_brief({}, "a_create") == ""

    def test_create_fn_override_is_honoured(self):
        # gh-602: tp_init calls the override, so its Doxygen is the class's.
        blk = parse_doxygen_block("@brief Continuous-mode constructor.")
        assert (
            authored_class_brief({"a_create_cont": blk}, "a_create_cont")
            == "Continuous-mode constructor."
        )


class TestBindAgreesWithApply:
    """bind reads the header alone but writes the same file apply does."""

    def test_bind_round_trip_is_byte_identical(self, tmp_path):
        from just_makeit._bind import run as bind_run

        root = _scaffold(tmp_path, module=False)
        _author(root)
        apply_run(root)
        ext = root / "native" / "src" / "widget" / "widget_ext.c"
        original = ext.read_text(encoding="utf-8")
        ext.unlink()
        bind_run(root, "widget")
        assert ext.read_text(encoding="utf-8") == original
