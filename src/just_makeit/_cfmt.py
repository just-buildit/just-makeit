"""
_cfmt.py — optional house-style pass over generated C (gh-265).

jm emits its own canonical 4-space C. Projects with a different committed style
(doppler, jm's poster-child, uses GNU 2-space) otherwise see spurious drift:
jm regenerates the ``*_ext.c`` binding in 4-space, the project's formatter
rewrites it to house style, and ``jm status --check`` then reports it stale
forever. Opting in::

    [project]
    c_format_command = ["uvx", "clang-format==22.1.8"]

makes jm reformat the binding to the project's ``.clang-format`` as it emits
it, so the regenerated file already matches what is committed and status stays
clean.

**Declaring the command is the opt-in** (gh-773), the same way ``_pyfmt``
reads ``py_format_command`` and has no ``py_style`` beside it. Naming the
binary is not a second, optional decision — it is what makes the output
reproducible across machines (gh-745), and a project that has not named one is
relying on whatever ``PATH`` happens to hold.

``c_style = "clang-format"`` still works and means exactly "format, using
PATH's ``clang-format``". It is the original spelling (gh-265) and predates
the command key; it carries no information the command does not, and the only
state the split ever produced on its own was a silent no-op — command set
correctly, ``c_style`` unset, nothing formatted, no warning, because the
missing-binary check only fires the other way round (doppler#616).

**It must resolve the same binary from any directory** (gh-758): jm formats
its temp scaffold from *outside* the project, so a CWD-dependent command
formats the two compared sides with two different formatters and the drift
gate can never go green. ``["uv", "run", "--group", "dev", "clang-format"]``
is the trap — outside a project ``uv`` warns ``--group dev has no effect``
and silently falls back to ``PATH``. ``jm status`` detects this directly (see
`cwd_dependent_version`) rather than leaving it to be rediscovered.

Default ``["clang-format"]`` — a bare PATH lookup, which is what jm did
unconditionally before. That default is fine on one machine and wrong across
two: doppler resolves 21.1.8 locally and 22.1.8 in CI, so the same input
produces different bytes and ``jm status --check`` flips red on a project that
has not changed. Formatting reproducibly requires naming the formatter, and
only the project knows how it pins one.

Scope: only the wholesale-regenerated ``*_ext.c`` glue is reformatted. Sacred
sources (``*_core.c`` and the splice-patched ``native/inc/**`` headers) are
left to the project's own formatter — reformatting them broke ``jm apply``
convergence (gh-493). See `_generated_c_files`.

Off by default (neither key declared) → output is byte-identical to before, so
existing projects are unaffected.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import _config as C
from . import _fmtprobe


def _generated_c_files(root: Path) -> list[Path]:
    """The C files jm regenerates wholesale — the only ones safe to reformat.

    The CPython binding under ``native/src/**`` — both the aggregator
    ``<module>_ext.c`` and the per-object fragments ``<module>_ext_<obj>.c``
    that gh-729 split out of it. These are the C files jm writes from the
    manifest, so they are the ones whose *style* can drift from the committed
    (house-style) version and make ``jm status --check`` report them stale.

    **The fragments were missed until gh-917, because the glob said
    ``*_ext.c`` and a fragment is named ``ddc_ext_ddcr.c``.** The split moved
    the file and left this rule behind. The visible symptom was a fragment
    carrying *two* C styles at once: `_docsync` rewrites individual members in
    place from a fresh render (jm's K&R), the project's own formatter had made
    the rest GNU, and nothing here ever reached the file to reconcile them.

    The safety argument differs between the two, and it is worth stating
    because it is not the same sentence:

    - an aggregator is overwritten from scratch each time, so clang-format has
      nothing to fight;
    - a fragment is reconciled member-by-member and *does* hold author-written
      wrapper bodies. Formatting it is still right — a project that sets
      ``c_style`` is asking for its own style over jm's glue, and its own
      formatter already covers these files, which is precisely why the
      spliced member stood out. What is excluded is the hand-written
      ``*_ext_extra.c``, which jm never writes and therefore does not format.

    Everything else under ``native/`` is skipped, deliberately (gh-493):

    - ``*_core.c`` is create-once and holds the user's algorithm — reformatting
      it on every command would churn hand-written code.
    - ``native/inc/**`` headers (each ``*_core.h`` and the ``<pkg>.h``
      umbrella) are *splice-patched*: jm injects declarations into them, and
      that detection is whitespace-sensitive. Reformatting a header makes a
      later ``jm apply`` believe a declaration moved and re-patch it, so
      ``apply`` never converges and ``status --check`` flaps on an unchanged
      manifest. A house-style project (doppler, this feature's motivating use
      case) already excludes ``native/inc/**`` from its own clang-format for
      exactly this reason; its own formatter owns the sacred sources.

    Vendored third-party C under ``[project] c_deps`` lives outside
    ``native/src`` and is never reformatted either.

    Sorted for a stable invocation order (and stable test assertions).
    """
    src = root / "native" / "src"
    if not src.is_dir():
        return []
    return sorted(
        p
        for p in src.rglob("*_ext*.c")
        # `<comp>_ext_extra.c` / `<module>_ext_<obj>_extra.c` — hand-written,
        # #included by the glue, and never touched by jm. The glob above
        # reaches it (`*_ext` then `ra.c`), so it is excluded by name rather
        # than by hoping the pattern misses it.
        if not p.name.endswith("_extra.c")
    )


# Upper bound on convergence passes (gh-758). Two is the observed worst case
# for clang-format 22 on jm's output; the extra headroom distinguishes "needs
# a few passes" from "oscillates forever", which is a formatter bug worth a
# warning rather than an infinite loop.
_MAX_PASSES = 5


def format_project(root: Path, cfg: dict, *, quiet: bool = False) -> None:
    """Reformat the project's generated C to its house style, if opted in.

    No-op unless the project opts in — see :func:`_config.c_formatting_on`,
    for which declaring ``[project] c_format_command`` is enough. Runs
    ``clang-format -i --style=file`` over the wholesale-regenerated ``*_ext.c``
    glue (see `_generated_c_files`), so the committed ``.clang-format`` (or
    ``--fallback-style`` when the project ships none) decides the layout.
    Idempotent: already-conformant files are left byte-identical.

    **Which** binary runs is ``[project] c_format_command`` (gh-745), default
    ``["clang-format"]``. That indirection is what makes the output
    reproducible: resolved on PATH, the version differs per machine, and a
    ``c_style`` project's drift gate then flips red between local and CI on
    identical input.

    A missing binary is a soft failure — a one-line warning to stderr, and the
    command that triggered this still succeeds; jm's own output is valid C
    either way, it just keeps its native indentation.

    gh-758: the formatter is re-run until the bytes stop changing, because
    ``clang-format`` is **not** idempotent on every construct jm emits. A
    ``.m_doc`` string long enough to be split inside the aligned
    ``PyModuleDef`` initializer is the known case: pass 1 breaks the literal,
    which drops ``.m_doc`` out of the ``AlignConsecutiveAssignments`` group,
    which re-indents the continuation by one column on pass 2. That mattered
    because jm's paths do not agree on how many passes they run — ``apply``
    formats the temp scaffold and the CLI post-command hook formats the real
    tree (two), while ``jm status``'s replay calls `_apply.run` directly and
    gets one. One pass versus two left every affected file permanently STALE,
    unclearable by any number of ``apply`` runs. Converging here makes the
    output canonical, so pass count stops being something a caller can get
    wrong.
    """
    if not C.c_formatting_on(cfg):
        return

    command = C.c_format_command(cfg)
    # Only argv[0] is resolved; the rest are that program's own arguments.
    # A command routed through a runner (`uv run … clang-format`) therefore
    # resolves `uv`, which is correct — `clang-format` may well not be on PATH
    # at all in exactly the setup this key exists to support.
    if shutil.which(command[0]) is None:
        print(
            "WARNING: this project formats its generated C, but the "
            f"formatter command {command[0]!r} was not\n  found on PATH; "
            "generated C keeps jm's default style. Install it, fix "
            "[project]\n  c_format_command, or unset both it and c_style.",
            file=sys.stderr,
        )
        return

    files = _generated_c_files(root)
    if not files:
        return
    _run_formatter(command, files)

    if not quiet:
        n = len(files)
        print(f"  format  {n} C file{'s' if n != 1 else ''} (clang-format)")


def format_files(
    root: Path, cfg: dict, paths: "list[Path]", *, quiet: bool = True
) -> None:
    """Reformat just *paths*, keeping to the generated-C set.

    gh-917. `format_project` runs on the throwaway scaffold `apply` builds and
    compares against (gh-493); the files `apply` then rewrites **in the real
    tree** — the member-level reconciliation in `_docsync`, which splices a
    freshly rendered member into a fragment the project already formatted —
    are written after that and were never formatted at all. One fragment ended
    up carrying two C styles: jm's for the members it had just rewritten, the
    project's for everything around them.

    Scoped to what `apply` actually touched rather than reformatting the whole
    tree. A c_style project whose committed glue has drifted should not
    discover it as a hundred-file diff attached to an unrelated command; that
    is `format_project`'s job, and the CLI post-command hook already calls it.

    *paths* may contain anything `apply` wrote — non-C files and files outside
    the generated set are filtered out here rather than at each call site.
    """
    if not C.c_formatting_on(cfg):
        return
    command = C.c_format_command(cfg)
    if shutil.which(command[0]) is None:
        # Silent here, unlike `format_project`: this runs inside `apply`,
        # which has already warned through that path in the same run.
        return
    wanted = set(_generated_c_files(root))
    files = sorted(
        {(root / p if not p.is_absolute() else p).resolve() for p in paths}
        & {f.resolve() for f in wanted}
    )
    if not files:
        return
    _run_formatter(command, files)
    if not quiet:
        n = len(files)
        print(f"  format  {n} reconciled C file{'s' if n != 1 else ''}")


def _run_formatter(command: "list[str]", files: "list[Path]") -> None:
    """Run *command* over *files* until the bytes stop changing."""

    # Each pass re-runs only the files the previous one changed, so reaching
    # the fixed point costs one extra invocation over the handful that are
    # actually unstable rather than a second sweep of the whole tree.
    pending = list(files)
    for _ in range(_MAX_PASSES):
        before = {f: f.read_bytes() for f in pending}
        # --style=file honours the project's committed .clang-format; the
        # fallback covers projects that opted in without shipping one.
        proc = subprocess.run(
            [
                *command,
                "-i",
                "--style=file",
                "--fallback-style=LLVM",
                *(str(f) for f in pending),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            print(
                f"WARNING: {' '.join(command)} failed; generated C left "
                f"unformatted.\n  {proc.stderr.strip()}",
                file=sys.stderr,
            )
            return
        pending = [f for f in pending if f.read_bytes() != before[f]]
        if not pending:
            break
    else:
        # A formatter that oscillates instead of converging cannot be made
        # canonical here. Say so loudly and name the files: silently stopping
        # mid-cycle is what a drift gate then reports as unexplained STALE.
        names = ", ".join(sorted(f.name for f in pending))
        print(
            f"WARNING: {' '.join(command)} did not converge after "
            f"{_MAX_PASSES} passes; these files still change on every run "
            f"and will read as stale to `jm status`:\n  {names}",
            file=sys.stderr,
        )


def format_version(cfg: dict, cwd: "Path | None" = None) -> str:
    """The formatter's own ``--version`` output, or ``""`` if unavailable.

    gh-745. The point of pinning the command is that two machines produce the
    same bytes, and the only way to *see* that is to ask the binary which
    version it is. Used by ``jm status`` to report the formatter alongside the
    drift result, so "stale on CI, clean locally" names its own cause instead
    of being rediscovered.

    *cwd* (gh-758) asks the question from a specific directory, which is how
    `cwd_dependent_version` detects a command that resolves differently
    depending on where it runs.

    Never raises: a missing or failing binary reports ``""`` exactly as it
    does for formatting itself.
    """
    if not C.c_formatting_on(cfg):
        return ""
    return _fmtprobe.command_version(C.c_format_command(cfg), cwd=cwd)


def cwd_dependent_version(root: Path, cfg: dict) -> "tuple[str, str] | None":
    """``(from_project, from_elsewhere)`` if the command is CWD-dependent.

    gh-758. ``c_format_command`` is run from more than one directory —
    `_apply.run` formats a **temp scaffold** outside the project, the CLI hook
    formats the real tree from the project root. A command that resolves a
    different binary depending on where it runs therefore formats the two
    sides with two formatters, and the drift gate goes red on input nobody
    touched.

    ``["uv", "run", "--group", "dev", "clang-format"]`` — the natural way to
    pin a version, and what this feature's own docs suggested — is exactly
    that trap: outside a project ``uv`` prints ``--group dev has no effect
    when used outside of a project`` and silently falls through to whatever
    is on ``PATH``. doppler lost a day to it (21.1.8 on the scaffold, 22.1.8
    on the real tree) before the cause was named.

    Returns ``None`` when the command is stable across directories, or when
    the project itself cannot answer — a formatter that is simply absent is a
    genuine unknown, which `format_version` already reports as ``""``.

    gh-772: the question is not C-specific and now has one implementation in
    `_fmtprobe`, shared with ``py_format_command``. This wrapper keeps the
    ``(here, there)`` shape its callers and tests use, and is the
    **version**-disagreement half only — a command that works here and cannot
    spawn elsewhere is `_fmtprobe`'s ``"spawn"`` kind, which this check used
    to return ``None`` for and which was exactly doppler's Python case.
    """
    if not C.c_formatting_on(cfg):
        return None
    dep = _fmtprobe.cwd_dependence(root, C.c_format_command(cfg))
    if dep is None or dep.kind != "version":
        return None
    return dep.from_project, dep.from_elsewhere
