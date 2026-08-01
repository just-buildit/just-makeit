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
import sys
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
