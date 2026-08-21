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

`test_every_parsed_flag_is_in_jm_help`
    `jm --help` is documentation too, and it is the copy a user reads first —
    at the terminal, without a browser. Also strict, as of gh-1015.

    It was not always. 17 parsed flags were absent from it at 0.62.0 while the
    reference docs stood at zero, so this started as a **ratchet** at 17 —
    the right shape for a real backlog, and the wrong shape for an invariant,
    since "no worse than yesterday" is precisely the reading that let the
    class recur. gh-496 was this same class on `jm app`, fixed as four
    hand-added flags in 0.30.2 with nothing left behind to check the parsers
    against the help block; thirteen more arrived the same way, silently, one
    feature at a time. gh-1015 burned the 17 down to zero, so there is no
    backlog left for a ratchet to hold and both checks now say the same thing.

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

#: gh-1015: the burn-down reached ZERO, so the ratchet is gone.
#:
#: It was 17, then 16 while gh-1074 documented `--count-default` on its way
#: past. A ratchet is the right shape for a backlog and the wrong shape for
#: an invariant — it says "no worse than yesterday", which is exactly the
#: reading that let this recur at four times its original size after gh-496
#: fixed the four instances and not the mechanism. There is nothing left to
#: burn down, so the check below is zero-tolerance, like its sibling over the
#: reference docs.


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


def test_every_parsed_flag_is_in_jm_help():
    """No flag the CLI accepts may be absent from `jm --help`.

    Zero-tolerance, because it reached zero (gh-1015). The reference docs had
    already been swept to zero; this is the copy a user reads **first**, at
    the terminal, without a browser — and it stood at 17 while the docs stood
    at 0, which is the gap this closes.

    gh-496 fixed four such flags by hand and left nothing checking the
    parsers against the help block, so the next thirteen arrived the same way:
    silently, one feature at a time. Derived from the parsers rather than
    listed, so a new flag is under this gate with nothing to register.
    """
    help_text = _help_text()
    missing = sorted(f for f in _parsed_flags() if f not in help_text)
    assert not missing, (
        f"these flags are parsed by the CLI but absent from `jm --help`: "
        f"{', '.join(missing)}.\n"
        "Add a line for each to the help block in src/just_makeit/_cli.py. "
        "The reference docs are not a substitute: `jm --help` is what a user "
        "reads at the terminal, and gh-1015 is what happens when only one of "
        "the two is kept honest."
    )
