"""gh-1633: every C symbol jm derives from a name takes its stem from `_csym`.

gh-1591's phase 2 turns `[project] c_prefix` on by changing ONE function,
`_csym.stem`. That is only true if nothing else spells a derived symbol by
hand: a site that writes ``f"{comp}_create"`` keeps the unprefixed name while
the header declares the prefixed one, and the project fails to link.

`tests/test_gh1591_one_symbol_stem.py` ratchets those sites by reading jm's
source for a suffix vocabulary, so it cannot see a suffix it does not know.
This is the oracle that needs no vocabulary of jm's source: build a broad
tree (`tests/_csym_fixtures.py`) with `stem()` overridden to return
``"zz_" + name`` -- what phase 2 will do with a prefix -- and read what jm
WROTE.

Three questions, because they fail differently:

- **(a) the file set does not move.** A symbol stem is not a file stem:
  ``<comp>_core.c``, the ``<comp>_core`` target and the header directory stay
  the component's name.
- **(b) the stem is the only difference.** Every file, with ``zz_`` /
  ``ZZ_`` removed, is byte-identical to the unoverridden render. A site that
  looks something up by the RAW name -- a doc block keyed ``fir_create`` --
  finds nothing under the override and renders differently.
- **(c) no derived symbol escapes the stem.** The generated C and headers,
  with comments and string literals masked, name no ``<comp>_create``,
  ``<comp>_<method>``, ``<COMP>_CORE_H``, bare module function, ... that the
  override did not reach.

GATE: under a stem override, every derived C identifier jm generates carries
      the override, nothing else changes, and no file is renamed.
"""

from __future__ import annotations

import pytest

import _csym_fixtures as FX
from just_makeit import _csym

MARK = FX.MARK


def _override(owner, name):
    return MARK + name


@pytest.fixture(scope="module")
def trees(tmp_path_factory):
    plain = tmp_path_factory.mktemp("plain")
    roots = FX.build(plain)
    mp = pytest.MonkeyPatch()
    mp.setattr(_csym, "stem", _override)
    try:
        over = tmp_path_factory.mktemp("over")
        over_roots = FX.build(over)
    finally:
        mp.undo()
    return roots, over_roots


def test_no_file_is_renamed(trees):
    roots, over = trees
    for row in roots:
        assert sorted(FX.tree(over[row])) == sorted(FX.tree(roots[row])), row


def test_the_stem_is_the_only_difference(trees):
    roots, over = trees
    bad = []
    for row in roots:
        a, b = FX.tree(roots[row]), FX.tree(over[row])
        for rel in sorted(a.keys() & b.keys()):
            if FX.stripped(b[rel]) != a[rel]:
                bad.append(f"{row}/{rel}")
    assert bad == [], (
        "under a stem override these files differ in more than the stem -- "
        "something looked up by the raw name, or derived a name the stem "
        "did not reach:\n" + "\n".join(bad)
    )


def test_no_derived_symbol_escapes_the_stem(trees):
    _roots, over = trees
    bad = [
        f"{row}/{line}"
        for row, root in over.items()
        for line in FX.escapes(root)
    ]
    assert bad == [], (
        "a C identifier jm derives from a name, spelled without `_csym` "
        f"(gh-1591): under stem() -> {MARK!r} + name these kept the raw "
        "name:\n" + "\n".join(bad)
    )
