"""Enrich the sacred ``fir_filter_core.h`` header with a real class summary.

The header is the single source of truth for documentation: ``jm`` parses the
``/** ... */`` comment on ``fir_filter_create`` and turns its ``@brief`` into
the summary line of the generated ``.pyi`` class docstring. Out of the box the
scaffold brief ("Create a fir_filter instance.") is generic, so jm falls back
to a bland "FirFilter component." summary. Replacing it with a real sentence
here makes the stub read like documentation. Run this after ``jm perf`` /
``jm add`` have settled the header; a follow-up ``jm apply`` re-derives the
``.pyi`` from the edited comment.

Usage:  python3 .steps/08_doxygen.py     # run from the project root
"""

from __future__ import annotations

import pathlib
import re
import sys

OBJ = "fir_filter"
CREATE_BRIEF = (
    "A 16-tap real-coefficient FIR filter for complex (I/Q) signals,"
    " with a scalar output gain."
)


def main() -> None:
    header = pathlib.Path("native/inc") / OBJ / f"{OBJ}_core.h"
    text = header.read_text(encoding="utf-8")

    # Replace jm's trivial scaffold brief on <obj>_create with a real one.
    scaffold_re = re.compile(
        rf"/\*\*\n \* @brief Create a {OBJ} instance\..*?"
        rf"(?={OBJ}_state_t \*{OBJ}_create)",
        re.DOTALL,
    )
    new_create = f"/**\n * @brief {CREATE_BRIEF}\n */\n"
    text, n = scaffold_re.subn(new_create, text, count=1)
    if n != 1:
        print(f"ERROR: {OBJ}_create scaffold brief not found", file=sys.stderr)
        sys.exit(1)

    header.write_text(text, encoding="utf-8")
    print(f"enriched {header}")


if __name__ == "__main__":
    main()
