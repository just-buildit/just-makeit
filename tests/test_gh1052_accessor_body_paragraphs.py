"""gh-1052: a header-derived accessor body is a paragraph, not one per line.

A `*_max_out` whose header block carries a body paragraph rendered with every
SOURCE LINE as its own paragraph, so the stub was double-spaced and the prose
visibly broken. A **method** body from the same header file in the same
`jm apply` joined and rewrapped correctly, which is what located the fault on
the accessor path rather than in the doc parser.

`GlueMethod._spaced` inserts a blank line between every entry of
`block.body`. That is right for jm's OWN definitions in `_gluedoc`, which
author one paragraph per entry so they stay readable as source -- and
catastrophic for a parsed header block, whose `body` holds *lines*. gh-684's
`_max_out_doc` swapped one for the other with a bare `replace(gm, block=blk)`
and left the spacing rule behind.

It penalised exactly the docs worth writing: a one-line `@brief` renders fine,
so an author is quietly pushed toward saying less, and the workaround is to
fold the explanation into a `@param` -- which puts the reasoning under the
wrong heading.

The preservation half is the one to read first. "Never space" would fix the
report and silently merge every jm-authored glue docstring into a blob, so
:class:`TestJmsOwnProseIsUnaffected` is what makes the fix a fix.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docstring import DoxyBlock  # noqa: E402
from just_makeit._gluedoc import GlueMethod  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

#: Three source lines that are ONE paragraph. The bug turns them into three.
_BODY = [
    "Exactly `n_in * n` - a convolutional code has no fill and no latency",
    "on the encode side, which is the asymmetry with viterbi_decode_max_out,",
    "where the traceback still owes bits at the start of a stream.",
]


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _doc(root: Path, name: str) -> str:
    text = (root / "src/demo/enc.pyi").read_text(encoding="utf-8")
    # The signature may wrap across lines (`def encode(\n self,\n ...`),
    # so match up to the return annotation rather than to the first newline.
    m = re.search(
        rf'    def {name}\(.*?\) -> [^\n]*:\n        """(.*?)\n        """',
        text,
        re.S,
    )
    assert m, f"no stub docstring for {name}()"
    return m.group(1)


def _project(tmp_path: Path, *, author_accessor: bool) -> Path:
    root = tmp_path / "demo"
    _quiet(new_run, "demo", root)
    _quiet(
        object_run,
        root,
        "enc",
        None,
        state_vars=[("n", "int", "0")],
        arg_type="uint8_t[]",
        return_type="uint8_t",
    )
    _quiet(
        method_run,
        root,
        "enc",
        "encode",
        None,
        "uint8_t[]",
        "uint8_t",
        True,
        [],
    )
    hdr = root / "native" / "inc" / "enc" / "enc_core.h"
    lines = hdr.read_text(encoding="utf-8").split("\n")
    # the METHOD block always gets the same body, as the control
    text = "\n".join(lines).replace(
        " * @brief encode.",
        " * @brief Encode a block.\n *\n"
        + "\n".join(f" * {ln}" for ln in _BODY),
        1,
    )
    lines = text.split("\n")
    if author_accessor:
        i = next(
            k
            for k, ln in enumerate(lines)
            if ln.startswith("size_t enc_encode_max_out")
        )
        lines[i:i] = (
            [
                "/**",
                " * @brief Symbols encode writes for n_in input bits.",
                " *",
            ]
            + [f" * {ln}" for ln in _BODY]
            + [
                " *",
                " * @param n_in Number of input bits.",
                " * @return Symbols that call will write.",
                " */",
            ]
        )
    hdr.write_text("\n".join(lines), encoding="utf-8")
    from just_makeit._apply import run as apply_run

    _quiet(apply_run, root)
    return root


def _paragraphs(doc: str) -> list[str]:
    """The docstring's body paragraphs, above `Parameters`."""
    body = doc.split("Parameters")[0]
    return [p.strip() for p in body.split("\n\n") if p.strip()]


class TestTheAuthoredAccessorBody:
    """The report: one paragraph per source line."""

    def test_it_is_one_paragraph(self, tmp_path):
        doc = _doc(_project(tmp_path, author_accessor=True), "encode_max_out")
        paras = _paragraphs(doc)
        # [0] is the @brief; the body must be exactly one more.
        assert len(paras) == 2, f"{len(paras)} paragraphs:\n{doc}"

    def test_the_prose_survives_intact(self, tmp_path):
        doc = _doc(_project(tmp_path, author_accessor=True), "encode_max_out")
        joined = " ".join(doc.split())
        for fragment in ("no fill and no latency", "still owes bits"):
            assert fragment in joined, doc

    def test_the_method_path_is_the_control(self, tmp_path):
        """Same header file, same run — this always joined correctly.

        Pins the premise: without it, "the accessor joins" could be true of a
        build where nothing renders a body at all.
        """
        doc = _doc(_project(tmp_path, author_accessor=True), "encode")
        assert len(_paragraphs(doc)) == 2, doc


class TestJmsOwnProseIsUnaffected:
    """The half that makes the fix a fix rather than a different bug.

    `_spaced` exists because `_gluedoc` authors one paragraph per list entry.
    Removing it outright would fix the report and merge every jm-authored glue
    docstring into a single blob.
    """

    def test_the_max_out_fallback_still_reads_as_prose(self, tmp_path):
        doc = _doc(_project(tmp_path, author_accessor=False), "encode_max_out")
        assert "Size an `out=` buffer" in doc
        assert len(_paragraphs(doc)) == 2, doc

    def test_a_multi_paragraph_glue_method_stays_separated(self, tmp_path):
        """`set_state` is jm's, has two paragraphs, and has no header."""
        root = tmp_path / "demo"
        _quiet(new_run, "demo", root)
        _quiet(
            object_run,
            root,
            "enc",
            None,
            state_vars=[("n", "int", "0")],
            arg_type="uint8_t[]",
            return_type="uint8_t",
            serializable=True,
        )
        text = (root / "src/demo/enc.pyi").read_text(encoding="utf-8")
        m = re.search(
            r'    def set_state\(.*?\) -> [^\n]*:\n        """(.*?)\n        """',
            text,
            re.S,
        )
        assert m, "no set_state stub"
        assert len(_paragraphs(m.group(1))) >= 3, m.group(1)


class TestTheProvenanceRule:
    """Unit-level, and the reason the swap is a method not a `replace`."""

    JM = GlueMethod("f", block=DoxyBlock(brief="B.", body=["one", "two"]))

    def test_jms_paragraphs_are_separated(self):
        assert self.JM._spaced().body == ["one", "", "two"]

    def test_a_header_body_is_left_alone(self):
        gm = self.JM.with_header_block(DoxyBlock(brief="B.", body=["a", "b"]))
        assert gm._spaced().body == ["a", "b"]

    def test_the_swap_cannot_be_set_halfway(self):
        """A bare `replace(gm, block=...)` is what caused this.

        `with_header_block` sets the block and the provenance together, so a
        caller cannot swap one and forget the other.
        """
        gm = self.JM.with_header_block(DoxyBlock(brief="B.", body=["a"]))
        assert gm.body_is_paragraphs is False
        assert gm.block.body == ["a"]
        assert self.JM.body_is_paragraphs is True, "the original was mutated"
