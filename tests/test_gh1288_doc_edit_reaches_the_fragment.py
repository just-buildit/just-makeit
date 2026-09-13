"""gh-1288: an EDIT to an authored doc must reach an existing fragment member.

A module object's binding lives in a sacred fragment, and `jm apply` refreshes
its doc slots rather than re-rendering the file. Filling an **empty** slot
worked (gh-1192). Editing one that already held text never did:

=======================  =================  ==========================
face                     after apply #1     after editing the header
=======================  =================  ==========================
``.pyi``                 ``PROSE_ONE``      ``PROSE_TWO``
fragment ``PyMethodDef`` ``PROSE_ONE``      ``PROSE_ONE``  *(frozen)*
=======================  =================  ==========================

Repeated `jm apply` did not converge and `jm status --check` was clean
throughout, so the two faces of one object disagreed permanently with nothing
able to say so. The `@code` half is the one that stings: jm flows a `@code`
into the `.pyi` as a runnable ``Examples`` section, so a project that doctests
its stubs gates the stub face while the runtime face keeps an example that no
longer runs.

**The mechanism, measured rather than assumed.** Tracing `_refresh_slot` on
the real repro: for the edited member ``cur`` held jm's own previous
header-derived render, ``der`` held the new one, and ``fb`` (the fragment
rendered with the Doxygen ignored) held the scaffold. ``cur != fb``, ``cur``
was not jm-shaped, ``cur`` was not empty -- so every test fell through to
"hand-written, preserve". The reference carried the edit all along; the
refresh refused it.

That is gh-1191's situation one source over, and gh-1191's own docstring is
the argument: the tests ask *"did jm write this"*, which is unanswerable once
jm's own earlier render is on disk. Asking instead *"did the author declare
this text"* is answerable, and the answer is in the header -- ``der != fb``
says the difference is prose the author wrote.

**What it costs, and the residual.** A docstring hand-tuned in the fragment to
differ from its header is now replaced, and reported by name. A member whose
header declares nothing keeps whatever the fragment holds; that residual is
what stops the rule being "jm overwrites everything", and it is pinned here
and in `tests/test_gh703_stale_fragment_doc_refresh.py`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit._docsync import _refresh_slot  # noqa: E402


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from just_makeit._cli import main; main()"]
        + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


class TestTheDecisionItself:
    """`der != fb` is the whole predicate. Stated at the unit layer first."""

    _HAND = '"Mine, hand written.\\n"'
    _SCAFFOLD = '"Get level.\\n"'
    _DERIVED = '"Authored prose.\\n"'

    def test_a_header_declared_doc_wins(self):
        assert (
            _refresh_slot(self._HAND, self._DERIVED, self._SCAFFOLD)
            == self._DERIVED
        )

    def test_nothing_declared_preserves(self):
        assert (
            _refresh_slot(self._HAND, self._SCAFFOLD, self._SCAFFOLD) is None
        )

    def test_no_fallback_preserves(self):
        """The question needs `fb` to be askable; unasked is not answered."""
        assert _refresh_slot(self._HAND, self._DERIVED, None) is None

    def test_already_current_is_not_an_edit(self):
        """Idempotence at its source: no diff, no write, no report."""
        assert (
            _refresh_slot(self._DERIVED, self._DERIVED, self._SCAFFOLD) is None
        )


def _doc_above(root: Path, symbol: str, doc: str) -> None:
    """Put a Doxygen block above *symbol*'s declaration in the sacred header."""
    h = root / "native" / "inc" / "o" / "o_core.h"
    t = h.read_text(encoding="utf-8")
    m = re.search(rf"^\w[\w ]*\b{re.escape(symbol)}\(", t, re.M)
    assert m, f"{symbol} not declared in {h}"
    h.write_text(t[: m.start()] + doc + t[m.start() :], encoding="utf-8")


def _frag(root: Path) -> str:
    return (root / "native" / "src" / "m" / "m_ext_o.c").read_text(
        encoding="utf-8"
    )


@pytest.fixture
def project(tmp_path) -> Path:
    assert _cli("new", "r", cwd=tmp_path).returncode == 0
    root = tmp_path / "r"
    for args in (
        ("module", "m"),
        ("object", "o", "--module", "m", "--state", "g:double:1.0"),
        ("property", "o", "level", "--module", "m", "--type", "double"),
        (
            "method",
            "o",
            "cfg",
            "--module",
            "m",
            "--param",
            "up:double",
            "--return-type",
            "void",
            "--arg-type",
            "void",
        ),
    ):
        r = _cli(*args, cwd=root)
        assert r.returncode == 0, f"{args}\n{r.stdout}{r.stderr}"
    assert _cli("apply", cwd=root).returncode == 0
    return root


class TestAnAccessorDocEdit:
    """The issue's first repro: a `@brief` on a property accessor."""

    def test_the_first_render_lands_and_so_does_the_edit(self, project: Path):
        _doc_above(
            project, "o_get_level", "/** @brief DOC_ONE the level. */\n"
        )
        assert _cli("apply", cwd=project).returncode == 0
        assert "DOC_ONE" in _frag(project), "gh-1192's empty-slot fill broke"

        h = project / "native" / "inc" / "o" / "o_core.h"
        h.write_text(
            h.read_text(encoding="utf-8").replace("DOC_ONE", "DOC_TWO"),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        frag = _frag(project)
        assert "DOC_TWO" in frag, frag
        assert "DOC_ONE" not in frag, frag

    def test_it_converges(self, project: Path):
        """Repeated apply did not converge, which is how it was found."""
        _doc_above(project, "o_get_level", "/** @brief DOC_ONE. */\n")
        assert _cli("apply", cwd=project).returncode == 0
        h = project / "native" / "inc" / "o" / "o_core.h"
        h.write_text(
            h.read_text(encoding="utf-8").replace("DOC_ONE", "DOC_TWO"),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        once = _frag(project)
        r = _cli("apply", cwd=project)
        assert r.returncode == 0
        assert _frag(project) == once
        # And the second run is silent: nothing was taken, so nothing is named.
        assert "rewritten from the header" not in r.stdout


_METHOD_DOC = """/**
 * @brief PROSE_{n} retune the thresholds.
 *
 * @code
 * obj.cfg(EXAMPLE_{n})
 * @endcode
 */
"""


class TestAMethodDocEditIncludingItsCode:
    """The half the issue calls the stinger.

    jm flows a `@code` into the `.pyi` as a runnable `Examples` section, so a
    project that doctests its stubs gates the stub face -- and the runtime
    face kept an example that no longer runs, with nothing to notice.
    """

    def test_both_the_brief_and_the_example_follow_the_header(
        self, project: Path
    ):
        _doc_above(project, "o_cfg", _METHOD_DOC.format(n="ONE"))
        assert _cli("apply", cwd=project).returncode == 0
        assert "EXAMPLE_ONE" in _frag(project)

        h = project / "native" / "inc" / "o" / "o_core.h"
        h.write_text(
            h.read_text(encoding="utf-8")
            .replace("PROSE_ONE", "PROSE_TWO")
            .replace("EXAMPLE_ONE", "EXAMPLE_TWO"),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        frag = _frag(project)
        assert "PROSE_TWO" in frag and "EXAMPLE_TWO" in frag, frag
        assert "PROSE_ONE" not in frag and "EXAMPLE_ONE" not in frag, frag

    def test_the_two_faces_agree_afterwards(self, project: Path):
        """The property underneath the whole issue, asserted as itself.

        Not "the fragment changed" but "the fragment and the stub say the
        same thing" -- a fix that moved the fragment to some third text would
        pass every assertion above and still leave the faces disagreeing.
        """
        _doc_above(project, "o_cfg", _METHOD_DOC.format(n="ONE"))
        assert _cli("apply", cwd=project).returncode == 0
        h = project / "native" / "inc" / "o" / "o_core.h"
        h.write_text(
            h.read_text(encoding="utf-8")
            .replace("PROSE_ONE", "PROSE_TWO")
            .replace("EXAMPLE_ONE", "EXAMPLE_TWO"),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        pyi = (project / "src" / "r" / "m" / "m.pyi").read_text(
            encoding="utf-8"
        )
        frag = _frag(project)
        for token in ("PROSE_TWO", "EXAMPLE_TWO"):
            assert token in pyi, pyi
            assert token in frag, frag


class TestWhatItTakesIsNamed:
    """The safety story, since jm can now replace prose someone may own.

    gh-871 set the rule when it made the glue reclaim unconditional -- named,
    in the same place a repaired arity is named, with the diff right there.
    gh-1288 widened the population, so the report widened with it.
    """

    def test_apply_names_the_member(self, project: Path):
        _doc_above(project, "o_cfg", "/** @brief DOC_ONE. */\n")
        assert _cli("apply", cwd=project).returncode == 0
        h = project / "native" / "inc" / "o" / "o_core.h"
        h.write_text(
            h.read_text(encoding="utf-8").replace("DOC_ONE", "DOC_TWO"),
            encoding="utf-8",
        )
        r = _cli("apply", cwd=project)
        assert r.returncode == 0
        assert "rewritten from the header" in r.stdout, r.stdout
        assert "cfg" in r.stdout, r.stdout


class TestTheResidual:
    """A member whose header declares nothing keeps what the fragment holds.

    This is the half that stops the rule being "jm overwrites everything",
    and it is what makes the preservation contract still mean something.
    """

    def test_a_hand_written_doc_with_no_header_doxygen_survives(
        self, project: Path
    ):
        frag_path = project / "native" / "src" / "m" / "m_ext_o.c"
        body = frag_path.read_text(encoding="utf-8")
        # `get_g` has no Doxygen on it -- jm's own one-liner is all there is.
        assert '"Get g.\\n"' in body, body
        frag_path.write_text(
            body.replace('"Get g.\\n"', '"HAND WRITTEN, KEEP ME.\\n"', 1),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        assert "HAND WRITTEN, KEEP ME." in _frag(project)
