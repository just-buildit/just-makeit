"""Run the doctests embedded in jm's own source (gh-490).

CLAUDE.md asks for testable examples "wherever possible — Python doctests that
run with `doctest`". They weren't running: `testpaths = ["tests"]` means the
suite never collected anything under `src/`, so every doctest in the package
was decorative. Two had been silently wrong — `_script._warning_flags` was
authored with the wrong quote style and never executed, and
`_render.fn_c_inline_stub` documented output from before unused-param
suppression was added.

A docstring example that nobody runs is worse than no example: it reads as
verified and isn't.

`templates/` and `examples/` are excluded deliberately — template files contain
`<<placeholder>>` tokens and are not valid Python, and the bundled examples are
scaffolding inputs rather than library code.
"""

import doctest
import importlib
import pkgutil

import pytest

import just_makeit

# Subpackages that are data, not library code: templates are not valid Python
# (they carry <<placeholder>> tokens), and examples are scaffolding inputs.
_EXCLUDED = ("just_makeit.templates", "just_makeit.examples")


def _modules():
    """Every importable library module in the package."""
    out = []
    for info in pkgutil.walk_packages(
        just_makeit.__path__, prefix="just_makeit."
    ):
        if info.name.startswith(_EXCLUDED):
            continue
        out.append(info.name)
    return sorted(out)


@pytest.mark.parametrize("modname", _modules())
def test_module_doctests(modname):
    mod = importlib.import_module(modname)
    results = doctest.testmod(mod, verbose=False, report=False)
    assert results.failed == 0, (
        f"{modname}: {results.failed} of {results.attempted} doctests failed. "
        f"Re-run with: python -m pytest --doctest-modules "
        f"{modname.replace('.', '/')}.py -q"
    )
