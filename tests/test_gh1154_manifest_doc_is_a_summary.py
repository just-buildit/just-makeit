"""gh-1154 / gh-1164 / gh-1493: what the renderer does to a manifest `doc`.

**Superseded in part by gh-1493**, which made every face render a ``doc``
verbatim: nothing below is truncated or flattened any more, so the only
finding left is a ``doc`` carrying a numpy section heading -- it now lays out
correctly and jm's GENERATED section follows it, so the docstring has two
(kind ``duplicated``). The history below is why the finding is shaped the way
it is; the tests assert today's rule.

gh-1153 made a `[[module.X.functions]]` `doc` survive the manifest round-trip.
The renderer half stayed broken, and gh-1154 gated on it with a single rule --
"a manifest `doc` is a summary" -- firing on any value holding more than one
paragraph.

gh-1164 measured what each face actually writes, and that one rule was wrong
in every shape it fired on. Reported from doppler as 31 findings, none of
which could act on the remedy the message named:

- 28 were `[module.X] doc`, which `_context/_modpath` renders **whole** to
  both faces. Nothing was being lost, and a module is also the one shape with
  no header to derive from -- `_modpath` says so itself -- so the advice
  ("write it in the sacred header") named a place that does not exist. The
  author's only options were to delete prose or to waive the finding.
- 3 were `field = true` properties, whose header field doxygen jm does not
  read. Trimming them to a summary deleted the only copy of the text.

And the sentence itself was false almost everywhere: an object, method or
property doc is **flattened**, not truncated, so every word survives -- as
reflowed prose. Only a module function's `.pyi` genuinely drops anything.

So there are three rules, one per shape, each measured against the artefact:

===============================  ==================  ======================
shape                            renderer does       finding when
===============================  ==================  ======================
`[module.X] doc`                 renders whole       never
`[[module.X.functions]] doc`     `.pyi` truncated    >1 paragraph
object / method / property       flattened to one    a section rule
===============================  ==================  ======================

The third is the defect worth gating and the one gh-1154's changelog actually
described: a `Parameters` heading and its `----------` rule reflowed into
prose, beside the real sections jm then generates. Unlike a paragraph count it
is fixable by editing the value in place, so the gate stays actionable in
every shape it fires on -- which is the property gh-1164 says it lost.

Reporting rather than repairing is unchanged, and so is the remedy: jm
GENERATES the numpy sections from the manifest, so an author-written block
duplicates them rather than merging. Deliberately a **warning**, not a
refusal.

`TestTheRulesMatchTheArtefacts` is the load-bearing class here. Each rule is a
claim about what a face contains, and a claim about generated output that is
only ever checked against the detector is how gh-1154 shipped a gate that
described a loss which was not happening.
"""

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402

from pathlib import Path

import pytest

from _jmrun import JmRun, run_cli


#: Two plain paragraphs: the author's text, rendered as written (gh-1493), and
#: never a finding.
MULTI = '"""One paragraph.\n\nAnd a second."""'

#: A numpy section heading and its rule: laid out as written, and followed by
#: the section jm generates -- the one finding left (`duplicated`).
HEADED = '"""One paragraph.\n\nParameters\n----------\nb : int\n    A bin."""'


def _cli(*args, cwd) -> JmRun:
    # gh-1374: in THIS process -- the child bought isolation only.
    return run_cli(*args, cwd=cwd)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    assert _cli("new", "pp", cwd=tmp_path).returncode == 0
    root = tmp_path / "pp"
    assert (
        _cli(
            "object", "eng", "--state", "gain:double:1.0", cwd=root
        ).returncode
        == 0
    )
    assert (
        _cli(
            "method",
            "eng",
            "exec",
            "--arg-type",
            "double",
            "--return-type",
            "double",
            cwd=root,
        ).returncode
        == 0
    )
    assert (
        _cli(
            "property", "eng", "span", "--type", "int", "--field", cwd=root
        ).returncode
        == 0
    )
    assert _cli("module", "dsp", cwd=root).returncode == 0
    assert (
        _cli(
            "function",
            "fmap",
            "--module",
            "dsp",
            "--param",
            "b:int",
            "--return-type",
            "int",
            cwd=root,
        ).returncode
        == 0
    )
    assert _cli("apply", cwd=root).returncode == 0
    assert _cli("status", "--check", cwd=root).returncode == 0
    return root


def _add_doc(root: Path, rel: str, anchor: str, value: str) -> None:
    p = root / rel
    body = p.read_text(encoding="utf-8")
    assert anchor in body, body
    p.write_text(body.replace(anchor, f"{anchor}\ndoc = {value}", 1), "utf-8")


def _found(root: Path) -> dict:
    from just_makeit import _config as C
    from just_makeit import _docstring as D

    return {
        d.where: d.kind for d in D.manifest_docs_with_sections(C.load(root))
    }


class TestTheRulesMatchTheArtefacts:
    """Each rule is a claim about a generated face. Check the face.

    gh-1154's rule was asserted, never measured, and was wrong about three
    shapes out of four. These build the tree and read what came out.
    """

    def test_a_module_doc_reaches_both_faces_whole(
        self, project: Path
    ) -> None:
        """Rule 1, and the 28-finding half of gh-1164.

        `_modpath` passes the value through `_py_docstring` / `_c_doc_literal`
        with no `group_paragraphs` and no truncation. If that ever changes,
        excluding module docs stops being correct and this fails.
        """
        _add_doc(
            project,
            "modules/dsp.toml",
            "[module.dsp]",
            '"""Sum line.\n\nPARA_TWO survives.\n\nPARA_THREE too."""',
        )
        assert _cli("apply", cwd=project).returncode == 0
        init = (project / "src" / "pp" / "dsp" / "__init__.py").read_text(
            encoding="utf-8"
        )
        ext = (project / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
        for marker in ("Sum line.", "PARA_TWO survives.", "PARA_THREE too."):
            assert marker in init, f"{marker!r} missing from __init__.py"
            assert marker in ext, f"{marker!r} missing from .m_doc"

    def test_a_module_function_doc_reaches_both_faces_whole(
        self, project: Path
    ) -> None:
        """gh-1493: the `.pyi` used to keep paragraph 1 while the extension
        kept it all. Both faces now carry the whole doc."""
        _add_doc(
            project,
            "modules/dsp.toml",
            'name = "fmap"',
            '"""Sum line.\n\nPARA_TWO is dropped."""',
        )
        assert _cli("apply", cwd=project).returncode == 0
        pyi = (project / "src" / "pp" / "dsp" / "dsp.pyi").read_text("utf-8")
        ext = (project / "native" / "src" / "dsp" / "dsp_ext.c").read_text(
            encoding="utf-8"
        )
        assert "Sum line." in pyi
        assert "PARA_TWO is dropped." in pyi, pyi
        assert "PARA_TWO is dropped." in ext, "runtime face lost it"

    def test_a_method_doc_heading_renders_verbatim_and_is_duplicated(
        self, project: Path
    ) -> None:
        """The one finding left, measured on the face.

        The heading now keeps its own line (it used to reflow into
        `Parameters ---------- b : int` prose) -- and jm still emits its own
        generated `Parameters` after it, so the docstring has two. Anchored on
        the literal lines: comparing whitespace-normalised text, as this
        test once did, reads the old wreckage and the new layout the same.
        """
        _add_doc(project, "objects/eng.toml", 'name = "exec"', HEADED)
        assert _cli("apply", cwd=project).returncode == 0
        pyi = (project / "src" / "pp" / "eng.pyi").read_text(encoding="utf-8")
        authored = (
            "\n        Parameters\n        ----------\n        b : int\n"
        )
        exec_doc = pyi[pyi.index("def exec(") :]
        exec_doc = exec_doc[: exec_doc.index('"""', exec_doc.index('"""') + 3)]
        assert authored in exec_doc, exec_doc
        heading = "\n        Parameters\n        ----------\n"
        assert exec_doc.count(heading) == 2, exec_doc


class TestTheDetector:
    def test_a_module_doc_is_never_a_finding(self, project: Path) -> None:
        """Even carrying a section rule: nothing flattens it, and a module has
        no header to move the prose into. This is the gh-1164 regression."""
        _add_doc(project, "modules/dsp.toml", "[module.dsp]", HEADED)
        assert _found(project) == {}

    def test_plain_paragraphs_are_never_a_finding(self, project: Path) -> None:
        """gh-1493: the module function's stub no longer truncates, so a
        paragraph break is just the author's text."""
        _add_doc(project, "modules/dsp.toml", 'name = "fmap"', MULTI)
        assert _found(project) == {}

    def test_plain_paragraphs_are_not_a_finding_where_they_flatten(
        self, project: Path
    ) -> None:
        """doppler's other 3: a `field = true` property whose prose has no
        headings. Flattening joins it into readable text, and the header
        derivation that would let the author move it does not exist -- so
        reporting it asked for a deletion and offered nothing."""
        _add_doc(project, "objects/eng.toml", 'name = "exec"', MULTI)
        _add_doc(project, "objects/eng.toml", 'name = "span"', MULTI)
        _add_doc(project, "objects/eng.toml", "[eng]", MULTI)
        assert _found(project) == {}

    def test_a_section_rule_is_a_finding_wherever_jm_generates_sections(
        self, project: Path
    ) -> None:
        _add_doc(project, "objects/eng.toml", 'name = "exec"', HEADED)
        _add_doc(project, "objects/eng.toml", 'name = "span"', HEADED)
        _add_doc(project, "objects/eng.toml", "[eng]", HEADED)
        _add_doc(project, "modules/dsp.toml", 'name = "fmap"', HEADED)
        assert _found(project) == {
            "eng.doc": "duplicated",
            "eng.methods.exec.doc": "duplicated",
            "eng.properties.span.doc": "duplicated",
            "module.dsp.functions.fmap.doc": "duplicated",
        }

    def test_it_names_entries_readably(self, project: Path) -> None:
        """An index would not tell a reader which method it is."""
        _add_doc(project, "objects/eng.toml", 'name = "exec"', HEADED)
        _add_doc(project, "modules/dsp.toml", 'name = "fmap"', HEADED)
        assert set(_found(project)) == {
            "eng.methods.exec.doc",
            "module.dsp.functions.fmap.doc",
        }

    def test_a_single_paragraph_is_not_a_finding(self, project: Path) -> None:
        """For the truncated shape the predicate is the paragraph BREAK. A
        soft line wrap is joined into flowing prose, which is
        `group_paragraphs` working correctly — flagging it would make the gate
        fire on every wrapped docstring."""
        _add_doc(
            project,
            "modules/dsp.toml",
            'name = "fmap"',
            '"""One paragraph\nsoftly wrapped."""',
        )
        assert _found(project) == {}

    def test_a_rule_with_nothing_above_it_is_not_a_heading(self) -> None:
        """A bare `---` opening a value is not the reST construct, and jm's
        own generated sections must not be read back as author wreckage."""
        from just_makeit import _docstring as D

        assert not D.has_section_rule("Sum.\n\n---\n\nMore.")
        assert D.has_section_rule("Sum.\n\nReturns\n-------\nint\n    A.")

    def test_jms_own_transients_are_skipped(self) -> None:
        """`_doc_blocks` is jm's cache of header Doxygen, not an authored
        manifest value; walking into it would report the header back at the
        author as if they had written it in the manifest."""
        from just_makeit import _docstring as D

        assert (
            D.manifest_docs_with_sections(
                {"eng": {"_doc_blocks": {"x": {"doc": HEADED}}}}
            )
            == []
        )


class TestBothReporters:
    def test_apply_warns_and_names_the_header(self, project: Path) -> None:
        _add_doc(project, "modules/dsp.toml", 'name = "fmap"', HEADED)
        out = _cli("apply", cwd=project)
        assert out.returncode == 0, out.stdout
        assert "module.dsp.functions.fmap.doc" in out.stdout
        assert "`_core.h`" in out.stdout, out.stdout
        assert "@code" in out.stdout, out.stdout

    def test_status_reports_and_check_fails(self, project: Path) -> None:
        _add_doc(project, "modules/dsp.toml", 'name = "fmap"', HEADED)
        assert _cli("apply", cwd=project).returncode == 0
        out = _cli("status", cwd=project)
        assert "DOC (1)" in out.stdout, out.stdout
        assert "doc-overflow (!)" in out.stdout, out.stdout
        assert _cli("status", "--check", cwd=project).returncode == 1

    def test_the_finding_states_todays_mechanism(self, project: Path) -> None:
        """gh-1164 was one wrong sentence for every shape. After gh-1493 the
        sentence must describe the duplication -- and must not still claim
        a flattening or a truncation that no longer happens."""
        _add_doc(project, "objects/eng.toml", 'name = "exec"', HEADED)
        assert _cli("apply", cwd=project).returncode == 0
        out = _cli("status", cwd=project).stdout
        assert "both appear" in out, out
        assert "flattened" not in out and "dropped from the" not in out, out

    def test_status_allow_suppresses_one_entry(self, project: Path) -> None:
        """A project that has decided to live with one keeps the gate on the
        rest."""
        _add_doc(project, "objects/eng.toml", 'name = "exec"', HEADED)
        _add_doc(project, "modules/dsp.toml", 'name = "fmap"', HEADED)
        p = project / "just-makeit.toml"
        p.write_text(
            p.read_text(encoding="utf-8").replace(
                "[project]",
                '[project]\nstatus_allow = ["eng.methods.exec.doc"]',
                1,
            ),
            encoding="utf-8",
        )
        out = _cli("status", cwd=project)
        assert "DOC (1)" in out.stdout, out.stdout
        assert "module.dsp.functions.fmap.doc" in out.stdout

    def test_the_advice_has_one_source(self) -> None:
        """`apply` and `status` both say it; a message duplicated across two
        reporters is a peer pair that drifts."""
        from just_makeit import _docstring as D

        text = D.manifest_doc_advice(D.ManifestDoc("eng.doc", "Sum."))
        assert "eng.doc" in text and "_core.h" in text


class TestTheRecommendedPathActuallyWorks:
    """The advice has to be true, or it is worse than no advice.

    This is the whole justification for reporting instead of teaching the
    renderer to accept author numpy: the header path already renders the
    complete docstring the manifest cannot.
    """

    def test_header_doxygen_renders_the_full_numpy_docstring(
        self, project: Path
    ) -> None:
        h = project / INC_ROOT / "dsp" / "dsp_core.h"
        body = h.read_text(encoding="utf-8")
        assert "int fmap(" in body, body
        h.write_text(
            body.replace(
                "int fmap(",
                "/**\n"
                " * @brief Map an FFT bin to a signed frequency.\n"
                " *\n"
                " * Bins above Nyquist wrap to negative offsets.\n"
                " *\n"
                " * @param b The bin index.\n"
                " * @return The signed offset.\n"
                " *\n"
                " * @code\n"
                " * >>> fmap(3)\n"
                " * 3\n"
                " * @endcode\n"
                " */\n"
                "int fmap(",
                1,
            ),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        pyi = (project / "src" / "pp" / "dsp" / "dsp.pyi").read_text("utf-8")
        for expected in (
            "Map an FFT bin to a signed frequency.",
            "Bins above Nyquist wrap to negative offsets.",
            "Parameters",
            "b : int",
            "Returns",
            "Examples",
            ">>> fmap(3)",
        ):
            assert expected in pyi, f"{expected!r} missing from:\n{pyi}"
