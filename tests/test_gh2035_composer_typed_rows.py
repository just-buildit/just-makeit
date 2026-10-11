"""gh-2035: a composer row converts its value by its declared type.

A composer's settings converted every value through ``PyLong_AsLong`` /
``PyLong_FromLong``, whatever ``type`` said, so a ``double`` setting whose C
value was 0.75 read back as ``0`` -- and ``0.5`` was refused on the way in
(truncated on Python 3.9). Its segment fields, computed properties and
source fields went through two private helpers that knew five types and sent
every other one through ``long``: a ``bool`` read back as an ``int``, a
complex computed property as its real part, a complex segment field could
not be set at all, and a ``size_t`` source field refused 2**63. A serializer
param looked its format char up in a five-type table with ``"i"`` as the
fallback, so an ``int64_t`` was parsed four bytes wide and ``-1`` reached C
as 4294967295. Nothing raised in any of these.

Every one of those faces now converts through the row the object faces use:
``_CTYPE_META``'s ``to_py`` out, and ``_context/_parse.scalar_parse_c`` /
``scalar_arg_c`` in (the parse-type local, then ``to_c``). A type no face can
convert -- an array, a spelling jm does not know, a string on a row that
holds a number, a complex segment field the JSON or CLI face would carry as
one real number -- is refused by ``jm apply`` with one ``error:`` line naming
the row, and the project is left byte-identical.

Two halves:

* **the round trip**, compiled: ONE project declares, for every numeric
  ``_CTYPE_META`` scalar, a setting, a segment field, a computed property and
  a serializer param of that type (and a source field for each type a source
  field may declare), builds it, and drives every face from Python. Each value
  is one that type holds and a wrong conversion cannot: a fraction for a
  floating type, both parts for a complex one, the far end of an integer
  type's range, and ``True`` for ``bool`` -- compared by value AND Python
  type. The cases are derived from the type table, not listed. gh-2144: one
  step OUTSIDE an integer type's range is refused on every face that takes
  a value, by the ``OverflowError`` and against the ratchet
  ``tests/test_gh2144_int_range_refused`` holds the object faces to.
* **the refusal**, through ``jm apply``: each row kind, typed as something it
  cannot convert, exits 1 naming the row, and writes nothing.

GATE: a composer setting, segment field, computed property, source field and
serializer param round-trips every numeric scalar exactly, and refuses an
integer its type cannot hold, naming the row; a type no face can convert is
refused by apply, naming the row, with the project unchanged.
"""

from __future__ import annotations

import contextlib
import copy
import io
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
from just_makeit._new import run as new_run

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: Every numeric scalar jm converts, from the type table.
SCALARS = sorted(
    ct
    for ct, meta in T._CTYPE_META.items()
    if meta["kind"] in ("int", "float", "complex")
)

#: The types a source field may declare (a source field's own vocabulary).
SOURCE_SCALARS = sorted(_composer._SOURCE_SCALARS)


def _slug(ctype: str) -> str:
    """A C and Python identifier fragment for *ctype*."""
    return re.sub(r"\W+", "_", ctype).strip("_")


def _signed(ctype: str) -> bool:
    np_t = getattr(np, T._CTYPE_META[ctype]["py_type"].split(".", 1)[1])
    return bool(np.iinfo(np_t).min < 0)


def _value(ctype: str):
    """A value *ctype* holds exactly and a wrong conversion loses.

    A fraction for a floating type (``long`` drops it, ``float`` narrows
    0.1), both parts for a complex one, the far end of an integer type's
    range (a narrower or wrongly signed parse cannot hold it), and ``True``
    -- whose Python type is the point -- for ``bool``.
    """
    meta = T._CTYPE_META[ctype]
    if ctype == "bool":
        return True
    if meta["kind"] == "float":
        return -2.75 if ctype == "float" else 0.1
    if meta["kind"] == "complex":
        if ctype.startswith("float"):
            return complex(-2.75, 0.5)
        return complex(0.1, -0.2)
    info = np.iinfo(getattr(np, meta["py_type"].split(".", 1)[1]))
    return int(info.min) if info.min < 0 else int(info.max)


def _c_literal(ctype: str, v) -> str:
    """*v* as a C expression of *ctype*."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, complex):
        return f"(({ctype})({v.real!r} + {v.imag!r} * I))"
    if isinstance(v, float):
        return f"(({ctype}){v!r})"
    if v < 0:
        return f"(({ctype})(-{-v - 1}LL - 1))"
    return f"(({ctype}){v}ULL)"


def _c_print(ctype: str, v: str) -> "tuple[str, str]":
    """``(printf conversion, its arguments)`` writing *v* back exactly."""
    kind = T._CTYPE_META[ctype]["kind"]
    if ctype == "bool":
        return "%d", f"(int){v}"
    if kind == "float":
        return "%a", f"(double){v}"
    if kind == "complex":
        z = f"(double _Complex){v}"
        return "%a,%a", f"creal({z}), cimag({z})"
    if _signed(ctype):
        return "%lld", f"(long long){v}"
    return "%llu", f"(unsigned long long){v}"


#: A ranged field -- a scalar, or a ``(lo, hi)`` pair -- reads its scalar
#: through the same conversion. One per table, of the type ``long`` cut.
RANGED = {"source": "r_double", "segment": "rg_double"}


def _header() -> str:
    src = "".join(f"  {t} f_{_slug(t)};\n" for t in SOURCE_SCALARS)
    src += f"  double {RANGED['source']};\n  double {RANGED['source']}_hi;\n"
    src += "  unsigned ranged;\n"
    seg = "".join(f"  {t} g_{_slug(t)};\n" for t in SCALARS)
    seg += f"  double {RANGED['segment']};\n"
    seg += f"  double {RANGED['segment']}_hi;\n  unsigned ranged;\n"
    fns = "#define MIX_RANGED 1u\n" + "".join(
        f"void mix_set_s_{_slug(t)}(mix_state_t *st, {t} v);\n"
        f"{t} mix_s_{_slug(t)}(const mix_state_t *st);\n"
        f"{t} src_c_{_slug(t)}(const src_t *s);\n"
        for t in SCALARS
    )
    params = "".join(f"{t} p_{_slug(t)}, " for t in SCALARS)
    return (
        "#ifndef MIX_CORE_H\n#define MIX_CORE_H\n"
        "#include <complex.h>\n#include <stdbool.h>\n"
        "#include <stddef.h>\n#include <stdint.h>\n"
        f"typedef struct {{\n{src}}} src_t;\n"
        "typedef struct {\n  src_t *sources;\n  size_t n_sources;\n"
        f"  double fs;\n{seg}}} seg_t;\n"
        "typedef struct mix_state mix_state_t;\n"
        "mix_state_t *mix_create(const seg_t *s, size_t n, int r, int c);\n"
        "size_t mix_execute(mix_state_t *st, float _Complex *o, size_t m);\n"
        "const seg_t *mix_segments(const mix_state_t *st, size_t *n,"
        " int *r, int *c);\n"
        "void mix_destroy(mix_state_t *st);\n"
        f"{fns}"
        f"char *mix_ser({params}const seg_t *segs, size_t n);\n"
        "#endif\n"
    )


def _source(pkg: str) -> str:
    state = "".join(f"  {t} s_{_slug(t)};\n" for t in SCALARS)
    fns = "".join(
        f"void mix_set_s_{_slug(t)}(mix_state_t *st, {t} v)"
        f" {{ st->s_{_slug(t)} = v; }}\n"
        f"{t} mix_s_{_slug(t)}(const mix_state_t *st)"
        f" {{ return st->s_{_slug(t)}; }}\n"
        f"{t} src_c_{_slug(t)}(const src_t *s)"
        f" {{ (void)s; return {_c_literal(t, _value(t))}; }}\n"
        for t in SCALARS
    )
    params = "".join(f"{t} p_{_slug(t)}, " for t in SCALARS)
    prints = "".join(
        "  k += snprintf(o + k, 4096 - k, "
        f'"p_{_slug(t)}={_c_print(t, "p_" + _slug(t))[0]} ", '
        f"{_c_print(t, 'p_' + _slug(t))[1]});\n"
        for t in SCALARS
    )
    return f"""#include "{pkg}/mix/mix_core.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
struct mix_state {{
  seg_t *segs;
  size_t n;
{state}}};
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
{fns}
char *mix_ser({params}const seg_t *segs, size_t n)
{{
  (void)segs; (void)n;
  char *o = malloc(4096);
  int k = 0;
{prints}  return o;
}}
"""


def _composer_module(**rows) -> dict:
    """A composer over the `mix` backing, plus the given row tables."""
    m = {
        "kind": "composer",
        "backing": "mix",
        "source": {
            "object": "src",
            "struct": "src_t",
            "type_name": "Src",
            "fields": [],
        },
        "segment": {
            "type_name": "Seg",
            "struct": "seg_t",
            "sources": "multi",
            "fields": [],
        },
        "oo": {"composer_type_name": "Mix"},
    }
    m.update(rows)
    return m


def _every_row() -> dict:
    """The round-trip module: every row kind, one row per type."""
    m = _composer_module(
        settings=[
            {
                "name": f"s_{_slug(t)}",
                "setter_fn": f"mix_set_s_{_slug(t)}",
                "getter_fn": f"mix_s_{_slug(t)}",
                "type": t,
            }
            for t in SCALARS
        ],
        serializers=[
            {
                "name": "ser",
                "fn": "mix_ser",
                "params": [
                    {"name": f"p_{_slug(t)}", "type": t} for t in SCALARS
                ],
            }
        ],
    )
    m["source"]["fields"] = [
        {"name": f"f_{_slug(t)}", "type": t} for t in SOURCE_SCALARS
    ] + [{"name": RANGED["source"], "type": "double"}]
    m["source"]["computed"] = [
        {"name": f"c_{_slug(t)}", "type": t, "fn": f"src_c_{_slug(t)}"}
        for t in SCALARS
    ]
    m["segment"]["fields"] = [
        {"name": f"g_{_slug(t)}", "type": t} for t in SCALARS
    ] + [{"name": RANGED["segment"], "type": "double"}]
    for table, name in RANGED.items():
        m[table]["ranged"] = [{"name": name, "flag": "MIX_RANGED"}]
    m["extra_link_libs"] = ["backing_core"]
    return m


#: Run in the built project's interpreter: drives every face of every type
#: and prints `{case: [repr, type name] | error}` as JSON. Each case builds
#: its own objects, so one conversion that raises reports that case and no
#: other. `PKG`, `CASES`, `ZERO`, `KIND`, `SOURCE_CASES` and `RANGED` are
#: prepended by the test, as Python literals.
_DRIVER = """
import json, sys
sys.path.insert(0, "src")
mod = __import__(PKG + ".mix.mix", fromlist=["Mix"])
Mix, Seg, Src = mod.Mix, mod.Seg, mod.Src

out = {}


def record(case, fn):
    try:
        v = fn()
        out[case] = [repr(v), type(v).__name__]
    except Exception as e:  # every case is reported, not the first
        out[case] = f"{type(e).__name__}: {e}"


def attr(obj, name, v):
    setattr(obj, name, v)
    return getattr(obj, name)


def seg(**kw):
    return Seg.sum(Src(), **kw)


def sent(s, v):
    # Every param is required, so the others carry their type's zero.
    kw = {f"p_{o}": z for o, z in ZERO.items()}
    kw[f"p_{s}"] = v
    text = dict(t.partition("=")[::2] for t in Mix([seg()]).ser(**kw).split())
    text = text[f"p_{s}"]
    if KIND[s] == "bool":
        return bool(int(text))
    if KIND[s] == "float":
        return float.fromhex(text)
    if KIND[s] == "complex":
        re_, im = text.split(",")
        return complex(float.fromhex(re_), float.fromhex(im))
    return int(text)


for s, v in CASES.items():
    st, g = f"s_{s}", f"g_{s}"
    record(f"setting {s} (constructor)",
           lambda: getattr(Mix([seg()], **{st: v}), st))
    record(f"setting {s} (attribute)", lambda: attr(Mix([seg()]), st, v))
    record(f"segment field {s} (Python)",
           lambda: getattr(seg(**{g: v}), g))
    record(f"segment field {s} (through C)",
           lambda: getattr(Mix([seg(**{g: v})]).segments[0], g))
    record(f"segment field {s} (attribute)", lambda: attr(seg(), g, v))
    record(f"computed {s}", lambda: getattr(Src(), f"c_{s}"))
    record(f"serializer param {s}", lambda: sent(s, v))
for s, v in SOURCE_CASES.items():
    f = f"f_{s}"
    record(f"source field {s} (constructor)",
           lambda: getattr(Src(**{f: v}), f))
    record(f"source field {s} (through C)",
           lambda: getattr(Mix([Seg.sum(Src(**{f: v}))]).segments[0]
                           .sources[0], f))
    record(f"source field {s} (attribute)", lambda: attr(Src(), f, v))
r, rg = RANGED["source"], RANGED["segment"]
record("ranged source field (constructor)",
       lambda: getattr(Src(**{r: 0.1}), r))
record("ranged source field (through C)",
       lambda: getattr(Mix([Seg.sum(Src(**{r: 0.1}))]).segments[0]
                       .sources[0], r))
record("ranged segment field (Python)", lambda: getattr(seg(**{rg: 0.1}), rg))
record("ranged segment field (through C)",
       lambda: getattr(Mix([seg(**{rg: 0.1})]).segments[0], rg))
# gh-2144: one step outside an integer type's range, on every face that
# takes a value from Python.
for s, vs in OUT.items():
    st, g = f"s_{s}", f"g_{s}"
    for v in vs:
        record(f"setting {s} (constructor) {v}",
               lambda: getattr(Mix([seg()], **{st: v}), st))
        record(f"setting {s} (attribute) {v}",
               lambda: attr(Mix([seg()]), st, v))
        record(f"segment field {s} (Python) {v}",
               lambda: getattr(seg(**{g: v}), g))
        record(f"segment field {s} (attribute) {v}",
               lambda: attr(seg(), g, v))
        record(f"serializer param {s} {v}", lambda: sent(s, v))
for s, vs in SOURCE_OUT.items():
    f = f"f_{s}"
    for v in vs:
        record(f"source field {s} (constructor) {v}",
               lambda: getattr(Src(**{f: v}), f))
        record(f"source field {s} (attribute) {v}",
               lambda: attr(Src(), f, v))
print(json.dumps(out))
"""


def _ints(types) -> "list[str]":
    """The integer scalars of *types*, whose range a value can leave."""
    return [
        t for t in types if T._CTYPE_META[t]["kind"] == "int" and t != "bool"
    ]


def _out(types) -> dict:
    """``{slug: [min - 1, max + 1]}`` for the integer scalars of *types*."""
    return {
        _slug(t): [int_range(t)[0] - 1, int_range(t)[1] + 1]
        for t in _ints(types)
    }


def _refusals() -> dict:
    """``{case: (ctype, the name its refusal leads with, value)}``."""
    want = {}
    for t in _ints(SCALARS):
        s = _slug(t)
        for v in _out([t])[s]:
            for face, label in (
                ("setting {} (constructor)", f"s_{s}"),
                ("setting {} (attribute)", f"s_{s}"),
                ("segment field {} (Python)", f"g_{s}"),
                ("segment field {} (attribute)", f"g_{s}"),
                ("serializer param {}", f"p_{s}"),
            ):
                want[f"{face.format(s)} {v}"] = (t, label, v)
    for t in _ints(SOURCE_SCALARS):
        s = _slug(t)
        for v in _out([t])[s]:
            for face in ("constructor", "attribute"):
                want[f"source field {s} ({face}) {v}"] = (t, f"f_{s}", v)
    return want


def _expected() -> dict:
    """``{case: [repr, type name]}``: every value as it was sent."""
    want = {}
    for t in SCALARS:
        s, v = _slug(t), _value(t)
        for case in (
            f"setting {s} (constructor)",
            f"setting {s} (attribute)",
            f"segment field {s} (Python)",
            f"segment field {s} (through C)",
            f"segment field {s} (attribute)",
            f"computed {s}",
            f"serializer param {s}",
        ):
            want[case] = [repr(v), type(v).__name__]
    for t in SOURCE_SCALARS:
        s, v = _slug(t), _value(t)
        for face in ("constructor", "through C", "attribute"):
            want[f"source field {s} ({face})"] = [repr(v), type(v).__name__]
    ranged_faces = {
        "source": ("constructor", "through C"),
        "segment": ("Python", "through C"),
    }
    assert set(ranged_faces) == set(RANGED)
    for table, faces in ranged_faces.items():
        for face in faces:
            want[f"ranged {table} field ({face})"] = ["0.1", "float"]
    return want


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


def test_the_cases_are_not_vacuous():
    """An empty derivation would build nothing and report all-green."""
    assert {"double", "float", "bool", "int64_t"} <= set(SCALARS), SCALARS
    assert any(T._CTYPE_META[t]["kind"] == "complex" for t in SCALARS)
    assert "double" in SOURCE_SCALARS, SOURCE_SCALARS


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_every_row_round_trips_every_scalar(tmp_path):
    """Build ONE composer holding every case and drive every face.

    The values cross Python -> C -> Python: a setting through its C setter
    and getter, a segment field and a source field through the segment
    array the backing copies and hands back, a serializer param into a C
    function that prints it exactly (``%a``). A computed property returns a
    C literal of the value. Compared by ``repr`` and Python type, so ``1``
    for ``True`` and ``2`` for ``2.0`` are both failures.
    """
    pkg = "tq"
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
    cfg.setdefault("module", {})["mix"] = _every_row()
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

    cases = {_slug(t): _value(t) for t in SCALARS}
    # `0` is a value every numeric parse accepts, so a serializer case fails
    # only for its own param.
    zero = {_slug(t): 0 for t in SCALARS}
    kinds = {
        _slug(t): "bool" if t == "bool" else T._CTYPE_META[t]["kind"]
        for t in SCALARS
    }
    source_cases = {_slug(t): _value(t) for t in SOURCE_SCALARS}
    driver = proj / "drive_gh2035.py"
    driver.write_text(
        f"PKG = {pkg!r}\nCASES = {cases!r}\nZERO = {zero!r}\n"
        f"KIND = {kinds!r}\nSOURCE_CASES = {source_cases!r}\n"
        f"RANGED = {RANGED!r}\nOUT = {_out(SCALARS)!r}\n"
        f"SOURCE_OUT = {_out(SOURCE_SCALARS)!r}\n{_DRIVER}",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(driver)], cwd=proj, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout)
    want = _expected()
    refusals = _refusals()
    assert set(got) == set(want) | set(refusals), sorted(
        (set(want) | set(refusals)) ^ set(got)
    )
    wrong = [
        f"{case}: sent {want[case][0]} ({want[case][1]}), got {got[case]}"
        for case in sorted(want)
        if got[case] != want[case]
    ]
    # gh-2144: refused naming the row, as every object face is.
    wrong += [
        w
        for case, (ct, label, v) in sorted(refusals.items())
        if (w := refusal_wrong(case, ct, label, v, got[case]))
    ]
    assert not wrong, "\n".join(wrong)


# ── the refusal ──────────────────────────────────────────────────────────────

#: An array in each spelling a manifest may write one, and a spelling that
#: names no type at all.
ARRAYS = ("float[]", "double _Complex[][]", "int32_t[8]")
UNKNOWN = "nope_t"
STRING = "const char *"


def _setting(t):
    return {
        "settings": [
            {
                "name": "zz",
                "setter_fn": "mix_set_zz",
                "getter_fn": "mix_zz",
                "type": t,
            }
        ]
    }


def _segment_field(t, **extra):
    def rows(m):
        m["segment"]["fields"].append({"name": "zz", "type": t})
        m.update(extra)

    return rows


def _computed(t):
    def rows(m):
        m["source"]["computed"] = [{"name": "zz", "type": t, "fn": "src_zz"}]

    return rows


def _serializer(t):
    return {
        "serializers": [
            {
                "name": "ser",
                "fn": "mix_ser",
                "params": [{"name": "zz", "type": t}],
            }
        ]
    }


def _row_kind(table):
    """Apply a dict of row tables, or a function editing the module."""

    def build(t, **extra):
        m = _composer_module()
        m["source"]["fields"] = [{"name": "k", "type": "double"}]
        m["segment"]["fields"] = [{"name": "g", "type": "double"}]
        made = table(t, **extra) if extra else table(t)
        if callable(made):
            made(m)
        else:
            m.update(made)
        return m

    return build


#: row kind -> (module builder, what the refusal names, types it refuses).
ROW_KINDS = {
    "settings": (
        _row_kind(_setting),
        "composer module 'mix' settings row 'zz'",
        (*ARRAYS, UNKNOWN, STRING),
    ),
    "segment.fields": (
        _row_kind(_segment_field),
        "composer module 'mix' segment.fields row 'zz'",
        (*ARRAYS, UNKNOWN, STRING),
    ),
    "source.computed": (
        _row_kind(_computed),
        "composer module 'mix' source.computed row 'zz'",
        (*ARRAYS, UNKNOWN, STRING),
    ),
    # A serializer param is read for the call alone, so a string is legal
    # there (`s`, borrowed for the call); only what no parse converts is not.
    "serializer params": (
        _row_kind(_serializer),
        "composer module 'mix' serializer 'ser' params row 'zz'",
        (*ARRAYS, UNKNOWN),
    ),
}

_CASES = [(k, t) for k, spec in sorted(ROW_KINDS.items()) for t in spec[-1]]


def _tree(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _project(root: Path, module: dict) -> dict:
    """A fresh project holding *module*; its tree before any apply."""
    _quiet(new_run, "proj", root, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(root)
    cfg.setdefault("module", {})["mix"] = copy.deepcopy(module)
    C.save(root, cfg)
    return _tree(root)


def _refused(r, before: dict, root: Path, where: str) -> str:
    """Exit 1, one `error:` line naming *where*, the tree unchanged."""
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]
    assert len(errors) == 1, r.stderr
    assert errors[0].startswith(f"error: {where}: "), errors[0]
    assert _tree(root) == before, "a refused apply changed the project"
    return errors[0]


@pytest.mark.parametrize("kind", sorted(ROW_KINDS))
def test_the_row_applies_when_its_type_converts(tmp_path, kind):
    """Armed: the module each refusal below edits applies cleanly with a
    type the row converts, so a refusal is about the type, not the fixture.
    """
    build = ROW_KINDS[kind][0]
    _project(tmp_path, build("double"))
    r = run_cli("apply", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("kind, ctype", _CASES)
def test_a_type_no_face_converts_is_refused(tmp_path, kind, ctype):
    build, where, _types = ROW_KINDS[kind]
    before = _project(tmp_path, build(ctype))
    err = _refused(run_cli("apply", cwd=tmp_path), before, tmp_path, where)
    if ctype in ARRAYS:
        assert "is an array" in err, err
    elif ctype == STRING:
        assert "is a string" in err, err
    else:
        assert f"unknown type '{ctype}'" in err, err


#: A face that carries a segment field as one real number, and what turns
#: it on. Each refuses a complex field rather than drop its imaginary part.
REAL_FACES = {
    "json": {"json": {"enabled": True}},
    "cli": {"cli": {"enabled": True}},
}


@pytest.mark.parametrize("face", sorted(REAL_FACES))
def test_a_complex_segment_field_is_refused_where_it_would_be_cut(
    tmp_path, face
):
    where = "composer module 'mix' segment.fields row 'zz'"
    build = _row_kind(_segment_field)
    # Armed: the face renders with a real field.
    _project(tmp_path / "ok", build("double", **REAL_FACES[face]))
    r = run_cli("apply", cwd=tmp_path / "ok")
    assert r.returncode == 0, r.stdout + r.stderr
    before = _project(
        tmp_path / "z", build("float _Complex", **REAL_FACES[face])
    )
    err = _refused(
        run_cli("apply", cwd=tmp_path / "z"), before, tmp_path / "z", where
    )
    assert "is complex" in err, err


# ── the stub ─────────────────────────────────────────────────────────────────


def test_the_stub_annotates_each_row_by_its_type():
    """The ``.pyi`` says what each row reads as: every setting said ``int``,
    and a ``bool`` / complex field or computed property ``int`` too."""
    pyi = _composer.render_pyi({"module": {"mix": _every_row()}}, "mix")
    missing = []
    for t in SCALARS:
        ann = T.scalar_py_annotation(t)
        for line in (
            f"    s_{_slug(t)}: {ann}",
            f"    g_{_slug(t)}: {ann}",
            f"    c_{_slug(t)}: {ann}",
        ):
            if line not in pyi.splitlines():
                missing.append(line)
    assert not missing, "\n".join(missing)
