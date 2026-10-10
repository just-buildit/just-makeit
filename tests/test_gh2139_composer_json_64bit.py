"""gh-2139: a composer's JSON face carries a 64-bit integer field exactly.

The generated ``to_json`` wrote every numeric source and segment field
through a ``double`` (``cJSON_AddNumberToObject(so, "poly",
(double)src->poly)``) and ``from_json`` read it back through one, so a
``uint64_t`` -- or an ``int64_t``, a ``size_t``, a ``ptrdiff_t`` -- past 2**53
came back a different value, with no error.

Now a field whose type a double cannot hold
(:func:`~just_makeit._types.wider_than_double`, read off the type row) is
written as a JSON number when its value is within the I-JSON safe range
(``_composer.JSON_SAFE_INT``, 2**53 - 1) and as a decimal string past it.
``from_json`` / ``from_file`` take either: a number only when it is a whole
number in that range, a string parsed exactly, and anything else is refused
with a ``ValueError`` naming the field. Narrower fields are unchanged.

Two halves:

* **the render**: no 64-bit field is written or read through a double, and
  a composer without one renders none of the new helpers.
* **the round trip**, compiled: ONE project declares a source field for each
  64-bit type a source field may be, a segment field for each 64-bit
  ``_CTYPE_META`` type, a ranged one of each, and a narrower one beside
  them. Every value at the edges of the double's range and of the type's
  survives ``to_json`` -> ``from_json`` and ``from_file``, with the wire form
  asserted; and a hand-written record holding each bad value is refused,
  naming the field. The types are derived from the type table, through
  numpy's width -- an oracle that is not the predicate under test.

GATE: every 64-bit composer field round-trips 0, 2**53 - 1, 2**53,
2**53 + 1 and its type's extremes exactly through the JSON face; a number
past 2**53 - 1, a fraction, junk, an out-of-range string and (unsigned) a
sign are refused naming the field; an old-style small number still reads.
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

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: I-JSON's safe integer range (RFC 7493, section 2.2): every integer a
#: double holds with no neighbour rounding onto it. Stated here, not read
#: from the code under test, and held to it below.
SAFE = 2**53 - 1


def _iinfo(ctype: str):
    """numpy's integer info for *ctype*'s row, or None for a non-integer."""
    meta = T._CTYPE_META[ctype]
    if meta["kind"] != "int" or ctype == "bool":
        return None
    return np.iinfo(getattr(np, meta["py_type"].split(".", 1)[1]))


#: Every integer row a double cannot hold, by numpy's width -- the oracle.
WIDE = sorted(
    ct
    for ct in T._CTYPE_META
    if _iinfo(ct) is not None and _iinfo(ct).bits > 53
)

#: The 64-bit types a SOURCE field may declare (its own vocabulary).
SOURCE_WIDE = sorted(set(WIDE) & set(_composer._SOURCE_SCALARS))

#: A narrower field beside them, which must read and write as it always did.
NARROW = "uint32_t"


def _slug(ctype: str) -> str:
    return re.sub(r"\W+", "_", ctype).strip("_")


def _values(ctype: str) -> "list[int]":
    """Each edge: of the double's exact range, past it, and of the type's --
    and, unsigned, the issue's own 0x8000000000000001, which a double turns
    into 0x8000000000000000."""
    info = _iinfo(ctype)
    vals = [0, 42, SAFE, SAFE + 1, SAFE + 2, int(info.max)]
    if info.min < 0:
        vals += [-SAFE, -SAFE - 1, -SAFE - 2, int(info.min)]
    else:
        vals.append(2**63 + 1)
    return vals


#: What each refusal says beside the field's name -- a reader that refused
#: every one of these for the same reason would not be telling the author
#: which of them to fix.
NUMBER = "must be a whole number"
JUNK = "is not a decimal integer"
RANGE = "is out of range"
SIGN = "is negative, and the field is unsigned"


def _bad(ctype: str) -> "list[list]":
    """``[value, what the refusal says]``: values the field must refuse."""
    info = _iinfo(ctype)
    bad = [
        [SAFE + 1, NUMBER],  # 2**53: a number past the safe range...
        [SAFE + 2, NUMBER],  # ...which a parser may have rounded to 2**53
        [1.5, NUMBER],
        ["12abc", JUNK],
        ["", JUNK],
        [" 5", JUNK],
        ["0x10", JUNK],
        [str(int(info.max) + 1), RANGE],
    ]
    if info.min < 0:
        bad += [[-SAFE - 2, NUMBER], [str(int(info.min) - 1), RANGE]]
        bad += [["-", JUNK]]
    else:
        bad += [[-1, NUMBER], ["-1", SIGN]]
    return bad


# -- the type row ------------------------------------------------------------


def test_the_cases_are_not_vacuous():
    """An empty derivation would build nothing and report all-green."""
    assert {"uint64_t", "int64_t"} <= set(WIDE), WIDE
    assert any(_iinfo(t).min < 0 for t in WIDE)
    assert SOURCE_WIDE, SOURCE_WIDE
    assert NARROW not in WIDE


def test_the_face_bounds_a_number_at_the_safe_range():
    """2**53 itself is not a number on the wire: 2**53 + 1 parses to it."""
    assert _composer.JSON_SAFE_INT == SAFE
    assert float(SAFE + 2) == float(SAFE + 1)  # the rounding it avoids


@pytest.mark.parametrize("ctype", sorted(T._CTYPE_META))
def test_wider_than_double_is_the_type_width(ctype):
    """The predicate reads the row's parse type; numpy states the width.

    A row whose parse type is a ``long long`` and whose range a double holds,
    or the converse, would put a field on the wrong path.
    """
    got = T.wider_than_double(ctype)
    assert (got is not None) == (ctype in WIDE), (ctype, got)
    if got is not None:
        unsigned = _iinfo(ctype).min == 0
        assert got == ("unsigned long long" if unsigned else "long long")


# -- the render --------------------------------------------------------------


def _with_wide() -> dict:
    """The shared composer on the GENERATED JSON face (it delegates), plus
    a field of every 64-bit type it may hold."""
    cfg = copy.deepcopy(_cfg())
    mod = cfg["module"]["wfm_compose"]
    mod["json"] = {"enabled": True}
    mod["source"]["fields"] += [
        {"name": f"w_{_slug(t)}", "type": t} for t in SOURCE_WIDE
    ]
    mod["segment"]["fields"] += [
        {"name": f"w_{_slug(t)}", "type": t} for t in WIDE
    ]
    return cfg


def _narrowed(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    mod = cfg["module"]["wfm_compose"]
    for table in ("source", "segment"):
        for f in mod[table]["fields"]:
            if f.get("type") in WIDE:
                f["type"] = NARROW
    return cfg


def test_no_64bit_field_crosses_a_double():
    """Every 64-bit field is written and read by its exact helper."""
    cfg = _with_wide()
    text = _composer.render_json_funcs(cfg, "wfm_compose")
    mod = cfg["module"]["wfm_compose"]
    wide_fields = [
        (table, f)
        for table in ("source", "segment")
        for f in mod[table]["fields"]
        if f.get("type") in WIDE and not f.get("enum")
    ]
    assert len(wide_fields) >= len(WIDE) + len(SOURCE_WIDE)
    for table, f in wide_fields:
        n = f["name"]
        assert f"(double)src->{n})" not in text, n
        assert f"(double)g->{n})" not in text, n
        assert f'_json_num(so, "{n}"' not in text, n
        assert f'_json_num(sj, "{n}"' not in text, n
        assert f"\"{table} field '{n}' ({f['type']})\"" in text, n


def test_a_composer_of_only_64bit_numbers_drops_the_double_reader():
    """``_json_num`` read every number; with each one 64-bit it has no
    caller, and an uncalled static fails a ``-Werror`` build (gh-1863)."""
    cfg = _with_wide()
    mod = cfg["module"]["wfm_compose"]
    for table in ("source", "segment"):
        mod[table]["fields"] = [
            f
            for f in mod[table]["fields"]
            if f.get("enum") or f.get("bytes") or f.get("type") in WIDE
        ]
    text = _composer.render_json_funcs(cfg, "wfm_compose")
    assert "_json_ull(" in text
    assert "_json_num" not in text


#: What a 64-bit field brings into the extension, and nothing else does.
_WIDE_ONLY = ("_json_ull", "_json_ll", "<errno.h>", "the reader refused")


def test_a_composer_without_one_renders_no_new_helper():
    """Narrowed, the shared composer has no 64-bit field and no helper."""
    wide = _composer.render_ext(_with_wide(), "wfm_compose")
    for name in _WIDE_ONLY:
        assert name in wide, name
    text = _composer.render_ext(_narrowed(_with_wide()), "wfm_compose")
    for name in _WIDE_ONLY:
        assert name not in text, name
    assert "_json_num(" in text


# -- the round trip ----------------------------------------------------------

#: A ranged field -- a scalar, or a ``[lo, hi]`` pair -- of a 64-bit type, on
#: each table: its pair elements cross the face as well as its scalar.
RANGED = {"source": ("r_wide", "uint64_t"), "segment": ("rg_wide", "int64_t")}


def _header() -> str:
    src = "".join(f"  {t} f_{_slug(t)};\n" for t in SOURCE_WIDE + [NARROW])
    name, ct = RANGED["source"]
    src += f"  {ct} {name};\n  {ct} {name}_hi;\n  unsigned ranged;\n"
    seg = "".join(f"  {t} g_{_slug(t)};\n" for t in WIDE + [NARROW])
    name, ct = RANGED["segment"]
    seg += f"  {ct} {name};\n  {ct} {name}_hi;\n  unsigned ranged;\n"
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


def _source(pkg: str) -> str:
    return f"""#include "{pkg}/mix/mix_core.h"
#include <stdlib.h>
#include <string.h>
struct mix_state {{
  seg_t *segs;
  size_t n;
}};
mix_state_t *mix_create(const seg_t *s, size_t n, int r, int c)
{{
  (void)r; (void)c;
  mix_state_t *st = calloc(1, sizeof *st);
  st->segs = calloc(n, sizeof *st->segs);
  for (size_t i = 0; i < n; i++) {{
    st->segs[i] = s[i];
    st->segs[i].sources = calloc(s[i].n_sources, sizeof(src_t));
    memcpy(st->segs[i].sources, s[i].sources,
           s[i].n_sources * sizeof(src_t));
  }}
  st->n = n;
  return st;
}}
size_t mix_execute(mix_state_t *st, float _Complex *o, size_t m)
{{ (void)st; (void)o; (void)m; return 0; }}
const seg_t *mix_segments(const mix_state_t *st, size_t *n, int *r, int *c)
{{ *n = st->n; *r = 0; *c = 0; return st->segs; }}
void mix_destroy(mix_state_t *st)
{{
  for (size_t i = 0; i < st->n; i++)
    free(st->segs[i].sources);
  free(st->segs);
  free(st);
}}
"""


def _module() -> dict:
    src_fields = [
        {"name": f"f_{_slug(t)}", "type": t} for t in SOURCE_WIDE + [NARROW]
    ]
    seg_fields = [
        {"name": f"g_{_slug(t)}", "type": t} for t in WIDE + [NARROW]
    ]
    for table, fields in (("source", src_fields), ("segment", seg_fields)):
        name, ct = RANGED[table]
        fields.append({"name": name, "type": ct})
    return {
        "kind": "composer",
        "backing": "mix",
        "source": {
            "object": "src",
            "struct": "src_t",
            "type_name": "Src",
            "fields": src_fields,
            "ranged": [{"name": RANGED["source"][0], "flag": "MIX_RANGED"}],
        },
        "segment": {
            "type_name": "Seg",
            "struct": "seg_t",
            "sources": "multi",
            "fields": seg_fields,
            "ranged": [{"name": RANGED["segment"][0], "flag": "MIX_RANGED"}],
        },
        "oo": {"composer_type_name": "Mix"},
        "json": {"enabled": True},
        "extra_link_libs": ["backing_core"],
    }


BACKING_CMAKE = """\
add_library(backing_core OBJECT mix_core.c cJSON.c)
target_include_directories(backing_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc
                                               ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(backing_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
# The test's cJSON stand-in is not the code under test.
set_source_files_properties(cJSON.c PROPERTIES COMPILE_OPTIONS "-w")
"""


#: Run in the built project's interpreter. Prints the list of problems as
#: JSON: each a sentence naming the case. `PKG`, `SRC`, `SEG`, `NARROW`,
#: `RANGED`, `SAFE` and `JUNK` are prepended by the test, as literals --
#: `SRC` / `SEG` map each 64-bit field to `[values, bad, what]`, where
#: `bad` is `[[value, what its refusal says], ...]`.
_DRIVER = r"""
import json, sys
sys.path.insert(0, "src")
mod = __import__(PKG + ".mix.mix", fromlist=["Mix"])
Mix, Seg, Src = mod.Mix, mod.Seg, mod.Src

problems = []


def wire(v):
    # The form the record must hold: a number within the safe range, else
    # a decimal string.
    return v if -SAFE <= v <= SAFE else str(v)


def build(i):
    src = {f: vals[i % len(vals)] for f, (vals, _b, _w) in SRC.items()}
    seg = {f: vals[i % len(vals)] for f, (vals, _b, _w) in SEG.items()}
    return Seg.sum(Src(**src, **{NARROW["source"]: 7}),
                   **seg, **{NARROW["segment"]: 9})


n = max(len(v) for v, _b, _w in list(SRC.values()) + list(SEG.values()))
m = Mix([build(i) for i in range(n)])
text = m.to_json()
doc = json.loads(text)

# The wire form, for every field and value.
for i, sj in enumerate(doc["segments"]):
    for f, (vals, _b, _w) in SEG.items():
        if sj[f] != wire(vals[i % len(vals)]):
            problems.append(f"to_json segment {i} {f}: wrote {sj[f]!r}")
    so = sj["sources"][0]
    for f, (vals, _b, _w) in SRC.items():
        if so[f] != wire(vals[i % len(vals)]):
            problems.append(f"to_json segment {i} source {f}: wrote {so[f]!r}")
    if sj[NARROW["segment"]] != 9 or so[NARROW["source"]] != 7:
        problems.append(f"to_json segment {i}: a narrow field changed")


def read_back(m2, how):
    for i, sg in enumerate(m2.segments):
        for f, (vals, _b, _w) in SEG.items():
            want = vals[i % len(vals)]
            got = getattr(sg, f)
            if got != want or type(got) is not int:
                problems.append(f"{how} segment {i} {f}: {want} -> {got!r}")
        sc = sg.sources[0]
        for f, (vals, _b, _w) in SRC.items():
            want = vals[i % len(vals)]
            got = getattr(sc, f)
            if got != want or type(got) is not int:
                problems.append(
                    f"{how} segment {i} source {f}: {want} -> {got!r}")
        if getattr(sg, NARROW["segment"]) != 9:
            problems.append(f"{how} segment {i}: narrow segment field")
        if getattr(sc, NARROW["source"]) != 7:
            problems.append(f"{how} segment {i}: narrow source field")


read_back(Mix.from_json(text), "from_json")
with open("gh2139_record.json", "w") as fh:
    fh.write(text)
read_back(Mix.from_file("gh2139_record.json"), "from_file")

# A ranged field's pair, and its scalar, cross the face as its value does.
for table, (f, lohi) in RANGED.items():
    for value in (lohi, lohi[1]):
        d = json.loads(text)
        holder = d["segments"][0]
        if table == "source":
            holder = holder["sources"][0]
        if isinstance(value, list):
            holder[f] = [wire(v) for v in value]
        else:
            holder[f] = wire(value)
        back = json.loads(Mix.from_json(json.dumps(d)).to_json())
        got = back["segments"][0]
        if table == "source":
            got = got["sources"][0]
        if got[f] != holder[f]:
            problems.append(
                f"ranged {table} {f}: {holder[f]!r} -> {got[f]!r}")


def put(d, table, f, value):
    holder = d["segments"][0]
    if table == "source":
        holder = holder["sources"][0]
    holder[f] = value


# What every reader still takes: an old-style small number, the same value
# as a string, and the field absent (its default, 0).
for table, fields in (("source", SRC), ("segment", SEG)):
    for f in fields:
        for value, want in ((42, 42), ("42", 42), (None, 0)):
            d = json.loads(text)
            if value is None:
                holder = d["segments"][0]
                if table == "source":
                    holder = holder["sources"][0]
                del holder[f]
            else:
                put(d, table, f, value)
            sg = Mix.from_json(json.dumps(d)).segments[0]
            got = getattr(sg.sources[0] if table == "source" else sg, f)
            if got != want:
                problems.append(f"{table} {f}: {value!r} read as {got!r}")

# What every reader refuses, naming the field.
cases = [(t, f, b, what) for t, fields in (("source", SRC), ("segment", SEG))
         for f, (_v, bad, what) in fields.items() for b in bad]
for table, (f, _lohi) in RANGED.items():
    what = (SRC if table == "source" else SEG)[f][2]
    cases.append((table, f, [["1", "12abc"], JUNK], what))
for table, f, (b, says), what in cases:
    d = json.loads(text)
    put(d, table, f, b)
    try:
        Mix.from_json(json.dumps(d))
    except ValueError as e:
        if what not in str(e) or says not in str(e):
            problems.append(f"{table} {f} = {b!r}: refused as {e}")
    except Exception as e:
        problems.append(f"{table} {f} = {b!r}: {type(e).__name__}: {e}")
    else:
        problems.append(f"{table} {f} = {b!r}: accepted")

m.close()
print(json.dumps(problems))
"""


def _what(table: str, name: str, ctype: str) -> str:
    """How a refusal names the field -- the message is the contract."""
    return f"{table} field '{name}' ({ctype})"


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_every_64bit_field_round_trips_through_json(tmp_path):
    """Build ONE composer holding every case and drive the JSON face."""
    pkg = "wide"
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

    # Warning-clean, as a downstream building with -Werror needs: the
    # helpers are new code in a file `apply` regenerates.
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

    src = {
        f"f_{_slug(t)}": [
            _values(t),
            _bad(t),
            _what("source", f"f_{_slug(t)}", t),
        ]
        for t in SOURCE_WIDE
    }
    seg = {
        f"g_{_slug(t)}": [
            _values(t),
            _bad(t),
            _what("segment", f"g_{_slug(t)}", t),
        ]
        for t in WIDE
    }
    ranged = {}
    for table, (name, ct) in RANGED.items():
        info = _iinfo(ct)
        lohi = [SAFE + 2, int(info.max)]
        if info.min < 0:
            lohi = [int(info.min), -SAFE - 2]
        ranged[table] = [name, lohi]
        (src if table == "source" else seg)[name] = [
            [0],
            [[SAFE + 2, NUMBER], ["12abc", JUNK]],
            _what(table, name, ct),
        ]
    narrow = {"source": f"f_{_slug(NARROW)}", "segment": f"g_{_slug(NARROW)}"}
    driver = proj / "drive_gh2139.py"
    driver.write_text(
        f"PKG = {pkg!r}\nSRC = {src!r}\nSEG = {seg!r}\n"
        f"NARROW = {narrow!r}\nRANGED = {ranged!r}\nSAFE = {SAFE!r}\n"
        f"JUNK = {JUNK!r}\n{_DRIVER}",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(driver)], cwd=proj, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
    problems = json.loads(r.stdout)
    assert not problems, "\n".join(problems)
