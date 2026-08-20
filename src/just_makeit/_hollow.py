"""_hollow.py — targets that pass without covering anything (gh-806).

Two findings, one failure mode: **a build target that reports success while
covering nothing.** Both are silent by construction, which is why they are
expensive — a red target gets fixed the day it appears.

Orphans
-------
`jm apply` materialises ``native/tests/test_<comp>_core.c`` and
``native/benchmarks/bench_<comp>_core.c`` for every component in the manifest,
and re-renders the CMake that builds *those names*. Rename a component and the
old files stay on disk under the old name, referenced by nothing, while a fresh
scaffold takes over their target.

Measured on doppler, twice in the same component: a hand-written 5.5 KB
four-arm benchmark and a **500-line** test suite were both displaced by
scaffolds during a ``telemetry`` -> ``dp_tlm`` migration. The benchmark half at
least wrote an empty ``benchmarks[]`` array. The test half was worse, for the
one reason that decides everything here: **it passed.** A scaffold compiles,
runs, prints ``test_dp_tlm_core PASSED`` and is counted, so ``ctest`` reports
"100% tests passed" with the real suite missing from the denominator. That tree
was green for weeks.

The detected property is not "was renamed" — jm has no memory of the previous
name and does not need one. It is the thing that is actually wrong and is
directly checkable: **a C source sitting in the canonical test/bench directory
that no build file compiles.** That is strictly more general than rename
detection (it also catches a target deleted by hand, or a file added to the
directory and never wired) and it cannot go stale.

Silent benchmarks
-----------------
A ``no_step`` component's benchmark has no ``step()`` to time, and every one of
its methods may be a shape `_bench_method_block` skips — ``variable_output``,
``out_type``, ``varargs``, ``codec``. doppler's had **eight** methods and timed
none of them, so the target built, ran, and wrote an empty ``benchmarks[]``.
That is defensible behaviour and a terrible thing to discover from an empty
JSON file six months later, so jm says it once, at apply time, where it is
actionable.

Read from the emitted source, never predicted from the manifest — the
`_codecheck` precedent. A hand-edited benchmark that added its own timing loop
is not silent, and a manifest-side prediction would call it one.

What this does *not* fix
------------------------
The generated C is create-only, so the runtime half of gh-806 — a test that
prints how many checks it ran, a `jm_bench_write_json` that says when it wrote
nothing — reaches new components only. Existing trees are served by this scan,
which needs nothing but the files already on disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import _config as C

#: Where jm puts each kind of generated C target, and the filename shape it
#: uses. A file in one of these directories matching the pattern is jm-shaped
#: whether or not jm wrote it, which is exactly the population worth checking.
_KINDS = (
    (
        "test",
        "native/tests",
        "test_{}_core.c",
        re.compile(r"^test_(\w+)_core\.c$"),
    ),
    (
        "bench",
        "native/benchmarks",
        "bench_{}_core.c",
        re.compile(r"^bench_(\w+)_core\.c$"),
    ),
)

#: A **call** to `jm_bench_add`, at statement position. Anchored, and that is
#: not a nicety: gh-840 put a worked `jm_bench_add(...)` example into the
#: `TODO` comment of every benchmark jm could not populate — which is exactly
#: the population this detector exists to find. A plain `"jm_bench_add" in
#: body` therefore matched the instructions telling the author the file is
#: empty, and `SILENT` stopped firing altogether. The comment lines begin
#: ` *`, so requiring the call to open its own line separates them.
_BENCH_ADD_CALL = re.compile(r"^\s*jm_bench_add\s*\(", re.M)

#: A build file that enumerates sources by wildcard tells us nothing about
#: which ones it picked up, so the scan stands down rather than guessing.
#: False positives here send an author to delete a file that *is* built.
_GLOB_HINT = re.compile(r"file\s*\(\s*glob", re.I)


@dataclass(frozen=True)
class Orphan:
    """A generated-shape C source that no build file compiles."""

    rel: str  #: POSIX path relative to the project root
    kind: str  #: "test" or "bench"
    stem: str  #: the ``<X>`` in ``test_<X>_core.c``
    lines: int  #: how much content is going unbuilt
    declared: bool  #: whether ``<X>`` is a component in the manifest

    def describe(self) -> str:
        """One warning line, plus the reason it is worth reading."""
        what = (
            "CTest never runs it" if self.kind == "test" else "it never runs"
        )
        why = (
            f"'{self.stem}' is not a component in {C.FILENAME}, which is\n"
            "  the shape a component rename leaves behind."
            if not self.declared
            else f"'{self.stem}' is a declared component, so its\n"
            "  target should exist — the wiring was removed or hand-edited."
        )
        return (
            f"{self.rel}\n"
            f"  is compiled by no build file ({self.lines} lines), so"
            f" {what}.\n"
            f"  {why}\n"
            "  Wire it up, rename it onto the component it belongs to, or"
            " delete it."
        )


@dataclass(frozen=True)
class SilentBench:
    """A benchmark source that records no measurement at all."""

    rel: str  #: POSIX path relative to the project root
    component: str
    methods: int  #: declared methods, none of which produced a timing block

    def describe(self) -> str:
        """One warning line naming why the JSON will come out empty."""
        detail = (
            f"no step(), and none of its {self.methods} method(s)\n"
            "  has a benchable shape"
            if self.methods
            else "no step() and no methods"
        )
        return (
            f"{self.rel}\n"
            f"  has {detail}, so it measures nothing and writes an empty\n"
            '  "benchmarks": [] array. It still builds, runs and exits 0.'
        )


def _build_texts(root: Path) -> list[str] | None:
    """Every file that can name a C target, read once.

    Returns ``None`` when one of them enumerates sources by wildcard, which
    makes "is this file compiled?" unanswerable by reading — see `_GLOB_HINT`.
    """
    texts: list[str] = []
    for cml in sorted(root.rglob("CMakeLists.txt")):
        if "build" in cml.relative_to(root).parts:
            continue
        try:
            texts.append(cml.read_text(encoding="utf-8"))
        except OSError:
            continue
    # `build = "make"` projects name their C tests in the root Makefile's
    # C_TESTS list instead, so the same question has a second place to look.
    for extra in ("Makefile", "local.mk"):
        path = root / extra
        if path.is_file():
            try:
                texts.append(path.read_text(encoding="utf-8"))
            except OSError:
                pass
    if any(_GLOB_HINT.search(t) for t in texts):
        return None
    return texts


def _is_built(stem: str, texts: list[str]) -> bool:
    r"""Does any build file name this target?

    Word-anchored, and that matters more than it looks. `orphans` used a bare
    ``stem in text``, which is lenient in the direction that is safe FOR IT —
    a false "built" is a missed finding, and a false "orphan" sends someone to
    delete a file that is compiled. gh-1023 gave the same question a second
    consumer with the opposite asymmetry: a false "built" makes `jm bench`
    build a target that does not exist, which is fatal to the whole run.

    ``bench_util_core`` is a substring of ``bench_util_core_simd``, so the
    bare test conflated them. ``_`` is a word character, so ``\b`` separates
    the two correctly while still matching the target wherever a build file
    names it — a CMake source path, a ``C_BENCHES`` list entry, either.

    One helper for both readers, so "jm knows this target exists" cannot come
    to mean two things.
    """
    pattern = re.compile(rf"\b{re.escape(stem)}\b")
    return any(pattern.search(t) for t in texts)


def built_stems(root: Path, kind: str) -> set[str] | None:
    """The ``<X>`` of every ``<kind>_<X>_core.c`` that a build file compiles.

    The complement of :func:`orphans`, sharing its scanner, its ``_KINDS``
    shapes and its stem-substring reference test — so "jm knows this target
    exists" has exactly one definition and the pair cannot come to disagree
    about what is built.

    gh-1023 is why the complement is wanted. ``jm bench`` enumerated
    ``C.components(cfg)``, which is the manifest's top-level tables — the
    objects — and used it as both the run set AND the validator's whitelist.
    A benchmark for anything else (a ``[project] c_deps`` directory carrying
    its own hand-written ``CMakeLists.txt``) could therefore be written,
    reviewed, compiled by every build, and executed by nothing, while
    ``jm bench <that name>`` answered ``unknown component``. Auditing doppler
    turned up four in exactly that state.

    Discovery by scan rather than by a manifest key is deliberate: a benchmark
    is already not manifest-owned (``jm status --check`` does not track one),
    the Python half of ``jm bench`` already discovers by collection rather
    than by declaration, and a list you must remember to append to fails the
    same silent way the bug does.

    Returns
    -------
    set of str or None
        ``None`` when a build file enumerates sources by wildcard, which makes
        "is this compiled?" unanswerable by reading (see `_GLOB_HINT`). That
        is a distinct answer from the empty set: the caller falls back to the
        manifest and should SAY it did, rather than quietly running fewer
        benchmarks than the tree holds.
    """
    texts = _build_texts(root)
    if texts is None:
        return None
    for k, subdir, _fmt, pattern in _KINDS:
        if k != kind:
            continue
        directory = root / subdir
        if not directory.is_dir():
            return set()
        return {
            m.group(1)
            for src in sorted(directory.glob("*.c"))
            if (m := pattern.match(src.name))
            and _is_built(src.name[: -len(".c")], texts)
        }
    raise ValueError(f"unknown kind {kind!r}")


def orphans(root: Path, cfg: dict) -> list[Orphan]:
    """Every ``test_*_core.c`` / ``bench_*_core.c`` that nothing compiles.

    The reference is looked up by **target stem** (``test_fir_core``) rather
    than by filename, because the two build systems spell it differently: CMake
    names the source path, the generated Makefile names the executable in
    ``C_TESTS``. The stem is the substring both contain.
    """
    texts = _build_texts(root)
    if texts is None:
        return []
    # A kind is only worth reporting if the tree builds *any* target of that
    # kind. gh-832 arrived as a backend special case — the make backend had
    # never emitted a bench rule, so every one of its bench sources was
    # unbuilt by construction and gating on them would fail the gate for
    # something no `jm apply` could clear (the gh-767 rule). Keying on the
    # capability instead of on `build_system` is strictly better: it covers a
    # cmake project that stripped its bench targets by hand, and it
    # **self-clears** — a make project that regenerates its Makefile with a
    # `C_BENCHES` list is under the gate from that moment, with nothing here
    # to update.
    #
    # The cost, stated because it is a real hole: a project with exactly one
    # component whose only bench went orphaned looks identical to a project
    # that builds no benchmarks. Accepted — a false positive here sends
    # someone to delete a file that *is* built, which is worse than a missed
    # finding in the narrowest possible tree.
    kinds = [
        k
        for k in _KINDS
        if any(re.search(rf"\b{k[0]}_\w+_core\b", t) for t in texts)
    ]
    declared = set(C.components(cfg))

    found: list[Orphan] = []
    for kind, subdir, _fmt, pattern in kinds:
        directory = root / subdir
        if not directory.is_dir():
            continue
        for src in sorted(directory.glob("*.c")):
            m = pattern.match(src.name)
            if not m:
                continue
            stem = src.name[: -len(".c")]
            if _is_built(stem, texts):
                continue
            try:
                body = src.read_text(encoding="utf-8")
            except OSError:
                continue
            found.append(
                Orphan(
                    rel=src.relative_to(root).as_posix(),
                    kind=kind,
                    stem=m.group(1),
                    lines=len(body.splitlines()),
                    declared=m.group(1) in declared,
                )
            )
    return found


def silent_benches(root: Path, cfg: dict) -> list[SilentBench]:
    """Every component benchmark whose source records no measurement.

    ``jm_bench_add`` is the only way a timing reaches the JSON, so its absence
    from the source *is* the emptiness — no need to run the binary, and no way
    for the answer to disagree with what the target will do.
    """
    out: list[SilentBench] = []
    # gh-836: `C.components` is ALREADY every component, module objects
    # included — a module object keeps its own top-level `[<obj>]` section and
    # `components` returns every top-level key that is not reserved. Unioning
    # `module_objects` on top of it therefore visited each module object twice
    # and reported its benchmark twice, so the count came out at exactly 2x
    # the files (doppler: 31 files, `SILENT (62)`). The two orderings doppler
    # saw in the listing are the two sources: `components` follows manifest
    # key order, `module_objects` follows the `objects = [...]` array.
    for comp in C.components(cfg):
        src = root / "native" / "benchmarks" / f"bench_{comp}_core.c"
        if not src.is_file():
            continue
        try:
            body = src.read_text(encoding="utf-8")
        except OSError:
            continue
        if _BENCH_ADD_CALL.search(body):
            continue
        out.append(
            SilentBench(
                rel=src.relative_to(root).as_posix(),
                component=comp,
                methods=len(C.methods(cfg, comp)),
            )
        )
    return out


def report(root: Path, cfg: dict, *, stream=None, indent: str = "  ") -> None:
    """Print both findings through `_report`, weighted.

    An orphan **gates**: `jm status --check` counts it, because "a real test
    suite is on disk and nothing runs it" is not a matter of taste, and the
    only reason it survived weeks on doppler is that nothing said it. A silent
    benchmark is advisory — measuring nothing is a legitimate state for a
    ``no_step`` component, and the bug gh-806 reports is the silence, which
    this line ends.
    """
    from . import _report

    # The one matcher every other check already honours -- imported here
    # rather than reimplemented, so a `status_allow` glob means the same
    # thing for an orphan as it does for a stale header. Lazy because
    # `_status` reaches back into this module for its own section.
    from ._status import _is_allowed

    allow = C.status_allow(cfg)
    for orphan in orphans(root, cfg):
        _report.warn(
            orphan.describe(),
            gates=not _is_allowed(orphan.rel, allow),
            stream=stream,
            indent=indent,
        )
    for silent in silent_benches(root, cfg):
        _report.warn(silent.describe(), stream=stream, indent=indent)
