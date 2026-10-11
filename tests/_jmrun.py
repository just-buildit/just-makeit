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
import re
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
    from just_makeit import _incpath
    from just_makeit._cli import main

    # gh-2095: a child starts with jm's manifest cache empty, so a file the
    # test changed by hand since the last command is read fresh. The cache
    # follows jm's OWN writes by itself; a hand edit is not one.
    _incpath._CFG_CACHE.clear()

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


def _warning_line() -> "re.Pattern[str]":
    """The pattern `warning_lines` matches, its marks read from `_report`.

    Built on first use rather than at import, so importing this module does
    not import jm -- `run_cli` defers that import for the same reason.
    """
    from just_makeit import _report

    marks = re.escape(_report._GATE_MARK + _report._ADVISORY_MARK)
    return re.compile(rf"[ \t]*warning(?:[ \t]+[{marks}])?[ \t]*:", re.I)


def warning_lines(text: str) -> "list[str]":
    r"""The lines of *text* that are jm warnings, by the form jm prints.

    gh-1936. jm prints the path of every file it writes, so a test that
    looked for the substring ``warning`` anywhere in its output went red
    whenever the TMPDIR, the checkout or a branch name contained the word,
    with no warning printed -- and a test asserting a warning WAS printed
    passed on the path alone. A warning is a LINE that opens with the word:
    optional indentation, ``warning``, the weight mark `_report.warn` adds
    (``!`` when `jm status --check` fails on it, ``~`` when it does not),
    then a colon. A path that merely contains the word never opens a line
    that way.

    The marks come from `_report`, the one place that decides how a warning
    reads, and `tests/test_gh1936_warning_anchor.py` renders `_report.warn`
    at both weights through this, so a new prefix there fails that test
    rather than leaving every caller here blind. Case is ignored and the
    mark is optional because the emitters that predate `_report` still
    print a bare ``warning:`` or ``WARNING:``.

    Parameters
    ----------
    text
        Captured output, stdout and stderr alike; split on line breaks.

    Returns
    -------
    list of str
        Each warning line, as printed, in order. Empty when there is none,
        so ``assert not warning_lines(err)`` reads as "warned about
        nothing".

    Examples
    --------
    >>> warning_lines("  create  /tmp/warning-dir/p/CMakeLists.txt")
    []
    >>> warning_lines("ok\nwarning ~: x\n  WARNING: y\n")
    ['warning ~: x', '  WARNING: y']
    """
    pattern = _warning_line()
    return [line for line in text.splitlines() if pattern.match(line)]


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


def script_round_trip(root: Path, where: Path) -> "tuple[dict, dict]":
    """Script *root*, replay it under *where*, and read both manifests.

    gh-1923. A round trip is compared over the MERGED manifest. `jm new`
    defaults to the fragment layout, so every component, method, property
    and module lives in ``objects/*.toml`` / ``modules/*.toml``, and the
    root ``just-makeit.toml`` holds only ``include`` and ``[project]``. Two
    copies of a helper that compared that one file
    (``test_toml_roundtrip.py``, ``test_pytest_framework.py``) passed for
    every component-level flag whether the replay kept it or not.

    Each side is read by `_config.load`, the one reader every command goes
    through, so the fragments merge exactly as jm merges them -- nothing
    here parses a fragment. `load` consumes the root's ``include`` list,
    which is the LAYOUT, so it is put back from `_config.load_manifest`: a
    replay that rebuilt the project in another layout is a difference too.

    Parameters
    ----------
    root
        The project to run ``jm script`` in.
    where
        Directory to replay into, created if absent. The script makes the
        project there and ``cd``s into it, as a user running it would.

    Returns
    -------
    tuple of dict
        ``(original, replayed)``. Equal when the script rebuilds the
        project's manifest; any difference is a key the script failed to
        replay.
    """
    from just_makeit import _config as C

    r = run_cli("script", cwd=root)
    assert r.returncode == 0, f"jm script failed:\n{r.stderr}"
    where.mkdir(parents=True, exist_ok=True)
    replayed = replay_script(r.stdout, where)

    def read(tree: Path) -> dict:
        cfg = C.load(tree)
        cfg["include"] = C.load_manifest(tree).get("include")
        return cfg

    return read(root), read(replayed)
