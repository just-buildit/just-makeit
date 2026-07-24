"""Enrich the sacred ``<obj>_core.h`` headers so the shared module ``.pyi``
carries a real class summary instead of the generic ``"<Type> component."``
fallback.

The header is the single source of truth for documentation: ``jm`` parses the
``/** ... */`` comment on ``<obj>_create`` and turns its ``@brief`` into the
Python class summary. These two objects are plain ``--state`` filters with no
custom ``jm method``, so this is light enrichment — a one-sentence summary per
class, nothing more (the generic per-``@param`` docs stay untouched). Run this
after the objects are declared; a follow-up ``jm apply`` regenerates the
``.pyi`` from these comments.

Usage:  python3 .steps/04b_doxygen.py     # run from the project root
"""

from __future__ import annotations

import pathlib
import sys

# Per-object class summary, replacing jm's trivial scaffold @brief on
# <obj>_create. Summaries only — params stay generic for these state objects.
SUMMARIES = {
    "fir": (
        "Create a windowed-sinc FIR filter: a 16-tap complex delay line "
        "convolved with real coefficients."
    ),
    "biquad": (
        "Create an RBJ biquad: a second-order IIR section evaluated in "
        "Direct-Form II transposed."
    ),
}


def _enrich(obj: str, summary: str) -> None:
    header = pathlib.Path("native/inc") / obj / f"{obj}_core.h"
    text = header.read_text(encoding="utf-8")

    scaffold = f" * @brief Create a {obj} instance."
    if scaffold not in text:
        print(
            f"ERROR: {obj}_create scaffold brief not found — already patched?",
            file=sys.stderr,
        )
        sys.exit(1)
    text = text.replace(scaffold, f" * @brief {summary}", 1)

    header.write_text(text, encoding="utf-8")
    print(f"enriched {header}")


def main() -> None:
    for obj, summary in SUMMARIES.items():
        _enrich(obj, summary)


if __name__ == "__main__":
    main()
