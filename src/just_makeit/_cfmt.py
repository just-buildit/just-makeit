"""
_cfmt.py — optional house-style pass over generated C (gh-265).

jm emits its own canonical 4-space C. Projects with a different committed style
(doppler, jm's poster-child, uses GNU 2-space) otherwise see spurious drift:
jm regenerates the ``*_ext.c`` binding in 4-space, the project's formatter
rewrites it to house style, and ``jm status --check`` then reports it stale
forever. Opting in with::

    [project]
    c_style = "clang-format"

makes jm reformat the binding to the project's ``.clang-format`` as it emits
it, so the regenerated file already matches what is committed and status stays
clean.

**Which binary does it is the project's to pin** (gh-745)::

    [project]
    c_format_command = ["uvx", "clang-format==22.1.8"]

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

Off by default (``c_style`` unset) → output is byte-identical to before, so
existing projects are unaffected.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import _config as C


def _generated_c_files(root: Path) -> list[Path]:
    """The C files jm regenerates wholesale — the only ones safe to reformat.

    Only the CPython binding ``*_ext.c`` under ``native/src/**`` is rewritten
    from the manifest on every mutating command, so it is the only C file
    whose *style* can drift from the committed (house-style) version and make
    ``jm status --check`` report it stale. Reformatting it is exactly what
    ``c_style`` is for, and it is safe: the file is overwritten from scratch
    each time, so clang-format has nothing to fight.

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
    return sorted(src.rglob("*_ext.c"))


# Upper bound on convergence passes (gh-758). Two is the observed worst case
# for clang-format 22 on jm's output; the extra headroom distinguishes "needs
# a few passes" from "oscillates forever", which is a formatter bug worth a
# warning rather than an infinite loop.
_MAX_PASSES = 5


def format_project(root: Path, cfg: dict, *, quiet: bool = False) -> None:
    """Reformat the project's generated C to its house style, if opted in.

    No-op unless ``[project] c_style == "clang-format"``. Runs
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
    if C.c_style(cfg) != "clang-format":
        return

    command = C.c_format_command(cfg)
    # Only argv[0] is resolved; the rest are that program's own arguments.
    # A command routed through a runner (`uv run … clang-format`) therefore
    # resolves `uv`, which is correct — `clang-format` may well not be on PATH
    # at all in exactly the setup this key exists to support.
    if shutil.which(command[0]) is None:
        print(
            'WARNING: [project] c_style = "clang-format" but the formatter '
            f"command {command[0]!r} was not found on PATH;\n  generated C "
            "keeps jm's default style. Install it, fix [project] "
            "c_format_command, or unset c_style.",
            file=sys.stderr,
        )
        return

    files = _generated_c_files(root)
    if not files:
        return

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

    if not quiet:
        n = len(files)
        print(f"  format  {n} C file{'s' if n != 1 else ''} (clang-format)")


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
    if C.c_style(cfg) != "clang-format":
        return ""
    command = C.c_format_command(cfg)
    if shutil.which(command[0]) is None:
        return ""
    try:
        proc = subprocess.run(
            [*command, "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=None if cwd is None else str(cwd),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


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

    Returns ``None`` when the command is stable across directories (or when
    the version cannot be read at all, which `format_version` already reports
    as ``""`` and is not this check's business to escalate).
    """
    here = format_version(cfg, cwd=root)
    if not here:
        return None
    with tempfile.TemporaryDirectory(prefix="jm-cfmt-") as tmp:
        there = format_version(cfg, cwd=Path(tmp))
    if not there or there == here:
        return None
    return here, there
