"""Implement ``ema_quantize`` and enrich the sacred ``ema_core.h`` header with
Doxygen so the generated ``.pyi`` carries a rich docstring and a runnable
doctest.

The header is the single source of truth for documentation: ``jm`` parses the
``/** ... */`` comments and turns them into numpy-style Python docstrings. A
``@brief`` on ``ema_create()`` becomes the class summary, and a ``@code`` block
on a *named method* becomes a runnable doctest that CI executes against the
built extension (``pytest --doctest-glob='*.pyi'``). Run this after the
``quantize`` method is declared; a follow-up ``jm apply`` regenerates the
``.pyi`` from these comments.

Usage:  python3 .steps/06_doxygen.py     # run from the my_arrays project root
"""

from __future__ import annotations

import pathlib
import sys

# The no-op scaffold body jm emits for the quantize stub, and the real
# implementation that replaces it: round a float sample to the nearest
# non-negative integer code (values <= 0 map to 0).
QUANTIZE_STUB = (
    "uint32_t\n"
    "ema_quantize(ema_state_t *state, float x)\n"
    "{\n"
    "    (void)state; (void)x;\n"
    "    return (uint32_t)0U;\n"
    "}"
)
QUANTIZE_IMPL = (
    "uint32_t\n"
    "ema_quantize(ema_state_t *state, float x)\n"
    "{\n"
    "    (void)state;\n"
    "    if (x <= 0.0f)\n"
    "        return 0U;\n"
    "    return (uint32_t)(x + 0.5f);\n"
    "}"
)

# jm's trivial scaffold @brief on ema_create(), replaced with a real summary
# that flows straight into the generated class docstring.
CREATE_SCAFFOLD_BRIEF = "@brief Create a ema instance."
CREATE_BRIEF = "@brief Exponential moving average filter over a float stream."

# The bare quantize declaration in the header, and the Doxygen block prepended
# above it. The @code block becomes a runnable Examples doctest.
QUANTIZE_DECL = "uint32_t ema_quantize("
QUANTIZE_DOXYGEN = (
    "/**\n"
    " * @brief Quantize one sample to an unsigned integer code.\n"
    " * @param x  Input sample; values <= 0 map to 0.\n"
    " * @return Nearest non-negative integer to x (round half up).\n"
    " * @code\n"
    " * >>> from my_arrays import Ema\n"
    " * >>> e = Ema()\n"
    " * >>> e.quantize(3.4)\n"
    " * 3\n"
    " * >>> e.quantize(3.6)\n"
    " * 4\n"
    " * @endcode\n"
    " */\n"
)


def main() -> None:
    core = pathlib.Path("native/src/ema/ema_core.c")
    text = core.read_text(encoding="utf-8")
    if QUANTIZE_STUB not in text:
        print("ERROR: ema_quantize stub not found", file=sys.stderr)
        sys.exit(1)
    core.write_text(text.replace(QUANTIZE_STUB, QUANTIZE_IMPL, 1), "utf-8")
    print(f"implemented {core}")

    header = pathlib.Path("native/inc/ema/ema_core.h")
    text = header.read_text(encoding="utf-8")
    if CREATE_SCAFFOLD_BRIEF not in text:
        print("ERROR: create() scaffold brief not found", file=sys.stderr)
        sys.exit(1)
    text = text.replace(CREATE_SCAFFOLD_BRIEF, CREATE_BRIEF, 1)
    if QUANTIZE_DECL not in text:
        print("ERROR: quantize declaration not found", file=sys.stderr)
        sys.exit(1)
    text = text.replace(QUANTIZE_DECL, QUANTIZE_DOXYGEN + QUANTIZE_DECL, 1)
    header.write_text(text, encoding="utf-8")
    print(f"enriched {header}")


if __name__ == "__main__":
    main()
