"""gh-633: a multi-line property doc must not break the C string literal.

A multi-line manifest `doc` was emitted into `PyGetSetDef`'s 4th field with its
newlines **unescaped**, producing an unterminated string literal and a module
that does not compile:

    { "note", (getter)Widget_getprop_note, NULL, "First line of the doc.
    Second line -- this newline is the whole bug.\\n", NULL },

Quotes and backslashes were escaped on the very same path, so the escaping step
existed and simply did not cover `\\n`.

Fixed in `_build_ml_doc` -- the shared emitter every C docstring goes through --
rather than at the property call site. A caller that hands over *prose* (a
manifest `doc =` triple-quoted string) should not have to know it must
pre-split; callers that already pass a list of lines are unaffected.

The load-bearing test is `test_generated_c_has_no_unterminated_literal`: it
compiles the generated file. Asserting on the emitted text alone would have
passed for several *other* wrong outputs (a literal `\\n` two-character escape
renders fine to the eye and is also correct here, but a stray quote would not
be), and "does it build" is the property that actually matters.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._config import load as cfg_load, save as cfg_save  # noqa: E402
from just_makeit._context._parse import _build_ml_doc  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402

_DOC = "First line of the doc.\nSecond line -- this newline is the whole bug."


class TestBuildMlDoc:
    """The shared emitter, where the fix lives."""

    def test_embedded_newline_is_split_into_adjacent_literals(self):
        # A real newline BETWEEN adjacent literals is correct C -- that is how
        # this emitter has always laid them out. The bug is a raw newline
        # INSIDE one, which shows up as an odd quote count on a line.
        out = _build_ml_doc(["one\ntwo"])
        for line in out.splitlines():
            assert line.count('"') % 2 == 0, f"unbalanced: {line!r}"
        assert out == _build_ml_doc(["one", "two"]), (
            "prose and a pre-split list must render identically, or the call "
            "site has to know which form to pass"
        )

    def test_already_split_input_is_unchanged(self):
        assert _build_ml_doc(["a", "b"]).count('"a\\n"') == 1

    def test_quotes_and_backslashes_still_escape(self):
        out = _build_ml_doc(['say "hi"\\here'])
        assert '\\"hi\\"' in out
        assert "\\\\here" in out

    def test_trailing_and_leading_newlines_do_not_emit_stray_literals(self):
        # A doc= block often ends with a newline; that must not become an
        # unterminated or malformed entry.
        for doc in ("x\n", "\nx", "a\n\nb"):
            out = _build_ml_doc([doc])
            for piece in out.split("\n"):
                piece = piece.strip()
                assert piece.startswith('"') and piece.endswith('"'), piece


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "demo"
    new_run("demo", root)
    module_run(root, "thing")
    object_run(
        root,
        "widget",
        "thing",
        state_vars=[("gain", "double", "1.0")],
        arg_type="double",
        return_type="double",
        mutable=True,
    )
    cfg = cfg_load(root)
    cfg.setdefault("widget", {}).setdefault("properties", []).append(
        {"name": "note", "type": "int", "doc": _DOC}
    )
    cfg_save(root, cfg)
    frag = root / "native" / "src" / "thing" / "thing_ext_widget.c"
    frag.unlink()  # the fragment is sacred; regenerate to see the emission
    apply_run(root)
    return root, frag


class TestGeneratedC:
    def test_no_raw_newline_inside_a_string_literal(self, project):
        _root, frag = project
        block = frag.read_text(encoding="utf-8").split("Widget_getset[]")[1]
        entry = block.split("{ NULL }")[0]
        # Every quoted run on every line must open and close on that line.
        for line in entry.splitlines():
            assert line.count('"') % 2 == 0, f"unbalanced quotes: {line!r}"

    def test_both_lines_of_the_doc_survive(self, project):
        _root, frag = project
        text = frag.read_text(encoding="utf-8")
        assert "First line of the doc." in text
        assert "Second line -- this newline is the whole bug." in text

    @pytest.mark.skipif(
        shutil.which("gcc") is None, reason="no C compiler available"
    )
    def test_generated_c_has_no_unterminated_literal(self, project):
        """The property that actually matters: it compiles.

        Syntax-only, and only the literal diagnostic is asserted on -- the
        fragment is a `#include`d piece of the aggregator, so it is full of
        unrelated 'unknown type' errors when compiled alone. An unterminated
        string is a *lexer* error and shows up regardless.
        """
        _root, frag = project
        r = subprocess.run(
            [
                "gcc",
                "-fsyntax-only",
                "-I",
                str(frag.parents[2] / "inc"),
                "-I",
                sysconfig.get_paths()["include"],
                str(frag),
            ],
            capture_output=True,
            text=True,
        )
        assert "missing terminating" not in r.stderr, r.stderr[:400]
        assert "unterminated" not in r.stderr, r.stderr[:400]
