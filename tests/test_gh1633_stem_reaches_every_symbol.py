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

import re

import pytest

import _csym_fixtures as FX
from just_makeit import _config as C
from just_makeit import _csym
from just_makeit._docsync import _code_mask

MARK = "zz_"

#: What jm derives from a component's stem (gh-1591's inventory).
_LIFECYCLE = (
    "create",
    "destroy",
    "reset",
    "step",
    "steps",
    "step_batch",
    "state_t",
    "state_ptr",
    "state_adopt",
    "state_bytes",
    "get_state",
    "set_state",
)


def _override(owner, name):
    return MARK + name


def _derived(cfg: dict) -> "list[re.Pattern]":
    """The patterns an UNPREFIXED derived symbol of *cfg*'s names matches."""
    comps = set(C.components(cfg))
    for mod in C.modules(cfg):
        comps |= set(C.module_objects(cfg, mod))
    pats = []
    for comp in sorted(comps):
        tails = list(_LIFECYCLE)
        tails += [m["name"] for m in C.methods(cfg, comp) if not m.get("fn")]
        for p in C.properties(cfg, comp):
            tails += [f"get_{p['name']}", f"set_{p['name']}"]
        pats.append(
            re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(comp)}_"
                rf"(?:{'|'.join(map(re.escape, tails))})(?![A-Za-z0-9_])"
            )
        )
        pats.append(
            re.compile(rf"(?<![A-Za-z0-9_]){re.escape(comp.upper())}_CORE_H\b")
        )
    for mod in C.modules(cfg):
        for fn in C.module_functions(cfg, mod):
            pats.append(
                re.compile(rf"(?<![A-Za-z0-9_]){re.escape(fn['name'])}\s*\(")
            )
    return pats


def _stripped(data: bytes) -> bytes:
    return data.replace(b"zz_", b"").replace(b"ZZ_", b"")


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
            if _stripped(b[rel]) != a[rel]:
                bad.append(f"{row}/{rel}")
    assert bad == [], (
        "under a stem override these files differ in more than the stem -- "
        "something looked up by the raw name, or derived a name the stem "
        "did not reach:\n" + "\n".join(bad)
    )


def test_no_derived_symbol_escapes_the_stem(trees):
    _roots, over = trees
    bad = []
    for row, root in over.items():
        pats = _derived(C.load(root))
        for path in sorted((root / "native").rglob("*")):
            if path.suffix not in (".c", ".h"):
                continue
            text = path.read_text(encoding="utf-8")
            code = _code_mask(text)
            lines = text.splitlines()
            for pat in pats:
                for m in pat.finditer(code):
                    n = code.count("\n", 0, m.start())
                    bad.append(
                        f"{row}/{path.relative_to(root).as_posix()}:"
                        f"{n + 1}: {m.group(0)}  | {lines[n].strip()}"
                    )
    assert bad == [], (
        "a C identifier jm derives from a name, spelled without `_csym` "
        f"(gh-1591): under stem() -> {MARK!r} + name these kept the raw "
        "name:\n" + "\n".join(sorted(set(bad)))
    )
