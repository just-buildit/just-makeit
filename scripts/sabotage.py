#!/usr/bin/env python3
"""Prove a gate by sabotaging its fix -- and refuse a sabotage that proves nothing.

gh-1430. The repo's method is "a mistake that can recur gets a check, proven
by sabotaging the fix and watching it go red". The sabotage step had no check
of its own, and it fails silently in ways that read exactly like a result:

- the edit never lands (a quoting error, or an anchor a formatter has since
  rewrapped), so the run is green -- read as "the test is not armed";
- the edit breaks collection or import, so everything ERRORS -- read as
  "well armed", when no assertion was exercised;
- the edit inverts the wrong thing and the run stays green -- read as a
  finding about coverage.

A sabotage that lands goes red and is self-evidencing; one that does not
lands on the same green as a genuinely unarmed test. So this refuses unless
ALL of these hold, and says which one did not:

1. *anchor* occurs in *file* exactly once (zero: a typo or a reformat; many:
   ambiguous about what was reverted);
2. the file's bytes actually changed;
3. *command* is green BEFORE the sabotage (a baseline, so a red result means
   something) and red AFTER it;
4. the red run names at least one FAILED test and reports no collection
   error -- "errored in collection" proves nothing about any assertion;
5. the file is restored byte-identical afterwards, and every ``__pycache__``
   under the repo is cleared, before and after, so neither the sabotaged run
   nor the NEXT one reads a stale ``.pyc``.

Usage::

    python3 scripts/sabotage.py FILE ANCHOR REPLACEMENT -- COMMAND...
    python3 scripts/sabotage.py FILE --anchor-file A.txt \\
        --replacement-file B.txt -- COMMAND...

Exit 0 when the sabotage went red for the right reason; 1 with the reason
otherwise. The failing test names are printed, so the caller can check they
are the ones the gate is about.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: pytest's per-test failure line, and its collection-error spellings --
#: every one anchored at column 0, where pytest prints its OWN run's lines. A
#: failing test's report can quote another run's output (this helper's tests
#: do), and an unanchored "errors during collection" matched inside that
#: quote and refused a sabotage that had gone red for the right reason.
_FAILED = re.compile(r"^FAILED (\S+)", re.M)
_COLLECTION = re.compile(
    r"^(?:ERROR collecting |!+ Interrupted: \d+ errors? during collection"
    r"|ImportError while)",
    re.M,
)


class Refused(Exception):
    """The sabotage proves nothing; the message says why."""


def _clear_pycache(root: Path) -> None:
    """Every ``__pycache__`` under *root*, not only ``src/``: a sabotaged
    module anywhere -- a test helper, a script -- is re-imported from a stale
    ``.pyc`` when the edit keeps its size and lands within the same second,
    and the run then measures the UNSABOTAGED code (this helper's own test
    found exactly that). Dot-directories (``.venv``, ``.git``) are pruned."""
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if "__pycache__" in dirnames:
            shutil.rmtree(Path(dirpath) / "__pycache__", ignore_errors=True)
            dirnames.remove("__pycache__")


def _run(cmd: "list[str]", root: Path) -> "tuple[int, str]":
    r = subprocess.run(
        cmd, cwd=root, capture_output=True, text=True, env=os.environ.copy()
    )
    return r.returncode, r.stdout + r.stderr


def sabotage(
    path: Path,
    anchor: str,
    replacement: str,
    cmd: "list[str]",
    root: Path = ROOT,
) -> "list[str]":
    """Apply the sabotage, run *cmd*, restore; return the failing tests.

    Raises :class:`Refused` naming the first condition that does not hold.
    The file is restored whatever happens, before this returns or raises.
    """
    original = path.read_bytes()
    text = original.decode("utf-8")
    n = text.count(anchor)
    if n != 1:
        raise Refused(
            f"the anchor occurs {n} times in {path}, not once"
            + (" -- a typo, or a formatter rewrapped it" if n == 0 else "")
        )
    sabotaged = text.replace(anchor, replacement).encode("utf-8")
    if sabotaged == original:
        raise Refused("the replacement equals the anchor: nothing changed")

    _clear_pycache(root)
    base, base_out = _run(cmd, root)
    if base != 0:
        raise Refused(
            f"the command is red BEFORE the sabotage (exit {base}), so a red"
            " result after it would prove nothing:\n" + base_out[-2000:]
        )
    try:
        path.write_bytes(sabotaged)
        if path.read_bytes() != sabotaged:
            raise Refused(f"the write to {path} did not land")
        _clear_pycache(root)
        code, out = _run(cmd, root)
    finally:
        path.write_bytes(original)
        _clear_pycache(root)
    if path.read_bytes() != original:
        raise Refused(f"{path} was not restored byte-identical -- check it")
    if code == 0:
        raise Refused(
            "the command stayed GREEN with the sabotage in place: the gate"
            " does not see this change"
        )
    if _COLLECTION.search(out):
        raise Refused(
            "the sabotaged run errored in COLLECTION, so no assertion was"
            " exercised:\n" + out[-2000:]
        )
    failed = _FAILED.findall(out)
    if not failed:
        raise Refused(
            f"the command went red (exit {code}) but named no FAILED test;"
            " a red that is not a test result proves nothing:\n" + out[-2000:]
        )
    return failed


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("usage: sabotage.py FILE ANCHOR REPLACEMENT -- COMMAND...")
        return 2
    split = argv.index("--")
    ap = argparse.ArgumentParser(prog="sabotage.py")
    ap.add_argument("file", type=Path)
    ap.add_argument("anchor", nargs="?")
    ap.add_argument("replacement", nargs="?")
    ap.add_argument("--anchor-file", type=Path)
    ap.add_argument("--replacement-file", type=Path)
    a = ap.parse_args(argv[:split])
    cmd = argv[split + 1 :]
    anchor = a.anchor_file.read_text("utf-8") if a.anchor_file else a.anchor
    repl = (
        a.replacement_file.read_text("utf-8")
        if a.replacement_file
        else a.replacement
    )
    if anchor is None or repl is None or not cmd:
        ap.error("need an anchor, a replacement and a command after --")
    try:
        failed = sabotage(a.file.resolve(), anchor, repl, cmd)
    except Refused as e:
        print(f"sabotage REFUSED: {e}", file=sys.stderr)
        return 1
    print(f"sabotage landed and went red: {len(failed)} FAILED")
    for name in failed:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
