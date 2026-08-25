"""gh-1154: a manifest `doc` is a summary, and jm now says so.

gh-1153 made a `[[module.X.functions]]` `doc` survive the manifest round-trip.
The renderer half stayed broken, and it failed differently on each face,
silently:

- a module function's doc was **truncated** to its first paragraph;
- an object/method/property doc was **flattened** — `group_paragraphs` joins
  it into one paragraph, so a numpy heading and its `----------` rule are
  reflowed into prose, and jm then appends its own generated
  `Parameters`/`Returns` after the wreckage.

Neither is fixed by preserving the value, and that is the decision recorded
here. jm **generates** the numpy sections from the manifest, so an
author-written block does not merge with them — it duplicates them, which the
flattened output already demonstrates. Teaching the renderer to accept
author numpy would add a second input dialect for one renderer, next to the
Doxygen one that already produces a complete docstring.

So jm reports and names that path. The sacred header is where a full
docstring goes: `@brief`, prose, `@param`, `@return` and `@code` render to
summary + extended prose + Parameters + Returns + Examples-with-doctest, and
survive `apply`. That is what the reporter of gh-1153 had already fallen back
to.

Deliberately a **warning**, not a refusal: object, method and property docs
have always accepted a multi-paragraph value, so erroring would break
manifests that carry one today over output that is bad rather than wrong.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"

MULTI = '"""One paragraph.\n\nAnd a second."""'


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


class TestTheDetector:
    def test_it_finds_both_faces_and_names_them_readably(
        self, project: Path
    ) -> None:
        """An index would not tell a reader which method it is."""
        from just_makeit import _config as C
        from just_makeit import _docstring as D

        _add_doc(project, "objects/eng.toml", 'name = "exec"', MULTI)
        _add_doc(project, "modules/dsp.toml", 'name = "fmap"', MULTI)
        found = {
            d.where: d.summary
            for d in D.manifest_docs_with_paragraphs(C.load(project))
        }
        assert found == {
            "eng.methods.exec.doc": "One paragraph.",
            "module.dsp.functions.fmap.doc": "One paragraph.",
        }

    def test_a_single_paragraph_is_not_a_finding(self, project: Path) -> None:
        """The predicate is the paragraph BREAK. A soft line wrap is joined
        into flowing prose, which is `group_paragraphs` working correctly —
        flagging it would make the gate fire on every wrapped docstring."""
        from just_makeit import _config as C
        from just_makeit import _docstring as D

        _add_doc(
            project,
            "objects/eng.toml",
            'name = "exec"',
            '"""One paragraph\nsoftly wrapped."""',
        )
        assert D.manifest_docs_with_paragraphs(C.load(project)) == []

    def test_jms_own_transients_are_skipped(self) -> None:
        """`_doc_blocks` is jm's cache of header Doxygen, not an authored
        manifest value; walking into it would report the header back at the
        author as if they had written it in the manifest."""
        from just_makeit import _docstring as D

        assert (
            D.manifest_docs_with_paragraphs(
                {"eng": {"_doc_blocks": {"x": {"doc": "a\n\nb"}}}}
            )
            == []
        )


class TestBothReporters:
    def test_apply_warns_and_names_the_header(self, project: Path) -> None:
        _add_doc(project, "modules/dsp.toml", 'name = "fmap"', MULTI)
        out = _cli("apply", cwd=project)
        assert out.returncode == 0, out.stdout
        assert "module.dsp.functions.fmap.doc" in out.stdout
        assert "`_core.h`" in out.stdout, out.stdout
        assert "@code" in out.stdout, out.stdout

    def test_status_reports_and_check_fails(self, project: Path) -> None:
        _add_doc(project, "modules/dsp.toml", 'name = "fmap"', MULTI)
        assert _cli("apply", cwd=project).returncode == 0
        out = _cli("status", cwd=project)
        assert "DOC (1)" in out.stdout, out.stdout
        assert "doc-overflow (!)" in out.stdout, out.stdout
        assert _cli("status", "--check", cwd=project).returncode == 1

    def test_status_allow_suppresses_one_entry(self, project: Path) -> None:
        """A project that has decided to live with one keeps the gate on the
        rest."""
        _add_doc(project, "objects/eng.toml", 'name = "exec"', MULTI)
        _add_doc(project, "modules/dsp.toml", 'name = "fmap"', MULTI)
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
        assert "eng.doc" in text and "Sum." in text and "_core.h" in text


class TestTheRecommendedPathActuallyWorks:
    """The advice has to be true, or it is worse than no advice.

    This is the whole justification for reporting instead of teaching the
    renderer to accept author numpy: the header path already renders the
    complete docstring the manifest cannot.
    """

    def test_header_doxygen_renders_the_full_numpy_docstring(
        self, project: Path
    ) -> None:
        h = project / "native" / "inc" / "dsp" / "dsp_core.h"
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
