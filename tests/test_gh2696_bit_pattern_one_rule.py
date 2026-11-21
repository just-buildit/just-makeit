"""gh-2696: a ``bit_pattern`` field reads one rule on every face.

A composer source field with ``bytes = true`` and ``coerce = "bit_pattern"``
is a bit array, one element per bit. The generated ``_attach_bytes`` took
it two ways: a sequence of ints mapped each element to ``v != 0``, while a
``bytes`` object was copied as it was, so ``bytes([8])`` stored 8 and
``[8]`` stored 1 -- two field values for one spelling of intent, and what
a stored 8 meant was up to whatever read the bits (doppler read the low
bit of one field and the whole byte of another). The JSON reader was a
third face, storing any element up to 255.

Now an element that is not 0 or 1 is refused on every face of a
bit_pattern field -- ``bytes``, ``bytearray``, a list, a numpy array, the
constructor, the setter, a project-read field's non-``str`` value, and
``from_json`` -- with one sentence naming the field
(:func:`~just_makeit._composer.bit_refusal_c`). A plain ``bytes`` field
beside it is not a bit array, and keeps taking any byte.

GATE: every spelling of every bit_pattern field reads 0/1 the same and
refuses a non-bit element with the field's sentence; a plain bytes field on
the same source stores ``bytes([8])`` as 8.
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

sys.path.insert(0, str(Path(__file__).parent))
from test_gh1711_composer_owned_pointer import CJSON_C, CJSON_H  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

PKG = "bpo"
HOST = "bpo_text_bits"

#: The source's bytes fields: two bit arrays -- one reading a str with
#: jm's grammar, one through the project's -- and a plain bytes field.
FIELDS = [
    {"name": "bits", "type": "uint8_t*", "bytes": True,
     "coerce": "bit_pattern"},
    {"name": "hosted", "type": "uint8_t*", "bytes": True,
     "coerce": "bit_pattern", "coerce_str_fn": HOST},
    {"name": "raw", "type": "uint8_t*", "bytes": True},
]  # fmt: skip

BIT_FIELDS = [f["name"] for f in FIELDS if _composer.is_bit_pattern(f)]
PLAIN_FIELDS = [f["name"] for f in FIELDS if not _composer.is_bit_pattern(f)]


def _module() -> dict:
    return {
        "kind": "composer",
        "backing": "mix",
        "source": {
            "object": "src",
            "struct": "src_t",
            "type_name": "Src",
            "fields": [dict(f) for f in FIELDS],
        },
        "segment": {
            "type_name": "Seg",
            "struct": "seg_t",
            "sources": "multi",
            "fields": [{"name": "dur", "type": "size_t", "default": "1"}],
        },
        "oo": {"composer_type_name": "Mix"},
        "json": {"enabled": True},
        "extra_link_libs": ["backing_core"],
    }


def _header() -> str:
    members = "".join(
        f"  uint8_t *{f['name']};\n  size_t n_{f['name']};\n" for f in FIELDS
    )
    return (
        "#ifndef MIX_CORE_H\n#define MIX_CORE_H\n"
        "#include <complex.h>\n#include <stddef.h>\n#include <stdint.h>\n"
        f"typedef struct {{\n{members}}} src_t;\n"
        "typedef struct {\n  src_t *sources;\n  size_t n_sources;\n"
        "  double fs;\n  size_t dur;\n} seg_t;\n"
        "typedef struct mix_state mix_state_t;\n"
        "mix_state_t *mix_create(const seg_t *s, size_t n, int r, int c);\n"
        "size_t mix_execute(mix_state_t *st, float _Complex *o, size_t m);\n"
        "const seg_t *mix_segments(const mix_state_t *st, size_t *n,"
        " int *r, int *c);\n"
        "void mix_destroy(mix_state_t *st);\n"
        "#endif\n"
    )


def _source() -> str:
    """The backing: keeps its own copy of every source's buffers, since
    ``from_json`` frees the ones it read once the state is created."""
    names = [f["name"] for f in FIELDS]
    copy = "".join(
        f"      d->{n} = NULL;\n"
        f"      if (s[i].sources[k].{n} && d->n_{n}) {{\n"
        f"        d->{n} = malloc(d->n_{n});\n"
        f"        memcpy(d->{n}, s[i].sources[k].{n}, d->n_{n});\n"
        "      }\n"
        for n in names
    )
    free = "".join(f"      free(st->segs[i].sources[k].{n});\n" for n in names)
    return f"""#include "{PKG}/mix/mix_core.h"
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
{copy}    }}
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
    for (size_t k = 0; k < st->segs[i].n_sources; k++) {{
{free}    }}
    free(st->segs[i].sources);
  }}
  free(st->segs);
  free(st);
}}
"""


#: The project's str reader: `0`/`1` digits. Only a str reaches it; every
#: other value goes on to `_attach_bytes`, which must still know the field.
_BITS_C = f"""#include "{PKG}/mix/mix_bridge.h"
#include <string.h>
size_t {HOST}(const char *text, uint8_t *out, size_t max_out,
{" " * (len(HOST) + 7)} const char **why)
{{
  size_t n = strlen(text);
  for (size_t i = 0; i < n; i++)
    if (text[i] != '0' && text[i] != '1') {{
      *why = "host: not a pattern";
      return 0;
    }}
  if (out)
    for (size_t i = 0; i < n && i < max_out; i++)
      out[i] = (uint8_t)(text[i] - '0');
  return n;
}}
"""

BACKING_CMAKE = """\
add_library(backing_core OBJECT mix_core.c mix_bits.c cJSON.c)
target_include_directories(backing_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc
                                               ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(backing_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
set_source_files_properties(cJSON.c PROPERTIES COMPILE_OPTIONS "-w")
"""


def _sentence(field: str, index: int, value: int) -> str:
    """The one refusal, as the user reads it -- the contract."""
    return (
        f"{field} is a bit array, one element per bit (0 or 1): element "
        f"{index} is {value}, not a bit; np.unpackbits turns octets into bits"
    )


#: The values every face is handed, and the one answer each must give: the
#: stored bytes, or ``(index, value)`` of the element refused.
GOOD = [[1, 0, 1, 1], [0], [1]]
BAD = [([8], (0, 8)), ([1, 0, 2], (2, 2)), ([0, 255], (1, 255))]

#: Each way a value reaches the field. `json` goes through `from_json`.
SPELLINGS = ["bytes", "bytearray", "list", "ndarray", "setter", "json"]


#: Run in the built project's interpreter, with `PKG`, `BITS`, `PLAIN`,
#: `GOOD`, `BAD` and `SPELLINGS` prepended. Prints `{case: answer}`.
_DRIVER = r"""
import json, sys
sys.path.insert(0, "src")
import numpy as np
mod = __import__(PKG + ".mix.mix", fromlist=["Mix"])
Mix, Seg, Src = mod.Mix, mod.Seg, mod.Src
base = json.loads(Mix([Seg.sum(Src())]).to_json())


def read(field, how, v):
    if how == "json":
        d = json.loads(json.dumps(base))
        d["segments"][0]["sources"][0][field] = list(v)
        m = Mix.from_json(json.dumps(d))
        got = getattr(m.segments[0].sources[0], field)
        m.close()
        return got
    if how == "setter":
        s = Src()
        setattr(s, field, list(v))
        return getattr(s, field)
    val = {
        "bytes": bytes, "bytearray": bytearray, "list": list,
        "ndarray": lambda x: np.array(x, dtype=np.uint8),
    }[how](v)
    return getattr(Src(**{field: val}), field)


out = {}
for field in BITS + PLAIN:
    for how in SPELLINGS:
        for v in GOOD + [b for b, _ in BAD]:
            case = f"{field} {how} {v}"
            try:
                got = read(field, how, v)
                out[case] = list(got) if got is not None else None
            except Exception as e:
                out[case] = f"{type(e).__name__}: {e}"
print(json.dumps(out))
"""


def _want(field: str, how: str, v: list) -> object:
    """What *v* by *how* must read as on *field*."""
    for bad, (i, x) in BAD:
        if v == bad and field in BIT_FIELDS:
            return f"ValueError: {_sentence(field, i, x)}"
    if field in PLAIN_FIELDS and how not in ("bytes", "json"):
        # Unchanged, and not this issue's: a plain field in a coercing
        # source shares the int-sequence branch's `v != 0`.
        return [int(x != 0) for x in v]
    return list(v)


def test_the_cases_are_not_vacuous() -> None:
    assert len(BIT_FIELDS) == 2 and PLAIN_FIELDS == ["raw"]
    assert [f for f in FIELDS if _composer.coerce_str_fn(f)], (
        "no field reads its str through the project"
    )


def test_each_field_passes_the_coercer_what_it_is() -> None:
    """The render, with no toolchain: a bit_pattern field's attach passes
    its name, a plain field's NULL -- through the host reader too."""
    cfg = {"project": {"name": PKG}, "module": {"mix": _module()}}
    text = _composer.render_source_type(cfg, "mix")
    for n in BIT_FIELDS:
        assert f'&_t.n_{n}, value, "{n}")' in text, n
    for n in PLAIN_FIELDS:
        assert f"&_t.n_{n}, value, NULL)" in text, n
    assert "return _attach_bytes(dst, n_dst, obj, bits);" in text
    json_text = _composer.render_json_funcs(cfg, "mix")
    for n in BIT_FIELDS:
        assert f'"{n}", (Py_ssize_t)(_k), (long)(_buf[_k])' in json_text, n
    for n in PLAIN_FIELDS:
        assert f'"{n}", (Py_ssize_t)(_k)' not in json_text, n


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_every_face_reads_one_rule(tmp_path: Path) -> None:
    """Build one composer and hand every spelling to every bytes field."""
    assert run_cli("new", PKG, cwd=tmp_path).returncode == 0
    proj = tmp_path / PKG
    inc = proj / "native" / "inc" / PKG / "mix"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "mix_core.h").write_text(_header(), encoding="utf-8")
    backing = proj / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    (backing / "mix_core.c").write_text(_source(), encoding="utf-8")
    (backing / "mix_bits.c").write_text(_BITS_C, encoding="utf-8")
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
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        proj,
    )
    _run(["cmake", "--build", "build", "--parallel", "4"], proj)

    driver = proj / "drive_gh2696.py"
    driver.write_text(
        f"PKG = {PKG!r}\nBITS = {BIT_FIELDS!r}\nPLAIN = {PLAIN_FIELDS!r}\n"
        f"GOOD = {GOOD!r}\nBAD = {BAD!r}\nSPELLINGS = {SPELLINGS!r}\n"
        f"{_DRIVER}",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(driver)], cwd=proj, capture_output=True, text=True
    )
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    got = json.loads(r.stdout)
    wrong = []
    for field in BIT_FIELDS + PLAIN_FIELDS:
        for how in SPELLINGS:
            for v in GOOD + [b for b, _ in BAD]:
                case = f"{field} {how} {v}"
                want = _want(field, how, v)
                if got.get(case) != want:
                    wrong.append(f"{case}: want {want!r}, got {got[case]!r}")
    assert len(got) == len(BIT_FIELDS + PLAIN_FIELDS) * len(SPELLINGS) * (
        len(GOOD) + len(BAD)
    )
    assert not wrong, f"{len(wrong)} wrong:\n" + "\n".join(wrong)
