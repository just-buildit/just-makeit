"""Run jm's CLI in THIS process, with a child's result shape (gh-1374).

52 test files each carried their own copy of::

    subprocess.run([sys.executable, "-c",
                    "from just_makeit._cli import main; main()", *args])

Those 52 files spawn ~3300 children per suite run, and every child pays for
itself three times under coverage: ~7 ms to start, ~43 ms more to start
coverage (`COVERAGE_PROCESS_START`, measured 2026-09-19), and a `.coverage.*`
data file that the run must combine at the end. Measured the same day:
`make test` 148 s, `make coverage` 503 s, and 276 s with the subprocess
instrumentation off -- so the children cost 227 s, 45% of the coverage run,
to buy one percentage point.

Nothing here needed a process. No test drives the installed console script;
the child existed for isolation -- argv, cwd, `SystemExit` -- and this
provides all three. Coverage of what `main()` does is then measured natively
by the parent, so the point the subprocesses bought is kept without them.

`JmRun` deliberately mirrors `subprocess.CompletedProcess` (`returncode`,
`stdout`, `stderr`), so a file migrates by rewriting its helper's body and
not one of its assertions.

**What still needs a real child**, and why -- `tests/test_gh1374_cli_in_process.py`
holds the list and refuses any other:

- `tests/test_gh1387_utf8_stdio.py` -- the subject IS the process's stdio
    encoding. In-process it would test `io.StringIO`.
- `tests/test_gh879_worker_env_isolation.py` -- asserts a property of a
    worker's own environment.
"""

from __future__ import annotations

import io
import os
import shlex
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JmRun:
    """What `subprocess.run(..., capture_output=True, text=True)` returns.

    Only the three attributes the call sites read. Anything else would be a
    promise this cannot keep -- there is no `.pid` and no `.args` worth
    reporting for a call that never left the process.
    """

    returncode: int
    stdout: str
    stderr: str


def run_cli(
    *args: str, cwd: str | Path | None = None, stdin: str = ""
) -> JmRun:
    r"""Call ``just-makeit <args>`` in this process and collect its output.

    Parameters
    ----------
    *args
        The command line, without the program name: ``run_cli("new", "p")``
        is ``just-makeit new p``.
    cwd
        Directory to run in, restored afterwards. ``None`` runs where the
        caller already is, as ``subprocess.run(cwd=None)`` does.
    stdin
        What the command reads. The default, empty, is what the child got:
        pytest gives a subprocess ``/dev/null``, so a prompt sees EOF and
        `_confirm` declines. It must be supplied explicitly, because
        pytest's own captured stdin raises ``OSError`` on read rather than
        EOF -- `jm regenerate` prompts, and four tests failed on that
        difference alone. ``stdin="y\n"`` answers a prompt, which the
        subprocess form could not do at all.

    Returns
    -------
    JmRun
        ``returncode`` is 0 when ``main()`` returns, the code carried by
        ``SystemExit`` when it exits, and 1 for an uncaught exception --
        whose traceback lands in ``stderr``, exactly where a child put it.

    Notes
    -----
    ``main()`` is imported per call rather than at module import, because
    importing jm is part of what the CLI does and a test may have just
    rewritten a template on disk.

    Examples
    --------
    >>> import tempfile
    >>> r = run_cli("--version", cwd=tempfile.mkdtemp())
    >>> r.returncode
    0
    >>> bool(r.stdout.strip())
    True
    """
    from just_makeit._cli import main

    # Captured at the FILE DESCRIPTOR, not just `sys.stdout`. jm shells out
    # to cmake, ctest and pytest, and a child writes to fd 1 -- which
    # `contextlib.redirect_stdout` does not touch. That output would vanish
    # from the result, and a test asserting something is ABSENT would then
    # pass having looked at nothing (gh-1374; `jm test`'s parsed summary is
    # how this was found).
    with (
        tempfile.TemporaryFile("w+", encoding="utf-8", newline="") as out,
        tempfile.TemporaryFile("w+", encoding="utf-8", newline="") as err,
    ):
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
                # `sys.exit("message")` prints the message and exits 1; the
                # child did that, so this must too.
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
        err.seek(0)
        return JmRun(code, out.read(), err.read())


def replay_script(script: str, where: Path) -> Path:
    """Run a `jm script` output through run_cli, one command at a time.

    Shared by every test that replays a script (gh-1489, gh-1587): one
    replayer, so a script shape it cannot run fails every such test at once.

    Understands exactly the three shapes the script emits: a `cd`, a
    `cat >> FILE <<'EOF'` block, and a (backslash-continued) `just-makeit`
    command. Anything else fails the test rather than being skipped, so a
    new shape cannot quietly go unreplayed.
    """
    cwd = where
    lines = iter(script.replace("\\\n", " ").splitlines())
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("cd "):
            cwd = cwd / line[3:].strip()
        elif line.startswith("cat >> "):
            target = cwd / line.split()[2]
            body = []
            for inner in lines:
                if inner == "EOF":
                    break
                body.append(inner + "\n")
            with target.open("a", encoding="utf-8") as fh:
                fh.write("".join(body))
        elif line.startswith("just-makeit "):
            r = run_cli(*shlex.split(line)[1:], cwd=cwd)
            assert r.returncode == 0, f"{line}\n{r.stderr}"
        else:
            raise AssertionError(f"unreplayable script line: {line!r}")
    return cwd
