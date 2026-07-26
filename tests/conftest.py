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
"""

from __future__ import annotations

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


def pytest_collection_modifyitems(session, config, items):
    """Fail the whole run early, with an explanation, rather than let a missing
    dependency masquerade as ~8 unrelated TOML round-trip failures."""
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
