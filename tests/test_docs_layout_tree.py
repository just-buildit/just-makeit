"""The project tree in docs/workflows/layout-and-api.md is a real scaffold.

That tree had drifted a long way before anyone noticed: no `objects/` (the
split layout became the default), no `CMakePresets.json`, `bootstrap.toml`,
`jm_test.h` or link-check table. A picture of the tool's output rots the
moment the output changes, so it is compared with the output.

Each line carries an ownership tag, and the tag is `_createonly`'s
classification of that path, so the page cannot tell a reader that a file is
theirs when `apply` rewrites it. Both directions: every file the scaffold
writes is in the tree, and every file in the tree is in the scaffold with
the tag the classifier gives it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _apply, _createonly

DOC = Path(__file__).parent.parent / "docs" / "workflows" / "layout-and-api.md"

#: The page's words for `_createonly`'s kinds.
TAGS = {
    "yours": _createonly.AUTHOR,
    "jm": _createonly.RECONCILED,
    "shared": _createonly.PARTIAL,
    "versioned": _createonly.JM,
    "derived": _createonly.DERIVED,
}

_LINE = re.compile(
    r"^(?P<lead>[│├└─\s]*)(?P<name>\S.*?)\s*(?:\[(?P<tag>\w+)\].*)?$"
)


def _tree() -> "list[tuple[str, str | None]]":
    """``(path, tag)`` for every entry of the tree block, ``/``-terminated
    for a directory. One line may name several files, space-separated."""
    text = DOC.read_text(encoding="utf-8")
    block = text.split("## Project layout (full)", 1)[1]
    block = block.split("```text\n", 1)[1].split("```", 1)[0]
    lines = block.splitlines()
    assert lines[0] == "my_dsp/", lines[0]
    out, stack = [], []
    for line in lines[1:]:
        m = _LINE.match(line)
        assert m, line
        depth = len(m["lead"]) // 4 - 1
        stack = stack[:depth]
        names = m["name"].split()
        for name in names:
            out.append(("/".join(stack + [name]), m["tag"]))
        if len(names) == 1 and names[0].endswith("/") and not m["tag"]:
            stack.append(names[0].rstrip("/"))
    return out


@pytest.fixture(scope="module")
def scaffold(tmp_path_factory) -> "dict[str, str]":
    root = tmp_path_factory.mktemp("layout")
    assert (
        run_cli("new", "my_dsp", "--object", "gain", cwd=root).returncode == 0
    )
    proj = root / "my_dsp"
    assert run_cli("perf", cwd=proj).returncode == 0
    out = {}
    for p in proj.rglob("*"):
        rel = p.relative_to(proj)
        if p.is_file() and not _apply.is_skipped(rel):
            rule = _createonly.classify(rel.as_posix(), proj)
            out[rel.as_posix()] = rule.kind if rule else "?"
    # the manifest is skipped by the walk and is still the author's file
    out["just-makeit.toml"] = _createonly.AUTHOR
    return out


def _covered(path: str, entries) -> bool:
    return any(
        path == p or (p.endswith("/") and path.startswith(p))
        for p, _ in entries
    )


def test_every_scaffolded_file_is_in_the_tree(scaffold) -> None:
    entries = _tree()
    missing = sorted(p for p in scaffold if not _covered(p, entries))
    assert not missing, f"scaffold writes files the tree omits: {missing}"


def test_every_tree_entry_exists_with_its_owner(scaffold) -> None:
    for path, tag in _tree():
        if tag is None:
            continue  # a plain directory heading
        assert tag in TAGS, f"{path}: unknown tag [{tag}]"
        hits = (
            [p for p in scaffold if p.startswith(path)]
            if path.endswith("/")
            else [path]
        )
        assert hits and all(h in scaffold for h in hits), (
            f"{path} is in the tree but not in a real scaffold"
        )
        wrong = [h for h in hits if scaffold[h] != TAGS[tag]]
        assert not wrong, (
            f"{path} is tagged [{tag}] but jm classifies it "
            f"{scaffold[wrong[0]]}"
        )
