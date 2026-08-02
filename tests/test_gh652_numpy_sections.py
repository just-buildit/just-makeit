"""gh-652: quarantined Doxygen block tags become real numpy sections.

gh-641 stopped unrecognized tags leaking into whichever section was open and
parked them on `DoxyBlock.tags`. The promise made at the time — in
`docs/developers/docstring-derivation.md` and repeated to doppler on
doppler-dsp/doppler#568 — was that a header authored *today* would start
rendering when the mapping shipped, **with no header rework**. They have been
writing `@note` / `@warning` / `@retval` since 0.35.0 on that basis. This is
that promise coming due, so the tests below are written against header text
rather than against parsed structures.

Because gh-642 unified the two faces onto one section builder, adding these
here reaches the `.pyi` and the runtime `__doc__` together; a test pins that
rather than assuming it.

Two decisions are asserted rather than left to a reader:

* **`@pre`/`@post` land in `Notes`.** numpydoc has no precondition section and
  inventing one puts non-standard headings into every downstream docs build.
* **`@retval` merges into `Returns`.** A C function returning 0/-1 becomes a
  Python method that raises or returns; the rows read correctly there.

The math row is not cosmetic: `@f$ … @f$` puts a backslash into the generated
`.pyi`, and `\\l` in a plain string is a `SyntaxWarning` on 3.12+ **in the
generated project**. `test_generated_stub_has_no_syntax_warning` compiles the
rendered docstring with warnings as errors, which is the only check that
actually proves the invariant.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docstring import (  # noqa: E402
    parse_doxygen_block,
    render_numpy_doc,
    render_runtime_doc,
)

PY_PARAMS = [("g", "float")]


def _render(tags: str, *, ret: str = "float", indent: int = 4) -> str:
    block = parse_doxygen_block(
        "/**\n"
        " * @brief Apply the gain.\n"
        " *\n"
        " * @param g Linear gain.\n"
        " * @return Scaled sample.\n"
        f"{tags}"
        " */",
        name="apply",
    )
    return "\n".join(
        render_numpy_doc(block, "apply", PY_PARAMS, ret, indent=indent)
    )


def _section(text: str, heading: str) -> list[str]:
    """The body lines of *heading*, dedented, up to the next section."""
    lines = [ln.strip() for ln in text.splitlines()]
    if heading not in lines:
        return []
    i = lines.index(heading) + 2  # skip the underline
    out: list[str] = []
    while i < len(lines) and not (
        i + 1 < len(lines) and set(lines[i + 1]) == {"-"} and lines[i + 1]
    ):
        if lines[i] == '"""':
            break
        out.append(lines[i])
        i += 1
    return [ln for ln in out if ln]


class TestMapping:
    @pytest.mark.parametrize(
        "cmd", ["note", "attention", "remark", "pre", "post", "invariant"]
    )
    def test_tags_that_land_in_notes(self, cmd):
        got = _render(f" * @{cmd} Something worth knowing.\n")
        assert "Something worth knowing." in _section(got, "Notes")

    def test_warning_gets_its_own_section(self):
        got = _render(" * @warning Undefined if g is zero.\n")
        assert "Undefined if g is zero." in _section(got, "Warnings")
        assert "Undefined if g is zero." not in _section(got, "Notes")

    @pytest.mark.parametrize("cmd", ["see", "sa"])
    def test_see_also(self, cmd):
        got = _render(f" * @{cmd} fir_reset\n")
        assert "fir_reset" in _section(got, "See Also")

    @pytest.mark.parametrize("cmd", ["throws", "exception"])
    def test_raises_is_type_then_description(self, cmd):
        """numpydoc wants the exception type on its own line."""
        got = _render(f" * @{cmd} ValueError if g is negative.\n")
        body = _section(got, "Raises")
        assert body[0] == "ValueError"
        assert "if g is negative." in body[1]

    def test_retval_becomes_extra_returns_rows(self):
        got = _render(" * @retval 0 Success.\n * @retval -1 Failure.\n")
        ret = _section(got, "Returns")
        assert "0" in ret and "Success." in " ".join(ret)
        assert "-1" in ret and "Failure." in " ".join(ret)

    def test_deprecated_becomes_a_directive(self):
        got = _render(" * @deprecated Use execute() instead.\n")
        assert ".. deprecated::" in got
        assert "Use execute() instead." in got

    @pytest.mark.parametrize(
        "cmd", ["todo", "bug", "since", "version", "ingroup", "copydoc"]
    )
    def test_c_side_metadata_is_dropped(self, cmd):
        """Dropped deliberately — and must not leak into any section."""
        got = _render(f" * @{cmd} irrelevant-to-python\n")
        assert "irrelevant-to-python" not in got

    def test_consecutive_notes_are_blank_separated(self):
        """Otherwise two @notes render as one run-on paragraph."""
        got = _render(" * @note First note.\n * @note Second note.\n")
        body = got[got.index("Notes") :]
        assert "First note.\n\n" in body.replace("    ", "")


class TestSectionOrder:
    def test_numpydoc_order_is_respected(self):
        got = _render(
            " * @note A note.\n"
            " * @warning A warning.\n"
            " * @see other\n"
            " * @throws ValueError bad input.\n"
        )
        order = [
            h
            for h in (
                "Parameters",
                "Returns",
                "Raises",
                "See Also",
                "Notes",
                "Warnings",
            )
            if h in got
        ]
        assert order == [
            "Parameters",
            "Returns",
            "Raises",
            "See Also",
            "Notes",
            "Warnings",
        ]


class TestMath:
    def test_inline_math_maps_to_the_role(self):
        got = _render(" * @note Gain is @f$ 20\\log_{10}(g) @f$ dB.\n")
        assert ":math:`20\\log_{10}(g)`" in got
        assert "@f$" not in got

    def test_math_in_a_param_description_maps_too(self):
        """The commonest place an author writes math is a @param."""
        block = parse_doxygen_block(
            "/**\n * @brief B.\n * @param g Gain, @f$ g > 0 @f$.\n */",
            name="apply",
        )
        got = "\n".join(
            render_numpy_doc(block, "apply", PY_PARAMS, "None", indent=4)
        )
        assert ":math:`g > 0`" in got

    def test_generated_stub_has_no_syntax_warning(self):
        r"""The invariant, proven rather than asserted.

        `\l` in a plain triple-quoted string is an invalid escape sequence and
        a SyntaxWarning on 3.12+ in the *generated* project. jm emits the stub
        docstring raw when the text contains a backslash.
        """
        got = _render(" * @note Gain is @f$ 20\\log_{10}(g) @f$ dB.\n")
        assert got.lstrip().startswith('r"""')
        src = "def f():\n" + got + "\n    pass\n"
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            compile(src, "<stub>", "exec")

    def test_no_backslash_means_no_raw_string(self):
        """Zero churn for the overwhelming majority of docstrings."""
        assert not _render(" * @note Plain prose.\n").lstrip().startswith("r")


class TestBothFaces:
    def test_runtime_gets_the_same_sections(self):
        """gh-642 unified the faces; this pins that tags ride along."""
        block = parse_doxygen_block(
            "/**\n * @brief B.\n * @param g Gain.\n * @return R.\n"
            " * @note A note.\n * @warning A warning.\n */",
            name="apply",
        )
        rt = "\n".join(render_runtime_doc(block, "apply", PY_PARAMS, "float"))
        assert "Notes" in rt and "A note." in rt
        assert "Warnings" in rt and "A warning." in rt


class TestNoHeaderRework:
    def test_a_header_written_before_the_mapping_renders_now(self):
        """The promise made in the docs and to doppler on #568.

        This is the whole contract of the quarantine: text authored while the
        tags were being *dropped* must render once the mapping ships, with no
        edit to the header.
        """
        authored_in_0_35 = (
            "/**\n"
            " * @brief Decide lock.\n"
            " *\n"
            " * @param g Linear gain.\n"
            " * @return True when locked.\n"
            " * @note Integrates over the whole block.\n"
            " * @warning Not safe across threads.\n"
            " * @retval 1 Locked.\n"
            " * @retval 0 Not locked.\n"
            " */"
        )
        block = parse_doxygen_block(authored_in_0_35, name="decide")
        got = "\n".join(
            render_numpy_doc(block, "decide", PY_PARAMS, "bool", indent=4)
        )
        assert "Integrates over the whole block." in _section(got, "Notes")
        assert "Not safe across threads." in _section(got, "Warnings")
        assert "Locked." in " ".join(_section(got, "Returns"))
