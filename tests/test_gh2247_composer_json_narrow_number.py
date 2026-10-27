"""gh-2247: a composer's JSON reader refuses a number its integer field
cannot hold.

The generated ``from_json`` read every field a double holds through
``_json_num`` and cast it: ``sg->g = (uint32_t)_json_num(...)``. For an
integer field that took ``1.5`` as ``1``, and a value past the type --
``4294967296``, ``-1``, ``1e30`` into a ``uint32_t`` -- is undefined
behaviour in C, which on x86 arrived as ``0``, ``4294967295`` and ``0``.
Nothing raised. A ``bytes`` field's elements were cast into ``uint8_t`` the
same way. gh-2144's rule -- a value the type cannot hold is refused, not
wrapped -- had not reached the JSON number path; gh-2139 made the 64-bit
rows exact and gh-2236 refuses a 64-bit parse that narrows.

Now ``_json_get`` -- the one read, which a bytes element goes through too --
reads an integer type a double holds into a ``double`` and refuses it,
naming the field with the reader's range sentence, unless it is within the
type's range (``_types.int_limits``, read off the type row) and a whole
number; only then is it cast.

The proof is compiled: ONE composer holds a field of every integer row the
double path serves -- a source field of each a source field may be, a
segment field of each, a ranged field per table (whose pair elements read
the same way) and a ``bytes`` field. Every type's ends are accepted and read
back exactly; a fraction, one past its ceiling, one past its floor (a
negative, on an unsigned type) and ``1e30`` are refused naming the field.
The range half cannot be seen in what a cast returns -- past the type the
cast is undefined, and on x86 its result happens to fail the whole-number
test too -- so the project is built with ``-fsanitize=float-cast-overflow``
trapping: a cast the range test did not guard kills the run. The types come
from the type table; their ends from numpy, an oracle that is not jm.

GATE: every integer field the JSON double path reads accepts its type's
ends and refuses a fraction and any value past them, naming the field --
plain, ranged and as a bytes element.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli

from just_makeit import _composer
from just_makeit import _config as C
from just_makeit import _types as T

sys.path.insert(0, str(Path(__file__).parent))
from test_gh1711_composer_owned_pointer import CJSON_C, CJSON_H  # noqa: E402
from test_gh2139_composer_json_64bit import RANGE  # noqa: E402
from test_gh2236_composer_json_narrowing import _iinfo, _slug  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: Every integer row the double path reads, by numpy's width: all that fit
#: in a double's 53-bit mantissa (``bool`` is not a range, and converting a
#: number to it is defined).
DOUBLE_PATH = sorted(
    t for t in T._CTYPE_META if _iinfo(t) is not None and _iinfo(t).bits <= 53
)

#: The ones a SOURCE field may declare (its own vocabulary).
SOURCE = sorted(set(DOUBLE_PATH) & set(_composer._SOURCE_SCALARS))

#: A ranged field on each table, whose pair elements read the same way.
RANGED = {"source": "uint32_t", "segment": "int8_t"}

#: What a bytes element is.
BYTE = "uint8_t"


def _fields() -> "list[tuple[str, str, str, str]]":
    """``(table, field, ctype, shape)`` for every field the record holds;
    *shape* is ``plain``, ``ranged`` or ``bytes``."""
    rows = [("source", f"f_{_slug(t)}", t, "plain") for t in SOURCE]
    rows += [("segment", f"g_{_slug(t)}", t, "plain") for t in DOUBLE_PATH]
    rows.append(("source", "r_range", RANGED["source"], "ranged"))
    rows.append(("segment", "q_range", RANGED["segment"], "ranged"))
    rows.append(("source", "bits", BYTE, "bytes"))
    return rows


def _what(table: str, f: str, ctype: str, shape: str) -> str:
    """How a refusal names the field -- the message is the contract."""
    return f"{table} field '{f}' ({'uint8_t*' if shape == 'bytes' else ctype})"


def _header() -> str:
    src = "".join(f"  {t} f_{_slug(t)};\n" for t in SOURCE)
    t = RANGED["source"]
    src += f"  {t} r_range;\n  {t} r_range_hi;\n  unsigned ranged;\n"
    src += "  uint8_t *bits;\n  size_t n_bits;\n"
    seg = "".join(f"  {t} g_{_slug(t)};\n" for t in DOUBLE_PATH)
    t = RANGED["segment"]
    seg += f"  {t} q_range;\n  {t} q_range_hi;\n  unsigned ranged;\n"
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
    """The backing: keeps its own copy of every source and its bytes, since
    ``from_json`` frees the ones it read once the state is created."""
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
    for (size_t k = 0; k < s[i].n_sources; k++) {{
      src_t *d = &st->segs[i].sources[k];
      *d = s[i].sources[k];
      d->bits = NULL;
      if (s[i].sources[k].bits && d->n_bits) {{
        d->bits = malloc(d->n_bits);
        memcpy(d->bits, s[i].sources[k].bits, d->n_bits);
      }}
    }}
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
  for (size_t i = 0; i < st->n; i++) {{
    for (size_t k = 0; k < st->segs[i].n_sources; k++)
      free(st->segs[i].sources[k].bits);
    free(st->segs[i].sources);
  }}
  free(st->segs);
  free(st);
}}
"""


#: The cJSON stand-in casts every number to ``int`` unguarded, as the real
#: one does not; it is not the code under test, so it is not sanitized.
BACKING_CMAKE = """\
add_library(backing_core OBJECT mix_core.c cJSON.c)
target_include_directories(backing_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc
                                               ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(backing_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
set_source_files_properties(cJSON.c PROPERTIES COMPILE_OPTIONS
                            "-w;-fno-sanitize=float-cast-overflow")
"""

#: A cast the range test did not guard traps, rather than returning a value
#: a later test happens to refuse. No sanitizer runtime is linked.
SANITIZE = "-fsanitize=float-cast-overflow -fsanitize-undefined-trap-on-error"


def _module() -> dict:
    return {
        "kind": "composer",
        "backing": "mix",
        "source": {
            "object": "src",
            "struct": "src_t",
            "type_name": "Src",
            "fields": [{"name": f"f_{_slug(t)}", "type": t} for t in SOURCE]
            + [
                {"name": "r_range", "type": RANGED["source"]},
                {"name": "bits", "type": "uint8_t*", "bytes": True},
            ],
            "ranged": [{"name": "r_range", "flag": "MIX_RANGED"}],
        },
        "segment": {
            "type_name": "Seg",
            "struct": "seg_t",
            "sources": "multi",
            "fields": [
                {"name": f"g_{_slug(t)}", "type": t} for t in DOUBLE_PATH
            ]
            + [{"name": "q_range", "type": RANGED["segment"]}],
            "ranged": [{"name": "q_range", "flag": "MIX_RANGED"}],
        },
        "oo": {"composer_type_name": "Mix"},
        "json": {"enabled": True},
        "extra_link_libs": ["backing_core"],
    }


def _cases() -> "tuple[dict, dict]":
    """``(cases, want)``: ``{case: [table, field, wire value]}`` and, per
    case, what reads back -- or ``None`` for a refusal.

    The type's two ends read back; a fraction, one past either end (below an
    unsigned type's floor: a negative) and a number no integer type holds
    are refused. A ranged field's pair carries the case in its high element,
    a bytes field's array as its one element.
    """
    cases: dict = {}
    want: dict = {}
    for table, f, t, shape in _fields():
        info = _iinfo(t)
        lo, hi = int(info.min), int(info.max)
        good = [(v, v) for v in (lo, hi)]
        bad = [(v, None) for v in (1.5, hi + 1, lo - 1, 1e30)]
        for v, back in good + bad:
            if shape == "ranged":
                wire, read = [lo, v], None if back is None else [lo, back]
            elif shape == "bytes":
                wire, read = [v], None if back is None else [back]
            else:
                wire, read = v, back
            case = f"{table} {f} ({t}) {wire!r}"
            cases[case] = [table, f, wire]
            want[case] = read
    return cases, want


#: Run in the built project's interpreter. `PKG`, `CASES`, `WHAT` and
#: `RANGE` are prepended by the test. A refusal counts only when it names
#: the field and says the reader's own range sentence.
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
        m = Mix.from_json(json.dumps(d))
        sg = m.segments[0]
        got = getattr(sg.sources[0] if table == "source" else sg, f)
        if isinstance(got, (tuple, bytes, bytearray)):
            got = list(got)
        out[case] = got
        m.close()
    except ValueError as e:
        said = WHAT[f] in str(e) and RANGE in str(e)
        out[case] = "refused" if said else f"ValueError: {e}"
    except Exception as e:
        out[case] = f"{type(e).__name__}: {e}"
print(json.dumps(out))
"""


def test_the_cases_are_not_vacuous():
    """An empty derivation would build nothing and report all-green."""
    assert {"int", "int8_t", "uint16_t", "int32_t", "uint32_t"} <= set(
        DOUBLE_PATH
    ), DOUBLE_PATH
    assert SOURCE, SOURCE
    # numpy's answer is the type table's: these are exactly the integer
    # rows with a range that the 64-bit path does not claim.
    claimed = {
        t
        for t in T._CTYPE_META
        if T.int_limits(t) and T.wider_than_double(t) is None
    }
    assert claimed == set(DOUBLE_PATH), (claimed, DOUBLE_PATH)
    _c, want = _cases()
    assert None in want.values() and any(w is not None for w in want.values())


def test_every_integer_read_is_checked_before_its_cast():
    """The render, with no toolchain: no integer field the double path
    reads is cast straight from ``_json_num``."""
    cfg = {"project": {"name": "nrw"}, "module": {"mix": _module()}}
    text = _composer.render_json_funcs(cfg, "mix")
    for table, f, t, shape in _fields():
        if shape == "plain":
            assert f"({t})_json_num(" not in text, f
        lo, hi = T.int_limits(t)
        assert f"_d < (double){hi} + 1.0" in text, t
    assert "(uint8_t)cJSON_GetNumberValue" not in text


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_number_the_field_cannot_hold_is_refused(tmp_path):
    """Build ONE composer holding every case and drive the JSON face."""
    pkg = "nrn"
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

    # Warning-clean, as a downstream building with -Werror needs.
    _run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            f"-DPython3_EXECUTABLE={sys.executable}",
            f"-DCMAKE_C_FLAGS=-Wall -Wextra -Werror {SANITIZE}",
        ],
        proj,
    )
    _run(["cmake", "--build", "build", "--parallel", "4"], proj)

    cases, want = _cases()
    what = {f: _what(table, f, t, shape) for table, f, t, shape in _fields()}
    driver = proj / "drive_gh2247.py"
    driver.write_text(
        f"PKG = {pkg!r}\nCASES = {cases!r}\nWHAT = {what!r}\n"
        f"RANGE = {RANGE!r}\n{_DRIVER}",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(driver)], cwd=proj, capture_output=True, text=True
    )
    # A negative return code is a signal: the sanitizer's trap, a cast the
    # range test did not guard.
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    got = json.loads(r.stdout)
    assert set(got) == set(cases)
    wrong = []
    for case in sorted(cases):
        expect = "refused" if want[case] is None else want[case]
        if got[case] != expect:
            wrong.append(f"{case}: want {expect!r}, got {got[case]!r}")
    assert not wrong, f"{len(wrong)} wrong:\n" + "\n".join(wrong)
