"""gh-2601: every page showing ``jbx just-makeit:install`` says what to do
when a new shell does not find jm.

The recommended install links jm into ``~/.local/bin`` (Linux, macOS) or
copies its launchers there (Windows). A new shell finds it only once that
directory is on PATH, which ``jbx update-path`` makes permanent. So every
code block a reader copies the install line from is followed, as the next
paragraph at the block's own indentation (inside a tab, too), by the one
sentence that says so: `_installers.UPDATE_PATH_HINT`, held word for word.

Registration-free: every tracked Markdown file is read (``git ls-files``),
so a page that starts showing the line is held the day it does. Files
whose subject is history or an issue thread are not pages a reader
installs from: the changelog and its pending fragments.

GATE: every fenced block holding the install line is followed by the
      update-path sentence.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from _installers import JBX_INSTALL_LINE, ROOT, UPDATE_PATH_HINT

#: History, not instructions.
NOT_PAGES = re.compile(r"^(CHANGELOG\.md|changelog\.d/)")

_FENCE = re.compile(
    r"^(?P<ind> *)```[^\n]*\n(?P<body>.*?)^(?P=ind)```\n", re.M | re.S
)


def _pages() -> "list[Path]":
    out = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [ROOT / f for f in out if not NOT_PAGES.match(f)]


def _blocks_missing_the_hint(text: str) -> "list[int]":
    """Line numbers of install-line blocks not followed by the hint."""
    bad = []
    for m in _FENCE.finditer(text):
        lines = [ln.strip() for ln in m.group("body").splitlines()]
        if JBX_INSTALL_LINE not in lines:
            continue
        after = text[m.end() :].lstrip("\n")
        if not after.startswith(m.group("ind") + UPDATE_PATH_HINT):
            bad.append(text[: m.start()].count("\n") + 1)
    return bad


def test_the_scan_sees_the_install_line():
    # Armed: the line is shown on the README and the Windows page.
    seen = {
        p.relative_to(ROOT).as_posix()
        for p in _pages()
        if JBX_INSTALL_LINE in p.read_text(encoding="utf-8")
    }
    assert {"README.md", "docs/windows.md"} <= seen, sorted(seen)


def test_every_install_line_block_is_followed_by_the_hint():
    bad = []
    blocks = 0
    for page in _pages():
        text = page.read_text(encoding="utf-8")
        blocks += text.count(JBX_INSTALL_LINE)
        for line in _blocks_missing_the_hint(text):
            bad.append(f"{page.relative_to(ROOT)}:{line}")
    assert blocks >= 10, blocks
    assert not bad, (
        f"these blocks show `{JBX_INSTALL_LINE}` without the next paragraph"
        f" being {UPDATE_PATH_HINT!r}:\n  " + "\n  ".join(bad)
    )
