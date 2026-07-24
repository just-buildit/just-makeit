"""Enrich the sacred ``<obj>_core.h`` headers with Doxygen so the generated
``.pyi`` carries rich docstrings and runnable doctests.

The header is the single source of truth for documentation: ``jm`` parses these
``/** ... */`` comments and turns them into numpy-style Python docstrings. A
``@code`` block on a *named method* becomes a runnable doctest that CI executes
against the built extension (``pytest --doctest-glob='*.pyi'``). Run this after
the methods are declared; a follow-up ``jm apply`` regenerates the ``.pyi`` from
these comments.

Usage:  python3 .steps/04b_doxygen.py     # run from the project root
"""

from __future__ import annotations

import pathlib
import re
import sys

# Per-object enrichment: the class summary (on <obj>_create) plus a Doxygen
# block for each named method, keyed by the C declaration it sits above.
ENRICHMENTS = {
    "acc_f32": {
        "create_brief": "Create a 32-bit float accumulator (running sum), zeroed.",
        "blocks": [
            (
                "float acc_f32_get(",
                "/**\n"
                " * @brief Return the current accumulated sum.\n"
                " * @return The running sum of every sample added so far.\n"
                " * @code\n"
                " * >>> from my_acc.accumulator import AccF32\n"
                " * >>> a = AccF32()\n"
                " * >>> a.step(1.0); a.step(2.0); a.step(3.0)\n"
                " * >>> a.get()\n"
                " * 6.0\n"
                " * @endcode\n"
                " */\n",
            ),
            (
                "float acc_f32_dump(",
                "/**\n"
                " * @brief Return the accumulated sum and reset it to zero.\n"
                " * @return The sum accumulated since the last reset or dump.\n"
                " */\n",
            ),
            (
                "void acc_f32_madd(",
                "/**\n"
                " * @brief Multiply-accumulate: add the weighted sum"
                " sum(x[i]*h[i]).\n"
                " * @param x  Input samples.\n"
                " * @param h  Per-sample weights (same length as x).\n"
                " * @code\n"
                " * >>> import numpy as np\n"
                " * >>> from my_acc.accumulator import AccF32\n"
                " * >>> a = AccF32()\n"
                " * >>> a.madd(np.array([1, 2, 3, 4], np.float32),"
                " np.array([0.25] * 4, np.float32))\n"
                " * >>> a.get()\n"
                " * 2.5\n"
                " * @endcode\n"
                " */\n",
            ),
        ],
    },
    "acc_cf64": {
        "create_brief": "Create a complex128 accumulator (running sum), zeroed.",
        "blocks": [
            (
                "double complex acc_cf64_get(",
                "/**\n"
                " * @brief Return the current accumulated complex sum.\n"
                " * @return The running sum of every sample added so far.\n"
                " * @code\n"
                " * >>> from my_acc.accumulator import AccCf64\n"
                " * >>> a = AccCf64()\n"
                " * >>> a.step(1 + 2j); a.step(3 + 4j)\n"
                " * >>> a.get()\n"
                " * (4+6j)\n"
                " * @endcode\n"
                " */\n",
            ),
            (
                "double complex acc_cf64_dump(",
                "/**\n"
                " * @brief Return the accumulated sum and reset it to zero.\n"
                " * @return The sum accumulated since the last reset or dump.\n"
                " */\n",
            ),
        ],
    },
}


def _enrich(obj: str, spec: dict) -> None:
    header = pathlib.Path("native/inc") / obj / f"{obj}_core.h"
    text = header.read_text(encoding="utf-8")

    # Replace jm's trivial scaffold brief on <obj>_create with a real one.
    scaffold_re = re.compile(
        rf"/\*\*\n \* @brief Create a {obj} instance\..*?"
        rf"(?={obj}_state_t \*{obj}_create)",
        re.DOTALL,
    )
    new_create = f"/**\n * @brief {spec['create_brief']}\n */\n"
    text, n = scaffold_re.subn(new_create, text, count=1)
    if n != 1:
        print(f"ERROR: {obj}_create scaffold brief not found", file=sys.stderr)
        sys.exit(1)

    # Prepend each method's Doxygen block above its bare declaration.
    for decl, block in spec["blocks"]:
        if decl not in text:
            print(f"ERROR: declaration not found: {decl!r}", file=sys.stderr)
            sys.exit(1)
        text = text.replace(decl, block + decl, 1)

    header.write_text(text, encoding="utf-8")
    print(f"enriched {header}")


def main() -> None:
    for obj, spec in ENRICHMENTS.items():
        _enrich(obj, spec)


if __name__ == "__main__":
    main()
