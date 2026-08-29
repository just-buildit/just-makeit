"""gh-1183: a doc slot that was DELETED is put back, not left absent.

Reported from doppler as "a view's `tp_doc` is still absent after gh-1160".
Measured first, because the title points at the wrong file: a view's `tp_doc`
does derive from its own `create_fn`'s Doxygen on the current pin, and so does
a manifest `[[<obj>.views]] doc`. Both faces agree. gh-1160 is not half-landed.

What is broken is one step further back. `_docsync` refreshes the runtime doc
slots by rendering a reference fragment in memory and **replacing** the doc
string-literals in the existing one, matched by entry name — deliberately, so
that every hand-written binding in a sacred fragment survives regeneration as
a structural guarantee rather than a promise. The consequence nobody had hit:
**there is nothing to replace when the slot is gone.**

`_doc_slots` skips an entry with fewer than four fields because there is no
span to write into, and `transplant_docs` did its `tp_doc` work under
`if ex_tp:`. So a fragment whose `.tp_doc = …` line had been deleted never got
one back. doppler hand-wrote that block while gh-1160 was open, deleted it when
the fix shipped, and got a class with `__doc__ == ''`.

`status` does see the difference — gh-767 already deletes these fragments from
its scratch so they rematerialise and get compared — but it classifies the
fragment AUTHOR-OWNED, "these differ because you wrote them that way", and
`--check` does not fail on that. Which is right for a wrapper body and wrong
for `.tp_doc`: that is a slot in jm's own `PyTypeObject` initialiser, jm's to
write.

**Not view-specific.** Measured identically on a parent object's fragment, on
a `PyMethodDef` row and on a `PyGetSetDef` row. The fix is one insert path
shared by all three, and it cannot weaken the preservation guarantee for the
reason that made the gap invisible in the first place: an absent slot has no
content to overwrite.

The asymmetry with a slot set to `NULL` is deliberate and stays. That is a
decision someone wrote down — "no docstring here" — and `_refresh_slot` fills
it only with real Doxygen, never with jm's generic one-liner. An absent field
is not a decision; jm's own render always emits one, so its absence is not
something jm produced.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

BRIEF = "HEADER_VIEW_BRIEF marker."


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
    """doppler's shape: a view over a second constructor, in a module.

    `Peek` is built by `o_create_peek`, whose Doxygen is added to the sacred
    header below — so the text the fix must restore is *derived*, not the
    generic placeholder, and an assertion on it cannot pass by accident. `o`
    itself carries a method and a property so all three slot kinds exist.
    """
    assert _cli("new", "sw", cwd=tmp_path).returncode == 0
    root = tmp_path / "sw"
    for step in (
        ("module", "m"),
        ("object", "o", "--module", "m", "--state", "g:double:1.0"),
        (
            "method",
            "o",
            "gain2",
            "--module",
            "m",
            "--arg-type",
            "double",
            "--return-type",
            "double",
        ),
        ("property", "o", "level", "--module", "m", "--type", "double"),
        ("view", "o", "Peek", "--module", "m", "--create-fn", "o_create_peek"),
    ):
        out = _cli(*step, cwd=root)
        assert out.returncode == 0, f"{step}: {out.stdout}{out.stderr}"

    header = root / "native" / "inc" / "o" / "o_core.h"
    body = header.read_text(encoding="utf-8")
    decl = "o_state_t *o_create_peek(double g);"
    assert decl in body, body
    header.write_text(
        body.replace(
            decl,
            "/**\n"
            f" * @brief {BRIEF}\n"
            " *\n"
            " * @param g The gain to peek with.\n"
            " * @return A peeking view, or NULL.\n"
            " */\n" + decl,
            1,
        ),
        encoding="utf-8",
    )
    assert _cli("apply", cwd=root).returncode == 0
    baseline = _cli("status", "--check", cwd=root)
    assert baseline.returncode == 0, baseline.stdout
    return root


def _frag(root: Path, name: str) -> Path:
    return root / "native" / "src" / "m" / f"m_ext_{name}.c"


def _strip_tp_doc(path: Path) -> None:
    """Delete the whole `.tp_doc = "…" "…",` initialiser — the workaround
    retirement that produced the report."""
    body = path.read_text(encoding="utf-8")
    new, n = re.subn(
        r'\n +\.tp_doc\s*=\s*"(?:[^"\\]|\\.)*"(?:\n\s+"(?:[^"\\]|\\.)*")*,',
        "",
        body,
        count=1,
    )
    assert n == 1, body
    path.write_text(new, encoding="utf-8")


class TestThePremise:
    """Stated as tests because the fix depends on them: if the derivation
    were broken, restoring the slot would restore the wrong text and every
    assertion below would still pass."""

    def test_the_views_tp_doc_derives_from_its_own_create_fn(
        self, project: Path
    ) -> None:
        assert BRIEF in _frag(project, "peek").read_text(encoding="utf-8")

    def test_the_views_stub_says_the_same_thing(self, project: Path) -> None:
        pyi = (project / "src/sw/m/m.pyi").read_text(encoding="utf-8")
        assert BRIEF in pyi, pyi


class TestADeletedTpDocComesBack:
    def test_apply_restores_it(self, project: Path) -> None:
        frag = _frag(project, "peek")
        _strip_tp_doc(frag)
        assert "tp_doc" not in frag.read_text(encoding="utf-8")
        assert _cli("apply", cwd=project).returncode == 0
        body = frag.read_text(encoding="utf-8")
        assert ".tp_doc" in body, body
        assert BRIEF in body, body

    def test_it_lands_where_jm_puts_it(self, project: Path) -> None:
        """Before `.tp_methods`, not appended at the end — the fragment has to
        read as something jm rendered, because next time it is regenerated it
        will be."""
        frag = _frag(project, "peek")
        _strip_tp_doc(frag)
        assert _cli("apply", cwd=project).returncode == 0
        body = frag.read_text(encoding="utf-8")
        assert body.index(".tp_doc") < body.index(".tp_methods"), body

    def test_a_second_apply_changes_nothing(self, project: Path) -> None:
        _strip_tp_doc(_frag(project, "peek"))
        assert _cli("apply", cwd=project).returncode == 0
        again = _cli("apply", cwd=project)
        assert again.returncode == 0
        assert "already matches" in again.stdout, again.stdout

    def test_the_parents_own_fragment_too(self, project: Path) -> None:
        """The report is about a view; the mechanism never was. A fix aimed at
        views would leave this one exactly as it was."""
        frag = _frag(project, "o")
        _strip_tp_doc(frag)
        assert _cli("apply", cwd=project).returncode == 0
        assert ".tp_doc" in frag.read_text(encoding="utf-8")

    def test_status_reports_it_before_apply(self, project: Path) -> None:
        """It was already reported — as AUTHOR-OWNED, which `--check` does not
        fail on. What changes is that `apply` now clears it, so the finding
        stops being one the author can do nothing about."""
        frag = _frag(project, "o")
        _strip_tp_doc(frag)
        out = _cli("status", cwd=project)
        assert "native/src/m/m_ext_o.c" in out.stdout, out.stdout


class TestTheOtherTwoSlotKinds:
    """One insert path, three slots. Enumerated because `_doc_slots`'
    four-field guard skips a short entry for every array it reads, so the
    `PyMethodDef` and `PyGetSetDef` rows had the same hole as `tp_doc`."""

    def test_a_methoddef_row_gets_its_doc_back(self, project: Path) -> None:
        frag = _frag(project, "o")
        body = frag.read_text(encoding="utf-8")
        new, n = re.subn(
            r'(\{"reset",\s+\(PyCFunction\)O_reset,\s+METH_NOARGS)'
            r',\n\s+"(?:[^"\\]|\\.)*"\}',
            r"\1}",
            body,
            count=1,
        )
        assert n == 1, body
        frag.write_text(new, encoding="utf-8")
        assert _cli("apply", cwd=project).returncode == 0
        after = frag.read_text(encoding="utf-8")
        assert "Reset state to post-create defaults." in after, after

    def test_a_getset_row_gets_its_doc_back(self, project: Path) -> None:
        frag = _frag(project, "o")
        body = frag.read_text(encoding="utf-8")
        row = '{ "level", (getter)O_getprop_level, NULL, "Level.\\n", NULL },'
        assert row in body, body
        frag.write_text(
            body.replace(
                row, '{ "level", (getter)O_getprop_level, NULL },', 1
            ),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        after = frag.read_text(encoding="utf-8")
        assert '"Level.\\n"' in after, after
        # The comma must land against the last field, not after the row's own
        # padding — inserting at the closing brace produced `NULL , "…"}`.
        assert "NULL , " not in after, after


class TestWhatIsStillPreserved:
    """The guarantee this module exists for. Inserting is only ever reached
    when the slot is ABSENT, so nothing written by hand is at stake — but that
    is an argument, and an argument is not a gate."""

    def test_a_hand_written_tp_doc_is_untouched(self, project: Path) -> None:
        frag = _frag(project, "peek")
        body = frag.read_text(encoding="utf-8")
        new, n = re.subn(
            r'\.tp_doc(\s*)=\s*"(?:[^"\\]|\\.)*"(?:\n\s+"(?:[^"\\]|\\.)*")*,',
            r'.tp_doc\1= "HAND WRITTEN, DO NOT TOUCH.\\n",',
            body,
            count=1,
        )
        assert n == 1, body
        frag.write_text(new, encoding="utf-8")
        assert _cli("apply", cwd=project).returncode == 0
        after = frag.read_text(encoding="utf-8")
        assert "HAND WRITTEN, DO NOT TOUCH." in after, after
        assert BRIEF not in after.split(".tp_doc")[1][:400], after

    def test_a_slot_set_to_null_stays_null(self, project: Path) -> None:
        """The deliberate asymmetry. `NULL` is a decision someone wrote down;
        `_refresh_slot` fills it only with real Doxygen, and `level`'s derived
        text is jm's own one-liner. An absent field is not a decision — jm's
        render always emits one."""
        frag = _frag(project, "o")
        body = frag.read_text(encoding="utf-8")
        row = '{ "level", (getter)O_getprop_level, NULL, "Level.\\n", NULL },'
        assert row in body, body
        frag.write_text(
            body.replace(
                row,
                '{ "level", (getter)O_getprop_level, NULL, NULL, NULL },',
                1,
            ),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        after = frag.read_text(encoding="utf-8")
        assert "NULL, NULL, NULL }" in after, after

    def test_a_hand_added_binding_survives(self, project: Path) -> None:
        """gh-770's contract, re-asserted here because this change writes into
        the same arrays it protects."""
        frag = _frag(project, "o")
        body = frag.read_text(encoding="utf-8")
        anchor = "static PyMethodDef O_methods[] = {\n"
        assert anchor in body, body
        frag.write_text(
            body.replace(
                anchor,
                anchor
                + '    {"handwritten", (PyCFunction)O_reset, METH_NOARGS,\n'
                '     "A binding the manifest does not know about."},\n',
                1,
            ),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        after = frag.read_text(encoding="utf-8")
        assert "A binding the manifest does not know about." in after, after
