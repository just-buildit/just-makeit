"""
_fmtprobe.py — does a formatter command mean the same thing from any directory?

gh-758 asked this of ``c_format_command`` and gh-772 pointed out that the
question is not C-specific: ``py_format_command`` is run from the same two
places and had no detection at all. `_cfmt` and `_pyfmt` are peers, so the
answer lives beside them rather than inside one of them.

The reason it matters is the same for both. jm formats its temp scaffold from
**outside** the project and the real tree from **inside** it, then compares
the two. A command that resolves differently depending on where it runs
therefore formats the two compared sides with two different tools, and no
amount of ``jm apply`` clears the resulting drift.

``["uv", "run", "--group", "dev", <tool>]`` — the natural way to pin a
version, and what jm's own docs once suggested — is exactly that trap.

**Two failure modes, not one**, and this is what gh-772 turned up: the C case
and the Python case do not fail the same way, and a detector written for the
first misses the second entirely.

* ``clang-format`` under ``uv run`` outside a project: ``uv`` prints
  ``--group dev has no effect when used outside of a project`` and falls
  through to whatever is on ``PATH``. Two binaries, two versions, both of
  which answer ``--version``. doppler lost a day to it (21.1.8 against
  22.1.8).
* ``ruff format`` under ``uv run`` outside a project: ``Failed to spawn:
  ruff``. Nothing runs at all.

gh-758's check returned "no problem" for the second, because it treated an
unanswerable ``--version`` as "cannot tell, not my business to escalate". For
a command that answers perfectly well *inside* the project, that is not an
unknown — it is the finding. Measured on a command contrived to fail exactly
that way::

    from project   : 'fakefmt 1.2.3'
    from elsewhere : ''
    detector says  : None          <-- silent, and this is doppler's case

The milder consequence is worth naming precisely: the generated Python simply
is not formatted on one side, rather than being formatted differently. But a
formatter jm cannot spawn is a formatter that did not run, and saying nothing
about it is the same silence that made gh-758 read as irreproducible for a
day.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple


class CwdDependence(NamedTuple):
    """What asking from two directories turned up.

    *kind* is ``"version"`` when both directories answer and disagree, and
    ``"spawn"`` when the project answers and elsewhere does not. They are
    different problems with the same cause and want different prose:
    "your formatter ran twice as two different tools" against "your formatter
    did not run".
    """

    kind: str
    from_project: str
    from_elsewhere: str


def command_version(command: "list[str]", cwd: "Path | None" = None) -> str:
    """The command's own ``--version`` output, or ``""`` if unavailable.

    Never raises: a missing or failing binary reports ``""`` exactly as it
    does for formatting itself. *cwd* asks from a specific directory, which is
    how :func:`cwd_dependence` sees a command that resolves differently
    depending on where it runs.
    """
    if not command or shutil.which(command[0]) is None:
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


def cwd_dependence(root: Path, command: "list[str]") -> "CwdDependence | None":
    """``None`` when *command* means the same thing from any directory.

    Asks from *root* and from a throwaway temp directory. When the project
    itself cannot answer, that is a genuine unknown — a formatter that is
    simply absent — and is reported as ``""`` by the caller's own version
    line rather than escalated here.
    """
    here = command_version(command, cwd=root)
    if not here:
        return None
    with tempfile.TemporaryDirectory(prefix="jm-fmt-") as tmp:
        there = command_version(command, cwd=Path(tmp))
    if not there:
        return CwdDependence("spawn", here, "")
    if there == here:
        return None
    return CwdDependence("version", here, there)


def describe(dep: CwdDependence, key: str, root: Path) -> str:
    """The warning text for *dep*, naming the manifest *key* that carries it.

    One renderer for both commands and both failure modes, so the C and
    Python reports cannot drift into saying different things about the same
    underlying cause.
    """
    head = f"\nWARNING: [project] {key} "
    if dep.kind == "spawn":
        body = (
            "does not run outside the project directory:\n"
            f"    in {root}: {dep.from_project.splitlines()[0]}\n"
            "    outside a project:  failed to run\n"
            "  `apply` formats a temp scaffold outside the project, so that "
            "side was left\n  unformatted while the real tree was formatted. "
            "A formatter jm cannot spawn\n  is a formatter that did not run."
        )
    else:
        body = (
            "resolves a different formatter depending on\n  the working "
            "directory, which is very likely this drift:\n"
            f"    in {root}: {dep.from_project.splitlines()[0]}\n"
            f"    outside a project:  {dep.from_elsewhere.splitlines()[0]}\n"
            "  `apply` formats a temp scaffold outside the project, so the "
            "two sides being\n  compared were formatted by different "
            "binaries."
        )
    return (
        head
        + body
        + "\n  `uv run --group <g> <tool>` is the usual cause — outside a "
        "project it either\n  no-ops and falls back to PATH, or fails to "
        "spawn. Use a CWD-independent\n  command (`uvx <tool>==<version>` "
        "or an absolute path)."
    )
