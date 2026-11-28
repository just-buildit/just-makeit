"""Shared pytest setup: import path and a fail-fast dependency check.

**Import path.** The suite tests the working tree, so this repo's ``src/`` goes
on ``sys.path`` ahead of everything else. Two problems that fixes:

- Most test modules already do their own ``sys.path.insert``, but a few (e.g.
  ``test_app_gen.py``) just ``from just_makeit... import`` and rely on some
  earlier-collected module having done it. Running one of those files on its own
  — ``pytest tests/test_app_gen.py``, the workflow CLAUDE.md documents — died
  with ``ModuleNotFoundError``.
- Position matters, not just presence. An editable install of a *different*
  checkout (a sibling worktree, say) otherwise wins, and the suite silently
  tests that tree instead of this one. The symptom is baffling: a brand-new
  function reported missing from a module you are looking at.

**Dependency check.** ``make test`` runs pytest in a ``uv run --no-project``
environment, which excludes the project *and its dependencies*, while the suite
imports ``just_makeit`` from source. A missing runtime dep therefore surfaces as
a pile of unrelated failures rather than "you are missing a dependency":

- ``tomlkit`` missing is *silent* — ``_config._write_doc`` falls back to
  ``_dump`` by design (it must stay importable in tool-installed envs), so
  comment/key preservation quietly stops and ~8 round-trip tests fail on
  content, pointing at TOML serialization instead of at the environment.
- ``tomli`` missing is fatal on 3.9/3.10, where it *is* ``C.tomllib``.

The Makefile passes both. This check exists for everyone who runs ``pytest``
directly, and turns the confusing pile into one actionable line.

**Virtualenv check.** The end-to-end example tests build a scaffolded project
in a temp directory by shelling out to ``uv pip install -e .``. uv finds the
environment to install into from ``VIRTUAL_ENV``, or by walking up from the
working directory for a ``.venv`` — and the working directory there is a temp
project that has neither. So the variable has to be set in the *parent*
process.

Both documented ways of running the suite set it: ``source .venv/bin/activate``
exports it, and ``uv run pytest`` (what CI uses) sets it for the child.
Invoking ``.venv/bin/pytest`` directly does **not** — the interpreter still
resolves ``sys.prefix`` to the venv via ``pyvenv.cfg``, so imports work and
everything looks normal, but the variable is absent and every one of those
tests errors in fixture setup with "No virtual environment found".

That reads like a broken machine rather than a wrong invocation, and it is
worth a fail-fast: it cost a session's worth of misattribution, with 34 errors
written off as "no C toolchain on this box" when the toolchain was fine and the
tests had simply never run.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# gh-2078: `--jm-shard=K/N`, which Coverage's CI jobs split the suite by. A
# plugin module rather than hooks written here, so the gate in
# test_gh2078_coverage_shards.py can load the same code into a child pytest.
pytest_plugins = ["_shard"]

SRC = Path(__file__).parent.parent / "src"

# Ahead of any editable install — see the module docstring.
_src = str(SRC)
if sys.path and sys.path[0] == _src:
    pass
else:
    while _src in sys.path:
        sys.path.remove(_src)
    sys.path.insert(0, _src)


def _missing_runtime_deps() -> list[str]:
    """Runtime dependencies of just-makeit that are not importable here.

    Kept in terms of *import* names rather than distribution names: what breaks
    the suite is the import failing, and ``tomli`` is only required below 3.11.
    """
    missing = []
    try:
        import tomlkit  # noqa: F401
    except ImportError:
        missing.append("tomlkit")
    if sys.version_info < (3, 11):
        try:
            import tomli  # noqa: F401
        except ImportError:
            missing.append("tomli")
    return missing


# Where this worker's private environment lives, so `pytest_unconfigure` can
# remove exactly what `pytest_configure` created. None when isolation is off.
_worker_env_root: Path | None = None


def _inheritable_paths(paths: list[str] | None = None) -> list[str]:
    """Every existing directory on the parent's ``sys.path``, in order.

    Deliberately not ``sysconfig.get_path("purelib")``, which names only *one*
    directory and is the wrong one under ``uv run``. uv assembles that
    environment from layers: when a ``.venv`` is present it installs ``--with``
    packages straight into it and ``purelib`` happens to hold everything, but
    with no ``.venv`` — which is CI, always — the base is an empty ephemeral
    venv under ``builds-v0/`` and the packages live in a separate
    ``archive-v0/`` overlay reached only through ``sys.path``.

    Inheriting ``purelib`` alone therefore works on a developer's machine and
    inherits nothing on CI, where it surfaced as ``Could NOT find Python3
    (missing: Python3_NumPy_INCLUDE_DIRS NumPy)`` in every test that compiles.
    Reading ``sys.path`` is correct under either layout.

    Parameters
    ----------
    paths : list of str, optional
        Stand-in for ``sys.path``; the default reads the live one. Present so
        the layout above can be tested without arranging one.

    Returns
    -------
    list of str
        Existing directories, order preserved, no duplicates.
    """
    seen = []
    for entry in sys.path if paths is None else paths:
        if entry and Path(entry).is_dir() and entry not in seen:
            seen.append(entry)
    return seen


#: Where ``tempfile`` puts a worker's environment, and the name it gives it:
#: ``jm-<worker>-env-<pid>-<random>``. The pid is the worker's own, so a run
#: can tell a live peer's environment from one whose worker is gone.
_WORKER_ENV_RE = re.compile(r"jm-gw\d+-env-(?:(\d+)-)?[^/\\]+")

#: An environment named without a pid (jm before the reaper) is reaped once
#: it is older than this: no worker lives for a day.
_LEGACY_ENV_AGE = 24 * 3600


#: ``ERROR_ACCESS_DENIED``, and ``STILL_ACTIVE`` (GetExitCodeProcess).
_WIN_ACCESS_DENIED = 5
_WIN_STILL_ACTIVE = 259


def _pid_alive_windows(pid: int, kernel32, get_last_error) -> bool:
    """`_pid_alive` on Windows, with *kernel32* and *get_last_error* passed
    in so the decision is testable on any platform.

    *kernel32* is loaded with ``use_last_error=True`` and *get_last_error*
    is ``ctypes.get_last_error``. A process ``OpenProcess`` cannot open
    because it is another user's (``ERROR_ACCESS_DENIED``) exists, so it
    reads as alive, which is the safe answer for a reaper; any other
    failure means the process is gone.
    """
    import ctypes

    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
    if not handle:
        return get_last_error() == _WIN_ACCESS_DENIED
    code = ctypes.c_ulong()
    ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    return bool(ok) and code.value == _WIN_STILL_ACTIVE


def _pid_alive(pid: int) -> bool:
    """Whether process *pid* exists (owned by anyone).

    POSIX asks with signal 0, which delivers nothing. Windows has no signal
    0 -- ``os.kill(pid, 0)`` there sends CTRL_C_EVENT -- so it opens the
    process and reads its exit code instead.

    A ZOMBIE is dead here, though signal 0 reaches it: it has exited and
    will never use its environment again, it only waits for its parent's
    ``wait()``. A killed run's workers are orphans, re-parented to PID 1,
    and in a container whose PID 1 never reaps (CI's) they stay zombies
    for good -- read as alive, their environments were never reaped
    (`_process_state`).

    Examples
    --------
    >>> _pid_alive(os.getpid())
    True
    """
    if sys.platform == "win32":
        import ctypes

        # `use_last_error`: ctypes saves the thread's last error right after
        # each call and `get_last_error` reads that copy. Calling
        # `GetLastError()` afterwards reads whatever ctypes' own calls in
        # between left there, which can read a live process as dead.
        kernel32 = ctypes.WinDLL(  # type: ignore[attr-defined]
            "kernel32", use_last_error=True
        )
        return _pid_alive_windows(
            pid,
            kernel32,
            ctypes.get_last_error,  # type: ignore[attr-defined]
        )
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return _process_state(pid) not in ("Z", "X")


def _process_state(pid: int) -> str:
    """The one-letter scheduler state of *pid* (``R``, ``S``, ``Z`` ...),
    or ``""`` when it cannot be read -- which keeps the process alive.

    Linux reads ``/proc/<pid>/stat``, whose state follows the command name
    in parentheses (a name may itself hold a parenthesis, so it is split at
    the LAST one). Elsewhere ``ps -o stat=`` answers the same question.

    Examples
    --------
    >>> _process_state(os.getpid()) in ("R", "S", "D")
    True
    """
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
                return f.read().rsplit(")", 1)[1].split()[0]
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        return out[:1]
    except (OSError, IndexError, subprocess.SubprocessError):
        return ""


def _reap_stale_worker_envs(tmp: Path) -> "list[Path]":
    """Remove every worker environment under *tmp* whose worker is gone.

    `pytest_unconfigure` removes a worker's environment when the worker
    exits cleanly, and only then: a run that is killed (SIGTERM, SIGKILL,
    the OOM killer, a cancelled CI job) leaves one per worker, ~600 files
    each. On a shared ``/tmp`` they accumulated until its inodes ran out,
    and every later run failed to set up. Moving them under pytest's
    basetemp would not help: pytest keeps a numbered basetemp whose
    ``.lock`` survives a killed run for three days.

    So each worker reaps before it provisions: an environment named for a
    pid that no longer exists is removed, and one named without a pid (an
    older checkout's) once it is a day old. A live peer's is never
    touched, because its pid exists; a reused pid only postpones the reap.

    One shared ``/tmp`` seen from two pid namespaces -- a container with the
    host's ``/tmp`` bind-mounted -- breaks that: a live peer's pid from the
    other namespace looks dead here, and its environment would be removed
    under it. Give such a run its own ``TMPDIR``.

    Returns the directories removed.
    """
    removed: "list[Path]" = []
    try:
        entries = list(tmp.iterdir())
    except OSError:
        return removed
    now = time.time()
    for d in entries:
        m = _WORKER_ENV_RE.fullmatch(d.name)
        if not m or not d.is_dir():
            continue
        if m.group(1):
            stale = not _pid_alive(int(m.group(1)))
        else:
            try:
                stale = now - d.stat().st_mtime > _LEGACY_ENV_AGE
            except OSError:
                continue
        if stale:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d)
    return removed


def _setup_error(worker: str, detail: str) -> pytest.UsageError:
    """The refusal of a worker that could not provision its environment.

    Naming ENOSPC when that is the cause, because it reads as anything but:
    uv reports a failed seed install, and an exhausted ``/tmp`` with
    gigabytes free (its *inodes* gone) looks like a broken machine.
    """
    tmp = tempfile.gettempdir()
    full = ("no space left on device", "os error 28", f"errno {errno.ENOSPC}")
    cause = ""
    if any(s in detail.lower() for s in full):
        cause = (
            f"{tmp} is out of space or out of inodes (ENOSPC). Check "
            f"`df -h {tmp}` and `df -i {tmp}`, clear it, or point TMPDIR "
            "at a filesystem with room.\n"
        )
    return pytest.UsageError(
        f"{cause}could not create the private environment for xdist worker "
        f"{worker}, so the end-to-end tests would share one environment and "
        f"race on numpy's headers (gh-879):\n{detail}"
    )


def _provision_worker_env() -> None:
    """Give this xdist worker its own environment for subprocess builds.

    **The problem.** Every xdist worker is a separate process, but they all
    inherit the single environment their parent ``uv run`` resolved —
    ``--no-project`` does not isolate anything, it only declines to install
    *this* project, and uv still discovers ``.venv`` and exports
    ``VIRTUAL_ENV``. So N workers share one site-packages, and the end-to-end
    tests *write* to it: they ``uv pip install -e .`` scaffolded projects into
    it, and every generated project's ``make`` runs the gh-824 header guard,
    which repairs an absent numpy include directory by shelling out to
    ``pip install --force-reinstall numpy``.

    Concurrently that races with teeth. A worker that force-reinstalls numpy
    deletes and rewrites the include directory every other compiling worker is
    reading, so their builds die on ``numpy/arrayobject.h: No such file or
    directory`` — and their own gh-824 guards then fire and reinstall in turn.
    One transient miss cascades, which is why gh-879 picked a different victim
    each time and only on the busiest runner. Two scaffolds also share the
    distribution name ``my_fir``, so the same env got two different projects
    installed over each other.

    **Why isolation rather than a lock.** A lock would serialize the slowest
    part of the suite. Instead each worker gets a venv whose ``site-packages``
    is its own, with a ``.pth`` adding the *parent's* ``site-packages`` behind
    it. Reads therefore still resolve to the shared base — which keeps numpy's
    C-API identical to the one the generated extension is compiled against and
    later imported with under ``sys.executable`` — while everything a worker
    writes lands in its own directory, invisible to its peers. The ordering is
    the point: the worker's own entry precedes the inherited one, so a local
    reinstall shadows the shared copy instead of being shadowed by it.

    ``--system-site-packages`` is deliberately *not* how that inheritance is
    done. It inherits the base *interpreter's* site-packages, and under ``uv
    run`` the packages the suite needs live in the project ``.venv`` rather
    than in the interpreter uv built it from — so the flag yields an
    environment with no numpy at all, which the gate in
    ``test_gh879_worker_env_isolation.py`` catches.

    Two variables are set, because the two consumers disagree on where to look:
    ``uv pip install`` reads ``VIRTUAL_ENV``, and the generated Makefile takes
    ``JUST_BUILDIT_PYTHON`` ahead of its ``python3``-off-``PATH`` fallback
    (templates/make/Makefile:18). ``PATH`` itself is left alone on purpose —
    see the note at the end of this function.

    No-ops outside xdist: with one process there is no concurrent writer, and
    paying a venv creation for ``pytest tests/test_cli.py`` would be a tax on
    the common case.
    """
    global _worker_env_root
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return

    tmp = Path(tempfile.gettempdir())
    _reap_stale_worker_envs(tmp)
    try:
        # The pid in the name is what lets a later run reap this directory
        # if this worker never reaches `pytest_unconfigure` (killed, OOM).
        root = Path(tempfile.mkdtemp(prefix=f"jm-{worker}-env-{os.getpid()}-"))
    except OSError as exc:
        raise _setup_error(worker, f"mkdtemp in {tmp}: {exc}") from exc
    venv = root / "venv"
    # `--seed` because the gh-824 header guard in every generated Makefile
    # shells out to `$(PYTHON) -m pip install --force-reinstall numpy`, and a
    # bare uv venv ships no pip at all — 16 × `No module named pip` on CI.
    proc = subprocess.run(
        ["uv", "venv", "--seed", "--python", sys.executable, str(venv)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        raise _setup_error(worker, proc.stderr)

    try:
        # Inherit the parent's packages for reading, behind this worker's
        # own.
        site_dirs = sorted(venv.glob("lib/python*/site-packages"))
        if not site_dirs:  # Windows lays it out flat
            site_dirs = sorted(venv.glob("Lib/site-packages"))
        inherited = "\n".join(_inheritable_paths())
        (site_dirs[0] / "_jm_inherited.pth").write_text(
            inherited + "\n", encoding="utf-8"
        )
    except OSError as exc:
        shutil.rmtree(root, ignore_errors=True)
        raise _setup_error(worker, f"writing {venv}: {exc}") from exc

    _worker_env_root = root
    bindir = venv / ("Scripts" if os.name == "nt" else "bin")
    os.environ["VIRTUAL_ENV"] = str(venv)
    os.environ["JUST_BUILDIT_PYTHON"] = str(
        bindir / ("python.exe" if os.name == "nt" else "python")
    )
    # Pin numpy to the parent's release for everything this worker installs.
    #
    # Inheriting the parent's packages through the `.pth` is only half the
    # job: it makes numpy *importable*, but uv resolves against the worker's
    # own site-packages, so the first scaffolded project that depends on
    # numpy installs a fresh copy — and takes the newest release, which
    # shadows the inherited one. The moment upstream publishes, the worker
    # builds the extension against 2.5.2 while `sys.executable` imports it
    # with 2.5.1. That is an ABI split across the compile/import boundary,
    # and it is exactly what `test_worker_env_inherits_the_base_numpy`
    # exists to catch — it caught this.
    #
    # A constraint rather than an up-front `uv pip install numpy==...`,
    # because it also binds anything pulled in transitively later, and costs
    # nothing when no install happens.
    try:
        from importlib.metadata import version as _dist_version

        _numpy_version = _dist_version("numpy")
    except Exception:  # numpy absent from the parent: nothing to pin to
        _numpy_version = ""
    if _numpy_version:
        constraint = root / "constraints.txt"
        try:
            constraint.write_text(
                f"numpy=={_numpy_version}\n", encoding="utf-8"
            )
        except OSError as exc:
            raise _setup_error(worker, f"writing {constraint}: {exc}") from exc
        os.environ["UV_CONSTRAINT"] = str(constraint)
    # PATH is deliberately NOT prepended with this venv's bin, though the
    # generated Makefile does fall back to `python3` off PATH. It does not need
    # to: `JUST_BUILDIT_PYTHON` is the first arm of that `$(or ...)`
    # (templates/make/Makefile:18) and already wins.
    #
    # Prepending it actively breaks things, which cost a red CI to learn. Some
    # tests invoke `cmake` directly rather than through the generated Makefile,
    # so they pass no `-DPython3_EXECUTABLE` and CMake discovers an interpreter
    # itself — off PATH. It would then find this venv, whose numpy is reachable
    # only through the `.pth` above, and `FindPython3`'s NumPy probe does not
    # honour that: `Could NOT find Python3 (missing: Python3_NumPy_INCLUDE_DIRS
    # NumPy)`. Isolation belongs on the writes, not on interpreter discovery
    # for every subprocess in the suite.
    # NOTE for anyone tempted to bridge the other direction with PYTHONPATH —
    # letting the *parent* interpreter see what the worker installed — so that
    # the e2e tests can keep running `sys.executable`. It does not work, and it
    # fails in a way that looks like it should: PYTHONPATH entries go on
    # sys.path, but `.pth` files are only executed for directories `site`
    # registers, and an editable install IS a `.pth` redirect. The directory
    # lands on sys.path with the redirect never run, so the import still fails.
    # The tests use `JUST_BUILDIT_PYTHON` instead: a project installed into an
    # environment is run by that environment's interpreter.


@pytest.fixture(autouse=True)
def _cmake_configures_name_their_interpreter(monkeypatch):
    """Fail any test whose cmake configure names no interpreter (gh-2857).

    Every process a test starts goes through ``subprocess.Popen`` --
    ``run``, ``call``, ``check_call`` and ``check_output`` construct one --
    so the argv is checked as it runs (`_cmake.unnamed_configure`), whatever
    the test's spelling. ``pytest.fail`` raises an ``OutcomeException``,
    not an ``Exception``, so a test's own ``except Exception`` cannot
    swallow it. Per test and stateless, so xdist workers cannot double
    count.
    """
    from _cmake import unnamed_configure

    real = subprocess.Popen

    class _Guarded(real):  # type: ignore[misc, valid-type]
        def __init__(self, args, *a, **kw):
            argv = unnamed_configure(args, kw.get("cwd"))
            if argv is not None:
                pytest.fail(
                    "a cmake configure names no interpreter, so CMake builds"
                    " the extension for the first python3 on PATH (gh-2857):"
                    f" pass `_cmake.PYTHON3_ARG` (or -DBUILD_PYTHON=OFF):"
                    f" {argv}",
                    pytrace=False,
                )
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", _Guarded)


def pytest_configure(config):
    """Refuse a CI session on the wrong Python, then isolate this worker.

    gh-2172: a CI leg names its Python only as ``UV_PYTHON``. Asked here,
    at the start of every session, so every leg that runs jm's tests is
    checked -- the unit suite, the examples, the compiled tests, the
    goldens -- not only the one that collects a particular file. Off CI a
    developer's own ``UV_PYTHON`` is theirs to set, so only a CI run is
    refused here; ``tests/test_gh2172_ci_leg_python.py`` asks it anywhere.
    """
    from _legpython import mismatch

    if os.environ.get("GITHUB_ACTIONS") == "true":
        why = mismatch(os.environ, tuple(sys.version_info))
        if why:
            raise pytest.UsageError(why)
    _provision_worker_env()


def pytest_unconfigure(config):
    """Remove what `pytest_configure` created, if anything."""
    if _worker_env_root is not None:
        shutil.rmtree(_worker_env_root, ignore_errors=True)


# Workers that died, as ``(id, error)``, collected on the controller.
_crashed_workers: "list[tuple[str, str]]" = []


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    """Record an xdist worker that went down with an error.

    A worker that cannot set up -- its `pytest_configure` raised, as
    `_provision_worker_env` does on ENOSPC -- dies before it is handed a
    test. xdist replaces it, each replacement dies the same way, and once
    the restarts run out the controller reports ``no tests ran`` and exits
    **0**: a suite that ran nothing read as green. A worker going down with
    an error is a broken run whatever else happened, so
    `pytest_sessionfinish` fails it.
    """
    if error:
        _crashed_workers.append((str(node.gateway.id), str(error)))


# The fixture that runs `uv pip install -e .` in a scaffolded temp project.
# Keying on the fixture rather than on module names keeps this tied to the
# actual dependency: a new e2e test file is covered the moment it requests it.
_UV_INSTALL_FIXTURE = "installed"


def _uses_uv_install(items) -> bool:
    """True when the collected set includes a test that shells out to uv."""
    return any(
        _UV_INSTALL_FIXTURE in getattr(it, "fixturenames", ()) for it in items
    )


def pytest_collection_modifyitems(session, config, items):
    """Fail the whole run early, with an explanation, rather than let a missing
    dependency masquerade as ~8 unrelated TOML round-trip failures."""
    if not os.environ.get("VIRTUAL_ENV") and _uses_uv_install(items):
        raise pytest.UsageError(
            "VIRTUAL_ENV is not set, and this run collects end-to-end example "
            "tests that shell out to `uv pip install -e .` in a temp "
            "project.\n"
            "uv resolves the target environment from VIRTUAL_ENV or a .venv "
            "beside the working directory; a scaffolded temp project has "
            "neither, so every one of those tests would error in fixture "
            "setup with 'No virtual environment found' — which reads as a "
            "broken machine rather than a wrong invocation.\n"
            "Running `.venv/bin/pytest` directly is what does this: the "
            "interpreter resolves sys.prefix to the venv via pyvenv.cfg, so "
            "imports work, but the variable is never exported.\n"
            "Fix: `source .venv/bin/activate` (then `pytest`), or `uv run "
            "pytest` — both set it. Selecting only unit tests avoids the "
            "check entirely."
        )
    missing = _missing_runtime_deps()
    if not missing:
        return
    names = ", ".join(missing)
    raise pytest.UsageError(
        f"just-makeit's runtime dependencies are missing from this "
        f"environment: {names}.\n"
        f"The suite imports just_makeit from src/, so its dependencies must be "
        f"installed even though the package is not.\n"
        f"Without tomlkit the failures are silent and misleading: "
        f"_config._write_doc falls back to _dump, and TOML round-trip tests "
        f"fail on content rather than on the real cause.\n"
        f"Fix: run `make test` (which supplies them), or `make setup` / "
        f"`uv sync --group dev` for a plain `pytest` run."
    )


# gh-744: re-exported, not reimplemented. The examples' own `test.py` files
# assert on signatures too and cannot import from `tests/`, so the one
# implementation lives in the package beside the reflow it inverts.
from just_makeit._pyfmt import flatten_signatures  # noqa: E402,F401


# ── the skip gate (gh-1442) ──────────────────────────────────────────────────
#
# 31 tests in five files were SKIPPED on every CI run and RUN on every
# developer laptop. `PYTEST` uses `uv run --no-project`, so a `skipif` asking
# the live environment about a dev-group tool got the isolated env's answer --
# while an activated `.venv` leaks PATH and imports, so the same command gave
# the opposite answer locally. Among the casualties: the gh-746 gates holding
# generated Python to the column target against ruff itself, and the 23
# stub-conformance tests that compile generated code.
#
# They are on the `PYTEST_EXAMPLES` path now. This is what keeps them there.
#
# An ALLOW-LIST rather than a count: a count says "something changed" and
# makes you go find out what, and it drifts every time a test is added. This
# names what the suite accepts, so a new skip is a reviewable diff and
# everything else is a failure.
#
# The line is not "environmental" -- it is whether a MAINTAINER COULD FIX IT.
# AVX-512 is absent because the host lacks the silicon; ruff is absent because
# nobody put it on PATH. The second is a gate that is not running.
from _compilers import (  # noqa: E402
    ASAN_NO_PRELOAD_MACOS,
    ASAN_NO_PRELOAD_WINDOWS,
    NO_AVX512,
    POSIX_ONLY,
    WINDOWS_CFLAGS_GAP,
    WINDOWS_CONSUMER_GAP,
    WINDOWS_NO_GNU_CC,
)

_ALLOWED_SKIPS = (
    # A ratchet with nothing left to hold. It shrinks to empty by design.
    "got empty parameter set",
    # Hardware. No install fixes this.
    NO_AVX512,
    # jm_simd.h's tiers are compiled and run, so each needs the machine
    # it targets: an x86-64 one for SSE2 and AVX2, an aarch64 one for NEON.
    # No install runs one architecture's tier on the other.
    "tier needs an x86-64 machine",
    "tier needs an aarch64 machine",
    # A different linker, not a missing one: macOS is Apple ld, and the
    # test asserts a GNU ld/lld flag SPELLING. No install changes which
    # linker the platform has.
    "GNU ld/lld flag spelling",
    # gh-1490's behaviour test poisons freed memory through glibc's
    # `malloc.perturb` tunable; macOS's allocator has no equivalent switch.
    # The ordering test beside it runs on every platform, so the fix is
    # still checked there -- only the runtime demonstration is glibc-only.
    "needs glibc's malloc.perturb",
    # gh-2346's ASan demonstrations: macOS resolves Apple clang's runtime,
    # but preloading it into the runner's interpreter does not take
    # ("Interceptors are not working", just-makeit#2353 run 40042035671).
    # The behaviour tests beside them run on macOS, so the fix is still
    # checked there. A missing runtime on Linux is not allowed. Windows
    # has no preload at all (gh-2294).
    ASAN_NO_PRELOAD_MACOS,
    ASAN_NO_PRELOAD_WINDOWS,
    # gh-1570: the Windows examples leg runs every PROJECT_ENV_TESTS file.
    # A consumer route not yet ported to the MSVC ABI (a filed gap, named
    # in the reason), and a subject with no Windows counterpart.
    WINDOWS_CONSUMER_GAP,
    # gh-2751: a composer get_fn driver's cmake flags, a filed gap.
    WINDOWS_CFLAGS_GAP,
    # gh-2754: the extern "C" linkage sweep lists functions with GNU gcc's
    # -aux-info; the clang-cl leg has no GNU gcc. The rule is C++'s, not
    # the platform's, and Linux and macOS run it.
    WINDOWS_NO_GNU_CC,
    POSIX_ONLY,
    # test_gh1374's own subject: it must contain the spelling it hunts for.
    "names the pattern in order to detect it",
    "allow-listed:",
)


def _skip_reason(report) -> str:
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])
    return str(longrepr or "")


def pytest_sessionfinish(session, exitstatus):
    """Fail the run on any skip the suite has not agreed to.

    A skip reports the same green as a pass, which is how a check stops
    running without anyone noticing -- for months, in the case this gate
    was written for.
    """
    if hasattr(session.config, "workerinput"):
        return  # an xdist worker; the controller has the aggregate
    if _crashed_workers:
        print("\nERROR: xdist workers went down, so this run is broken:\n")
        for wid, error in _crashed_workers:
            lines = error.strip().splitlines() or [""]
            # The ENOSPC sentence when `_setup_error` wrote one, else the
            # error's last line (its exception).
            line = next((ln for ln in lines if "ENOSPC" in ln), lines[-1])
            print(f"  [{wid}] {line}")
        print(
            "\nA worker that crashes is a broken run, whatever the rest "
            "report: one that\ndies in setup is handed no tests at all, "
            "and xdist can then report what is\nleft as a pass. Each "
            "worker's full error is above.\n"
        )
        session.exitstatus = pytest.ExitCode.INTERNAL_ERROR
        return
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr is None:
        return
    unexpected = [
        (r.nodeid, _skip_reason(r))
        for r in tr.stats.get("skipped", [])
        if not any(a in _skip_reason(r) for a in _ALLOWED_SKIPS)
    ]
    if not unexpected:
        return
    print("\nERROR: skips this suite has not agreed to:\n")
    for nodeid, reason in sorted(set(unexpected)):
        print(f"  {nodeid}\n      {reason}")
    print(
        "\nA skip reports the same green as a pass. If the tool is one a\n"
        "maintainer can install, the test belongs on the PROJECT_ENV_TESTS\n"
        "path (Makefile) where the dev group is visible -- not behind a\n"
        "skipif. If the skip is genuinely unfixable (hardware, an empty\n"
        "ratchet), add it to _ALLOWED_SKIPS in tests/conftest.py with the\n"
        "reason why.\n"
    )
    session.exitstatus = 1
