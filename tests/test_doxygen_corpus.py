"""Construct-level golden corpus for Doxygen -> docstring derivation (gh-649).

Every file in ``tests/fixtures/doxygen/`` is one small C header exercising one
Doxygen construct. For each, this module runs the whole derivation front end —
``extract_doc_blocks`` -> ``parse_doxygen_block`` -> the numpy renderer — and
compares the result against a committed ``.golden`` snapshot.

Why a corpus rather than more hand-written assertions
-----------------------------------------------------
``_docstring.py`` is a regex parser over free-form C comments, so almost every
change to it has effects far from the case being fixed. Before this corpus the
suite pinned the four happy-path tags and nothing else, which meant ~25
constructs (``@note``, ``\\brief``, ``@param[out]``, markdown tables, …) could
change behaviour silently. The goldens turn each of those into a reviewable
diff in the pull request that causes it.

That matters most for the staged work under gh-655: gh-641/gh-650 *remove*
garbage from rendered docstrings and gh-652 later *adds* numpy sections for the
same tags. Both are deliberate output changes, and the point of the snapshot is
to show exactly which constructs each one touches.

The rendering convention
------------------------
A fixture declares exactly one function. To keep rendering deterministic
without per-fixture configuration, the Python-facing signature is derived from
the block itself: each parsed ``@param`` name becomes a ``float`` argument in
order, the return annotation is always ``float``, and a block with no params
renders against a single ``x: float`` so the ``Parameters`` section (and its
``Input.`` fallback) still appears.

Regenerating
------------
After an intentional derivation change::

    JM_UPDATE_DOXYGEN_GOLDENS=1 pytest tests/test_doxygen_corpus.py

then read the resulting ``git diff`` as the change's user-visible effect and
commit it alongside the code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._docstring import (  # noqa: E402
    extract_doc_blocks,
    parse_doxygen_block,
    render_numpy_doc,
)

FIXTURES = Path(__file__).parent / "fixtures" / "doxygen"
UPDATE = os.environ.get("JM_UPDATE_DOXYGEN_GOLDENS") == "1"


def _cases() -> list[str]:
    """Return every fixture stem, sorted, so the corpus is order-stable."""
    return sorted(p.stem for p in FIXTURES.glob("*.h"))


def _render(stem: str) -> str:
    """Return the full derivation report for fixture *stem*.

    The report is deliberately plain text rather than a repr of the dataclass:
    it is read as a diff by a human reviewing a parser change, so every field
    gets its own labelled line and the rendered docstring is reproduced
    verbatim.
    """
    header = (FIXTURES / f"{stem}.h").read_text(encoding="utf-8")
    blocks = extract_doc_blocks(header)

    out: list[str] = [f"# fixture: {stem}", ""]
    out.append(f"extracted: {sorted(blocks)}")
    if not blocks:
        out.append("")
        out.append("<no doc block extracted>")
        return "\n".join(out) + "\n"

    fn = sorted(blocks)[0]
    block = parse_doxygen_block(blocks[fn], fn)
    out.append("")
    if block is None:
        out.append("parsed: None  (no usable content; name fallback applies)")
        out.append("")
        out.append("rendered:")
        out += render_numpy_doc(None, fn, [("x", "float")], "float")
        return "\n".join(out) + "\n"

    out.append(f"brief:    {block.brief!r}")
    out.append(f"body:     {block.body!r}")
    out.append(f"params:   {block.params!r}")
    out.append(f"dirs:     {getattr(block, 'param_dirs', {})!r}")
    out.append(f"returns:  {block.returns!r}")
    out.append(f"examples: {block.examples!r}")
    out.append(f"tags:     {getattr(block, 'tags', [])!r}")
    out.append("")
    out.append("rendered:")

    py_params = [(n, "float") for n, _ in block.params] or [("x", "float")]
    out += render_numpy_doc(block, fn, py_params, "float")
    return "\n".join(out) + "\n"


@pytest.mark.parametrize("stem", _cases())
def test_derivation_matches_golden(stem: str) -> None:
    """Derivation output for *stem* is byte-identical to its golden."""
    golden = FIXTURES / f"{stem}.golden"
    actual = _render(stem)
    if UPDATE:
        golden.write_text(actual, encoding="utf-8")
        return
    assert golden.exists(), (
        f"missing golden for {stem}; regenerate with "
        f"JM_UPDATE_DOXYGEN_GOLDENS=1 pytest tests/test_doxygen_corpus.py"
    )
    assert actual == golden.read_text(encoding="utf-8")


def test_corpus_is_not_empty() -> None:
    """Guard against the fixture directory silently disappearing.

    Every assertion above is parametrized over the fixture glob, so an empty
    directory would collect zero tests and report a green run.
    """
    assert len(_cases()) >= 30


def test_no_orphan_goldens() -> None:
    """Every ``.golden`` has a fixture; a deleted case leaves no snapshot."""
    stems = set(_cases())
    orphans = sorted(
        p.name for p in FIXTURES.glob("*.golden") if p.stem not in stems
    )
    assert not orphans, f"golden files with no fixture: {orphans}"
