"""An enum's per-choice `///<` docs reach the parameter (gh-901, was gh-710).

`extract_member_docs` has parsed trailing enum-value docs since gh-671 —

    FIR_LOW = 0,  ///< Lowpass.

— and they reached nothing. ~43 of doppler's trailing docs sit on enum values.

**The mapping is declared, never guessed.** A `string_enum`'s choices are
Python strings that need not correspond to a C enum at all, so nothing
connected `"low"` to `FIR_LOW`. An `[[enum]]` may now carry an `enumerators`
list parallel to `values`. Inferring it by stripping a common prefix was the
obvious alternative and is the one thing gh-901 rules out: `"low"` could mean
`FIR_LOW`, `FIR_LOWPASS` or `LOW`, and a wrong guess attaches confident prose
to the wrong value — worse than none, and invisible in review.

**Where the text lands** was the other open decision. numpydoc has no
`Choices` section, and a parameter's description is free prose, so the list
goes there: a reader already looks under the parameter for "what may I pass",
and it renders in every consumer rather than needing one to understand a
custom heading.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._apply import run as apply_run
from just_makeit._docstring import DoxyBlock, member_doc_key
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run

_ENUM_C = (
    "typedef enum {\n"
    "    FIR_LOW = 0,   ///< Lowpass, cutoff at fs/4.\n"
    "    FIR_HIGH = 1,  ///< Highpass.\n"
    "    FIR_BAND = 2,  ///< Bandpass.\n"
    "} fir_kind_t;\n\n#endif"
)


def _project(tmp_path, *, enumerators=True, documented=True):
    root = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", root, [], [])
    cfg = C.load(root)
    entry = {"name": "fir_kind", "values": ["low", "high", "band"]}
    if enumerators:
        entry["enumerators"] = ["FIR_LOW", "FIR_HIGH", "FIR_BAND"]
    cfg["enum"] = [entry]
    C.save(root, cfg)
    with contextlib.redirect_stdout(io.StringIO()):
        object_run(
            root,
            "fir",
            None,
            arg_type="float",
            return_type="float",
            init_params=[("kind", "string_enum:low,high,band", "low")],
        )
    if documented:
        h = root / "native/inc/fir/fir_core.h"
        h.write_text(h.read_text().replace("#endif", _ENUM_C, 1))
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            apply_run(root)
    return root


def _pyi(root):
    return (root / "src/p/fir.pyi").read_text()


def test_the_choices_are_documented_under_the_parameter(tmp_path):
    """numpydoc has no Choices section; the description is where this goes."""
    text = _pyi(_project(tmp_path))
    assert '- ``"low"`` — Lowpass, cutoff at fs/4.' in text, text[:800]
    assert '- ``"high"`` — Highpass.' in text
    assert '- ``"band"`` — Bandpass.' in text


def test_a_blank_line_precedes_the_list(tmp_path):
    """reST does not read a bullet list butted against the paragraph above.

    Without it a docs build renders one run-on sentence, which looks like a
    content bug rather than a formatting one.
    """
    text = _pyi(_project(tmp_path))
    i = text.index('- ``"low"``')
    preceding = text[:i].rstrip(" ")
    assert preceding.endswith("\n\n"), (
        f"no blank line before the choice list:\n{text[i - 200 : i + 80]}"
    )


def test_both_faces_carry_it(tmp_path):
    """`help(Obj)` and `Obj.pyi` must agree — gh-446/gh-642/gh-871's lesson.

    The runtime gate fires on an authored `@brief`, and this object has none;
    a declared choice list is now also a reason to build the block, exactly as
    a declared `create_error` is (gh-805 §F).
    """
    root = _project(tmp_path)
    ext = (root / "native/src/fir/fir_ext.c").read_text()
    assert "Lowpass, cutoff at fs/4." in ext, (
        "the stub documents choices that help(Obj) does not"
    )


def test_nothing_happens_without_the_declared_mapping(tmp_path):
    """No `enumerators`, no guess. This is the whole design decision."""
    text = _pyi(_project(tmp_path, enumerators=False))
    assert "Lowpass" not in text, (
        "choices were documented without a declared enumerator mapping, so "
        "something inferred the correspondence — which is what gh-901 rules "
        "out"
    )


def test_an_undocumented_enum_adds_nothing(tmp_path):
    """The mapping alone is not enough; the header must actually say something."""
    text = _pyi(_project(tmp_path, documented=False))
    assert '- ``"low"``' not in text


def test_the_lookup_keys_on_the_resolved_spec():
    """`resolve_enum_type` drops the name, so the value list is the identity."""
    cfg = {
        "enum": [
            {
                "name": "fir_kind",
                "values": ["low", "high"],
                "enumerators": ["FIR_LOW", "FIR_HIGH"],
            }
        ]
    }
    blocks = {
        member_doc_key("FIR_LOW"): DoxyBlock(brief="Lowpass."),
        member_doc_key("FIR_HIGH"): DoxyBlock(brief="Highpass."),
    }
    got = C.enum_choice_docs(cfg, blocks)
    assert got == {
        "string_enum:low,high": [("low", "Lowpass."), ("high", "Highpass.")]
    }


def test_two_enums_with_identical_values_are_skipped():
    """Indistinguishable at this layer, so picking one would be a guess.

    They collide on the resolved spec — the only key a doc face has — and
    their per-choice prose may differ. Resolving first-wins would attach one
    enum's documentation to the other's parameter.
    """
    cfg = {
        "enum": [
            {
                "name": "a",
                "values": ["x", "y"],
                "enumerators": ["A_X", "A_Y"],
            },
            {
                "name": "b",
                "values": ["x", "y"],
                "enumerators": ["B_X", "B_Y"],
            },
        ]
    }
    blocks = {
        member_doc_key("A_X"): DoxyBlock(brief="A's x."),
        member_doc_key("B_X"): DoxyBlock(brief="B's x."),
    }
    assert C.enum_choice_docs(cfg, blocks) == {}


def test_a_project_with_no_enums_is_unaffected():
    assert C.enum_choice_docs({}, {}) == {}
    assert (
        C.enum_choice_docs({"enum": [{"name": "z", "values": ["q"]}]}, {})
        == {}
    )
