"""gh-1167: a struct field documented ABOVE the declaration is derived too.

gh-671 taught jm to read a field's **trailing** doc:

    int span;   /**< Coalescing window, in samples. */

and it works on both faces. What the issue asked for -- "derive a
``field = true`` property's docstring from its struct field's Doxygen" -- was
therefore already half built, which measuring found before any of it was
written. The real gap was narrower and sharper: the moment a field needs more
than one short sentence, a C author writes the block ABOVE it instead, and
that form was invisible.

Measured before the fix, one object with both spellings:

    span   (trailing)   -> "TRAILING_MARKER coalescing window."   both faces
    width  (preceding)  -> "Width."                               both faces

So a property whose documentation already existed in the sacred header still
fell through to the name stub, and the only way to document it was to restate
the sentence in a manifest ``doc`` -- maintained twice, drifting
independently, which is the exact redundancy gh-671 exists to remove.

Extending `extract_member_docs` fixes both faces at once, because the
precedence chain (`TOML doc` > getter ``@brief`` > member doc > name) already
runs through one function that both the `.pyi` and the `PyGetSetDef` read.

**Only the summary is taken**, and that is the honest ceiling rather than a
shortcut: a property docstring renders as one flowing paragraph, so a
multi-paragraph block cannot be carried whole by either face wherever it is
written (gh-1154/gh-1164).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """One object, two `field = true` properties, one comment form each."""
    assert _cli("new", "d", cwd=tmp_path).returncode == 0
    root = tmp_path / "d"
    assert _cli("object", "o", cwd=root).returncode == 0
    for name in ("span", "width"):
        assert (
            _cli(
                "property", "o", name, "--type", "int", "--field", cwd=root
            ).returncode
            == 0
        )
    h = root / "native" / "inc" / "o" / "o_core.h"
    s = h.read_text(encoding="utf-8")
    assert "    int span;" in s and "    int width;" in s, s
    s = s.replace(
        "    int span;",
        "    int span; /**< TRAILING_MARKER coalescing window. */",
    )
    s = s.replace(
        "    int width;",
        "    /** PRECEDING_MARKER filter width in taps.\n"
        "     *\n"
        "     * PRECEDING_PROSE is the extended detail.\n"
        "     */\n    int width;",
    )
    h.write_text(s, encoding="utf-8")
    assert _cli("apply", cwd=root).returncode == 0
    return root


def _faces(root: Path) -> tuple[str, str]:
    """(stub, runtime binding). Keyed off `root.name`, which IS the package."""
    return (
        (root / "src" / root.name / "o.pyi").read_text(encoding="utf-8"),
        (root / "native" / "src" / "o" / "o_ext.c").read_text("utf-8"),
    )


class TestBothCommentForms:
    def test_the_preceding_form_reaches_both_faces(
        self, project: Path
    ) -> None:
        """The gap. Before this it read "Width." on both."""
        pyi, ext = _faces(project)
        for face in (pyi, ext):
            assert "PRECEDING_MARKER filter width in taps." in face, face[:600]
        assert '"""Width."""' not in pyi

    def test_the_trailing_form_still_works(self, project: Path) -> None:
        """gh-671 must not regress: it is the more specific spelling and stays
        the one that wins."""
        pyi, ext = _faces(project)
        for face in (pyi, ext):
            assert "TRAILING_MARKER coalescing window." in face

    def test_a_trailing_comment_beats_a_preceding_one(
        self, tmp_path: Path
    ) -> None:
        """Both on one member: the trailing comment is attached to that
        declaration specifically, so it wins."""
        assert _cli("new", "e", cwd=tmp_path).returncode == 0
        root = tmp_path / "e"
        assert _cli("object", "o", cwd=root).returncode == 0
        assert (
            _cli(
                "property", "o", "span", "--type", "int", "--field", cwd=root
            ).returncode
            == 0
        )
        h = root / "native" / "inc" / "o" / "o_core.h"
        h.write_text(
            h.read_text(encoding="utf-8").replace(
                "    int span;",
                "    /** LEADING_LOSES. */\n"
                "    int span; /**< TRAILING_WINS. */",
            ),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=root).returncode == 0
        pyi, ext = _faces(root)
        for face in (pyi, ext):
            assert "TRAILING_WINS." in face
            assert "LEADING_LOSES" not in face


class TestPrecedenceIsUnchanged:
    def test_a_manifest_doc_still_outranks_the_header(
        self, project: Path
    ) -> None:
        """`TOML doc` > getter @brief > member doc > name. The new source
        slots in at the same rung gh-671 put the trailing form on."""
        p = project / "objects" / "o.toml"
        body = p.read_text(encoding="utf-8")
        assert 'name = "width"' in body, body
        p.write_text(
            body.replace(
                'name = "width"',
                'name = "width"\ndoc = "MANIFEST_PROP_DOC wins."',
                1,
            ),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        pyi, ext = _faces(project)
        for face in (pyi, ext):
            assert "MANIFEST_PROP_DOC wins." in face
            assert "PRECEDING_MARKER" not in face

    def test_only_the_summary_is_taken(self, project: Path) -> None:
        """The extended paragraph is deliberately not carried: a property
        docstring is one flowing paragraph, so it could not survive whole
        wherever it were written (gh-1154/gh-1164). Better to state the
        ceiling than to flatten prose into it."""
        pyi, ext = _faces(project)
        for face in (pyi, ext):
            assert "PRECEDING_PROSE" not in face


class TestItDoesNotOverreach:
    """The pattern walks a whole header, so what it must NOT match matters as
    much as what it must."""

    def test_a_function_doxygen_is_not_a_member_doc(self) -> None:
        """Its parameter would otherwise be recorded as a struct field."""
        from just_makeit._docstring import _leading_member_docs

        assert (
            _leading_member_docs(
                "/**\n * @brief Maps a bin.\n * @param b The bin.\n */\n"
                "int fmap(int b);"
            )
            == {}
        )

    def test_a_plain_comment_is_not_a_member_doc(self) -> None:
        from just_makeit._docstring import _leading_member_docs

        assert _leading_member_docs("/* just a note */\nint taps;") == {}

    def test_a_block_with_nothing_after_it_is_not_a_member_doc(self) -> None:
        """A doc comment at the very end of a header documents no
        declaration, and the scan must run out rather than reach past the
        end."""
        from just_makeit._docstring import _leading_member_docs

        assert _leading_member_docs("/** Trailing note. */") == {}
        assert _leading_member_docs("/** Note. */\n\n\n") == {}

    def test_a_blank_line_between_block_and_member_is_fine(self) -> None:
        """Ordinary formatting. The scan skips blank lines to find the thing
        the block documents, rather than requiring them to be adjacent."""
        from just_makeit._docstring import _leading_member_docs

        assert _leading_member_docs("/** Tap count. */\n\n    int taps;") == {
            "taps": "Tap count."
        }

    def test_an_empty_block_documents_nothing_and_does_not_crash(
        self,
    ) -> None:
        """`parse_doxygen_block` returns None for a block with nothing in it,
        and an empty `/** */` is legal C. Reaching through that None raised
        AttributeError -- found by covering this branch after codecov flagged
        it as untaken, which is why a coverage signal is worth chasing rather
        than waiving: the untested path was not merely untested."""
        from just_makeit._docstring import _leading_member_docs

        assert _leading_member_docs("/** */\n    int taps;") == {}
        assert _leading_member_docs("/**\n */\n    int taps;") == {}

    def test_a_trailing_block_is_not_read_as_a_leading_one(self) -> None:
        """`/**<` is the other half of the extractor. Matching it here too
        would attribute a member's own comment to the NEXT declaration."""
        from just_makeit._docstring import _leading_member_docs

        assert _leading_member_docs("int a; /**< A. */\nint b;") == {}
