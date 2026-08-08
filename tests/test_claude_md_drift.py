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
this repo's ``CLAUDE.md``                    33 of 60 source modules absent
                                             from its own file table
===========================================  ==================================

The first two are outside this repository and were fixed by deleting the
numbers: a doc that restates a version resets its own clock every time it is
updated, so the honest form is the command that derives it. Notably, the SSOT
carried the disclaimer *"don't trust this header"* directly above the number
it was disclaiming — the tell that the number should never have been written
down.

The third is in here, and so it is gated rather than described. Two rules,
both **registration-free** — no list to extend when a module is added:

1. every ``src/just_makeit/_*.py`` appears in ``CLAUDE.md``;
2. every path ``CLAUDE.md`` names actually exists;
3. ``CLAUDE.md`` states no version or schema literal outside a fenced example.

Rule 2 passes today with nothing held back, which is the more valuable half:
it means the table has never pointed at a file that moved. Rule 1 is where
the drift is, so it carries a ratchet seeded with what was already missing.
The ratchet may only shrink, and `test_the_ratchet_only_holds_real_gaps`
fails if an entry becomes documented or its module disappears — so it cannot
rust into a permanent allowlist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE_MD = _ROOT / "CLAUDE.md"
_PKG = _ROOT / "src" / "just_makeit"

# A backticked module basename, the form CLAUDE.md's file table already uses.
_NAMED_MODULE = re.compile(r"`(_[A-Za-z0-9_]+\.py)`")

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

# Modules missing from the table when this gate landed. Ratchet: may only
# shrink. Documenting one is the fix; deleting the entry is how it lands.
_KNOWN_UNDOCUMENTED = {
    "_bind.py",
    "_capsule.py",
    "_cfmt.py",
    "_ci.py",
    "_cli_function.py",
    "_cli_method.py",
    "_cli_new.py",
    "_cli_object.py",
    "_cli_parse.py",
    "_cli_remove.py",
    "_cli_view.py",
    "_codec.py",
    "_codecheck.py",
    "_coerce.py",
    "_composer.py",
    "_docstring.py",
    "_docsync.py",
    "_error.py",
    "_fmtprobe.py",
    "_glue.py",
    "_gluedoc.py",
    "_handle.py",
    "_hollow.py",
    "_keys.py",
    "_migrate.py",
    "_pyfmt.py",
    "_record.py",
    "_regenerate.py",
    "_report.py",
    "_status.py",
    "_termynal_fence.py",
    "_view.py",
    "_warning.py",
}


@pytest.fixture(scope="module")
def md() -> str:
    return _CLAUDE_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def documented(md: str) -> set[str]:
    return set(_NAMED_MODULE.findall(md))


@pytest.fixture(scope="module")
def on_disk() -> set[str]:
    return {p.name for p in _PKG.glob("_*.py")}


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

    def test_the_ratchet_describes_this_tree(self, on_disk):
        # A ratchet entry for a module that no longer exists is dead weight
        # that hides a real gap behind a familiar-looking name.
        stale = sorted(_KNOWN_UNDOCUMENTED - on_disk)
        assert not stale, (
            f"{stale} are in _KNOWN_UNDOCUMENTED but not on disk — delete "
            "them; the ratchet must describe the tree it guards."
        )


class TestEveryModuleIsDescribed:
    """CLAUDE.md's file table covers the package."""

    def test_no_undocumented_module_outside_the_ratchet(
        self, documented, on_disk
    ):
        missing = sorted(on_disk - documented - _KNOWN_UNDOCUMENTED)
        assert not missing, (
            f"these modules exist but CLAUDE.md does not mention them: "
            f"{missing}. Add a row to the source-layout table — a reader "
            "who trusts the table will not know they are there."
        )

    @pytest.mark.parametrize("name", sorted(_KNOWN_UNDOCUMENTED))
    def test_the_ratchet_only_holds_real_gaps(self, documented, name):
        # Documenting a module is the fix; removing it from the ratchet is
        # how that fix lands. Without this the set rusts into an allowlist.
        assert name not in documented, (
            f"{name} is now documented — remove it from "
            "_KNOWN_UNDOCUMENTED so the gate covers it."
        )


class TestEveryClaimIsTrue:
    """No ratchet here: both rules pass outright, and must keep passing."""

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

    def test_no_version_or_schema_literal_in_prose(self, md):
        claims = _VERSION_CLAIM.findall(_strip_fenced(md))
        assert not claims, (
            f"CLAUDE.md states live values in prose: {sorted(set(claims))}. "
            "Every prose copy of a version in this project has gone stale — "
            "the SSOT skill said 0.33.13 at 0.54.3, and the onboarding guide "
            "said schema 6 at schema 7. Name the command that derives it "
            "(`grep '^version' pyproject.toml`) instead of the value."
        )
