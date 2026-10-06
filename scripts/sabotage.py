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
   A failing subtest counts as a failed test, named by its test: pytest 9
   reports a unittest ``self.subTest`` failure ONLY as ``SUBFAILED``.
   A run whose output could hide a collection error is refused too, since
   "no error shown" is then not "no error" (gh-1945): with no tracebacks
   (``--tb=no``) and no ``ERROR`` lines (an ``-r`` without ``E``), an error
   is at most a count, ``1 failed, 1 error``, which a fixture that raised
   shares, and under ``-qq`` not even that.
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

#: How a red run reads, from pytest's own lines. Every pattern is anchored
#: at column 0, where pytest prints its OWN run's lines: a failing test's
#: report can quote another run's output (this helper's tests do), and an
#: unanchored "errors during collection" matched inside that quote and
#: refused a sabotage that had gone red for the right reason. Which lines a
#: run prints depends on how pytest ran (pytest 9.1, xdist 3.8, measured;
#: gh-1933, gh-1945).
#:
#: The rule above the short test summary. The result lines and the count
#: line are read only below the LAST one: pytest prints nothing there but
#: its own summary, while what a test prints -- captured stdout, a log
#: record, a ``-s`` run's output -- lands above it, at column 0 too. Read
#: anywhere, a test that printed ``ERROR x.py`` was a module that failed to
#: collect, and one that printed ``FAILED x.py::y`` named a test that never
#: ran (gh-1945).
_SUMMARY = re.compile(r"^=+ short test summary info =+$", re.M)
#: One short-summary line, ``<word> <node id>[ - <message>]``, as
#: (word, id):
#:
#: - ``FAILED x.py::test_y`` -- a failed test.
#: - ``SUBFAILED(i=1) x.py::T::test_y`` -- a failed subtest (pytest 9),
#:   described as ``(k=v, ...)``, ``[msg]``, ``[msg] (k=v, ...)`` or
#:   ``(<subtest>)``. A unittest ``self.subTest`` failure is reported ONLY
#:   so, with no FAILED line for its test (the ``subtests`` fixture's test
#:   gets both), and a red run of nothing else was refused as naming none.
#: - ``ERROR test_a.py - RuntimeError: boom`` -- a node that errored. An id
#:   with no ``::`` is a module, or a directory whose conftest failed
#:   (``ERROR sub``), that did not collect; a test's id always has one, so
#:   a fixture that raised (``ERROR x.py::test_y``) is not. Under
#:   pytest-xdist (``make test`` runs ``-n auto``) and
#:   ``--continue-on-collection-errors`` the run goes on, names the FAILED
#:   tests beside it, prints no ``Interrupted:``, and this line and the
#:   ``ERROR collecting`` header are ALL it says -- so a sabotage that only
#:   broke an import was accepted (gh-1933).
#:
#: The id runs to the `` - `` that opens pytest's message, or to the end of
#: the line. It may hold whitespace -- a directory name, a parametrize id
#: (``test_w[a b]``) -- and its bracketed part may hold `` - `` itself.
#: Read as ``\S+``, a module under ``my tests/`` was not seen to fail
#: collection (silently, under ``--tb=no``), and ``test_w[a b]`` was named
#: ``test_w[a``.
_RESULT = re.compile(
    r"^(FAILED|ERROR|SUBFAILED(?:\[.*?\](?: \(.*?\))?|\(.*?\))) "
    r"(\S[^\[\n]*?(?:\[.*?\])?)(?: - |$)",
    re.M,
)
#: The other lines saying a module did not collect, read anywhere:
#:
#: - ``!!! Interrupted: 1 error during collection !!!`` -- default mode
#:   only, which stops before any test runs.
#: - ``____ ERROR collecting test_a.py ____`` -- the ERRORS section header,
#:   which only a collection error gets (a fixture's reads ``ERROR at setup
#:   of``); ``--tb=no`` drops it. It was spelled ``^ERROR collecting ``,
#:   which pytest never prints at column 0, so it never matched.
#: - ``ImportError while ...`` -- the body under that header for an
#:   ImportError, and pytest's line for a conftest that cannot import.
#:
#: A test that PRINTS one of these at column 0 is read as a module that did
#: not collect: a loud refusal, where a miss would be silent.
_COLLECTION = re.compile(
    r"^(?:!+ Interrupted: \d+ errors? during collection"
    r"|_+ ERROR collecting "
    r"|ImportError while)",
    re.M,
)
#: How a run SHOWS an error it names in no ERROR line (its ``-r`` lacks
#: ``E``): its tracebacks -- an ERRORS or FAILURES section, which a red run
#: prints unless ``--tb=no`` -- or its count line, ``1 failed, 2 errors in
#: 0.30s`` (``=``-ruled unless ``-q``, and gone under ``-qq``), read below
#: the summary rule like the result lines. Under ``-rf --tb=no`` a module
#: that failed to collect, beside a FAILED test, is that count and nothing
#: else, the same count a fixture that raised gives; under ``-qq`` it is
#: nothing at all. Such a run is refused as unable to show it (gh-1945).
_TRACEBACKS = re.compile(r"^=+ (?:ERRORS|FAILURES) =+$", re.M)
_COUNT = re.compile(r"^(?:=+ )?(\d+ [a-z].*?) in \d+(?:\.\d+)?s\b", re.M)
#: An SGR escape (``\x1b[31m``, ``\x1b[0m``), removed from every run's output
#: before any pattern above reads it (gh-1845). A coloured pytest puts one
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


def _uncollected(out: str, results: "list[tuple[str, str]]") -> bool:
    """Whether the run says a module failed to collect: an ``ERROR`` result
    whose id has no ``::``, or any :data:`_COLLECTION` line."""
    modules = [i for w, i in results if w == "ERROR" and "::" not in i]
    return bool(modules) or bool(_COLLECTION.search(out))


def _hidden_errors(
    out: str, summary: str, results: "list[tuple[str, str]]"
) -> "str | None":
    """Why the run could hide a module that failed to collect, or None.

    It cannot when it names its errors (an ``ERROR`` result: the ``-r``
    has ``E``) or prints its tracebacks (every error then gets a header,
    and a collection error's is :data:`_COLLECTION`'s). Otherwise its count
    line is all there is: one reporting an error says a node errored and
    not which, and no count line at all says nothing.
    """
    if any(w == "ERROR" for w, _ in results) or _TRACEBACKS.search(out):
        return None
    count = _COUNT.search(summary)
    if count is None:
        return (
            "it prints no tracebacks, no ERROR line and no count line;"
            " drop --tb=no, or one -q"
        )
    errors = re.search(r"\b\d+ errors?\b", count.group(1))
    if errors is None:
        return None
    return (
        f"it counts {errors.group()} and names none; name them with an -r"
        " holding E, or drop --tb=no"
    )


def _failed_tests(code: int, out: str) -> "list[str]":
    """The tests a red run names as failed, in order and each once.

    Raises :class:`Refused` when the run reports a collection error, names
    no failed test, or could hide a collection error -- condition 4 of the
    module docstring. *code* is the run's exit status and *out* its output,
    colour removed.
    """
    rules = list(_SUMMARY.finditer(out))
    summary = out[rules[-1].end() :] if rules else ""
    results = _RESULT.findall(summary)
    if _uncollected(out, results):
        raise Refused(
            "the sabotaged run errored in COLLECTION, so no assertion was"
            " exercised:\n" + out[-2000:]
        )
    failed = list(dict.fromkeys(i for w, i in results if w != "ERROR"))
    if not failed:
        raise Refused(
            f"the command went red (exit {code}) but named no FAILED test;"
            " a red that is not a test result proves nothing:\n" + out[-2000:]
        )
    why = _hidden_errors(out, summary, results)
    if why:
        raise Refused(
            f"the sabotaged run could HIDE a collection error: {why}. A"
            " FAILED test beside a module that did not import proves nothing"
            " about that module's tests:\n" + out[-2000:]
        )
    return failed


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
    return _failed_tests(code, out)


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
