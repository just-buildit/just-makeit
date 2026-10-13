"""gh-2236: a composer's JSON reader refuses a value its field cannot hold.

#2221 (gh-2139) reads a field whose type a double cannot hold
(:func:`~just_makeit._types.wider_than_double`) through an exact reader
into the row's parse type -- an ``unsigned long long`` or a ``long long``
-- and then cast that to the field's C type. A ``size_t`` or a
``ptrdiff_t`` parses through a ``long long`` on every target, and is 32 bits
on a 32-bit one, so ``"4294967296"`` passed the reader and the cast dropped
its high bits, with no error. The 64-bit targets CI builds on hold every
value the reader can, which is why it never showed.

Now the read value is converted back and compared with the parsed one, and
a value the cast would change is refused with the reader's own range
``ValueError``, naming the field.

The proof needs no 32-bit target. What reaches the cast is decided by the
predicate alone, so the test has it answer for every integer row narrower
than its parse type (numpy's width, from the type table) what it answers
for a ``size_t`` -- the parse type of the row's signedness. That builds
exactly the read a 32-bit ``size_t`` gets: the 64-bit reader, then the cast
to the narrower type. Every value at the type's edges must read back
exactly, as a number and as a string; one step past either edge must be
refused naming the field.

GATE: a JSON value the field's C type cannot hold is refused naming the
field, on the number path and the string path, plain and ranged.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from _compilers import default_cc

from _jmrun import run_cli

from just_makeit import _composer
from just_makeit import _config as C
from just_makeit import _types as T

sys.path.insert(0, str(Path(__file__).parent))
from test_composer_codegen import _cfg  # noqa: E402
from test_gh1711_composer_owned_pointer import CJSON_C, CJSON_H  # noqa: E402
from test_gh2139_composer_json_64bit import (  # noqa: E402
    BACKING_CMAKE,
    RANGE,
    _source,
)

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None


def _iinfo(ctype: str):
    """numpy's integer info for *ctype*'s row, or None for a non-integer."""
    meta = T._CTYPE_META[ctype]
    if meta["kind"] != "int" or ctype == "bool":
        return None
    return np.iinfo(getattr(np, meta["py_type"].split(".", 1)[1]))


#: Every integer row narrower than the 64-bit type it would parse through on
#: the reader's path -- a ``size_t`` on a 32-bit target, by width.
NARROW = sorted(
    ct
    for ct in T._CTYPE_META
    if _iinfo(ct) is not None and _iinfo(ct).bits < 64
)

#: The narrow types a SOURCE field may declare (its own vocabulary).
SOURCE_NARROW = sorted(set(NARROW) & set(_composer._SOURCE_SCALARS))

#: A ranged field on each table, whose pair elements read the same way.
RANGED = {"source": "uint32_t", "segment": "int16_t"}


def _as_wide(real):
    """The predicate, answering for a narrow row what it does for a
    ``size_t`` / ``ptrdiff_t``: the parse type of the row's signedness."""

    def wider_than_double(ctype: str) -> "str | None":
        if ctype in NARROW:
            signed = _iinfo(ctype).min < 0
            return "long long" if signed else "unsigned long long"
        return real(ctype)

    return wider_than_double


def _slug(ctype: str) -> str:
    return re.sub(r"\W+", "_", ctype).strip("_")


def _what(table: str, name: str, ctype: str) -> str:
    """How a refusal names the field -- the message is the contract."""
    return f"{table} field '{name}' ({ctype})"


def _header() -> str:
    src = "".join(f"  {t} f_{_slug(t)};\n" for t in SOURCE_NARROW)
    t = RANGED["source"]
    src += f"  {t} r_{_slug(t)};\n  {t} r_{_slug(t)}_hi;\n  unsigned ranged;\n"
    seg = "".join(f"  {t} g_{_slug(t)};\n" for t in NARROW)
    t = RANGED["segment"]
    seg += f"  {t} q_{_slug(t)};\n  {t} q_{_slug(t)}_hi;\n  unsigned ranged;\n"
    return (
        "#ifndef MIX_CORE_H\n#define MIX_CORE_H\n"
        "#include <complex.h>\n#include <stddef.h>\n#include <stdint.h>\n"
        "#define MIX_RANGED 1u\n"
        f"typedef struct {{\n{src}}} src_t;\n"
        "typedef struct {\n  src_t *sources;\n  size_t n_sources;\n"
        f"  double fs;\n{seg}}} seg_t;\n"
        "typedef struct mix_state mix_state_t;\n"
        "mix_state_t *mix_create(const seg_t *s, size_t n, int r, int c);\n"
        "size_t mix_execute(mix_state_t *st, float _Complex *o, size_t m);\n"
        "const seg_t *mix_segments(const mix_state_t *st, size_t *n,"
        " int *r, int *c);\n"
        "void mix_destroy(mix_state_t *st);\n"
        "#endif\n"
    )


def _module() -> dict:
    rs, rg = RANGED["source"], RANGED["segment"]
    return {
        "kind": "composer",
        "backing": "mix",
        "source": {
            "object": "src",
            "struct": "src_t",
            "type_name": "Src",
            "fields": [
                {"name": f"f_{_slug(t)}", "type": t} for t in SOURCE_NARROW
            ]
            + [{"name": f"r_{_slug(rs)}", "type": rs}],
            "ranged": [{"name": f"r_{_slug(rs)}", "flag": "MIX_RANGED"}],
        },
        "segment": {
            "type_name": "Seg",
            "struct": "seg_t",
            "sources": "multi",
            "fields": [{"name": f"g_{_slug(t)}", "type": t} for t in NARROW]
            + [{"name": f"q_{_slug(rg)}", "type": rg}],
            "ranged": [{"name": f"q_{_slug(rg)}", "flag": "MIX_RANGED"}],
        },
        "oo": {"composer_type_name": "Mix"},
        "json": {"enabled": True},
        "extra_link_libs": ["backing_core"],
    }


def _fields() -> "list[tuple[str, str, str, bool]]":
    """``(table, field, ctype, ranged)`` for every field the record holds."""
    rows = [("source", f"f_{_slug(t)}", t, False) for t in SOURCE_NARROW]
    rows += [("segment", f"g_{_slug(t)}", t, False) for t in NARROW]
    rows.append(
        ("source", f"r_{_slug(RANGED['source'])}", RANGED["source"], True)
    )
    rows.append(
        ("segment", f"q_{_slug(RANGED['segment'])}", RANGED["segment"], True)
    )
    return rows


def _cases() -> "tuple[dict, dict]":
    """``(cases, want)``: ``{case: [table, field, wire value]}`` and, per
    case, the value it reads back or ``None`` for a refusal.

    Each edge of the type, as a number and as a string, reads back; one
    step past either edge is refused -- below an unsigned type's floor the
    reader's own refusals answer (a sign), so only the ceiling is the cast.
    A ranged field's pair puts the case in its high element.
    """
    cases: dict = {}
    want: dict = {}
    for table, f, t, ranged in _fields():
        info = _iinfo(t)
        lo, hi = int(info.min), int(info.max)
        good = [lo, hi]
        bad = [hi + 1] + ([lo - 1] if lo < 0 else [])
        for v, back in [(v, v) for v in good] + [(v, None) for v in bad]:
            for wire in (v, str(v)):
                case = f"{table} {f} {wire!r}"
                cases[case] = [table, f, [lo, wire] if ranged else wire]
                want[case] = (
                    [lo, back] if ranged and back is not None else back
                )
    return cases, want


#: Run in the built project's interpreter. `PKG`, `CASES`, `RANGE` and
#: `WHAT` (``{field: how its refusal names it}``) are prepended by the
#: test. A refusal counts only when it names the field and says the
#: reader's own range sentence.
_DRIVER = r"""
import json, sys
sys.path.insert(0, "src")
mod = __import__(PKG + ".mix.mix", fromlist=["Mix"])
Mix, Seg, Src = mod.Mix, mod.Seg, mod.Src

base = json.loads(Mix([Seg.sum(Src())]).to_json())
out = {}
for case, (table, f, value) in CASES.items():
    d = json.loads(json.dumps(base))
    holder = d["segments"][0]
    if table == "source":
        holder = holder["sources"][0]
    holder[f] = value
    try:
        sg = Mix.from_json(json.dumps(d)).segments[0]
        got = getattr(sg.sources[0] if table == "source" else sg, f)
        out[case] = list(got) if isinstance(got, tuple) else got
    except ValueError as e:
        said = WHAT[f] in str(e) and RANGE in str(e)
        out[case] = "refused" if said else f"ValueError: {e}"
    except Exception as e:
        out[case] = f"{type(e).__name__}: {e}"
print(json.dumps(out))
"""


def test_the_cases_are_not_vacuous():
    """An empty derivation would build nothing and report all-green."""
    assert {"int8_t", "uint16_t", "int32_t", "uint32_t"} <= set(NARROW)
    assert SOURCE_NARROW, SOURCE_NARROW
    assert all(T.wider_than_double(t) is None for t in NARROW), NARROW
    # The stand-in answers what the real predicate does for a size_t.
    fake = _as_wide(T.wider_than_double)
    assert fake("uint32_t") == T.wider_than_double("size_t")
    assert fake("int16_t") == T.wider_than_double("ptrdiff_t")
    assert fake("double") is None
    _c, want = _cases()
    assert None in want.values()


def test_the_read_refuses_what_the_cast_would_change():
    """The render, with no toolchain: each read the real predicate sends
    down the 64-bit path converts its value back before storing it."""
    cfg = copy.deepcopy(_cfg())
    mod = cfg["module"]["wfm_compose"]
    mod["json"] = {"enabled": True}
    mod["segment"]["fields"] += [
        {"name": f"w_{_slug(t)}", "type": t}
        for t in T._CTYPE_META
        if T.wider_than_double(t)
    ]
    text = _composer.render_json_funcs(cfg, "wfm_compose")
    for t in T._CTYPE_META:
        pt = T.wider_than_double(t)
        if pt:
            assert f"({pt})({t})_w != _w" in text, t


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_value_the_field_cannot_hold_is_refused(tmp_path, monkeypatch):
    """Build ONE composer whose narrow fields read as a 32-bit ``size_t``
    does, and drive the JSON face with every edge."""
    monkeypatch.setattr(T, "wider_than_double", _as_wide(T.wider_than_double))
    pkg = "nrw"
    assert run_cli("new", pkg, cwd=tmp_path).returncode == 0
    proj = tmp_path / pkg
    inc = proj / "native" / "inc" / pkg / "mix"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "mix_core.h").write_text(_header(), encoding="utf-8")
    backing = proj / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    (backing / "mix_core.c").write_text(_source(pkg), encoding="utf-8")
    (backing / "cJSON.h").write_text(CJSON_H, encoding="utf-8")
    (backing / "cJSON.c").write_text(CJSON_C, encoding="utf-8")
    (backing / "CMakeLists.txt").write_text(BACKING_CMAKE, encoding="utf-8")
    cfg = C.load(proj)
    cfg["project"]["c_deps"] = ["backing"]
    cfg.setdefault("module", {})["mix"] = _module()
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    ext = (proj / "native" / "src" / "mix" / "mix_ext.c").read_text()
    assert "_json_ull(" in ext and "_json_ll(" in ext, "not on the wide path"

    # Warning-clean, as a downstream building with -Werror needs.
    _run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            f"-DPython3_EXECUTABLE={sys.executable}",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        proj,
    )
    _run(["cmake", "--build", "build", "--parallel", "4"], proj)

    cases, want = _cases()
    what = {f: _what(table, f, t) for table, f, t, _r in _fields()}
    driver = proj / "drive_gh2236.py"
    driver.write_text(
        f"PKG = {pkg!r}\nCASES = {cases!r}\nWHAT = {what!r}\n"
        f"RANGE = {RANGE!r}\n{_DRIVER}",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(driver)], cwd=proj, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout)
    assert set(got) == set(cases)
    wrong = []
    for case in sorted(cases):
        expect = "refused" if want[case] is None else want[case]
        if got[case] != expect:
            wrong.append(f"{case}: want {expect!r}, got {got[case]!r}")
    assert not wrong, f"{len(wrong)} wrong:\n" + "\n".join(wrong)
