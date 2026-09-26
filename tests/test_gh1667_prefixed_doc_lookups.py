"""gh-1667: a `c_prefix` changes the C names jm reads docs by, not the docs.

Every Python docstring jm writes -- both `.pyi` faces and the runtime
`__doc__` -- is looked up in the sacred header by a C name: ``<stem>_<method>``,
``<stem>_get_<prop>``, ``<stem>_state_t``'s field comments, the gh-761
``<stem>_<method>_max_out`` arity. Under ``[project] c_prefix`` the header
declares ``dp_ddc_execute``; a lookup that spells the key from the RAW name
(``ddc_execute``) finds nothing and the member silently falls back to its name
stub ("Norm freq."). doppler measured 25 stub descriptions and 27 properties
lost that way after adopting ``c_prefix = "dp"``, every one on a view: the
view re-key in `_stubs._view_doc_blocks` rewrote ``f"{obj}_"`` to
``f"{synth}_"`` on both ends.

The gh-1633 / gh-1591 oracles render the same broad fixture with and without
a stem and require the prefix to be the only difference -- but on SCAFFOLD
headers, whose prose jm already knows to ignore, so a doc lookup that missed
changed nothing they could see. This is the same oracle over headers that
carry authored prose on every block, every undocumented prototype and every
struct field, plus one state-only ``_max_out``: a lookup anywhere in the
class that builds its key without `_csym` renders a different docstring, and
the tree comparison names the file. The fixture rows are the gh-1633 table
plus one that puts every doc-bearing member shape behind a view.

GATE: under a `c_prefix`, every docstring jm derives from the sacred header
      -- stub and runtime, object and view, method, property, struct field
      and `_max_out` arity -- is identical to the unprefixed render.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import _csym_fixtures as FX
from _jmrun import run_cli

PREFIX = FX.MARK.rstrip("_")

#: The row that carries every doc-bearing member shape a view inherits:
#: a getter-backed property, a field-backed one with no getter (doppler's
#: ``FIR.num_taps`` shape), a plain method, a `variable_output` method and
#: one whose ``_max_out`` the header makes state-only (gh-761).
DOCS = {
    "docs": (
        ("dd",),
        [
            (
                "object",
                "fir",
                "--state",
                "gain:double:1.0",
                "--arg-type",
                "float",
                "--return-type",
                "float",
            ),
            ("property", "fir", "num_taps", "--type", "size_t", "--field"),
            ("property", "fir", "level", "--type", "double"),
            (
                "method",
                "fir",
                "drain",
                "--arg-type",
                "float[]",
                "--return-type",
                "float",
                "--variable-output",
            ),
            ("module", "m"),
            ("object", "o", "--module", "m", "--state", "g:double:1.0"),
            (
                "property",
                "o",
                "taps",
                "--module",
                "m",
                "--type",
                "size_t",
                "--field",
            ),
            ("property", "o", "lvl", "--module", "m", "--type", "double"),
            (
                "method",
                "o",
                "scale",
                "--module",
                "m",
                "--arg-type",
                "double",
                "--return-type",
                "double",
            ),
            (
                "method",
                "o",
                "drain",
                "--module",
                "m",
                "--arg-type",
                "float[]",
                "--return-type",
                "float",
                "--variable-output",
            ),
            ("view", "o", "Peek", "--module", "m", "--create-fn", "o_open"),
        ],
    ),
}

PROJECTS = {**FX.PROJECTS, **DOCS}

_BRIEF = re.compile(r"(@brief [^\n]*)")
# A struct field: an indented declaration with no call, initialiser or
# comment -- the only such lines in a scaffolded `_core.h` are fields.
_FIELD = re.compile(
    r"^(    [A-Za-z_][\w \t*]*?\b(\w+)(\s*\[[^\]\n]*\])?;)$", re.M
)
# A one-line prototype at column 0.
_PROTO = re.compile(r"^[A-Za-z_][^\n;{}()]*\b\w+\([^;{}]*\);$", re.M)
# gh-761: `drain`'s capacity query takes the state alone.
_DRAIN_MAX_OUT = re.compile(
    r"(\b\w+_drain_max_out\([^,)]*?)\s*,\s*size_t\s+\w+\)"
)


def _undocumented(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        if _PROTO.match(line.rstrip("\n")):
            prev = next((p for p in reversed(out) if p.strip()), "")
            if not prev.rstrip().endswith("*/"):
                out.append("/** @brief Authored undocumented. */\n")
        out.append(line)
    return "".join(out)


def _author_docs(root: Path) -> None:
    """Write authored prose into every sacred ``_core.h`` under *root*: the
    same edit in a prefixed and an unprefixed tree, so the two stay equal
    but for the stem."""
    for h in sorted((root / "native" / "inc").rglob("*_core.h")):
        text = h.read_text(encoding="utf-8")
        text = _BRIEF.sub(r"\1 Authored.", text)
        text = _FIELD.sub(r"\1 /**< Authored field \2. */", text)
        text = _DRAIN_MAX_OUT.sub(r"\1)", text)
        h.write_text(_undocumented(text), encoding="utf-8")
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, f"{root.name}: {r.stdout}{r.stderr}"


@pytest.fixture(scope="module")
def trees(tmp_path_factory):
    out = []
    for tag, extra in (("plain", ()), ("pre", ("--c-prefix", PREFIX))):
        roots = FX.build(
            tmp_path_factory.mktemp(tag), *extra, projects=PROJECTS
        )
        for root in roots.values():
            _author_docs(root)
        out.append(roots)
    return tuple(out)


def _view_section(pyi: str, cls: str) -> str:
    start = pyi.index(f"class {cls}")
    nxt = pyi.find("\nclass ", start + 1)
    return pyi[start : nxt if nxt != -1 else len(pyi)]


def _member(section: str, name: str) -> str:
    """The text from ``def <name>`` to the next ``def``."""
    start = section.index(f"def {name}(")
    nxt = section.find("\n    def ", start + 1)
    nxt = section.find("\n    @", start + 1) if nxt == -1 else nxt
    return section[start : nxt if nxt != -1 else len(section)]


@pytest.mark.parametrize("tag", [0, 1], ids=["plain", "prefixed"])
def test_the_authored_prose_reaches_the_view(trees, tag):
    """Armed: the fixture's prose is what each face derives, in BOTH trees,
    so the comparison below compares documentation, not two name stubs."""
    root = trees[tag]["docs"]
    pyi = (root / "src" / "dd" / "m" / "m.pyi").read_text(encoding="utf-8")
    peek = _view_section(pyi, "Peek")
    for member in ("reset", "scale", "drain", "lvl"):
        assert "Authored" in _member(peek, member), (member, peek)
    assert "Authored field taps." in _member(peek, "taps"), peek
    # gh-761: a state-only `_max_out` takes no count in the view either.
    assert "def drain_max_out(self) -> int" in peek, peek
    runtime = (root / "native" / "src" / "m" / "m_ext_peek.c").read_text(
        encoding="utf-8"
    )
    assert "Authored field taps." in runtime


def test_prefixed_docstrings_equal_the_unprefixed(trees):
    """The stem is the only difference once the headers carry prose."""
    plain, pre = trees
    bad = []
    for row in plain:
        a, b = FX.tree(plain[row]), FX.tree(pre[row])
        for rel in sorted(a.keys() & b.keys() - {"just-makeit.toml"}):
            if FX.stripped(b[rel]) != FX.stripped(a[rel]):
                bad.append(f"{row}/{rel}")
    assert bad == [], (
        "with c_prefix these files differ in more than the prefix once the "
        "sacred headers carry prose -- a doc lookup keyed its C name without "
        "`_csym` (gh-1667):\n" + "\n".join(bad)
    )
