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
   error -- "errored in collection" proves nothing about any assertion.
   That holds whether pytest stopped at the error (its default) or went on
   past it to run the rest (under pytest-xdist, as ``make test`` runs it,
   and ``--continue-on-collection-errors``; gh-1933), where a FAILED named
   beside it is no proof either: the module that did not import ran none
   of its tests, and the gate may be one of them.
   Both are read with the run's colour escapes removed, so a caller whose
   pytest colours its output is answered like one whose does not;
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
#:
#: Which spellings a run prints depends on how pytest ran (pytest 9.1,
#: xdist 3.8, measured; gh-1933):
#:
#: - ``!!! Interrupted: 1 error during collection !!!`` -- default mode
#:   only, which stops before any test runs.
#: - ``ERROR test_a.py - RuntimeError: boom`` -- the short summary's line
#:   for a node id with no ``::``: a module, or a directory whose conftest
#:   failed (``ERROR sub``). A test's id always has one, so a fixture that
#:   raised (``ERROR x.py::test_y``) is not this. Under pytest-xdist
#:   (``make test`` runs ``-n auto``) and ``--continue-on-collection-errors``
#:   the run goes on, names the FAILED tests beside it, prints no
#:   ``Interrupted:``, and this line and the header below are ALL it says
#:   -- so a sabotage that only broke an import was accepted. The id holds
#:   no whitespace, so a captured log line (``ERROR    x:a.py:3 broke``) is
#:   not read as one. ``-r`` without ``E`` drops this line.
#: - ``____ ERROR collecting test_a.py ____`` -- the ERRORS section header,
#:   which only a collection error gets (a fixture's reads ``ERROR at setup
#:   of``); ``--tb=no`` drops it. It was spelled ``^ERROR collecting ``,
#:   which pytest never prints at column 0, so it never matched.
#: - ``ImportError while ...`` -- the body under that header for an
#:   ImportError, and pytest's line for a conftest that cannot import.
_FAILED = re.compile(r"^FAILED (\S+)", re.M)
_COLLECTION = re.compile(
    r"^(?:!+ Interrupted: \d+ errors? during collection"
    r"|ERROR (?:(?!::)\S)+(?: - |$)"
    r"|_+ ERROR collecting "
    r"|ImportError while)",
    re.M,
)
#: An SGR escape (``\x1b[31m``, ``\x1b[0m``), removed from every run's output
#: before either pattern above reads it (gh-1845). A coloured pytest puts one
#: at column 0 -- ``\x1b[31mFAILED\x1b[0m test_mod.py::\x1b[1mtest_one`` --
#: so under FORCE_COLOR ``^FAILED`` matched nothing and EVERY red sabotage
#: was refused as naming no test; ``^ImportError while`` misses the same way.
#:
#: Read colour-blind rather than run the command with colour forced off.
#: The command is the caller's, and a proof is about the run its gate makes,
#: so the helper does not edit its environment (jm's own CLI reads NO_COLOR).
#: Nor could it: pytest obeys ``--color=yes``, on the command line or in
#: PYTEST_ADDOPTS, over NO_COLOR=1 and PY_COLORS=0 alike, and FORCE_COLOR=0
#: turns colour ON (pytest 9.1, measured). gh-1456 read a child pytest's
#: summary line the same way.
_SGR = re.compile(r"\x1b\[[0-9;]*m")


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
    return r.returncode, _SGR.sub("", r.stdout + r.stderr)


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
