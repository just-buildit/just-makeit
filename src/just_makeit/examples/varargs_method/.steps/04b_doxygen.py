"""Enrich the sacred ``filter_core.h`` with Doxygen so the generated ``.pyi``
carries rich docstrings and a runnable doctest.

The header is the single source of truth for documentation: ``jm`` parses these
``/** ... */`` comments and turns them into numpy-style Python docstrings. A
``@code`` block on a *typed named method* becomes a runnable doctest that CI
executes against the built extension (``pytest --doctest-glob='*.pyi'``).

Note the split of responsibilities here (varargs vs. typed):

* ``configure()`` is a ``--varargs`` method — its binding lives in the sacred
  ``filter_configure_core.c`` (a ``PyObject *`` file), *not* in the header, so
  ``jm`` cannot attribute a Doxygen block to it and its ``.pyi`` stub stays the
  flexible ``(*args, **kwargs) -> Any``. That is the trade-off of ``--varargs``:
  an open-ended signature, but no header-derived docs or doctest.
* ``current_gain()`` is a plain typed method declared *in the header*, so its
  ``@brief``/``@return``/``@code`` flow straight into a numpy-style docstring
  with a runnable ``Examples`` block. The doctest deliberately exercises the
  varargs ``configure()`` too, tying both faces of the object together.

Run this after the methods are declared and their bodies patched; a follow-up
``jm apply`` regenerates the ``.pyi`` from these comments.

Usage:  python3 .steps/04b_doxygen.py     # run from the project root
"""

from __future__ import annotations

import pathlib
import re
import sys

HEADER = pathlib.Path("native/inc/filter/filter_core.h")


def _project_name() -> str:
    """Read ``[project] name`` from just-makeit.toml (the Python package name).

    The doctest imports ``from <package> import Filter``; deriving the name
    here keeps the enrichment correct whatever the project was scaffolded as.
    """
    toml = pathlib.Path("just-makeit.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', toml)
    if not m:
        print("ERROR: [project] name not found in just-makeit.toml")
        sys.exit(1)
    return m.group(1)


# A real one-line @brief on create() replaces jm's trivial scaffold brief and
# becomes the class docstring summary. (@param/@return are kept for C readers;
# gain is a plain state var, so its description is documented generically in the
# .pyi Parameters regardless — the summary is what changes.)
CREATE_BLOCK = (
    "/**\n"
    " * @brief A single-tap gain stage, retunable at runtime via configure().\n"
    " *\n"
    " * step() multiplies each input sample by the current gain; configure()\n"
    " * retunes that gain in place through a flexible **kwargs binding.\n"
    " * @param gain  Initial gain (default: 1.0).\n"
    " * @return Heap-allocated state, or NULL on allocation failure.\n"
    " * @note Caller must call filter_destroy() when done.\n"
    " */\n"
)

# Doxygen for the typed named method. The @code block becomes a runnable
# Examples doctest in the .pyi; its output must match the built extension.
# ``<<PKG>>`` is filled from the project name so the import line is correct
# whatever the project was scaffolded as.
CURRENT_GAIN_BLOCK = (
    "/**\n"
    " * @brief Return the filter's current gain coefficient.\n"
    " *\n"
    " * The typed, self-documenting companion to the flexible varargs\n"
    " * configure(): configure() writes the gain, current_gain() reads it\n"
    " * back.\n"
    " * @return The gain most recently set by the constructor or configure().\n"
    " * @code\n"
    " * >>> from <<PKG>> import Filter\n"
    " * >>> f = Filter(gain=1.0)\n"
    " * >>> f.configure(gain=6.0)\n"
    " * >>> f.current_gain()\n"
    " * 6.0\n"
    " * @endcode\n"
    " */\n"
)

CURRENT_GAIN_DECL = "double filter_current_gain(filter_state_t *state);"


def main() -> None:
    text = HEADER.read_text(encoding="utf-8")

    # 1. Swap the scaffold create() brief for a real one.
    scaffold_re = re.compile(
        r"/\*\*\n \* @brief Create a filter instance\..*?"
        r"(?=filter_state_t \*filter_create)",
        re.DOTALL,
    )
    text, n = scaffold_re.subn(CREATE_BLOCK, text, count=1)
    if n != 1:
        print("ERROR: filter_create scaffold brief not found", file=sys.stderr)
        sys.exit(1)

    # 2. Prepend the Doxygen block above the bare current_gain declaration,
    #    stamping the real package name into the doctest import line.
    if CURRENT_GAIN_DECL not in text:
        print(
            f"ERROR: declaration not found: {CURRENT_GAIN_DECL!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    block = CURRENT_GAIN_BLOCK.replace("<<PKG>>", _project_name())
    text = text.replace(CURRENT_GAIN_DECL, block + CURRENT_GAIN_DECL, 1)

    HEADER.write_text(text, encoding="utf-8")
    print(f"enriched {HEADER}")


if __name__ == "__main__":
    main()
