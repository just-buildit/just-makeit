"""Which create-only files can be *behind*, and which merely differ (gh-949).

A **create-only** file is one `apply` writes when it is absent and never
rewrites. `status` copies the tree, replays the manifest over the copy and
diffs, so a create-only file is byte-identical on both sides *because neither
run touched it* — the diff is empty by construction rather than by the file
being current. Every such file is invisible to drift detection.

The set is far larger than the guarded ``if not path.exists()`` write sites
suggest. Measured on a plain cmake project with one object, `status` is blind
to 28 of its 32 manifest-owned files and sees drift in **four**: the umbrella
header, the component `CMakeLists.txt`, `<comp>_ext.c` and `<comp>.pyi`.
Create-only is a property of *`apply`'s reconciliation*, not of the syntax of
the write site — the eight guarded write sites account for well under half of
it, and the rest are files `jm new` writes once and nothing revisits. That is
why nothing here is derived from the write sites, and why
:mod:`tests.test_gh949_outdated` derives the set by corrupting each file and
asking `status` whether it noticed.

Reporting *every* create-only file whose bytes differ from jm's current render
would be worse than reporting none: `<comp>_core.c` holds the author's
algorithm and differs from its scaffold the moment the project is real, so a
whole-set diff marks every project outdated forever. The classification below
splits them on the one question that has a stable answer:

    Is jm's current render this file's **content**, or its **starting**
    content?

A file whose render is its content can be behind — jm would be right to offer
today's render as a wholesale replacement, and a difference means the project
is on an older one. That is :data:`JM`. A file whose render is starting content
cannot be behind: the difference *is* the author's work, and there is no
version for it to be behind. That is :data:`AUTHOR`.

Note this is deliberately not "who edits it". Authors edit their Makefile, and
`jm_test.h` is create-only precisely so a project may extend it; asked that way
the question has no stable answer and the boundary moves per reader.

The classification is a declared judgement — it is about intent, so it must be
— but it is not a list anyone maintains. Three derived checks hold it, all in
:mod:`tests.test_gh949_outdated`:

- the measured create-only set must be *exactly* the paths classified anything
  other than :data:`RECONCILED`. Equality both ways, so it fails on a new
  unclassified file **and** on a file quietly ceasing to be reconciled — which
  is how coverage disappears without anyone noticing;
- a pristine project must report nothing outdated, so a wrong :data:`JM` turns
  a brand-new project red in `make test` rather than turning every user's
  `status` into noise;
- no rule may match nothing, so the registry cannot keep entries for paths jm
  stopped emitting and read as broader coverage than it has.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import NamedTuple


# The four states a manifest-owned file can be in. Four rather than a bool
# because the measurement distinguishes four, and collapsing any pair hides a
# real difference: `partial` and `reconciled` are both "apply writes here" but
# only one is covered by the copy/diff, and `jm` and `author` are both
# create-only but only one can be behind.
JM = "jm"
"""Create-only, and jm's content. A difference means the project is behind."""

AUTHOR = "author"
"""Create-only, and the author's content. jm renders its *starting* content,
so a difference is the author's work and there is no version to be behind."""

PARTIAL = "partial"
"""Create-only as a whole file, but `apply` splices managed blocks into it.
A whole-file diff would report the author's own additions as jm being behind,
on every run. Per-block coverage is the right shape and is filed as gh-959."""

RECONCILED = "reconciled"
"""Not create-only: `apply` rewrites it, so `status`'s copy/diff already sees
drift in it. Present so the derivation gate can assert this file is *absent*
from the create-only set, which is what proves the set was measured."""

KINDS = (JM, AUTHOR, PARTIAL, RECONCILED)


class Rule(NamedTuple):
    """One classification, matched against a project-relative POSIX path.

    ``pattern`` is an :mod:`fnmatch` glob whose ``*`` crosses ``/`` — so
    ``src/*/tests/*.py`` covers a dotted module's nested
    ``src/pkg/dsp/filters/tests/`` as well as the flat case, which a
    ``PurePath.match``-style glob would miss.

    ``why`` is printed nowhere; it is the record of the judgement, which is the
    part that goes stale silently when it lives only in a reviewer's head.
    """

    pattern: str
    kind: str
    why: str

    @property
    def versioned(self) -> bool:
        """True when a difference from jm's render means *outdated*."""
        return self.kind == JM


# First match wins, so the specific entries precede the general ones.
RULES: tuple[Rule, ...] = (
    # ── jm's content: a difference means the project is on an older jm ──
    Rule(
        "Makefile",
        JM,
        "jm defines the target set; v0.58 added compile-commands and tidy to"
        " it, which is the migration that motivated gh-949.",
    ),
    Rule(
        ".clang-tidy",
        JM,
        "jm's check selection. gh-941 was this file shipped dead; a project"
        " carrying the dead one wants to hear about it.",
    ),
    Rule(
        ".clang-format",
        JM,
        "jm's house style for generated C. Never fires today — see the"
        " carve-out in `outdated`, and gh-960.",
    ),
    Rule(".gitignore", JM, "jm's ignore set, tracking what jm builds."),
    Rule("Doxyfile", JM, "jm's doxygen configuration."),
    Rule("zensical.toml", JM, "jm's docs-site configuration."),
    Rule("bootstrap.toml", JM, "jm's CI bootstrap declaration."),
    Rule(
        "cmake/*",
        JM,
        "packaging plumbing (`.pc.in`, `-config.cmake.in`) rendered from the"
        " manifest; nothing here is authored.",
    ),
    Rule("native/inc/jm_perf.h", JM, "the JM_DEFINE_STEPS macro is jm's."),
    Rule("native/inc/jm_simd.h", JM, "jm's SIMD helpers."),
    Rule("native/inc/clib_common.h", JM, "jm's shared C preamble."),
    Rule("native/inc/pyex_common.h", JM, "jm's CPython glue preamble."),
    Rule(
        "native/tests/jm_test.h",
        JM,
        "jm's assertion harness (gh-934). Create-only so a project may extend"
        " it — which is the reason to *report* rather than rewrite, not a"
        " reason to treat the harness as the author's.",
    ),
    Rule("native/benchmarks/jm_bench.h", JM, "jm's timing harness."),
    # ── `apply` writes here, but only into managed blocks ────────────────
    Rule(
        "CMakeLists.txt",
        PARTIAL,
        "`apply` splices the external-deps and components blocks into the real"
        " tree and rewrites nothing else, so a whole-file diff would report"
        " the author's own targets as jm being behind, forever. gh-959.",
    ),
    # ── the author's content: there is no version to be behind ───────────
    Rule(
        "pyproject.toml",
        AUTHOR,
        "the author's dependencies, metadata and tool configuration.",
    ),
    Rule("README.md", AUTHOR, "the author's prose."),
    Rule("docs/*", AUTHOR, "the author's prose."),
    Rule(
        "native/inc/*/*_core.h",
        AUTHOR,
        "sacred: the author's struct and inline step() body.",
    ),
    Rule("native/src/*/*_core.c", AUTHOR, "sacred: the author's algorithm."),
    Rule("native/src/*_lib.c", AUTHOR, "a stub the author fills in."),
    Rule(
        "native/tests/test_*_core.c",
        AUTHOR,
        "the author's C tests; the scaffold is a seed (gh-806 counts its"
        " checks precisely because it is expected to grow).",
    ),
    Rule("native/benchmarks/bench_*_core.c", AUTHOR, "the author's C bench."),
    Rule(
        "src/*/tests/*.py",
        AUTHOR,
        "the author's Python tests, plus the empty package __init__.",
    ),
    Rule("src/*/benchmarks/*.py", AUTHOR, "the author's Python benchmarks."),
    Rule(
        "benchmarks/history/*",
        AUTHOR,
        "a placeholder and the project's own dated snapshots.",
    ),
    # BELOW the two rules above, deliberately: fnmatch's `*` crosses `/`, so
    # this also matches `src/pkg/tests/__init__.py`, and first-match-wins is
    # what keeps those empty package markers with the author's files.
    Rule(
        "src/*/__init__.py",
        PARTIAL,
        "`apply` splices each component's import line in and leaves the rest,"
        " including whatever the package re-exports, alone. gh-959.",
    ),
    Rule(
        "native/src/*/*_ext_*.c",
        PARTIAL,
        "a module's per-object binding fragment — sacred, but `_docsync`"
        " refreshes its runtime __doc__ on every apply. gh-959.",
    ),
    Rule(
        "objects/*.toml",
        AUTHOR,
        "a split-layout manifest fragment: jm's INPUT, not its output. Nothing"
        " here has a jm version to be behind.",
    ),
    Rule("modules/*.toml", AUTHOR, "as objects/*.toml above."),
    # ── not create-only: `status` already covers these ───────────────────
    # Declared rather than omitted so the derivation gate can assert they are
    # ABSENT from the measured create-only set. An omitted entry proves
    # nothing; this one fails the moment `apply` stops rewriting one of them,
    # which is exactly how a file becomes invisible without anyone noticing.
    Rule(
        "native/inc/*.h",
        RECONCILED,
        "the umbrella header — `apply` refreshes its include list. Below the"
        " jm_*.h / common-header rules above, which are more specific.",
    ),
    Rule(
        "native/src/*/CMakeLists.txt",
        RECONCILED,
        "regenerated per component from the manifest.",
    ),
    Rule("native/src/*/*_ext.c", RECONCILED, "generated CPython glue."),
    Rule("src/*/*.pyi", RECONCILED, "generated type stubs."),
)


def classify(rel_posix: str) -> Rule | None:
    """Return the first rule matching *rel_posix*, or None if unclassified.

    An unclassified path is never reported outdated — the safe direction at
    runtime, since the cost of a false OUTDATED is a user chasing a difference
    that is their own work. It is a hard failure in the test suite instead,
    where the cost of missing one is a line nobody reads.
    """
    for rule in RULES:
        if fnmatch.fnmatchcase(rel_posix, rule.pattern):
            return rule
    return None


def is_versioned(rel_posix: str) -> bool:
    """True when a difference from jm's current render means *outdated*."""
    rule = classify(rel_posix)
    return rule is not None and rule.versioned


def outdated(root: Path, replay_root: Path) -> list[str]:
    """Versioned files in *root* whose bytes differ from *replay_root*.

    *replay_root* is the tree `apply` builds by replaying the manifest from
    scratch — jm's current render of the whole project, already put through the
    project's own C and Python formatting passes so both sides are comparable.
    It is used rather than re-rendering the templates here because a second
    template-to-path map beside the one `apply` already builds is a peer
    implementation, and peer implementations in this codebase have drifted
    every time.

    Returned paths are project-relative POSIX strings, sorted.

    **Carve-out.** `.clang-format` can never be reported, and the reason is
    structural rather than an omission here: `_apply._replay` calls `_new.run`
    without the project's ``c_style``, so the replay grows no `.clang-format`
    of its own, and `apply` then *copies the real one in* before the formatting
    passes so both trees format alike. The replay's copy is therefore the
    project's file by construction and always compares equal. It stays
    classified ``JM`` because it is jm's content and the day the replay carries
    its own render this starts working; it is tracked as a gap rather than
    papered over with a render-the-template fallback, which is the peer
    implementation this function exists to avoid. Tracked as gh-960.
    """
    from . import _apply

    found: list[str] = []
    for src in sorted(replay_root.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(replay_root)
        if _apply.is_skipped(rel):
            continue
        rel_posix = rel.as_posix()
        if not is_versioned(rel_posix):
            continue
        dst = root / rel
        # A file absent from the real tree is MISSING, which `apply` fixes and
        # `status` already reports. Outdated is only about a file that is
        # there and behind.
        if not dst.is_file():
            continue
        if dst.read_bytes() != src.read_bytes():
            found.append(rel_posix)
    return sorted(found)
