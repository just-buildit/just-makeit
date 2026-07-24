"""Enrich the sacred ``running_stats_core.h`` header with a real Doxygen
``@brief`` so the generated ``.pyi`` class docstring reads as a sentence.

The header is the single source of truth for documentation: ``jm`` parses the
``/** ... */`` comment on ``running_stats_create()`` and turns its ``@brief``
into the summary line of the Python class docstring. Straight off the scaffold
that summary is the generic ``"RunningStats component."``; replacing the
boilerplate ``@brief`` with a one-line description of what the object *does*
gives the class a proper summary. A follow-up ``jm apply`` re-derives the
``.pyi`` from the edited header.

This is a *light* enrichment: ``running_stats`` exposes only auto-generated
state accessors (``get_mean`` / ``get_min_val`` / ...), no hand-written named
method, so there is nothing to hang a runnable ``@code`` doctest on — the class
summary is the whole win.

Usage:  python3 .steps/07_doxygen.py     # run from the project root
"""

from __future__ import annotations

import pathlib
import re
import sys

OBJ = "running_stats"
# One-line summary of the object, dropped in as create()'s @brief. jm lifts it
# verbatim into the `.pyi` class docstring's summary line.
CREATE_BRIEF = (
    "Streaming mean, variance, and running min/max via Welford's "
    "online algorithm."
)


def _enrich() -> None:
    header = pathlib.Path("native/inc") / OBJ / f"{OBJ}_core.h"
    text = header.read_text(encoding="utf-8")

    # Replace jm's trivial scaffold brief on <obj>_create with a real one. The
    # scaffold block carries boilerplate @param/@return/@note; collapsing it to
    # a bare @brief keeps the enrichment to the class summary alone (the
    # Parameters section of the .pyi still derives from the state fields).
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
    _enrich()
