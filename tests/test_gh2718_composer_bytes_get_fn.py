"""A generated ``bytes`` member reads as its bits and survives a copy
(gh-2718).

doppler's bit fields are ``wfm_seq_t`` members: a kind (literal, PN, Gold),
``len`` (the length in bits) and ``bits``, non-NULL for a literal only. A
generated Field such as ``"pn:192:15"`` is ``bits == NULL, len == 192``.
Two generated sites dropped it:

1. the source getter of a ``bytes`` field returned ``None`` whenever the
   pointer was NULL, and ``get_fn`` -- the key that names a field's read --
   was refused on a ``bytes`` field, so nothing could render the bits;
2. ``segments_to_list`` copied a pair as ``if (p && len) dup else
   p = NULL, len = 0``, zeroing the length of a generated member, so
   ``Composer(c.segments)`` composed a different source.

(a) ``get_fn`` is allowed on a ``bytes`` field, as
``size_t fn(const <struct> *, double fs, uint8_t *out, size_t max)``: the
length in elements, at most ``max`` written, ``out = NULL, max = 0`` sizing.
The getter and ``to_json`` read through it.
(b) The pair copy keeps the whole member and duplicates the pointer only
when it is non-NULL, so ``len`` survives -- and a ``complex`` stream, which
the copy did not duplicate at all, is duplicated too.

GATE: compiled. A backing that turns ``gen = N`` into a generated member
(``bits`` NULL, ``len`` N) and a ``get_fn`` that renders it. The source read
returns N bits; ``seq_len`` (a computed read of the member) shows the copy
kept ``len``, apart from the getter; ``Gen(c.segments)`` keeps the field;
``to_json`` round-trips; a ``complex`` stream copies without a double free;
and a ``flat_sources`` Segment's read of the field renders at the segment's
``fs``, as the source's own does at its ``fs`` (gh-2717).
"""

from __future__ import annotations

import copy
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
from test_composer_codegen import _cfg  # noqa: E402
from test_gh1711_composer_owned_pointer import CJSON_C, CJSON_H  # noqa: E402

MOD = "wfm_compose"


def _bits_cfg(**extra: object) -> dict:
    """The shared composer cfg with a relocated ``bytes`` field naming a
    ``get_fn``."""
    cfg = copy.deepcopy(_cfg())
    cfg["module"][MOD]["source"]["fields"].append(
        {
            "name": "seq",
            "type": "uint8_t*",
            "bytes": True,
            "c_ptr": "seq.bits",
            "c_len": "seq.len",
            "get_fn": "seq_bits",
            **extra,
        }
    )
    return cfg


class TestTheSeam:
    def test_the_bytes_prototype_is_declared_in_the_bridge(self) -> None:
        h = _composer.render_bridge_h(_bits_cfg(), MOD)
        assert (
            "size_t seq_bits(const wfm_source_t *, double, uint8_t *, size_t);"
        ) in h
        assert "seq_bits" in _composer.seam_fns(_bits_cfg(), MOD)

    def test_the_getter_sizes_then_renders(self) -> None:
        src = _composer.render_source_type(_bits_cfg(), MOD)
        assert "seq_bits(&self->src, fs, NULL, 0)" in src
        assert "Py_RETURN_NONE" in src
        # gh-2717: the read takes the rate, so a flat Segment read can hand
        # it the segment's.
        assert "Synth_read_seq(SynthObject *self, double fs)" in src
        assert "return Synth_read_seq(self, self->fs);" in src

    def test_to_json_renders_a_generated_member(self) -> None:
        cfg = _bits_cfg()
        cfg["module"][MOD]["json"] = {"enabled": True}
        js = _composer.render_json_funcs(cfg, MOD)
        assert "seq_bits(src, fs, NULL, 0)" in js

    def test_a_field_without_get_fn_renders_as_before(self) -> None:
        """No `get_fn`, no change: the record has no rate parameter."""
        cfg = copy.deepcopy(_cfg())
        cfg["module"][MOD]["json"] = {"enabled": True}
        js = _composer.render_json_funcs(cfg, MOD)
        assert "_json_add_source(cJSON *so, const wfm_source_t *src)\n" in js

    def test_a_complex_field_is_still_refused(self) -> None:
        cfg = copy.deepcopy(_cfg())
        cfg["module"][MOD]["source"]["fields"].append(
            {
                "name": "iq",
                "type": "float _Complex*",
                "complex": True,
                "get_fn": "nope",
            }
        )
        with pytest.raises(ValueError, match="gh-2718"):
            _composer.render_bridge_h(cfg, MOD)


class TestThePairCopy:
    def test_the_copy_keeps_len(self) -> None:
        """The whole member is copied; only a non-NULL pointer is
        duplicated, so a NULL pointer keeps its length."""
        ty = _composer.render_composer_type(_bits_cfg(), MOD)
        assert "syn->src.seq.len = 0;" not in ty
        assert "= syn->src.seq.bits;\n" in ty
        assert (
            "size_t _sz = syn->src.seq.len * sizeof *syn->src.seq.bits;" in ty
        )

    def test_the_cli_frees_the_relocated_member(self) -> None:
        cfg = _bits_cfg()
        cfg["module"][MOD]["cli"] = {"enabled": True}
        cli = _composer.render_cli(cfg, MOD)
        assert "free((void *)src.seq.bits);" in cli
        assert "free(src.seq);" not in cli


# -- the compiled proof ------------------------------------------------------

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

PKG = "genp"

_HEADER = """\
#ifndef GEN_CORE_H
#define GEN_CORE_H
#include <complex.h>
#include <stddef.h>
#include <stdint.h>

/* A run of bits however produced: a literal (bits non-NULL) or generated
 * (bits NULL, len the length the generator renders). */
typedef struct
{
  const uint8_t *bits;
  size_t len;
} gseq_t;

typedef struct
{
  double amp;
  size_t gen;
  gseq_t seq;
  float _Complex *iq;
  size_t n_iq;
} gsrc_t;

typedef struct
{
  gsrc_t *sources;
  size_t  n_sources;
  double  fs;
} gseg_t;

typedef struct gen_state gen_state_t;
gen_state_t *gen_create (const gseg_t *s, size_t n, int r, int c);
size_t gen_execute (gen_state_t *st, float _Complex *o, size_t m);
const gseg_t *gen_segments (const gen_state_t *st, size_t *n, int *r,
                            int *c);
void gen_destroy (gen_state_t *st);
#endif
"""

#: The state owns deep copies; `gen = N` on a source with no literal becomes
#: a generated member, and is then consumed, so only the member says so.
_SOURCE = f"""\
#include "{PKG}/gen/gen_core.h"
#include <stdlib.h>
#include <string.h>
struct gen_state
{{
  gseg_t *segs;
  size_t  n;
}};
gen_state_t *
gen_create (const gseg_t *s, size_t n, int r, int c)
{{
  (void)r;
  (void)c;
  gen_state_t *st = calloc (1, sizeof *st);
  st->segs        = calloc (n, sizeof *st->segs);
  for (size_t i = 0; i < n; i++)
    {{
      st->segs[i]         = s[i];
      st->segs[i].sources = calloc (s[i].n_sources, sizeof (gsrc_t));
      for (size_t k = 0; k < s[i].n_sources; k++)
        {{
          gsrc_t *d = &st->segs[i].sources[k];
          *d        = s[i].sources[k];
          if (d->seq.bits)
            {{
              uint8_t *b = malloc (d->seq.len);
              memcpy (b, d->seq.bits, d->seq.len);
              d->seq.bits = b;
            }}
          else if (d->gen)
            d->seq.len = d->gen;
          d->gen = 0;
          if (d->iq)
            {{
              float _Complex *q = malloc (d->n_iq * sizeof *q);
              memcpy (q, d->iq, d->n_iq * sizeof *q);
              d->iq = q;
            }}
        }}
    }}
  st->n = n;
  return st;
}}
size_t
gen_execute (gen_state_t *st, float _Complex *o, size_t m)
{{
  (void)st;
  (void)o;
  (void)m;
  return 0;
}}
const gseg_t *
gen_segments (const gen_state_t *st, size_t *n, int *r, int *c)
{{
  *n = st->n;
  *r = 0;
  *c = 0;
  return st->segs;
}}
void
gen_destroy (gen_state_t *st)
{{
  for (size_t i = 0; i < st->n; i++)
    {{
      for (size_t k = 0; k < st->segs[i].n_sources; k++)
        {{
          free ((void *)st->segs[i].sources[k].seq.bits);
          free (st->segs[i].sources[k].iq);
        }}
      free (st->segs[i].sources);
    }}
  free (st->segs);
  free (st);
}}
"""

#: The project's read of the member: a literal as stored, a generated one
#: rendered (every third bit set). A source not yet composed reads its
#: ``gen`` request at the rate it is read at, ``gen`` bits per microsecond,
#: so the rate a read is handed shows. Written against the bridge's
#: prototypes.
_RESOLVE = f"""\
#include "{PKG}/gen/gen_bridge.h"

size_t
gen_seq_bits (const gsrc_t *src, double fs, uint8_t *out, size_t max)
{{
  size_t n = src->seq.len;
  if (!n && !src->seq.bits)
    n = (size_t)((double)src->gen * fs / 1e6);
  for (size_t i = 0; i < n && i < max; i++)
    out[i] = src->seq.bits ? src->seq.bits[i] : (uint8_t)(i % 3 == 0);
  return n;
}}

size_t
gen_seq_len (const gsrc_t *src)
{{
  return src->seq.len;
}}
"""

_BACKING_CMAKE = """\
add_library(backing_core OBJECT gen_core.c gen_resolve.c cJSON.c)
target_include_directories(backing_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc
                                               ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(backing_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
# The test's cJSON stand-in is not the code under test.
set_source_files_properties(cJSON.c PROPERTIES COMPILE_OPTIONS "-w")
"""


def _module() -> dict:
    return {
        "kind": "composer",
        "backing": "gen",
        "source": {
            "struct": "gsrc_t",
            "type_name": "Src",
            "fields": [
                {"name": "amp", "type": "double", "default": "1.0"},
                {"name": "gen", "type": "size_t", "default": "0"},
                {
                    "name": "seq",
                    "type": "uint8_t*",
                    "bytes": True,
                    "c_ptr": "seq.bits",
                    "c_len": "seq.len",
                    "get_fn": "gen_seq_bits",
                },
                {"name": "iq", "type": "float _Complex*", "complex": True},
            ],
            "computed": [
                {"name": "seq_len", "type": "size_t", "fn": "gen_seq_len"}
            ],
        },
        "segment": {
            "type_name": "Seg",
            "struct": "gseg_t",
            "sources": "multi",
            "flat_sources": True,
            "fields": [{"name": "fs", "type": "double", "default": "1e6"}],
        },
        "oo": {"composer_type_name": "Gen"},
        "json": {"enabled": True},
        "extra_link_libs": ["backing_core"],
    }


#: Each block is one claim of the issue; a failure names it.
_DRIVER = """\
import json, sys
sys.path.insert(0, "src")
import numpy as np
from genp.gen.gen import Src, Seg, Gen

N = 12
WANT = bytes(int(i % 3 == 0) for i in range(N))

c = Gen([Seg.sum(Src(gen=N), fs=1e6)])
s = c.segments[0].sources[0]
# (b) the segment-list copy keeps a generated member's length...
assert s.seq_len == N, ("the copy kept len", s.seq_len)
# (a) ...and the read renders it through get_fn, not None.
assert s.seq == WANT, ("the read renders the bits", s.seq)

# The copy composes the same source: the field is kept, not dropped.
c2 = Gen(c.segments)
s2 = c2.segments[0].sources[0]
assert s2.seq_len == N, ("Gen(c.segments) kept len", s2.seq_len)
assert s2.seq == WANT, ("Gen(c.segments) kept the field", s2.seq)

# to_json writes the rendered bits, and the record round-trips.
text = c.to_json()
so = json.loads(text)["segments"][0]["sources"][0]
assert so.get("seq") == list(WANT), ("to_json kept the field", so)
back = Gen.from_json(text)
assert back.segments[0].sources[0].seq == WANT
assert back.to_json() == text, (back.to_json(), text)

# A literal reads as itself; an empty field reads None.
assert Src(seq=b"\\x01\\x00\\x01").seq == b"\\x01\\x00\\x01"
assert Src().seq is None

# A complex stream is duplicated by the copy, not aliased: the state and
# each copied source free their own (a double free aborts here).
iq = np.arange(4, dtype=np.complex64)
cq = Gen([Seg.sum(Src(iq=iq), fs=1e6)])
for _ in range(3):
    got = cq.segments[0].sources[0].iq
    assert np.array_equal(got, iq), got
del got
del cq

# gh-2717: a Segment's flat read of the field renders at the segment's fs,
# as the source's own read does at its fs -- not the inner source's.
src = Src(gen=3, fs=4e6)
seg = Seg(gen=3, fs=4e6)
assert len(src.seq) == 12, src.seq
assert seg.sources[0].fs != 4e6, ("the inner source keeps its own fs",
                                   seg.sources[0].fs)
assert seg.seq == src.seq, ("flat read at the segment's fs", seg.seq,
                            src.seq)
assert seg.seq != seg.sources[0].seq, "not the inner source's fs"
print("BYTES GET_FN OK")
"""


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    if _NO_TOOLCHAIN:
        pytest.skip("no cmake / C compiler")
    tmp = tmp_path_factory.mktemp("gh2718")
    assert run_cli("new", PKG, cwd=tmp).returncode == 0
    proj = tmp / PKG
    inc = proj / "native" / "inc" / PKG / "gen"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "gen_core.h").write_text(_HEADER, encoding="utf-8")
    backing = proj / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    (backing / "gen_core.c").write_text(_SOURCE, encoding="utf-8")
    (backing / "gen_resolve.c").write_text(_RESOLVE, encoding="utf-8")
    (backing / "cJSON.h").write_text(CJSON_H, encoding="utf-8")
    (backing / "cJSON.c").write_text(CJSON_C, encoding="utf-8")
    (backing / "CMakeLists.txt").write_text(_BACKING_CMAKE, encoding="utf-8")
    cfg = C.load(proj)
    cfg["project"]["c_deps"] = ["backing"]
    # The test's backing links into the extension only; no C consumer.
    cfg["project"]["status_allow"] = ["CMakeLists.txt:backing_core"]
    cfg.setdefault("module", {})["gen"] = _module()
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    # Warning-clean, so an undeclared call (implicit declaration) is red.
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
    return proj


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_generated_member_reads_copies_and_round_trips(
    project: Path,
) -> None:
    driver = project / "drive_gh2718.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(driver)],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0 and "BYTES GET_FN OK" in r.stdout, (
        r.returncode,
        r.stdout + r.stderr,
    )
