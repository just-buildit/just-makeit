"""gh-1191: an EDIT to a manifest `doc` reaches the runtime face too.

A manifest `doc` reached the `.pyi` and, the first time, the fragment as well.
**Editing it afterwards reached only the `.pyi`.** `jm apply` exited 0 and said
nothing, so `help(Obj.x)` and a type checker told a reader different things —
and the correct face is what stops anyone checking the other.

Found the expensive way: doppler#1085 was a property docstring that was wrong.
Correcting it updated the manifest, the header and the `.pyi`; the fragment
kept the disproved text, and `jm apply`, `jm status --check` and the project's
own lint were green over it.

Why it declined
---------------
`_docsync._refresh_slot` overwrites a populated slot only when it can
recognise the text as jm's own — the scaffold form, a jm-shaped synopsis, or
reclaimable glue — and otherwise preserves it, so a hand-written docstring
survives regeneration. jm's own *earlier manifest-derived render* matches none
of those: the comparison is against the header-derived and scaffold forms, and
a manifest-authored doc is neither. So the second write was classified
hand-written and dropped, with the corrected text sitting in `der` unused.

The fix is not to loosen "did jm write this", which is unanswerable once jm's
own output is on disk. It is to ask a different question — **"did the author
declare this text?"** — whose answer is in the manifest. A member with a
manifest `doc` is refreshed whatever the slot holds; a member without one is
preserved exactly as before. The `.pyi` beside it has always worked this way,
which is precisely how the two faces came to disagree.

`TestWhatIsStillPreserved` is the half that matters for anyone relying on the
old behaviour, and `test_a_hand_edited_slot_loses_to_a_declared_doc` states the
deliberate trade rather than leaving it to be discovered.

Not fixed here, filed instead (gh-1192): before `apply` runs, `status` files
the fragment under AUTHOR-OWNED — "Nothing to do; they stay unreconciled
permanently" — which is false for any in-place doc refresh. Measured as
**pre-existing**: a plain header-`@brief` edit, with no manifest `doc`
anywhere, reports the same line on 0.71.0 and is likewise fixed by the next
`apply`.
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


def _frag(root: Path, name: str = "o") -> Path:
    return root / "native" / "src" / "m" / f"m_ext_{name}.c"


def _pyi(root: Path) -> Path:
    return root / "src" / "r" / "m" / "m.pyi"


def _edit(root: Path, old: str, new: str) -> None:
    p = root / "objects" / "o.toml"
    body = p.read_text(encoding="utf-8")
    assert old in body, body
    p.write_text(body.replace(old, new), encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A module object with a documented method and property, plus a view.

    The docs are declared in the manifest and applied once, so the fragment
    slots are POPULATED with jm's own manifest-derived render — which is the
    only state in which this bug exists. The view is here because its fragment
    carries inherited copies of the same two members.
    """
    assert _cli("new", "r", cwd=tmp_path).returncode == 0
    root = tmp_path / "r"
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

    p = root / "objects" / "o.toml"
    body = p.read_text(encoding="utf-8")
    body = body.replace(
        'name = "level"\n', 'name = "level"\ndoc = "FIRST property doc."\n', 1
    )
    body = body.replace(
        'name = "gain2"\n', 'name = "gain2"\ndoc = "FIRST method doc."\n', 1
    )
    body = body.replace(
        'no_step = "false"\n',
        'no_step = "false"\ndoc = "FIRST class doc."\n',
        1,
    )
    p.write_text(body, encoding="utf-8")
    assert _cli("apply", cwd=root).returncode == 0
    baseline = _cli("status", "--check", cwd=root)
    assert baseline.returncode == 0, baseline.stdout
    # The premise: the first write DID land, on both faces. Without this the
    # tests below could pass against a fragment that never had the text.
    assert "FIRST property doc." in _frag(root).read_text(encoding="utf-8")
    assert "FIRST method doc." in _frag(root).read_text(encoding="utf-8")
    return root


class TestAnEditReachesBothFaces:
    """gh-1191's own failure. Each slot kind separately, because
    `_doc_slots` walks `PyMethodDef` and `PyGetSetDef` through the same code
    but `tp_doc` through its own, and the report named all three."""

    @pytest.fixture
    def edited(self, project: Path) -> Path:
        _edit(project, "FIRST", "SECOND")
        assert _cli("apply", cwd=project).returncode == 0
        return project

    def test_a_property_doc(self, edited: Path) -> None:
        body = _frag(edited).read_text(encoding="utf-8")
        assert "SECOND property doc." in body, body
        assert "FIRST property doc." not in body, body

    def test_a_method_doc(self, edited: Path) -> None:
        body = _frag(edited).read_text(encoding="utf-8")
        assert "SECOND method doc." in body, body
        assert "FIRST method doc." not in body, body

    def test_the_class_doc(self, edited: Path) -> None:
        body = _frag(edited).read_text(encoding="utf-8")
        assert "SECOND class doc." in body, body
        assert "FIRST class doc." not in body, body

    def test_the_two_faces_agree(self, edited: Path) -> None:
        """The property this is really about. Either face alone can be
        checked and look right; only the pair is the contract."""
        frag = _frag(edited).read_text(encoding="utf-8")
        pyi = _pyi(edited).read_text(encoding="utf-8")
        for marker in (
            "SECOND property doc.",
            "SECOND method doc.",
            "SECOND class doc.",
        ):
            assert marker in frag, marker
            assert marker in pyi, marker

    def test_the_views_inherited_copies_too(self, edited: Path) -> None:
        """A view inherits the members it does not exclude, so the parent's
        declaration is the text for its fragment as well. Keying only on the
        view's own table would leave the stale copy in the file a reader of
        that class opens."""
        body = _frag(edited, "peek").read_text(encoding="utf-8")
        assert "SECOND property doc." in body, body
        assert "FIRST" not in body, body

    def test_a_second_apply_changes_nothing(self, edited: Path) -> None:
        again = _cli("apply", cwd=edited)
        assert again.returncode == 0
        assert "already matches" in again.stdout, again.stdout


class TestWhatIsStillPreserved:
    """The guarantee `_refresh_slot` exists for. The new branch is reached
    only when the manifest declares the text, so everything else is untouched
    — but that is an argument, and an argument is not a gate."""

    def test_a_hand_written_doc_with_no_manifest_doc_survives(
        self, project: Path
    ) -> None:
        """The case the conservative rule was written for: `reset` is jm's,
        the author documented it in the fragment, and no manifest key claims
        that text."""
        frag = _frag(project)
        body = frag.read_text(encoding="utf-8")
        old = '"Reset state to post-create defaults.\\n"'
        assert old in body, body
        frag.write_text(
            body.replace(old, '"HAND WRITTEN, DO NOT TOUCH.\\n"', 1),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        after = frag.read_text(encoding="utf-8")
        assert "HAND WRITTEN, DO NOT TOUCH." in after, after

    def test_a_hand_added_binding_survives(self, project: Path) -> None:
        """gh-770's contract, re-asserted because this change writes into the
        same arrays it protects."""
        frag = _frag(project)
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

    def test_a_hand_edited_slot_loses_to_a_declared_doc(
        self, project: Path
    ) -> None:
        """The deliberate trade, stated rather than discovered.

        A member with a manifest `doc` and a hand-edited fragment slot is a
        contradiction the author wrote, and jm resolves it toward the
        declaration — which is what the `.pyi` has always done with the same
        key. Preserving the fragment instead is what produced gh-1191.
        """
        frag = _frag(project)
        body = frag.read_text(encoding="utf-8")
        assert '"FIRST property doc.\\n"' in body, body
        frag.write_text(
            body.replace(
                '"FIRST property doc.\\n"', '"HAND EDIT ON A DECLARED DOC.\\n"'
            ),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        after = frag.read_text(encoding="utf-8")
        assert "FIRST property doc." in after, after
        assert "HAND EDIT ON A DECLARED DOC." not in after, after


class TestTheHeaderPathIsUnchanged:
    """A header-derived doc has its own refresh path and its own hazards
    (gh-805 §F, gh-871). This change must not move it."""

    def test_a_header_brief_still_reaches_the_fragment(
        self, tmp_path: Path
    ) -> None:
        assert _cli("new", "r", cwd=tmp_path).returncode == 0
        root = tmp_path / "r"
        for step in (
            ("module", "m"),
            ("object", "o", "--module", "m", "--state", "g:double:1.0"),
            ("property", "o", "level", "--module", "m", "--type", "double"),
        ):
            assert _cli(*step, cwd=root).returncode == 0
        assert _cli("apply", cwd=root).returncode == 0
        hdr = root / "native" / "inc" / "o" / "o_core.h"
        body = hdr.read_text(encoding="utf-8")
        decl = "double o_get_level(const o_state_t *state);"
        assert decl in body, body
        hdr.write_text(
            body.replace(
                decl, "/**\n * @brief HEADERDOC marker.\n */\n" + decl, 1
            ),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=root).returncode == 0
        assert "HEADERDOC marker." in _frag(root).read_text(encoding="utf-8")


class TestTheSlotSetIsDerivedFromTheManifest:
    """`manifest_documented` is what separates "declared" from "hand-written",
    so it gets asserted directly rather than only through the CLI — the
    difference between the two is the whole safety argument."""

    @staticmethod
    def _call(cfg, obj, frag):
        sys.path.insert(0, str(SRC))
        from just_makeit._docsync import manifest_documented

        return manifest_documented(cfg, obj, frag)

    def test_only_members_with_a_doc_are_named(self) -> None:
        cfg = {
            "o": {
                "methods": [
                    {"name": "documented", "doc": "x"},
                    {"name": "bare"},
                ],
                "properties": [{"name": "prop", "doc": "y"}],
            }
        }
        names, tp = self._call(cfg, "o", "o")
        assert names == frozenset({"documented", "prop"})
        assert tp is False

    def test_an_empty_doc_is_not_a_declaration(self) -> None:
        """`doc = ""` is the absence of a declaration, not a declaration of
        emptiness — refreshing on it would blank a slot from a key the author
        wrote to clear a stale value."""
        cfg = {"o": {"methods": [{"name": "m", "doc": ""}], "doc": ""}}
        names, tp = self._call(cfg, "o", "o")
        assert names == frozenset()
        assert tp is False

    def test_a_view_unions_its_own_members_with_the_parents(self) -> None:
        cfg = {
            "o": {
                "methods": [{"name": "inherited", "doc": "x"}],
                "views": [
                    {
                        "class_name": "Peek",
                        "doc": "v",
                        "properties": [{"name": "own", "doc": "z"}],
                    }
                ],
            }
        }
        names, tp = self._call(cfg, "o", "peek")
        assert names == frozenset({"inherited", "own"})
        assert tp is True

    def test_an_unknown_fragment_claims_nothing(self) -> None:
        """A fragment jm cannot tie back to a view declares no authored
        members, so the conservative path is what runs."""
        cfg = {"o": {"methods": [{"name": "m", "doc": "x"}], "views": []}}
        names, tp = self._call(cfg, "o", "ghost")
        assert names == frozenset({"m"})
        assert tp is False
