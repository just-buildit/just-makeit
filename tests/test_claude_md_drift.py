"""``CLAUDE.md`` must describe the tree that exists, and state no live values.

**This gate exists because the notes were measured and they had drifted.**
Three prose files describe this project, and every one of them was wrong in a
different way at 0.54.3:

===========================================  ==================================
file                                         drift
===========================================  ==================================
``skills://just-makeit`` (the SSOT)          header said 0.33.13, schema 7,
                                             "backlog is empty — zero open
                                             issues, zero open PRs"
``~/.claude/just-makeit-onboarding``         "0.17.1 (schema 6)" — a *wrong*
                                             schema, which routes a reader to
                                             a migration they do not need
this repo's ``CLAUDE.md``                    27 source modules absent from its
                                             own file table, and 5 of the 11
                                             ``_context/`` builders
===========================================  ==================================

The first two are outside this repository and were fixed by deleting the
numbers: a doc that restates a version resets its own clock every time it is
updated, so the honest form is the command that derives it. Notably, the SSOT
carried the disclaimer *"don't trust this header"* directly above the number
it was disclaiming — the tell that the number should never have been written
down.

The third is in here, and so it is gated rather than described. Three rules,
all **registration-free** — nothing to extend when a module is added:

1. every ``src/just_makeit/_*.py`` and ``_context/_*.py`` appears in
   ``CLAUDE.md``;
2. every path ``CLAUDE.md`` names actually exists;
3. ``CLAUDE.md`` states no version or schema literal outside a fenced example.

**All three pass outright, with no allowlist.** Rule 1 shipped with a
33-entry ratchet; six of those turned out to be the gate's own fault (the
table names the per-command parsers collectively, without a ``.py`` suffix,
which is the better documentation for seven near-identical files), and the
remaining 27 were written up from their own module docstrings. The ratchet is
gone rather than left empty: re-introducing one should feel like a decision.

Rule 1 covers ``_context/`` because that sub-package had gone stale the same
way — a gate that stops at the package root teaches the tree to hide things
one directory down.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE_MD = _ROOT / "CLAUDE.md"
_PKG = _ROOT / "src" / "just_makeit"

# A backticked module name, with or without the `.py`. Both spellings count:
# the table gives each major module its own row as `_object.py`, but names the
# per-command parsers collectively — "`_cli_*.py` — Per-command argument
# parsers (`_cli_new`, `_cli_object`, ...)" — which is the *better* documentation
# for seven near-identical files, and requiring seven rows would be noise.
#
# Insisting on the suffix reported six of those as undocumented when they were
# named one line apart. A gate whose failures are its own formatting rule
# teaches people to widen the allowlist rather than fix the doc.
_NAMED_MODULE = re.compile(r"`(_[A-Za-z0-9_]+)(?:\.py)?`")

# A backticked repo-relative path. Templated segments (`<comp>`) are skipped
# by the caller — they name a shape, not a file.
#
# `just-makeit.toml` and `native/...` are deliberately absent: jm is a
# generator, so most paths in this document describe the tree it *writes*,
# not the tree it *is*. Matching them would report the generator's own
# vocabulary as broken links. Only paths that must exist here are checked.
_NAMED_PATH = re.compile(
    r"`((?:src/just_makeit|tests|docker|\.github|\.devcontainer)/[^`]+"
    r"|standard\.mk|local\.mk|pyproject\.toml|CHANGELOG\.md)`"
)

# A release version or schema number stated as fact. `0.1.0` is deliberately
# NOT matched: it is the scaffolded default inside the example manifest, a
# property of the template rather than a claim about this tree.
_VERSION_CLAIM = re.compile(r"\b0\.(?!1\.0\b)\d+\.\d+\b|schema\s+\d+", re.I)

# A cardinality claim about a set that lives in the tree — "jm has exactly
# one", "there are three". Same disease as `_VERSION_CLAIM` one level out: the
# value is derivable, the prose copy is not maintained, and nothing reads the
# prose back. Deliberately anchored to the sentence that also names the set,
# so ordinary counting prose elsewhere in the file is untouched.
_COUNT_WORD = (
    r"(?:exactly |only |just )?"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
)

# There is deliberately NO allowlist. It landed with 33 entries, shrank to 27
# once the gate stopped insisting on a `.py` suffix the table does not always
# use, and emptied when those 27 were written up from their own module
# docstrings. Re-introducing one should feel like a decision, not a default.


@pytest.fixture(scope="module")
def md() -> str:
    return _CLAUDE_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def documented(md: str) -> set[str]:
    # Keyed on the STEM, since the table spells a name both ways.
    return set(_NAMED_MODULE.findall(md))


@pytest.fixture(scope="module")
def on_disk() -> set[str]:
    """Every module the table should account for, `_context/` included.

    The sub-package was outside the original rule and had gone stale the same
    way the top level had — six of its eleven builders named, five silently
    absent. A gate that stops at the package root teaches the tree to hide
    things one directory down.
    """
    return {p.stem for p in _PKG.glob("_*.py")} | {
        p.stem for p in (_PKG / "_context").glob("_*.py")
    }


def _strip_fenced(text: str) -> str:
    """*text* with fenced code blocks removed.

    An example manifest legitimately contains ``version = "0.1.0"``; it is
    showing the reader what a file looks like, not asserting this project's
    version. Only prose is held to the no-live-values rule.
    """
    return re.sub(r"```.*?```", "", text, flags=re.S)


class TestTheGateIsArmed:
    """Silence has to mean something before it means anything."""

    def test_claude_md_exists_and_is_substantial(self, md):
        assert len(md) > 5_000, "CLAUDE.md is missing or truncated"

    def test_the_table_parsed(self, documented):
        # A regex that matched nothing would make every assertion below pass
        # vacuously — the empty-result-reads-as-green failure.
        assert len(documented) > 20, sorted(documented)

    def test_the_package_was_found(self, on_disk):
        assert len(on_disk) > 40, sorted(on_disk)


class TestEveryModuleIsDescribed:
    """CLAUDE.md's file table covers the package."""

    def test_every_module_is_mentioned(self, documented, on_disk):
        missing = sorted(on_disk - documented)
        assert not missing, (
            f"these modules exist but CLAUDE.md does not mention them: "
            f"{missing}. Add a row to the source-layout table — a reader "
            "who trusts the table will not know they are there."
        )


class TestEveryClaimIsTrue:
    """Both rules pass outright, and must keep passing."""

    def test_every_named_path_exists(self, md):
        broken = []
        for raw in _NAMED_PATH.findall(md):
            # `<comp>`/`<obj>`/`*` name a shape, not a file on disk.
            if any(c in raw for c in "<>*"):
                continue
            path = raw if raw.startswith("src/just_makeit") else raw
            if not (_ROOT / path).exists():
                broken.append(raw)
        assert not broken, (
            f"CLAUDE.md names paths that do not exist: {sorted(set(broken))}"
        )

    def test_local_targets_are_not_counted_or_enumerated(self, md):
        """CLAUDE.md may explain a local target; it may not inventory them.

        `LOCAL_TARGETS` is a set the tree owns, and the prose copy went stale
        exactly the way every version literal did — CLAUDE.md said jm "has
        exactly one: `examples-clean`" while `local.mk` listed five. Nothing
        ever reads a prose count back, so it fails silently and forever.

        Both failure shapes are rejected: a **cardinality** claim, and an
        **enumeration** (naming two or more of the members, which is an
        inventory whether or not it says so). Naming exactly one as the
        illustrative example stays legal — that is the durable content, since
        `examples-clean` is in `local.mk` for a reason worth writing down.

        The member list is parsed from `local.mk`, so adding a local target
        needs no edit here. `make help`'s *Local* section is the SSOT the
        prose should point at.
        """
        local_mk = (_ROOT / "local.mk").read_text(encoding="utf-8")
        m = re.search(r"^LOCAL_TARGETS\s*=\s*(.+)$", local_mk, re.M)
        assert m, "local.mk no longer defines LOCAL_TARGETS"
        members = m.group(1).split()
        assert len(members) >= 2, (
            "this gate needs at least two local targets to distinguish an "
            "illustrative mention from an inventory"
        )

        for para in _strip_fenced(md).split("\n\n"):
            if "LOCAL_TARGETS" not in para:
                continue
            counted = re.search(
                r"\b(?:has|have|is|are)\s+" + _COUNT_WORD + r"\b", para
            )
            assert not counted, (
                f"CLAUDE.md states how many local targets jm has "
                f"({counted.group(0)!r}). `make help` answers that; prose "
                f"does not, and this exact sentence was wrong by four."
            )
            named = [t for t in members if t in para]
            assert len(named) < 2, (
                f"CLAUDE.md enumerates local targets {sorted(named)}. An "
                "inventory in prose goes stale the moment local.mk gains a "
                "target — name one as an example, and point at `make help`."
            )

    def test_no_version_or_schema_literal_in_prose(self, md):
        claims = _VERSION_CLAIM.findall(_strip_fenced(md))
        assert not claims, (
            f"CLAUDE.md states live values in prose: {sorted(set(claims))}. "
            "Every prose copy of a version in this project has gone stale — "
            "the SSOT skill said 0.33.13 at 0.54.3, and the onboarding guide "
            "said schema 6 at schema 7. Name the command that derives it "
            "(`grep '^version' pyproject.toml`) instead of the value."
        )
