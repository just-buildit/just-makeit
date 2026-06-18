"""
_cfmt.py — optional house-style pass over generated C (gh-265).

jm emits its own canonical 4-space C. Projects with a different committed style
(doppler, jm's poster-child, uses GNU 2-space) otherwise have to run
``clang-format`` over the generated ``native/**`` fragments by hand after every
mutating command. Opting in with::

    [project]
    c_style = "clang-format"

makes jm run that pass itself, so the emitted code already matches the project's
``.clang-format`` and the manual reformat step disappears.

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
    """All generated C/H translation units under the project's ``native/``.

    Sorted for a stable invocation order (and stable test assertions). Only
    ``native/inc`` and ``native/src`` are walked — vendored third-party C under
    ``[project] c_deps`` lives elsewhere and is never reformatted.
    """
    files: list[Path] = []
    for sub in ("inc", "src"):
        base = root / "native" / sub
        if base.is_dir():
            files.extend(base.rglob("*.h"))
            files.extend(base.rglob("*.c"))
    return sorted(files)


def format_project(root: Path, cfg: dict, *, quiet: bool = False) -> None:
    """Reformat the project's generated C to its house style, if opted in.

    No-op unless ``[project] c_style == "clang-format"``. Runs
    ``clang-format -i --style=file`` over every generated ``native/**`` C/H
    file, so the committed ``.clang-format`` (or ``--fallback-style`` when the
    project ships none) decides the layout. Idempotent: already-conformant
    files are left byte-identical.

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
