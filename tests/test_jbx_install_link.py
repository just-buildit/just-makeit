"""Every mention of jbx links to the one place that says how to install it.

jbx (just-runit) is just-bashit's tool, so how to install it is just-bashit's
to document: its "Getting just-runit" section is the single source, and jm's
docs link there instead of carrying their own copy. A reader who meets
``jbx install-deps -g dev`` -- in jm's README, or in the README jm writes
into every project it scaffolds -- is one click from installing it, and a
change to the installer is made once, upstream.

Two rules, over every Markdown file jm ships or publishes (found, not
listed, so a new page is covered the day it is added):

1. A file that mentions ``jbx`` links ``JBX_INSTALL``.
2. No file carries its own install recipe (``get-jb.sh``): the recipe is the
   source's to state, and a copy is what drifts.

The frozen ``stale_project`` tree is a 0.33.14 project's output, kept
verbatim on purpose, and the changelogs are history; neither is read.

GATE: every jbx mention in jm's docs links the one jbx install section.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: The one place that says how to install jbx: just-bashit's own page.
JBX_INSTALL = (
    "https://just-buildit.github.io/just-bashit/just-runit/#getting-just-runit"
)
#: Markdown jm publishes: the docs site, the README, the templates every
#: scaffolded project gets, and the bundled examples (and their .steps).
SCANNED = (
    [ROOT / "README.md"]
    + sorted((ROOT / "docs").rglob("*.md"))
    + sorted((ROOT / "src" / "just_makeit" / "templates").rglob("*.md"))
    + sorted((ROOT / "src" / "just_makeit" / "examples").rglob("*.md"))
)
#: Kept verbatim on purpose (see the module docstring).
FROZEN = ROOT / "src" / "just_makeit" / "examples" / "stale_project" / "tree"

_JBX = re.compile(r"(?<![\w/.-])jbx(?![\w-])")


def _files() -> "list[Path]":
    return [p for p in SCANNED if FROZEN not in p.parents]


def test_the_scan_sees_the_known_mentions():
    """Armed: the two pages that tell a user to run jbx are in the scan."""
    seen = {p for p in _files() if _JBX.search(p.read_text(encoding="utf-8"))}
    for must in (
        ROOT / "README.md",
        ROOT / "src" / "just_makeit" / "templates" / "doc" / "README.md",
    ):
        assert must in seen, f"{must.relative_to(ROOT)} no longer mentions jbx"


def test_every_jbx_mention_links_the_install_section():
    bare = [
        str(p.relative_to(ROOT))
        for p in _files()
        if _JBX.search(text := p.read_text(encoding="utf-8"))
        and f"]({JBX_INSTALL})" not in text
    ]
    assert not bare, (
        "these mention jbx without linking how to install it; add a "
        f"Markdown link to {JBX_INSTALL} where jbx first appears:\n  "
        + "\n  ".join(bare)
    )


def test_no_page_carries_its_own_jbx_install_recipe():
    copies = [
        str(p.relative_to(ROOT))
        for p in _files()
        if "get-jb.sh" in p.read_text(encoding="utf-8")
    ]
    assert not copies, (
        "these spell out how to install jbx; link "
        f"{JBX_INSTALL} instead, which is where that recipe lives:\n  "
        + "\n  ".join(copies)
    )
