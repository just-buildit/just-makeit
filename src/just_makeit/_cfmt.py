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

    A missing ``clang-format`` binary is a soft failure — a one-line warning to
    stderr, and the command that triggered this still succeeds; jm's own output
    is valid C either way, it just keeps its native indentation.
    """
    if C.c_style(cfg) != "clang-format":
        return

    binary = shutil.which("clang-format")
    if binary is None:
        print(
            'WARNING: [project] c_style = "clang-format" but clang-format was '
            "not found on PATH;\n  generated C keeps jm's default style. "
            "Install clang-format or unset c_style.",
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
            binary,
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
            f"WARNING: clang-format failed; generated C left unformatted.\n"
            f"  {proc.stderr.strip()}",
            file=sys.stderr,
        )
        return

    if not quiet:
        n = len(files)
        print(f"  format  {n} C file{'s' if n != 1 else ''} (clang-format)")
