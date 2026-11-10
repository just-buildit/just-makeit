"""
_example.py — `just-makeit example` command.

Runs a bundled example end-to-end (scaffold -> implement -> cmake build ->
test) inside a temporary directory, printing live output.

    just-makeit example fir_filter
    just-makeit example              # list available examples
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator


@contextlib.contextmanager
def scratch_dir() -> "Iterator[str]":
    """The scratch directory a bundled example builds into, run standalone.

    Every example's ``__main__`` builds a real project in here, and several
    import the extension they built. On Windows a loaded ``.pyd`` cannot be
    deleted until the process exits, so ``tempfile.TemporaryDirectory``'s
    cleanup raised ``PermissionError`` AFTER the example had passed -- which
    failed v0.90.0's pre-publish Windows smoke on ``nco_tone``, the first
    example to build its extension there (gh-1377). Cleanup is best effort:
    what the OS will not let go of is left for the temp reaper, and the
    example's own result stands. (``ignore_cleanup_errors`` is Python 3.10+;
    jm supports 3.9.)

    >>> with scratch_dir() as d:
    ...     _ = (Path(d) / "x").write_text("x")
    >>> Path(d).exists()
    False
    """
    path = tempfile.mkdtemp()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@dataclass(frozen=True)
class JmRun:
    """What `subprocess.run(..., capture_output=True, text=True)` returns.

    Only the three attributes callers read. Anything else would be a
    promise this cannot keep -- there is no `.pid` and no `.args` worth
    reporting for a call that never left the process.
    """

    returncode: int
    stdout: str
    stderr: str


def run_cli(
    *args: str,
    cwd: "str | Path | None" = None,
    stdin: str = "",
    merge_stderr: bool = False,
) -> JmRun:
    r"""Call ``just-makeit <args>`` in this process and collect its output.

    The ONE in-process jm runner: the test suite's ``tests/_jmrun.run_cli``
    and every bundled example call this, and
    ``tests/test_gh2597_one_in_process_runner.py`` refuses a ``main()``
    called anywhere else. It lives in the package, not the tests, because
    the wheel ships the examples and they run it.

    Why in-process (gh-1374): 52 test files each spawned
    ``python -c "from just_makeit._cli import main; main()"``, ~3300
    children per suite run, and under coverage each child paid ~7 ms to
    start, ~43 ms more to start coverage and a data file to combine:
    measured 2026-09-19, the children cost 227 s of a 503 s coverage run to
    buy one percentage point. Nothing needed a process; the child existed
    for isolation -- argv, cwd, stdin, ``SystemExit`` -- and this provides
    all of it. ``tests/test_gh1374_cli_in_process.py`` holds the few files
    that still need a real child, and why.

    Copies of this drifted before it was one (gh-2597): two examples
    redirected ``sys.stdout`` only and kept the manifest cache, so both
    fixes below were missing there.

    Parameters
    ----------
    *args
        The command line, without the program name: ``run_cli("new", "p")``
        is ``just-makeit new p``.
    cwd
        Directory to run in, restored afterwards on every way out. ``None``
        runs where the caller already is, as ``subprocess.run(cwd=None)``
        does.
    stdin
        What the command reads. The default, empty, is what a child got:
        pytest gives a subprocess ``/dev/null``, so a prompt sees EOF and
        `_confirm` declines. It is supplied explicitly because pytest's own
        captured stdin raises ``OSError`` on read rather than EOF -- `jm
        regenerate` prompts, and four tests failed on that difference alone.
        ``stdin="y\n"`` answers a prompt.
    merge_stderr
        Write both streams to ``stdout``, in the order written, as a
        terminal shows them; ``stderr`` is then empty. What an example
        recording a user's view wants, so a warning sits beside the line it
        is about.

    Returns
    -------
    JmRun
        ``returncode`` is 0 when ``main()`` returns, the code carried by
        ``SystemExit`` when it exits (``sys.exit("message")`` prints the
        message and is 1, as in a child), and 1 for an uncaught exception,
        whose traceback lands in ``stderr`` exactly where a child put it.

    Notes
    -----
    Two things a child had for free, kept here:

    - **An empty manifest cache** (gh-2095). ``_incpath._CFG_CACHE`` follows
      jm's own writes, but a hand edit the caller made since the last
      command is not one; it is cleared so the manifest is read fresh.
    - **Capture at the file descriptor**, not only ``sys.stdout``. jm shells
      out to cmake, ctest and pytest, and a child writes to fd 1, which
      ``contextlib.redirect_stdout`` does not touch. That output would
      vanish from the result, and an assertion that something is ABSENT
      would pass having looked at nothing (gh-1374; `jm test`'s parsed
      summary is how this was found).

    ``main()`` is imported per call rather than at module import, because
    importing jm is part of what the CLI does and a caller may have just
    rewritten a template on disk.

    Examples
    --------
    >>> r = run_cli("--version", cwd=tempfile.mkdtemp())
    >>> r.returncode
    0
    >>> bool(r.stdout.strip())
    True
    """
    from just_makeit import _incpath
    from just_makeit._cli import main

    _incpath._CFG_CACHE.clear()

    def capture() -> "IO[str]":
        # Line-buffered, so this process's lines and its children's, which
        # share the file offset through fd 1 / fd 2, land in the order
        # written.
        return tempfile.TemporaryFile(
            "w+", encoding="utf-8", newline="", buffering=1
        )

    with contextlib.ExitStack() as files:
        out = files.enter_context(capture())
        err = out if merge_stderr else files.enter_context(capture())
        previous_argv, previous_stdin = sys.argv, sys.stdin
        previous_out, previous_err = sys.stdout, sys.stderr
        previous_cwd = os.getcwd()
        sys.stdout.flush()
        sys.stderr.flush()
        saved_fds = os.dup(1), os.dup(2)
        sys.argv = ["just-makeit", *args]
        sys.stdin = io.StringIO(stdin)
        sys.stdout, sys.stderr = out, err
        os.dup2(out.fileno(), 1)
        os.dup2(err.fileno(), 2)
        if cwd is not None:
            os.chdir(cwd)
        try:
            try:
                main()
                code = 0
            except SystemExit as exit_:
                if exit_.code is None:
                    code = 0
                elif isinstance(exit_.code, int):
                    code = exit_.code
                else:
                    print(exit_.code, file=sys.stderr)
                    code = 1
            except Exception:
                traceback.print_exc(file=sys.stderr)
                code = 1
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved_fds[0], 1)
            os.dup2(saved_fds[1], 2)
            for fd in saved_fds:
                os.close(fd)
            sys.argv, sys.stdin = previous_argv, previous_stdin
            sys.stdout, sys.stderr = previous_out, previous_err
            os.chdir(previous_cwd)
        out.seek(0)
        stdout = out.read()
        stderr = ""
        if err is not out:
            err.seek(0)
            stderr = err.read()
        return JmRun(code, stdout, stderr)


def run_jm(cwd: "str | Path", *args: str, expect: int = 0) -> str:
    """Run ``just-makeit <args>`` in *cwd* through `run_cli`; its output.

    What an example calls when its step IS a jm command line: both streams
    in one, as the user's terminal shows them, and the exit code held to
    *expect*.

    Parameters
    ----------
    cwd
        The directory the command runs in.
    *args
        The command line without the program name.
    expect
        The exit code the command must return; ``jm status`` on a stale
        project is 1, by design.

    Returns
    -------
    str
        Everything the command and its children printed, in order.

    Raises
    ------
    AssertionError
        When the exit code is not *expect*, naming the command and quoting
        its output.

    Examples
    --------
    >>> with scratch_dir() as d:
    ...     out = run_jm(d, "new", "demo")
    ...     (Path(d) / "demo" / "just-makeit.toml").exists()
    True
    """
    r = run_cli(*args, cwd=cwd, merge_stderr=True)
    assert r.returncode == expect, (
        f"jm {' '.join(args)} exited {r.returncode}, not {expect}\n{r.stdout}"
    )
    return r.stdout


def _examples_root() -> Path | None:
    """Return the examples/ directory, searching installed then editable paths."""
    import just_makeit

    pkg = Path(just_makeit.__file__).parent
    for candidate in (pkg / "examples", pkg.parent.parent / "examples"):
        if candidate.is_dir():
            return candidate
    return None


def _discover() -> list[str]:
    """Return sorted list of example names found on disk."""
    root = _examples_root()
    if root is None:
        return []
    return sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir() and (d / "test.py").exists()
    )


# Kept for callers that import _EXAMPLES directly (e.g. CI).
_EXAMPLES = _discover()


def _find(name: str) -> Path | None:
    """Return path to the named example directory, or None."""
    root = _examples_root()
    if root is None:
        return None
    candidate = root / name
    return (
        candidate
        if candidate.is_dir() and (candidate / "test.py").exists()
        else None
    )


def run(name: str | None) -> None:
    if name is None:
        print("Available examples:")
        for ex in _EXAMPLES:
            print(f"  {ex}")
        print("\nUsage: just-makeit example <name>")
        return

    example_dir = _find(name)
    if example_dir is None:
        print(f"error: example '{name}' not found.", file=sys.stderr)
        print(f"Available: {', '.join(_EXAMPLES)}", file=sys.stderr)
        sys.exit(1)

    print(f"just-makeit: running example '{name}'")
    print(f"  source: {example_dir}")
    print(flush=True)
    # gh-1387: the child writes to this process's stdout, so it must encode
    # it as main() now does; on Windows a pipe would otherwise be cp1252.
    # UTF-8 mode also covers what stdio does not: the `text=True` decode of
    # every jm the example captures, and every grandchild, which inherits
    # it. PYTHONIOENCODING too, because an inherited one outranks the mode
    # for stdio.
    r = subprocess.run(
        [sys.executable, str(example_dir / "test.py")],
        timeout=600,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    sys.exit(r.returncode)
