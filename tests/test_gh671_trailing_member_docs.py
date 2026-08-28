"""gh-671: a struct field's trailing `///<` / `/**<` documents its property.

`double gain;  /**< Linear output gain. */` is where a C author naturally
documents a field, and jm turns fields into Python properties and state
accessors. That text was invisible to derivation, so the same sentence had to
be re-stated in a manifest `doc=` or on a getter `@brief` and then maintained
twice, drifting independently. doppler measured **700** trailing member docs,
~518 of them on struct fields, against **369** properties documented the
redundant way.

Two properties are pinned here, and the second is what makes this safe to ship
into an existing project:

1. a documented field reaches every face it surfaces on — the property getset
   doc, the property stub, and both state-accessor faces;
2. it sits **below** every authored source (manifest `doc=`, getter `@brief`),
   so nothing already documented changes. It only replaces the name stub.

The parser half is deliberately shallow — it keys on the identifier preceding
the comment on that line — so a field, an enum value, or anything else declared
one-per-line all work without the parser knowing which construct it sits in.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._docstring import (  # noqa: E402
    extract_member_docs,
    member_doc,
    member_doc_key,
)
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402


class TestExtractor:
    """Every spelling Doxygen accepts for an 'after the member' comment."""

    @pytest.mark.parametrize(
        "line,expected",
        [
            ("    double phase;  /**< Phase, radians. */", "Phase, radians."),
            ("    double phase;  /*!< Phase, radians. */", "Phase, radians."),
            ("    double phase;  ///< Phase, radians.", "Phase, radians."),
            ("    double phase;  //!< Phase, radians.", "Phase, radians."),
        ],
        ids=["block", "block-bang", "line", "line-bang"],
    )
    def test_all_four_openers(self, line, expected):
        assert extract_member_docs(line) == {"phase": expected}

    def test_array_bound_does_not_confuse_the_name(self):
        got = extract_member_docs("    float c[64];  ///< Tap coefficients.")
        assert got == {"c": "Tap coefficients."}

    def test_enum_value_with_initialiser(self):
        got = extract_member_docs("    FIR_LOW = 0,  ///< Lowpass.")
        assert got == {"FIR_LOW": "Lowpass."}

    def test_a_plain_comment_is_not_a_member_doc(self):
        """The `<` is what makes it 'after the member' — without it, no."""
        assert extract_member_docs("    double g;  /* just a note */") == {}
        assert extract_member_docs("    double g;  // just a note") == {}

    def test_a_leading_block_IS_a_member_doc_since_gh1167(self):
        """This asserted `== {}` until gh-1167, and that was gh-671 SCOPING
        itself rather than ruling the form out: the parser was "deliberately
        shallow" and read one line, so a block above the member was simply
        out of range.

        gh-1167 brought it in, because the moment a field needs more than one
        short sentence a C author writes the block above it — and until then
        such a field fell through to the name stub on both faces, which is the
        exact redundancy this issue exists to remove.
        """
        text = "    /** Leading block. */\n    double g;\n"
        assert extract_member_docs(text) == {"g": "Leading block."}

    def test_a_trailing_comment_still_wins_over_a_leading_one(self):
        """gh-671's form stays the more specific one: it is attached to that
        declaration, so it outranks a block above it."""
        text = "    /** Above. */\n    double g;  ///< Beside.\n"
        assert extract_member_docs(text) == {"g": "Beside."}

    def test_first_declaration_wins(self):
        text = "    int n;  ///< First.\n    int n;  ///< Second.\n"
        assert extract_member_docs(text) == {"n": "First."}

    def test_undocumented_field_is_absent(self):
        assert "g" not in extract_member_docs("    double g;\n")

    def test_member_doc_accessor_round_trips(self):
        from just_makeit._docstring import DoxyBlock

        blocks = {member_doc_key("gain"): DoxyBlock(brief="Linear gain.")}
        assert member_doc(blocks, "gain") == "Linear gain."
        assert member_doc(blocks, "other") == ""
        assert member_doc(None, "gain") == ""

    def test_the_reserved_key_cannot_collide_with_a_c_name(self):
        assert not member_doc_key("x").isidentifier()


# ── generated project ───────────────────────────────────────────────────────

_FIELD_DOC = "Linear output gain, applied after the oscillator."
_PHASE_DOC = "Accumulated phase in radians."


def _project(tmp_path: Path, *, manifest_doc: str = "") -> Path:
    root = tmp_path / "demo"
    new_run("demo", root)
    object_run(
        root,
        "osc",
        None,
        state_vars=[("gain", "double", "1.0"), ("phase", "double", "0.0")],
        arg_type="void",
        return_type="float",
    )
    property_run(root, "osc", "gain", None, "double", False, doc=manifest_doc)
    hdr = root / "native" / "inc" / "osc" / "osc_core.h"
    text = hdr.read_text(encoding="utf-8")
    assert "    double gain;\n" in text, "scaffold no longer emits the field"
    hdr.write_text(
        text.replace(
            "    double gain;\n",
            f"    double gain;  /**< {_FIELD_DOC} */\n",
            1,
        ).replace(
            "    double phase;\n", f"    double phase;  ///< {_PHASE_DOC}\n", 1
        ),
        encoding="utf-8",
    )
    apply_run(root)
    return root


def _getset(root: Path) -> str:
    ext = (root / "native/src/osc/osc_ext.c").read_text(encoding="utf-8")
    return ext[ext.index("Osc_getset[]") : ext.index("{ NULL }")]


class TestGeneratedProject:
    def test_property_getset_doc(self, tmp_path):
        assert _FIELD_DOC in _getset(_project(tmp_path))

    def test_property_stub_doc(self, tmp_path):
        pyi = (_project(tmp_path) / "src/demo/osc.pyi").read_text()
        assert f'"""{_FIELD_DOC}"""' in pyi

    def test_state_accessor_both_faces(self, tmp_path):
        """`phase` has no property — it surfaces as get_/set_ accessors."""
        root = _project(tmp_path)
        ext = (root / "native/src/osc/osc_ext.c").read_text()
        pyi = (root / "src/demo/osc.pyi").read_text()
        assert _PHASE_DOC in ext
        assert f'"""{_PHASE_DOC}"""' in pyi

    def test_a_manifest_doc_still_wins(self, tmp_path):
        """Precedence: the field doc must not override an authored source.

        This is what makes the feature safe for an existing project — it only
        fills what was falling through to the name stub.
        """
        root = _project(tmp_path, manifest_doc="Declared in the manifest.")
        got = _getset(root)
        assert "Declared in the manifest." in got
        assert _FIELD_DOC not in got

    def test_apply_is_idempotent(self, tmp_path):
        root = _project(tmp_path)
        before = _getset(root)
        apply_run(root)
        assert _getset(root) == before

    def test_undocumented_field_keeps_the_name_stub(self, tmp_path):
        """Zero churn where no member doc was written."""
        root = tmp_path / "plain"
        new_run("plain", root)
        object_run(
            root,
            "osc",
            None,
            state_vars=[("gain", "double", "1.0")],
            arg_type="void",
            return_type="float",
        )
        property_run(root, "osc", "gain", None, "double", False)
        apply_run(root)
        ext = (root / "native/src/osc/osc_ext.c").read_text()
        assert '"Gain.\\n"' in ext
