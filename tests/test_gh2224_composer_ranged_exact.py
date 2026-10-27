"""gh-2224: a composer's ranged field crosses the Python face at its own type.

A ranged field takes a scalar or a ``(lo, hi)`` pair. Its Python face
carried both through a ``double``: the shared ``_jm_parse_range`` read each
bound with ``PyFloat_AsDouble`` and the caller cast it to the field's type,
and the getter built the pair with ``Py_BuildValue("(dd)", ...)`` on a
source and ``"(nn)"`` (``Py_ssize_t``) on a segment. So a ``uint64_t`` --
or an ``int64_t``, a ``size_t``, a ``ptrdiff_t`` -- past 2**53 changed when
set and again when read; an integer source pair read back as two floats; a
``double`` segment pair read back truncated to integers; and an ``int8_t``
given 300 wrapped to 44. Nothing raised. #2221 (gh-2139) made the JSON face
exact for these fields and named this one.

Now ``_jm_parse_range`` only splits the value into its bound objects, and
each bound converts by the field's own ``_CTYPE_META`` row -- through
``_context/_parse.scalar_parse_c``, the parse every plain field uses, so an
out-of-range bound is refused as gh-2144 refuses one -- and the pair is built
from the row's ``to_py``.

The round trip, compiled: ONE project declares a ranged source field for
each integer and floating type a source field may be, and a ranged segment
field for each such ``_CTYPE_META`` type. Every pair at its type's extremes,
and every scalar at 2**53 + 1 where the type holds it, survives every face
that takes a value -- the constructors, the attribute, ``Seg.sum`` -- and the
segment array the backing hands back, compared by ``repr``. One step outside
an integer type is refused on each bound, by the ``OverflowError`` and the
ratchet ``tests/test_gh2144_int_range_refused`` holds every face to. The
types are derived from the type table; which of them a double cannot hold
is numpy's width, an oracle that is not the code under test.

GATE: every ranged field round-trips ``(min, max)`` and ``2**53 + 1`` (where
its type holds it) exactly through the Python face, and refuses a bound its
type cannot hold.
"""

from __future__ import annotations

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
from test_gh2144_int_range_refused import int_range, refusal_wrong

from just_makeit import _composer
from just_makeit import _config as C
from just_makeit import _types as T

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None


def _rangeable(types) -> "list[str]":
    """The integer and floating scalars of *types*: a value with a range.

    ``bool`` has no range to draw from and a complex number no order, so
    neither is a ranged field's type.
    """
    return sorted(
        t
        for t in types
        if T._CTYPE_META[t]["kind"] in ("int", "float") and t != "bool"
    )


#: A ranged segment field of each type, and a source field of each type a
#: source field may declare (its own vocabulary).
SEGMENT = _rangeable(T._CTYPE_META)
SOURCE = _rangeable(_composer._SOURCE_SCALARS)

#: 2**53 + 1: the first integer a double cannot hold, and rounds to 2**53.
PAST = 2**53 + 1


def _bits(ctype: str) -> int:
    """numpy's width of *ctype*'s row -- the oracle, not jm's predicate."""
    meta = T._CTYPE_META[ctype]
    if meta["kind"] != "int":
        return 0
    return int(np.iinfo(getattr(np, meta["py_type"].split(".", 1)[1])).bits)


#: Every integer type a double cannot hold.
WIDE = [t for t in SEGMENT if _bits(t) > 53]


def _slug(ctype: str) -> str:
    return re.sub(r"\W+", "_", ctype).strip("_")


def _values(ctype: str) -> list:
    """``[sent, ...]``: each value a face must hand back unchanged.

    An integer type's whole range as a pair, sent as a tuple and as a list
    (read back as the tuple); the issue's 2**53 + 1 -- which a double turns
    into 2**53 -- where the type holds it, else its far end; and its floor.
    A floating type keeps its fraction, and ``float`` a value it holds.
    """
    if T._CTYPE_META[ctype]["kind"] == "float":
        v = -2.75 if ctype == "float" else 0.1
        return [(v, 0.5), v]
    lo, hi = int_range(ctype)
    return [(lo, hi), [lo, hi], min(PAST, hi), lo]


def _out(ctype: str) -> "list[tuple[object, int]]":
    """``[(sent, the bound that is out of range)]`` for an integer type:
    each bound of a pair one step outside it, and a scalar."""
    if T._CTYPE_META[ctype]["kind"] != "int":
        return []
    lo, hi = int_range(ctype)
    return [((lo - 1, hi), lo - 1), ((lo, hi + 1), hi + 1), (hi + 1, hi + 1)]


def _flags(prefix: str, types: "list[str]") -> str:
    return "".join(
        f"#define {prefix}_{_slug(t).upper()} (1u << {i})\n"
        for i, t in enumerate(types)
    )


def _header() -> str:
    src = "".join(
        f"  {t} r_{_slug(t)};\n  {t} r_{_slug(t)}_hi;\n" for t in SOURCE
    )
    seg = "".join(
        f"  {t} g_{_slug(t)};\n  {t} g_{_slug(t)}_hi;\n" for t in SEGMENT
    )
    return (
        "#ifndef MIX_CORE_H\n#define MIX_CORE_H\n"
        "#include <complex.h>\n#include <stddef.h>\n#include <stdint.h>\n"
        + _flags("MIX_R", SOURCE)
        + _flags("MIX_G", SEGMENT)
        + f"typedef struct {{\n{src}  unsigned ranged;\n}} src_t;\n"
        "typedef struct {\n  src_t *sources;\n  size_t n_sources;\n"
        f"  double fs;\n{seg}  unsigned ranged;\n}} seg_t;\n"
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
    def table(prefix: str, flag: str, types: "list[str]") -> dict:
        return {
            "fields": [
                {"name": f"{prefix}_{_slug(t)}", "type": t} for t in types
            ],
            "ranged": [
                {
                    "name": f"{prefix}_{_slug(t)}",
                    "flag": f"{flag}_{_slug(t).upper()}",
                }
                for t in types
            ],
        }

    return {
        "kind": "composer",
        "backing": "mix",
        "source": {
            "object": "src",
            "struct": "src_t",
            "type_name": "Src",
            **table("r", "MIX_R", SOURCE),
        },
        "segment": {
            "type_name": "Seg",
            "struct": "seg_t",
            "sources": "multi",
            **table("g", "MIX_G", SEGMENT),
        },
        "oo": {"composer_type_name": "Mix"},
        "extra_link_libs": ["backing_core"],
    }


#: ``table: {face: Python expression of field name `f` and value `v`}``.
FACES = {
    "source": {
        "constructor": "getattr(Src(**{f: v}), f)",
        "attribute": "attr(Src(), f, v)",
        "through C": (
            "getattr(Mix([Seg.sum(Src(**{f: v}))]).segments[0].sources[0], f)"
        ),
    },
    "segment": {
        "constructor": "getattr(Seg(**{f: v}), f)",
        "sum": "getattr(Seg.sum(Src(), **{f: v}), f)",
        "attribute": "attr(Seg.sum(Src()), f, v)",
        "through C": "getattr(Mix([Seg.sum(Src(), **{f: v})]).segments[0], f)",
    },
}

#: The faces that take a value from Python, which refuse one out of range.
TAKES = {
    "source": ("constructor", "attribute"),
    "segment": ("constructor", "sum", "attribute"),
}

#: Run in the built project's interpreter: evaluates every case and prints
#: ``{case: [repr, type name] | "<Exception>: <message>"}`` as JSON. Each case
#: builds its own objects, so one conversion that raises reports that case
#: and no other. `PKG` and `CASES` (``{case: [expression, field, value]}``)
#: are prepended by the test, as literals.
_DRIVER = """
import json, sys
sys.path.insert(0, "src")
mod = __import__(PKG + ".mix.mix", fromlist=["Mix"])
Mix, Seg, Src = mod.Mix, mod.Seg, mod.Src


def attr(obj, name, v):
    setattr(obj, name, v)
    return getattr(obj, name)


out = {}
for case, (expr, f, v) in CASES.items():
    try:
        got = eval(expr, {**globals(), "f": f, "v": v})
        out[case] = [repr(got), type(got).__name__]
    except Exception as e:  # every case is reported, not the first
        out[case] = f"{type(e).__name__}: {e}"
print(json.dumps(out))
"""


def _cases() -> "tuple[dict, dict, dict]":
    """``(cases, want, refusals)`` for the driver and the comparison.

    *want* is ``{case: [repr, type name]}`` of each value as sent (a list
    reads back as the tuple it stands for); *refusals* is ``{case: (ctype,
    the field its refusal names, the bound out of range)}``.
    """
    cases: dict = {}
    want: dict = {}
    refusals: dict = {}
    for table, prefix, types in (
        ("source", "r", SOURCE),
        ("segment", "g", SEGMENT),
    ):
        for t in types:
            f = f"{prefix}_{_slug(t)}"
            for face, expr in FACES[table].items():
                for v in _values(t):
                    case = f"{table} {t} ({face}) {v!r}"
                    back = tuple(v) if isinstance(v, list) else v
                    cases[case] = [expr, f, v]
                    want[case] = [repr(back), type(back).__name__]
                if face not in TAKES[table]:
                    continue
                for v, bound in _out(t):
                    case = f"{table} {t} ({face}) {v!r} refused"
                    cases[case] = [expr, f, v]
                    refusals[case] = (t, f, bound)
    return cases, want, refusals


def test_the_cases_are_not_vacuous():
    """An empty derivation would build nothing and report all-green."""
    assert {"uint64_t", "int64_t", "size_t", "ptrdiff_t"} <= set(WIDE), WIDE
    assert set(WIDE) & set(SOURCE), (WIDE, SOURCE)
    assert {"double", "float", "int8_t", "uint32_t"} <= set(SEGMENT)
    # Every type a double cannot hold is on the path #2221 named.
    assert all(T.wider_than_double(t) for t in WIDE), WIDE
    _c, want, refusals = _cases()
    assert any(str(PAST) in case for case in want)
    assert refusals


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_every_ranged_field_round_trips_exactly(tmp_path):
    """Build ONE composer holding every case and drive the Python face."""
    pkg = "rgx"
    assert run_cli("new", pkg, cwd=tmp_path).returncode == 0
    proj = tmp_path / pkg
    inc = proj / "native" / "inc" / pkg / "mix"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "mix_core.h").write_text(_header(), encoding="utf-8")
    backing = proj / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    (backing / "mix_core.c").write_text(_source(pkg), encoding="utf-8")
    (backing / "CMakeLists.txt").write_text(
        "add_library(backing_core OBJECT mix_core.c)\n"
        "target_include_directories(backing_core PUBLIC"
        " ${CMAKE_SOURCE_DIR}/native/inc)\n",
        encoding="utf-8",
    )
    cfg = C.load(proj)
    cfg["project"]["c_deps"] = ["backing"]
    cfg.setdefault("module", {})["mix"] = _module()
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

    # Warning-clean, as a downstream building with -Werror needs: every
    # conversion is new code in a file `apply` regenerates.
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

    cases, want, refusals = _cases()
    driver = proj / "drive_gh2224.py"
    driver.write_text(
        f"PKG = {pkg!r}\nCASES = {cases!r}\n{_DRIVER}", encoding="utf-8"
    )
    r = subprocess.run(
        [sys.executable, str(driver)], cwd=proj, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout)
    assert set(got) == set(cases), sorted(set(cases) ^ set(got))
    wrong = [
        f"{case}: want {want[case][0]} ({want[case][1]}), got {got[case]}"
        for case in sorted(want)
        if got[case] != want[case]
    ]
    # gh-2144: a bound out of range is refused naming the field, as every
    # other face refuses one.
    wrong += [
        w
        for case, (ct, label, bound) in sorted(refusals.items())
        if (w := refusal_wrong(case, ct, label, bound, got[case]))
    ]
    assert not wrong, f"{len(wrong)} wrong:\n" + "\n".join(wrong)
