"""Enrich the ``utils_core.h`` header with Doxygen so the generated ``.pyi``
carries rich docstrings and runnable doctests.

The header is the single source of truth for documentation: ``jm`` parses these
``/** ... */`` comments and turns them into numpy-style Python docstrings. A
``@code`` block on a module-level function becomes a runnable doctest that CI
executes against the built extension (``pytest --doctest-glob='*.pyi'``). Run
this after the stubs are implemented; a follow-up ``jm apply`` regenerates the
``.pyi`` from these comments.

Free functions are an especially good home for doctests: they take plain
scalars and return plain scalars, so the ``>>>`` lines read like ordinary
Python. The two functions here — ``my_utils_linear_to_db`` (regular, its own ``.c``) and
``my_utils_clamp`` (``--inline``, body in the header) — are both enriched the same way:
the Doxygen sits above the declaration ``jm`` derives the stub from.

Usage:  python3 .steps/03_doxygen.py     # run from the project root
"""

from __future__ import annotations

import pathlib
import sys

# Each entry keys the C declaration the Doxygen block sits above. Both live in
# the same module header: my_utils_linear_to_db as a bare prototype, my_utils_clamp as the
# `static inline` definition itself.
BLOCKS = [
    (
        # Regular function: bare prototype in the module header. The body is in
        # native/src/utils/my_utils_linear_to_db.c, but the docstring comes from here.
        "float my_utils_linear_to_db(float x);",
        "/**\n"
        " * @brief Convert linear amplitude to dB (20*log10(x)).\n"
        " * @param x  Linear amplitude (must be > 0).\n"
        " * @return The amplitude expressed in decibels.\n"
        " * @code\n"
        " * >>> from my_utils.utils import my_utils_linear_to_db\n"
        " * >>> my_utils_linear_to_db(1.0)\n"
        " * 0.0\n"
        " * >>> my_utils_linear_to_db(10.0)\n"
        " * 20.0\n"
        " * @endcode\n"
        " */\n",
    ),
    (
        # Inline function: the `static inline` definition IS the declaration.
        # Anchor on the signature line, which is identical before and after
        # 02_patch.py swaps the placeholder body for the real ternary.
        "static inline float\nclamp(float x, float lo, float hi)",
        "/**\n"
        " * @brief Clamp x to the closed interval [lo, hi].\n"
        " * @param x   Value to my_utils_clamp.\n"
        " * @param lo  Lower bound.\n"
        " * @param hi  Upper bound.\n"
        " * @return lo if x < lo, hi if x > hi, otherwise x.\n"
        " * @code\n"
        " * >>> from my_utils.utils import my_utils_clamp\n"
        " * >>> my_utils_clamp(5.0, 0.0, 3.0)\n"
        " * 3.0\n"
        " * >>> my_utils_clamp(-1.0, 0.0, 3.0)\n"
        " * 0.0\n"
        " * >>> my_utils_clamp(1.5, 0.0, 3.0)\n"
        " * 1.5\n"
        " * @endcode\n"
        " */\n",
    ),
]


def main() -> None:
    header = pathlib.Path("native/inc/my_utils/utils/utils_core.h")
    text = header.read_text(encoding="utf-8")

    for decl, block in BLOCKS:
        if decl not in text:
            print(f"ERROR: declaration not found: {decl!r}", file=sys.stderr)
            sys.exit(1)
        text = text.replace(decl, block + decl, 1)

    header.write_text(text, encoding="utf-8")
    print(f"enriched {header}")


if __name__ == "__main__":
    main()
