"""
_example.py — `just-makeit example` command.

Runs a bundled example end-to-end (scaffold -> implement -> cmake build ->
test) inside a temporary directory, printing live output.

    just-makeit example fir_filter
    just-makeit example              # list available examples
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterator


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


def run_jm(cwd: "str | Path", *args: str) -> str:
    """Run ``just-makeit <args>`` in *cwd*, in this process; its output.

    What an example calls when its step IS a jm command line, so the
    example runs the words its README shows rather than the Python function
    behind them. In-process for the same reason the test suite's
    ``run_cli`` is (gh-1374): a child costs a process start and, under
    coverage, a data file, and isolates nothing a restored ``sys.argv`` and
    working directory do not.

    Parameters
    ----------
    cwd
        The directory the command runs in; the caller's own working
        directory is restored afterwards, on every way out.
    *args
        The command line without the program name.

    Returns
    -------
    str
        Everything the command printed, both streams in the order written,
        as a terminal would show them.

    Raises
    ------
    AssertionError
        When the command exits non-zero, naming it and quoting its output.

    Examples
    --------
    >>> with scratch_dir() as d:
    ...     out = run_jm(d, "new", "demo")
    ...     (Path(d) / "demo" / "just-makeit.toml").exists()
    True
    """
    import io

    from just_makeit._cli import main

    buf = io.StringIO()
    argv, here = sys.argv, os.getcwd()
    sys.argv = ["just-makeit", *args]
    os.chdir(cwd)
    code = 0
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = argv
        os.chdir(here)
    text = buf.getvalue()
    assert code == 0, f"jm {' '.join(args)} exited {code}\n{text}"
    return text


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
