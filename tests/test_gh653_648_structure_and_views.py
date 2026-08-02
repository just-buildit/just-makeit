"""gh-653 / gh-648: preserved list-and-table structure, and a view's own doc.

**gh-653** — `group_paragraphs` joined every run of consecutive non-blank lines
with spaces, so a markdown bullet list became
`"- fast: low quality - slow: high quality"`: one run-on line that reads as
neither prose nor a list, and a pipe table became a row of pipes. The parse was
always faithful; the paragraph pass destroyed it. doppler measured **28**
headers with bullet lists and **5** with tables, and the lists are typically
enumerating modes or flags for an enum-valued parameter — exactly where a
reader most needs the structure.

Structure preservation has two halves that must agree: `group_paragraphs` keeps
a structured run together (as one paragraph carrying newlines), and the
renderer emits any such paragraph verbatim instead of re-wrapping it. A test
covers the round trip rather than either half alone.

**gh-648** — every other view overlay key was set and `doc` was not, so a view
declaring its own class documentation still inherited the parent's in the
`.pyi` while `tp_doc` used the view's. Two faces of one class disagreeing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docstring import (  # noqa: E402
    group_paragraphs,
    is_structured_line,
    parse_doxygen_block,
    render_numpy_doc,
    render_runtime_doc,
)
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._view import run as view_run  # noqa: E402


class TestStructuredLines:
    @pytest.mark.parametrize(
        "line",
        [
            "- bullet",
            "* bullet",
            "+ bullet",
            "1. num",
            "2) num",
            "| a | b |",
            "@li entry",
            "\\arg entry",
        ],
    )
    def test_recognised(self, line):
        assert is_structured_line(line)

    @pytest.mark.parametrize(
        "line", ["prose", "a - b", "1 apple", "not|a table", ""]
    )
    def test_prose_is_not_structured(self, line):
        assert not is_structured_line(line)


class TestGroupParagraphs:
    def test_prose_still_joins(self):
        """The behaviour everything else depends on must not change."""
        assert group_paragraphs(["one", "two"]) == ["one two"]

    def test_a_bullet_list_keeps_its_breaks(self):
        got = group_paragraphs(["- fast: low", "- slow: high"])
        assert got == ["- fast: low\n- slow: high"]

    def test_a_table_keeps_its_rows(self):
        rows = ["| mode | cost |", "|------|------|", "| fast | 1    |"]
        assert group_paragraphs(rows) == ["\n".join(rows)]

    def test_prose_then_list_are_separate_paragraphs(self):
        got = group_paragraphs(["Modes:", "- fast", "- slow"])
        assert got == ["Modes:", "- fast\n- slow"]

    def test_list_then_prose_are_separate_paragraphs(self):
        got = group_paragraphs(["- fast", "- slow", "Pick one."])
        assert got == ["- fast\n- slow", "Pick one."]


_STRUCTURED_HEADER = """/**
 * @brief Choose a mode.
 *
 * The available modes are:
 *
 * - floor:   nearest index at or below the point
 * - linear:  interpolate between the two neighbouring indices
 *
 * | mode   | cost |
 * |--------|------|
 * | floor  | 1    |
 *
 * Pick linear unless cost dominates.
 *
 * @param mode The mode name.
 */"""


class TestRenderedStructure:
    """The round trip — grouping and rendering must agree."""

    @staticmethod
    def _stub() -> list[str]:
        block = parse_doxygen_block(_STRUCTURED_HEADER, name="set_mode")
        return render_numpy_doc(
            block, "set_mode", [("mode", "str")], "None", indent=4
        )

    def test_bullets_render_one_per_line(self):
        lines = [ln.strip() for ln in self._stub()]
        assert "- floor:   nearest index at or below the point" in lines
        assert (
            "- linear:  interpolate between the two neighbouring indices"
            in lines
        )

    def test_table_rows_render_one_per_line(self):
        lines = [ln.strip() for ln in self._stub()]
        assert "| mode   | cost |" in lines
        assert "|--------|------|" in lines
        assert "| floor  | 1    |" in lines

    def test_surrounding_prose_still_wraps(self):
        joined = "\n".join(self._stub())
        assert "The available modes are:" in joined
        assert "Pick linear unless cost dominates." in joined

    def test_no_run_on_line(self):
        """The reported symptom, asserted directly."""
        joined = "\n".join(self._stub())
        assert (
            "- floor:   nearest index at or below the point - linear"
            not in joined
        )
        assert "| mode   | cost | |--------|------|" not in joined

    def test_the_runtime_face_agrees(self):
        """Both faces share the section builder, so structure rides along."""
        block = parse_doxygen_block(_STRUCTURED_HEADER, name="set_mode")
        rt = render_runtime_doc(block, "set_mode", [("mode", "str")], "None")
        assert "- floor:   nearest index at or below the point" in rt
        assert "| mode   | cost |" in rt


# ── gh-648 ──────────────────────────────────────────────────────────────────

_VIEW_DOC = "Burst acquisition over one captured buffer."


@pytest.fixture
def viewed(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    new_run("demo", root)
    module_run(root, "acq")
    object_run(
        root,
        "detector",
        "acq",
        state_vars=[("th", "double", "1.0")],
        arg_type="double[]",
        return_type="double",
    )
    view_run(
        root,
        "detector",
        "BurstDet",
        "acq",
        create_fn="detector_create_burst",
        doc=_VIEW_DOC,
    )
    apply_run(root)
    return root


def _class_doc(pyi: str, cls: str) -> str:
    marker = f"class {cls}:"
    body = pyi[pyi.index(marker) + len(marker) :]
    return body[body.index('"""') + 3 : body.index('"""') + 200].split("\n")[0]


class TestViewClassDoc:
    def test_view_uses_its_own_doc(self, viewed):
        pyi = (viewed / "src/demo/acq/acq.pyi").read_text(encoding="utf-8")
        assert _class_doc(pyi, "BurstDet").strip() == _VIEW_DOC

    def test_parent_keeps_its_own(self, viewed):
        """The overlay must not leak the view's doc back onto the parent."""
        pyi = (viewed / "src/demo/acq/acq.pyi").read_text(encoding="utf-8")
        assert _class_doc(pyi, "Detector").strip().startswith("Detector")

    def test_both_faces_agree(self, viewed):
        """tp_doc already used the view's doc; the stub now matches it."""
        frag = next(
            (viewed / "native/src/acq").glob("*_ext_*burstdet*.c"), None
        )
        text = (
            frag.read_text(encoding="utf-8")
            if frag
            else (viewed / "native/src/acq/acq_ext.c").read_text()
        )
        assert _VIEW_DOC in text

    def test_a_view_without_its_own_doc_inherits(self, tmp_path):
        """Zero churn: declaring nothing keeps today's behaviour."""
        root = tmp_path / "plain"
        new_run("plain", root)
        module_run(root, "acq")
        object_run(
            root,
            "detector",
            "acq",
            state_vars=[("th", "double", "1.0")],
            arg_type="double[]",
            return_type="double",
        )
        view_run(
            root,
            "detector",
            "BurstDet",
            "acq",
            create_fn="detector_create_burst",
        )
        apply_run(root)
        pyi = (root / "src/plain/acq/acq.pyi").read_text(encoding="utf-8")
        assert _VIEW_DOC not in pyi
