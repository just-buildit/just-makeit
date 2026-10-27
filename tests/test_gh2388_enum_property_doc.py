"""gh-2388: an enum-backed property is documented by its choices.

The sibling of gh-2379. A property bound to an ``[[enum]]`` returns one of the
strings (``Literal["le", "be"]``), but with no manifest ``doc`` its text came
from the C state struct's trailing comment, which describes the ``int``:
``0 little, 1 big.`` beside a return type that is not a number.

`_docstring.property_doc` is the one chain both the binding's getset slot and
the ``.pyi`` read (and ``jm status --docs`` asks), so the rule lives there: a
manifest ``doc`` and the getter's ``@brief`` -- both written for the
property -- still win, and the choices replace only the struct-member rung.
The generated ``get_<name>()`` / ``set_<name>()`` accessors return the raw
``int``, so the C comment IS their description and they keep it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _docgaps  # noqa: E402
from just_makeit import _docstring as D  # noqa: E402

C_PROSE = "0 little, 1 big."
ONE_OF = 'One of ``"le"``, ``"be"``.'


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _project(root: Path, *, doc: str = "", brief: str = "") -> Path:
    _ok("new", "q", "--no-c-prefix", cwd=root)
    q = root / "q"
    cfg = C.load(q)
    cfg["enum"] = [{"name": "endianE", "values": ["le", "be"]}]
    C.save(q, cfg)
    _ok("object", "w", "--state", "endian:int:0", cwd=q)
    _ok(
        "property", "w", "endian", "--type", "int", "--field",
        "--enum", "endianE", cwd=q,
    )  # fmt: skip
    hdr = q / "native/inc/q/w/w_core.h"
    text = hdr.read_text(encoding="utf-8")
    text, hits = re.subn(
        r"(int\s+endian;)[^\n]*", rf"\1  /**< {C_PROSE} */", text
    )
    assert hits, text
    if brief:
        text, hits = re.subn(
            r"(@brief) Get current endian\.", rf"\1 {brief}", text
        )
        assert hits, text
    hdr.write_text(text, encoding="utf-8")
    if doc:
        cfg = C.load(q)
        for p in cfg["w"]["properties"]:
            p["doc"] = doc
        C.save(q, cfg)
    _ok("apply", cwd=q)
    return q


def _prop_stub(q: Path) -> str:
    pyi = (q / "src/q/w.pyi").read_text(encoding="utf-8")
    start = pyi.index("    def endian(self)")
    return pyi[start : pyi.index("\n    def ", start + 1)]


def _getset_doc(q: Path) -> str:
    ext = (q / "native/src/w/w_ext.c").read_text(encoding="utf-8")
    table = ext[ext.index("PyGetSetDef W_getset[]") :]
    return table[: table.index("NULL, NULL, NULL, NULL}")].replace('\\"', '"')


def test_an_undocumented_enum_property_is_documented_by_its_choices(tmp_path):
    q = _project(tmp_path)
    stub = _prop_stub(q)
    assert ONE_OF in stub and C_PROSE not in stub, stub
    runtime = _getset_doc(q)
    assert ONE_OF.replace("\\", "") in runtime.replace("\\", ""), runtime
    assert C_PROSE not in runtime, runtime


def test_the_int_accessors_keep_the_c_comment(tmp_path):
    """`get_endian()` returns the raw int: the C comment describes it."""
    pyi = (_project(tmp_path) / "src/q/w.pyi").read_text(encoding="utf-8")
    for name in ("get_endian", "set_endian"):
        section = pyi[pyi.index(f"def {name}(") :].split("\n    def ")[0]
        assert C_PROSE in section, (name, section)


def test_a_manifest_doc_still_wins(tmp_path):
    q = _project(tmp_path, doc="Byte order of the file.")
    stub = _prop_stub(q)
    assert "Byte order of the file." in stub, stub
    assert C_PROSE not in stub, stub


def test_a_getter_brief_still_wins(tmp_path):
    q = _project(tmp_path, brief="Byte order of the samples.")
    stub = _prop_stub(q)
    assert "Byte order of the samples." in stub, stub
    assert C_PROSE not in stub, stub


def test_it_is_still_reported_as_undocumented(tmp_path):
    """The choices are generated, not written: `jm status --docs` keeps
    asking for a `doc`, as it did when the property fell to its name."""
    q = _project(tmp_path)
    found = _docgaps.gaps(q, C.load(q))
    assert any(g.name == "endian" for g in found), found


def test_a_property_without_an_enum_is_unchanged():
    blocks = {D.struct_members_key(): {"psd_state_t": {"n": "Frame len."}}}
    assert D.property_doc(
        "psd", {"name": "n"}, blocks, csym="psd", enums={"end": ["a"]}
    ) == ("Frame len.", False)


@pytest.mark.parametrize("enums", [None, {}, {"other": ["a"]}])
def test_an_unresolved_enum_keeps_the_old_text(enums):
    """No registry entry, no choices to document: nothing is replaced."""
    blocks = {D.struct_members_key(): {"psd_state_t": {"e": "0 le, 1 be."}}}
    assert D.property_doc(
        "psd", {"name": "e", "enum": "end"}, blocks, csym="psd", enums=enums
    ) == ("0 le, 1 be.", False)
