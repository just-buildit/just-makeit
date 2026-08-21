"""Every flag the CLI accepts must be reachable in the docs.

A flag that ships without a doc line is indistinguishable, from the outside,
from a flag that does not exist — and jm has paid for that twice at issue
scale. gh-1003 asked for an omittable array init-param to be *built*; it had
shipped as `default = "[]"` and the quick reference documented its argument
ordering without ever saying what it was for. gh-528 made a downstream publish
a struct it did not need to, because the computed-property path that already
kept it opaque was not written down. In both cases the capability existed, the
prose did not, and the cost landed on a consumer rather than on jm.

This file is the registration-free version of "remember to document the flag".
It does not hold a list of flags — a list is the thing that goes stale
silently, which is the bug. It **derives** the flag set from the parsers
themselves, so a flag added tomorrow is covered with no edit here.

Two properties, at two different layers:

`test_every_parsed_flag_is_documented`
    The reference docs must mention every flag. Strict: generated example
    walkthroughs under `docs/examples/` do NOT count. An example that happens
    to use a flag is not a description of it, and it would let the gate pass on
    a flag whose only appearance is inside somebody's sample command line.

`test_help_coverage_does_not_regress`
    `jm --help` is documentation too, and it is the copy a user reads first.
    17 parsed flags are absent from it as of 0.62.0 — a real backlog, tracked
    in gh-1015 rather than fixed here, so the number is a **ratchet that may
    only shrink**. Fixing a flag's help text lowers it; adding a flag without
    help text cannot raise it.

    That backlog is a recurrence, which is why it is a ratchet and not a note:
    gh-496 was this exact class on `jm app`, fixed as four hand-added flags in
    0.30.2 with nothing left behind to check the parsers against the help
    block. Thirteen more arrived the same way.

**Why the extraction is a scan for string literals rather than an import.**
`docs-check` runs this suite under `uv run --no-project`, so `just_makeit` is
not importable here — and that is the right constraint anyway: a gate that
imports the thing it audits can be fooled by the same bug twice. Reading the
source means the check sees exactly what a reader of the source sees.

The extraction's own blind spot, stated rather than discovered later: a flag
recognised by prefix matching (`arg.startswith("--")`) instead of an equality
test against a literal is invisible to it. No parser does that today, and
`test_the_scan_is_armed` is what stops the whole file from passing vacuously
if that changes and the literal set collapses to nothing.
"""

# `set[str]` in an annotation is evaluated at def-time and is 3.9-hostile;
# jm's floor interpreter is 3.9, so keep annotations as strings.
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
CLI_DIR = ROOT / "src" / "just_makeit"
DOCS = ROOT / "docs"

#: A flag as the parsers spell it: a whole string literal, nothing else inside
#: the quotes. That is what excludes a flag *named* inside an error message
#: ("error: --error requires an exception name"), which is not a parse site.
_FLAG_LITERAL = re.compile(r'"(--[a-z0-9][a-z0-9-]*)"')

#: Flags absent from `jm --help` on 2026-08-17, counted by this file's own
#: measurement. A RATCHET: lower it when you document one, never raise it.
#: The burn-down is gh-1015; see the module docstring for why it is tracked
#: there rather than fixed here.
HELP_GAP_RATCHET = 16


def _parsed_flags():
    """Every flag literal the CLI argument parsers compare against.

    Returns
    -------
    set of str
        Flag spellings including the leading ``--``, gathered from every
        ``_cli*.py`` module. Derived, never listed: adding a parser module or
        a flag to one needs no change here.
    """
    flags = set()
    for src in sorted(CLI_DIR.glob("_cli*.py")):
        flags |= set(_FLAG_LITERAL.findall(src.read_text(encoding="utf-8")))
    return flags


def _reference_docs_text():
    """Concatenated reference docs — every ``docs/**/*.md`` except examples.

    Returns
    -------
    str
        One blob, because the question is "is this flag described anywhere a
        reader would look", not "which page describes it".
    """
    pages = [
        p
        for p in sorted(DOCS.rglob("*.md"))
        if "examples" not in p.relative_to(DOCS).parts
    ]
    return "\n".join(p.read_text(encoding="utf-8") for p in pages)


def _help_text():
    """The text `jm --help` prints, read from its source.

    Returns
    -------
    str
        ``_cli.py`` in full. The help block is a module-level string literal
        there, so the file's own text is a faithful superset — a flag missing
        from this is missing from the help.
    """
    return (CLI_DIR / "_cli.py").read_text(encoding="utf-8")


def test_the_scan_is_armed():
    """The extraction finds a real flag set, so a pass means something.

    A scan that silently matches nothing passes every other test in this file
    for free — the failure mode that makes a gate a description of itself. Two
    independent anchors: a plausible count, and a specific long-lived flag.
    """
    flags = _parsed_flags()
    assert len(flags) > 80, f"flag extraction collapsed to {len(flags)}"
    assert "--module" in flags
    assert "--variable-output" in flags


def test_every_parsed_flag_is_documented():
    """No flag the CLI accepts may be absent from the reference docs.

    Zero-tolerance, because it reached zero: `--fn`, `--record-dtype`,
    `--status-return`, `--manual-stub`, `--capsule`, `--opaque-state`,
    `--out-size`, `--after`, `--strict-examples` and the `--error*` trio were
    all parsed-but-undocumented until 0.62.0's docs sweep, and `--fn` had by
    then become the discriminator deciding which kind of view override you get.
    """
    docs = _reference_docs_text()
    missing = sorted(f for f in _parsed_flags() if f not in docs)
    assert not missing, (
        "these flags are parsed by the CLI but appear in no reference doc "
        f"page: {', '.join(missing)}.\n"
        "Document each where its command is described (docs/commands/), or "
        "in docs/configuration.md if it is a manifest key's CLI form. A flag "
        "nobody can find is a flag that gets re-requested as a feature."
    )


def test_help_coverage_does_not_regress():
    """`jm --help` must not omit more flags than it already does.

    The ratchet is the whole mechanism: it lets a known backlog stay known
    without letting it grow. A new flag whose help line was forgotten pushes
    the count past `HELP_GAP_RATCHET` and fails here.
    """
    help_text = _help_text()
    missing = sorted(f for f in _parsed_flags() if f not in help_text)
    assert len(missing) <= HELP_GAP_RATCHET, (
        f"{len(missing)} flags are absent from `jm --help`, over the "
        f"{HELP_GAP_RATCHET} already known: {', '.join(missing)}.\n"
        "Add the flag to the help block in src/just_makeit/_cli.py."
    )
    if len(missing) < HELP_GAP_RATCHET:
        raise AssertionError(
            f"good news, and the ratchet has to follow: only {len(missing)} "
            f"flags are now missing from `jm --help`, not "
            f"{HELP_GAP_RATCHET}. Lower HELP_GAP_RATCHET in this file to "
            f"{len(missing)} so the improvement cannot be undone."
        )
