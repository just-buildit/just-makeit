"""gh-2176: an authored `doc` that ends in a doctest keeps its stub parseable.

A text-mode ``.pyi`` doctest (``pytest --doctest-glob='*.pyi'``) reads an
example's expected output up to the next blank line. `authored_docstring`
put the closing quotes straight under the last line, so a `doc` ending in an
example swallowed the quotes and the declaration after them: a parse error
(``inconsistent leading whitespace``) or an example that can never match.
That is gh-691's failure, fixed then for a class's authored ``@code``, and
gh-2059 made it reachable everywhere a `doc` is the whole docstring, starting
with an ``[[<obj>.extra_methods]]`` row's numpy `doc` (measured on doppler's
``wfm_synth``: ``make test-stubs`` failed on the first of six setters). The
author cannot fix it: `authored_doc_lines` drops trailing blank lines.

GATE: every `doc` position a stub renders, given a `doc` that ends in an
      example, yields that example with exactly its own expected output.
"""

from __future__ import annotations

import doctest
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _docstring as D  # noqa: E402
from just_makeit._docstring import authored_docstring  # noqa: E402
from just_makeit._extramethods import pyi_member  # noqa: E402
from test_gh2059_doc_stands_alone import (  # noqa: E402
    _every_position_project,
    _inject,
    _jm,
)


class TestTheIssue:
    def test_the_repro_parses(self):
        """The issue's row, rendered into a class with a member after it."""
        row = {
            "name": "two",
            "fn": "O_two",
            "returns": "int",
            "doc": "Return two.\n\nExamples\n--------\n>>> 1 + 1\n2\n",
        }
        stub = (
            "\n".join(
                ["class O:"]
                + pyi_member(row)
                + ["    def other(self) -> None: ..."]
            )
            + "\n"
        )
        [ex] = doctest.DocTestParser().get_examples(stub)
        assert ex.want == "2\n", stub

    def test_a_doc_that_does_not_end_in_one_is_as_written(self):
        """No churn: only a `doc` the old layout broke changes."""
        for lines in (
            ["One.", "", "Two."],
            ["One.", "", ">>> 1 + 1", "2", "", "Prose after the example."],
        ):
            out = authored_docstring(lines, 4)
            assert out[-2] == f"    {lines[-1]}", out


def _ending_in_an_example(cfg: dict) -> "dict[str, int]":
    """Every `doc` gh-2059's `_inject` sets, rewritten to end in an example
    whose answer names it; ``{marker: answer}``."""
    marks = _inject(cfg)
    answers = {mk: 1000 + int(mk[3:]) for mk in marks}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            m = re.match(r"Summary (ZZD\d{3})\.", str(node.get("doc", "")))
            if m:
                n = answers[m.group(1)]
                node["doc"] = (
                    f"Summary {m.group(1)}.\n\nExamples\n--------\n"
                    f">>> {n - 1} + 1\n{n}"
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(cfg)
    return answers


def _plain(cfg: dict, markers: "set[str]") -> None:
    """Reduce each of *markers*' docs to its one-line summary."""

    def walk(node: object) -> None:
        if isinstance(node, dict):
            m = re.match(r"Summary (ZZD\d{3})\.", str(node.get("doc", "")))
            if m and m.group(1) in markers:
                node["doc"] = f"Summary {m.group(1)}."
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(cfg)


@pytest.fixture(scope="module")
def stubs(tmp_path_factory) -> "tuple[dict[str, str], dict[str, int]]":
    """``({stub name: text}, {marker: answer})`` for the applied fixture.

    Only the positions whose `doc` is the whole docstring keep their
    example. Where jm writes sections beside a `doc` it is a summary
    (gh-1493), and a section in it is already a `status --check` finding
    (gh-2059), so those are reduced to one line. Which is which is the
    detector's answer, not a list kept here.
    """
    root = _every_position_project(tmp_path_factory.mktemp("every"))
    cfg = C.load(root)
    answers = _ending_in_an_example(cfg)
    C.save(root, cfg)
    beside = {
        m.group(0)
        for d in D.manifest_docs_with_sections(C.load(root), root)
        for m in [re.search(r"ZZD\d{3}", d.summary)]
        if m
    }
    cfg = C.load(root)
    _plain(cfg, beside)
    C.save(root, cfg)
    answers = {mk: n for mk, n in answers.items() if mk not in beside}
    _jm("apply", cwd=root)
    texts = {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted((root / "src").rglob("*.pyi"))
    }
    return texts, answers


class TestEveryPosition:
    def test_every_stub_parses(self, stubs) -> None:
        texts, _ = stubs
        broken = {}
        for name, text in texts.items():
            try:
                doctest.DocTestParser().get_examples(text)
            except ValueError as e:
                broken[name] = str(e)
        assert not broken, broken

    def test_every_example_keeps_its_own_answer(self, stubs) -> None:
        """An example that swallowed the quotes parses when the declaration
        after them sits at the same indent -- so the answer is read too."""
        texts, answers = stubs
        seen = set()
        wrong = []
        for name, text in texts.items():
            for ex in doctest.DocTestParser().get_examples(text):
                m = re.fullmatch(r"(\d+) \+ 1\n", ex.source)
                if m and int(m.group(1)) + 1 in answers.values():
                    seen.add(int(m.group(1)) + 1)
                    want = f"{int(m.group(1)) + 1}\n"
                    if ex.want != want:
                        around = text.split("\n")[
                            max(0, ex.lineno - 8) : ex.lineno + 4
                        ]
                        wrong.append(
                            f"{name}: {ex.want!r}\n" + "\n".join(around)
                        )
        assert seen, "no injected example reached a stub"
        assert not wrong, "\n".join(wrong)
