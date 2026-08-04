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
    c_format_command = ["uv", "run", "--group", "dev", "clang-format"]

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

    # --style=file honours the project's committed .clang-format; the fallback
    # covers projects that opted in without shipping one.
    proc = subprocess.run(
        [
            *command,
            "-i",
            "--style=file",
            "--fallback-style=LLVM",
            *(str(f) for f in files),
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

    if not quiet:
        n = len(files)
        print(f"  format  {n} C file{'s' if n != 1 else ''} (clang-format)")


def format_version(cfg: dict) -> str:
    """The formatter's own ``--version`` output, or ``""`` if unavailable.

    gh-745. The point of pinning the command is that two machines produce the
    same bytes, and the only way to *see* that is to ask the binary which
    version it is. Used by ``jm status`` to report the formatter alongside the
    drift result, so "stale on CI, clean locally" names its own cause instead
    of being rediscovered.

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
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""
