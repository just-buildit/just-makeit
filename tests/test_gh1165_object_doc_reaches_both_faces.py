"""gh-1165: an object's manifest `doc` must reach both generated faces.

A standalone object's `[<comp>] doc` reached NEITHER the `.pyi` class
docstring nor the runtime `tp_doc`. Both kept the scaffold seed ("`<C>`
component." / "`<C>` component. Wraps `<c>_state_t`."), single- or
multi-paragraph, and `jm regenerate` did not pick it up either.

The plumbing all looked right, which is what made it survive:
`_glue.component_ctx` feeds the manifest doc to
`_docstring.authored_class_brief`, which documents it as outranking the
header's `@brief`, and `_object` passes it on the creation path too.

The bug was one line ABOVE all of that. `_apply` re-renders both faces from
an enriched context only under

    if _real_blocks or _pg:

-- "did the HEADER enrich anything, or is there a rendezvous". A manifest
`doc` is neither, so with a plain header the branch was skipped entirely and
the temp scaffold's trivial text stood. The trigger asked a narrower question
than the render it guarded answered.

Widening it is safe for the reason gh-805 §F already records in `_glue`: the
hazard behind that narrow gate is about *header* doc_blocks, which
`jm object` renders without and `jm apply` renders with -- so an
unconditional re-render would make a freshly scaffolded project report STALE
against itself. A manifest `doc` comes from the manifest, which both paths
read alike, so it cannot produce that disagreement. `TestNoFalseDrift` is
what holds that claim to account.

Discovered while measuring gh-1164: the doc gate reported an object doc as
"only '<summary>' survives" when in truth none of it did.
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
    assert _cli("new", "pp", cwd=tmp_path).returncode == 0
    root = tmp_path / "pp"
    assert (
        _cli(
            "object", "eng", "--state", "gain:double:1.0", cwd=root
        ).returncode
        == 0
    )
    assert _cli("apply", cwd=root).returncode == 0
    assert _cli("status", "--check", cwd=root).returncode == 0
    return root


def _set_doc(root: Path, value: str) -> None:
    p = root / "objects" / "eng.toml"
    body = p.read_text(encoding="utf-8")
    assert "[eng]\n" in body, body
    p.write_text(
        body.replace("[eng]\n", f"[eng]\ndoc = {value}\n", 1), "utf-8"
    )


def _faces(root: Path) -> tuple[str, str]:
    """(stub class docstring source, runtime binding source)."""
    return (
        (root / "src" / "pp" / "eng.pyi").read_text(encoding="utf-8"),
        (root / "native" / "src" / "eng" / "eng_ext.c").read_text("utf-8"),
    )


class TestItReachesBothFaces:
    def test_a_manifest_doc_lands_in_stub_and_runtime(
        self, project: Path
    ) -> None:
        """The whole issue. Before the fix neither face contained it."""
        _set_doc(project, '"OBJ_DOC_MARKER is the summary."')
        assert _cli("apply", cwd=project).returncode == 0
        pyi, ext = _faces(project)
        assert "OBJ_DOC_MARKER is the summary." in pyi, pyi[:400]
        assert "OBJ_DOC_MARKER is the summary." in ext
        # ...and the scaffold seed it replaced is gone from the runtime face,
        # which is the half that read "Wraps eng_state_t" however the object
        # was documented.
        assert "Wraps eng_state_t" not in ext

    def test_it_outranks_the_headers_brief(self, project: Path) -> None:
        """`authored_class_brief` documents manifest > header. That precedence
        was unobservable while the manifest value reached nothing."""
        h = project / "native" / "inc" / "eng" / "eng_core.h"
        body = h.read_text(encoding="utf-8")
        assert "eng_create" in body, body
        h.write_text(
            body.replace(
                "eng_state_t *eng_create",
                "/**\n * @brief HEADER_BRIEF_MARKER.\n */\n"
                "eng_state_t *eng_create",
                1,
            ),
            encoding="utf-8",
        )
        _set_doc(project, '"MANIFEST_MARKER wins."')
        assert _cli("apply", cwd=project).returncode == 0
        pyi, ext = _faces(project)
        for face in (pyi, ext):
            assert "MANIFEST_MARKER wins." in face
            assert "HEADER_BRIEF_MARKER" not in face

    def test_a_header_only_doc_still_lands(self, project: Path) -> None:
        """The pre-existing path must keep working: with no manifest `doc`,
        the header's @brief is still what both faces show."""
        h = project / "native" / "inc" / "eng" / "eng_core.h"
        body = h.read_text(encoding="utf-8")
        h.write_text(
            body.replace(
                "eng_state_t *eng_create",
                "/**\n * @brief HEADER_ONLY_MARKER.\n */\n"
                "eng_state_t *eng_create",
                1,
            ),
            encoding="utf-8",
        )
        assert _cli("apply", cwd=project).returncode == 0
        pyi, ext = _faces(project)
        assert "HEADER_ONLY_MARKER." in pyi
        assert "HEADER_ONLY_MARKER." in ext


class TestNoFalseDrift:
    """The widened trigger must not make a project disagree with itself.

    This is the hazard the narrow condition was protecting, and the reason it
    was written that way -- so it is the claim that has to be checked, not
    asserted.
    """

    def test_a_doc_bearing_project_is_stable(self, project: Path) -> None:
        _set_doc(project, '"OBJ_DOC_MARKER is the summary."')
        assert _cli("apply", cwd=project).returncode == 0
        out = _cli("status", "--check", cwd=project)
        assert out.returncode == 0, out.stdout

    def test_apply_is_idempotent_with_a_doc(self, project: Path) -> None:
        """A second apply must change nothing -- a re-render that differed
        from itself would show up here rather than as a mystery STALE later."""
        _set_doc(project, '"OBJ_DOC_MARKER is the summary."')
        assert _cli("apply", cwd=project).returncode == 0
        before = _faces(project)
        assert _cli("apply", cwd=project).returncode == 0
        assert _faces(project) == before

    def test_a_project_without_a_doc_is_unchanged(self, project: Path) -> None:
        """Zero churn where nothing is declared: the trigger only widens for
        an object that actually carries a manifest `doc`."""
        before = _faces(project)
        assert _cli("apply", cwd=project).returncode == 0
        assert _faces(project) == before
        assert _cli("status", "--check", cwd=project).returncode == 0
