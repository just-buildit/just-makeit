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

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

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

    root = Path(tempfile.mkdtemp(prefix=f"jm-{worker}-env-"))
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
        raise pytest.UsageError(
            f"could not create the private environment for xdist worker "
            f"{worker}, so the end-to-end tests would share one environment "
            f"and race on numpy's headers (gh-879):\n{proc.stderr}"
        )

    # Inherit the parent's packages for reading, behind this worker's own.
    site_dirs = sorted(venv.glob("lib/python*/site-packages"))
    if not site_dirs:  # Windows lays it out flat
        site_dirs = sorted(venv.glob("Lib/site-packages"))
    inherited = "\n".join(_inheritable_paths())
    (site_dirs[0] / "_jm_inherited.pth").write_text(inherited + "\n")

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
        constraint.write_text(f"numpy=={_numpy_version}\n")
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


def pytest_configure(config):
    """Isolate this worker before anything collects or builds."""
    _provision_worker_env()


def pytest_unconfigure(config):
    """Remove what `pytest_configure` created, if anything."""
    if _worker_env_root is not None:
        shutil.rmtree(_worker_env_root, ignore_errors=True)


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
