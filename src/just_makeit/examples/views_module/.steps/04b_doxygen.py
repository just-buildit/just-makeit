"""Enrich the sacred ``acc_core.h`` header with Doxygen so the generated
``bank.pyi`` carries a rich class summary and a runnable doctest.

The header is the single source of truth for documentation: ``jm`` parses these
``/** ... */`` comments and turns them into numpy-style Python docstrings. A
``@code`` block on a *named method* becomes a runnable doctest that CI executes
against the built extension (``pytest --doctest-glob='*.pyi'``). Run this after
``step()`` and the view's constructor are implemented; a follow-up ``jm apply``
re-derives the glue (``.pyi`` included) from the edited header.

View-specific note (verified empirically):

  - The *parent* ``Acc`` class summary derives from ``acc_create``'s ``@brief``.
  - The *view* ``SeededAcc`` summary does NOT: the stub generator keys the
    class summary off ``<obj>_create``, and the view shares ``acc`` (there is
    no ``seededacc_create``), so its summary stays the generic
    ``"SeededAcc component."`` default — the header cannot enrich it.
  - Field-backed property docstrings (``depth``, ``runs``) come from the
    manifest ``jm property --doc`` value, not a header ``@brief`` — those
    getters are auto-implemented inline and have no header declaration.

So the header-authored enrichment here is: a real ``@brief`` on ``acc_create``
(the ``Acc`` summary) and a ``@brief`` / ``@return`` / ``@code`` doctest on the
``acc_total`` named method.

Usage:  python3 .steps/04b_doxygen.py     # run from the project root
"""

from __future__ import annotations

import pathlib
import re
import sys

OBJ = "acc"

# The class summary for the parent object, injected over jm's scaffold brief.
CREATE_BRIEF = "Create an empty accumulator (running sum), zeroed."

# Doxygen blocks keyed by the C declaration they sit above. `acc_total`'s
# `@code` block becomes a runnable Examples doctest. `step()` returns the
# running sum (this component's step is not void), so each `a.step(...)` line
# echoes that sum — the expected-output lines reflect it.
BLOCKS = [
    (
        "double acc_total(",
        "/**\n"
        " * @brief Return the running sum without mutating the accumulator.\n"
        " * @return The sum of every sample stepped so far.\n"
        " * @code\n"
        " * >>> from acc_bank.bank import Acc\n"
        " * >>> a = Acc(sum=0.0)\n"
        " * >>> a.step(1.0)\n"
        " * 1.0\n"
        " * >>> a.step(2.5)\n"
        " * 3.5\n"
        " * >>> a.total()\n"
        " * 3.5\n"
        " * @endcode\n"
        " */\n",
    ),
]


def main() -> None:
    header = pathlib.Path("native/inc") / OBJ / f"{OBJ}_core.h"
    text = header.read_text(encoding="utf-8")

    # Replace jm's trivial scaffold brief on acc_create with a real summary.
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

    # Prepend each method's Doxygen block above its bare declaration.
    for decl, block in BLOCKS:
        if decl not in text:
            print(f"ERROR: declaration not found: {decl!r}", file=sys.stderr)
            sys.exit(1)
        text = text.replace(decl, block + decl, 1)

    header.write_text(text, encoding="utf-8")
    print(f"enriched {header}")


if __name__ == "__main__":
    main()
