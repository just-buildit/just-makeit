"""Enrich the sacred ``<obj>_core.h`` headers with Doxygen so the generated
``conv.pyi`` carries real class summaries and rich property docstrings.

The header is the single source of truth for documentation: ``jm`` parses these
``/** ... */`` comments and turns them into numpy-style Python docstrings. Run
this after the C kernels are implemented (``04_patch_writer.py`` /
``04_patch_reader.py``); a follow-up ``jm apply`` re-derives the glue (``.pyi``
included) from the edited headers.

Which source flows where — verified empirically (mirrors the views_module
finding):

  - **Class summary** comes from ``<obj>_create``'s ``@brief``. Both converter
    objects own a real ``<obj>_create`` (they are distinct module objects, not
    views), so a header ``@brief`` enriches each class summary.
  - **Field-backed** property docstrings (``samples_written``, ``samples_read``)
    come from the manifest ``jm property --doc`` value, NOT a header ``@brief``:
    a ``--field`` getter is auto-implemented inline, so there is no header
    declaration to annotate. Those docs are set in ``test.py`` / step 3.
  - A **computed** property's getter has a real declaration, so it CAN carry a
    header ``@brief``. ``eof`` is computed (``q15_to_cf32_get_eof``), so its
    docstring is enriched here.

iqfile exposes no named ``jm method`` (only ``step``/``steps`` plus
properties), and a property getter's docstring renders as prose only — a
``@code`` block on a property is not turned into a runnable doctest. So this
example ships rich class summaries and property docs, but no runnable method
doctest (like the other "light" examples).

Usage:  python3 .steps/04b_doxygen.py     # run from the project root
"""

from __future__ import annotations

import pathlib
import re
import sys

# Class summaries, injected over jm's scaffold brief on each <obj>_create.
CREATE_BRIEFS = {
    "cf32_to_q15": (
        "Pack complex float samples into interleaved q15 (int16 I/Q)."
    ),
    "q15_to_cf32": (
        "Read interleaved q15 (int16 I/Q) samples as complex float."
    ),
}

# Computed-property getters keyed by the C declaration they sit above. Their
# @brief becomes the property __doc__ (field-backed props are documented via
# the manifest instead — see the module docstring).
GETTER_BLOCKS = {
    "q15_to_cf32": [
        (
            "int32_t q15_to_cf32_get_eof(",
            "/**\n"
            " * @brief True (1) once the backing file descriptor is"
            " exhausted.\n"
            " */\n",
        ),
    ],
}


def _enrich(obj: str) -> None:
    header = pathlib.Path("native/inc") / obj / f"{obj}_core.h"
    text = header.read_text(encoding="utf-8")

    # Replace jm's trivial scaffold brief on <obj>_create with a real summary.
    scaffold_re = re.compile(
        rf"/\*\*\n \* @brief Create a {obj} instance\..*?"
        rf"(?={obj}_state_t \*{obj}_create)",
        re.DOTALL,
    )
    new_create = f"/**\n * @brief {CREATE_BRIEFS[obj]}\n */\n"
    text, n = scaffold_re.subn(new_create, text, count=1)
    if n != 1:
        print(f"ERROR: {obj}_create scaffold brief not found", file=sys.stderr)
        sys.exit(1)

    # Prepend each computed getter's Doxygen block above its declaration.
    for decl, block in GETTER_BLOCKS.get(obj, []):
        if decl not in text:
            print(f"ERROR: declaration not found: {decl!r}", file=sys.stderr)
            sys.exit(1)
        text = text.replace(decl, block + decl, 1)

    header.write_text(text, encoding="utf-8")
    print(f"enriched {header}")


def main() -> None:
    for obj in CREATE_BRIEFS:
        _enrich(obj)


if __name__ == "__main__":
    main()
