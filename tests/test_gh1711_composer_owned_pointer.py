"""gh-1711: a composer source field that holds a pointer the source owns.

doppler's `wfm_source_t.frame` is a `const wfm_frame_desc_t *`: a frame
DESCRIPTION the host already reads, writes and frees. No composer field shape
could carry one, so the generated `Source` had no `frame=` and Python had to
frame a source through flat scalar fields instead.

The design (settled on the issue) is an **owned pointer**, and ownership is a
COPY, not a kept reference. `Composer.segments` rebuilds sources from the
kernel's resolved structs and `from_json` builds one from text; neither has a
Python object to keep alive, so a borrowed pointer would alias state the
composer frees. Four host functions answer every face the same way:

========== ======================================================
copy_fn    a capsule's borrowed pointer -> the source's own copy;
           and the `segments` rebuild
free_fn    dealloc, the setter's old value, every teardown
parse_fn   a `str` value, the JSON record, the c-face CLI flag
format_fn  the getter (text), the JSON record
========== ======================================================

The e2e fixture below is the proof that matters: a real project is built, and
a live-copy counter in the host's own functions shows every copy freed exactly
once -- after dropping the host object, after a refused assignment, after the
`segments` rebuild, and after the composer closes.
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _composer  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _keys  # noqa: E402
from just_makeit import _textio  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from just_makeit._property import run as property_run  # noqa: E402
from test_composer_codegen import _cfg  # noqa: E402

MOD = "wfm_compose"

#: The owned-pointer row a unit test adds to the shared composer fixture.
FRAME = {
    "name": "frame",
    "type": "wfm_frame_desc_t *",
    "capsule": "p.frame.desc",
    "header": "wfm/frame.h",
    "copy_fn": "frame_copy",
    "free_fn": "frame_free",
    "parse_fn": "frame_parse",
    "format_fn": "frame_format",
}


def _with(**field) -> dict:
    cfg = copy.deepcopy(_cfg())
    cfg["module"][MOD]["source"]["fields"].append(field)
    return cfg


def _all_c(cfg: dict) -> str:
    """Every emitter a source field reaches -- one renderer's text is how a
    face goes missing while a test about another passes."""
    return "\n".join(
        (
            _composer.render_ext(cfg, MOD),
            _composer.render_json_funcs(cfg, MOD),
            _composer.render_cli(cfg, MOD),
            _composer.render_bridge_h(cfg, MOD),
        )
    )


# -- the declaration ---------------------------------------------------------


class TestTheDeclaration:
    def test_all_four_functions_are_required(self) -> None:
        for fn in _keys.COMPOSER_OWNED_PTR_FNS:
            row = {k: v for k, v in FRAME.items() if k != fn}
            with pytest.raises(ValueError) as exc:
                _composer.render_source_type(_with(**row), MOD)
            assert f"lacks {fn}" in str(exc.value)

    def test_object_alone_names_what_it_lacks(self) -> None:
        """`object` or `capsule` written alone is an owned pointer missing
        its functions, not a scalar missing its `type`."""
        with pytest.raises(ValueError) as exc:
            _composer.render_source_type(
                _with(name="frame", capsule="p.frame.desc"), MOD
            )
        assert "copy_fn, free_fn, parse_fn, format_fn" in str(exc.value)

    @pytest.mark.parametrize("key", ["bytes", "enum", "default", "c_len"])
    def test_another_shapes_key_is_refused(self, key: str) -> None:
        with pytest.raises(ValueError) as exc:
            _composer.render_source_type(_with(**FRAME, **{key: "x"}), MOD)
        assert key in str(exc.value)

    def test_the_type_must_be_a_pointer(self) -> None:
        with pytest.raises(ValueError) as exc:
            _composer.render_source_type(
                _with(**{**FRAME, "type": "wfm_frame_desc_t"}), MOD
            )
        assert "pointer" in str(exc.value)

    def test_object_and_capsule_together_are_refused(self) -> None:
        with pytest.raises(ValueError) as exc:
            _composer.render_source_type(_with(**FRAME, object="frame"), MOD)
        assert "Drop the `capsule` key" in str(exc.value)

    def test_a_segment_field_has_no_owned_pointer_face(self) -> None:
        assert "copy_fn" not in _keys.COMPOSER_FIELD_KEYS
        assert "copy_fn" in _keys.COMPOSER_SOURCE_FIELD_KEYS


class TestTheRowSurvivesASave:
    """The writer enumerates keys one by one, so a key it does not name is
    silently gone on the next mutating command (gh-1229, gh-1236)."""

    def test_every_owned_pointer_key_round_trips(self, tmp_path: Path) -> None:
        cfg = _with(**FRAME)
        C.save(tmp_path, cfg)
        rows = C.load(tmp_path)["module"][MOD]["source"]["fields"]
        assert rows[-1] == FRAME

    def test_an_object_row_with_no_type_round_trips(
        self, tmp_path: Path
    ) -> None:
        """`object` resolves the type, so the row has none -- the writer
        indexed `type` unconditionally."""
        row = {k: v for k, v in FRAME.items() if k not in ("type", "capsule")}
        row["object"] = "frame"
        C.save(tmp_path, _with(**row))
        rows = C.load(tmp_path)["module"][MOD]["source"]["fields"]
        assert rows[-1] == row


class TestEveryFaceCallsItsFunction:
    @pytest.fixture
    def c(self) -> str:
        return _all_c(_with(**FRAME))

    def test_the_header_is_included(self, c: str) -> None:
        assert '#include "wfm/frame.h"' in c

    def test_the_bridge_header_declares_the_four_functions(self) -> None:
        """The signatures are the design's contract, written down once where
        every other seam's is (gh-998), so a host function declared another
        way fails to compile -- and the binding and the CLI include it."""
        cfg = _with(**FRAME)
        h = _composer.render_bridge_h(cfg, MOD)
        assert '#include "wfm/frame.h"' in h
        for proto in (
            "wfm_frame_desc_t *frame_copy(const wfm_frame_desc_t *);",
            "void frame_free(wfm_frame_desc_t *);",
            "wfm_frame_desc_t *frame_parse(const char *);",
            "char *frame_format(const wfm_frame_desc_t *);",
        ):
            assert proto in h, proto
        inc = f'#include "{_composer.bridge_h(MOD)}"'
        assert inc in _composer.render_ext(cfg, MOD)
        assert inc in _composer.render_cli(cfg, MOD)
        # ...and so `c_prefix` never reads them as derived names (gh-1694).
        assert {
            "frame_copy",
            "frame_free",
            "frame_parse",
            "frame_format",
        } <= set(_composer.seam_fns(cfg, MOD))

    def test_a_segment_field_is_refused(self) -> None:
        cfg = copy.deepcopy(_cfg())
        cfg["module"][MOD]["segment"].setdefault("fields", []).append(
            {
                "name": "frame",
                **{k: v for k, v in FRAME.items() if k != "name"},
            }
        )
        with pytest.raises(ValueError) as exc:
            _composer.render_ext(cfg, MOD)
        msg = str(exc.value)
        assert "composer segment field 'frame'" in msg
        assert "only a source field" in msg

    def test_bind_copies_a_capsule_and_parses_text(self, c: str) -> None:
        assert "_jm_new = frame_copy(frame);" in c
        assert "_jm_new = frame_parse(_jm_s);" in c

    def test_the_old_value_is_freed_after_the_new_is_built(
        self, c: str
    ) -> None:
        body = c[c.index("_attach_frame(") :]
        assert body.index("frame_parse(") < body.index("frame_free(")

    def test_dealloc_frees(self, c: str) -> None:
        assert "frame_free((wfm_frame_desc_t *)self->src.frame);" in c

    def test_the_getter_formats(self, c: str) -> None:
        assert "char *_t = frame_format(self->src.frame);" in c

    def test_the_segments_rebuild_copies(self, c: str) -> None:
        assert "syn->src.frame = frame_copy(_a0)" in c

    def test_the_record_formats_and_parses(self, c: str) -> None:
        assert 'cJSON_AddItemToObject(so, "frame", _j);' in c
        assert "src->frame = _s ? frame_parse(_s) : NULL;" in c
        assert "frame_free((wfm_frame_desc_t *)segs[j].sources[k].frame);" in c

    def test_the_cli_parses_and_frees(self, c: str) -> None:
        assert "src.frame = frame_parse(frame);" in c
        assert "frame_free((wfm_frame_desc_t *)src.frame);" in c

    def test_c_ptr_names_the_member(self) -> None:
        """gh-1184's key: the struct member the pointer lives in, which
        every face must read and write, not the field's name."""
        c = _all_c(_with(**FRAME, c_ptr="fd"))
        assert "_jm_src->fd = _jm_new;" in c
        assert "frame_free((wfm_frame_desc_t *)self->src.fd);" in c
        assert "char *_t = frame_format(self->src.fd);" in c
        assert "syn->src.fd = frame_copy(_a0)" in c
        assert "src.fd = frame_parse(frame);" in c
        assert "src->frame" not in c and "src.frame" not in c

    def test_the_stub(self) -> None:
        pyi = _composer.render_pyi(_with(**FRAME), MOD)
        assert "frame: object | str | None = ..." in pyi
        assert "def frame(self) -> str | None: ..." in pyi
        assert "def frame(self, value: object | str | None) -> None: ..." in (
            pyi
        )


# -- end to end --------------------------------------------------------------

#: The backing kernel and the owned pointer's host functions. The pointed-to
#: type is the `frame` component's own state, published by its capsule, so
#: `object = "frame"` resolves the type, capsule and class unaided.
PLAYLIST_H = """\
#ifndef PLAYLIST_CORE_H
#define PLAYLIST_CORE_H

#include <complex.h>
#include <stddef.h>

#include "studio/frame/frame_core.h"

typedef studio_frame_state_t desc_t;

/* `frame` is const: the struct is written for a borrowing consumer. */
typedef struct
{
  double        gain;
  const desc_t *frame;
} clip_t;

typedef struct
{
  clip_t *sources;
  size_t  n_sources;
  size_t  dur;
  double  fs;
} track_t;

typedef struct playlist_state playlist_state_t;

playlist_state_t *playlist_create (const track_t *tracks, size_t n,
                                   int repeat, int continuous);
size_t playlist_execute (playlist_state_t *state, float _Complex *out,
                         size_t max);
const track_t *playlist_segments (const playlist_state_t *state, size_t *n,
                                  int *repeat, int *continuous);
void           playlist_destroy (playlist_state_t *state);
playlist_state_t *playlist_from_file (const char *path);

/* The four host functions, and a count of live copies so the test can see
 * every one freed exactly once. */
desc_t *desc_copy (const desc_t *d);
void    desc_free (desc_t *d);
desc_t *desc_parse (const char *text);
char   *desc_format (const desc_t *d);
int     desc_live (void);

#endif
"""

PLAYLIST_C = """\
#include "studio/playlist/playlist_core.h"

#include <stdio.h>
#include <stdlib.h>

static int live;

int
desc_live (void)
{
  return live;
}

desc_t *
desc_copy (const desc_t *d)
{
  desc_t *c = malloc (sizeof *c);
  if (!c)
    return NULL;
  *c = *d;
  live++;
  return c;
}

void
desc_free (desc_t *d)
{
  if (!d)
    return;
  live--;
  free (d);
}

/* Two spellings, so both shapes of the record are reachable: a JSON object
 * below 100, and bare `n=<n>` text -- not JSON -- from 100 up. */
desc_t *
desc_parse (const char *text)
{
  desc_t d = { 0 };
  if (sscanf (text, " { \\"n\\" : %d }", &d.n) != 1
      && sscanf (text, "n=%d", &d.n) != 1)
    return NULL;
  if (d.n < 0)
    return NULL;
  return desc_copy (&d);
}

char *
desc_format (const desc_t *d)
{
  char *s = malloc (32);
  if (s && d->n < 100)
    snprintf (s, 32, "{\\"n\\": %d}", d->n);
  else if (s)
    snprintf (s, 32, "n=%d", d->n);
  return s;
}

struct playlist_state
{
  track_t *tracks;
  size_t   n_tracks;
  size_t   track_i;
  size_t   pos;
};

void
playlist_destroy (playlist_state_t *st)
{
  if (!st)
    return;
  for (size_t i = 0; i < st->n_tracks; i++)
    {
      for (size_t k = 0; k < st->tracks[i].n_sources; k++)
        desc_free ((desc_t *)st->tracks[i].sources[k].frame);
      free (st->tracks[i].sources);
    }
  free (st->tracks);
  free (st);
}

/* Deep-copies every frame: the array jm passes is transient, and its
 * pointers belong to the Python sources. */
playlist_state_t *
playlist_create (const track_t *tracks, size_t n, int repeat, int continuous)
{
  (void)repeat;
  (void)continuous;
  playlist_state_t *st = calloc (1, sizeof *st);
  if (!st || !(st->tracks = calloc (n, sizeof *st->tracks)))
    {
      free (st);
      return NULL;
    }
  for (size_t i = 0; i < n; i++)
    {
      st->tracks[i]         = tracks[i];
      st->tracks[i].sources = calloc (tracks[i].n_sources, sizeof (clip_t));
      st->n_tracks          = i + 1;
      if (!st->tracks[i].sources)
        {
          st->tracks[i].n_sources = 0;
          playlist_destroy (st);
          return NULL;
        }
      for (size_t k = 0; k < tracks[i].n_sources; k++)
        {
          st->tracks[i].sources[k] = tracks[i].sources[k];
          if (tracks[i].sources[k].frame)
            st->tracks[i].sources[k].frame
                = desc_copy (tracks[i].sources[k].frame);
        }
    }
  return st;
}

/* Each sample is the sum of gain + frame->n, so the frame reaches the
 * output. */
size_t
playlist_execute (playlist_state_t *st, float _Complex *out, size_t max)
{
  size_t n = 0;
  while (n < max && st->track_i < st->n_tracks)
    {
      track_t *tr  = &st->tracks[st->track_i];
      double   sum = 0.0;
      for (size_t k = 0; k < tr->n_sources; k++)
        sum += tr->sources[k].gain
               + (tr->sources[k].frame ? tr->sources[k].frame->n : 0);
      out[n++] = (float _Complex)sum;
      if (++st->pos >= tr->dur)
        {
          st->pos = 0;
          st->track_i++;
        }
    }
  return n;
}

const track_t *
playlist_segments (const playlist_state_t *st, size_t *n, int *repeat,
                   int *continuous)
{
  *n          = st->n_tracks;
  *repeat     = 0;
  *continuous = 0;
  return st->tracks;
}

/* The CLI's --from-file; this fixture has no file format. */
playlist_state_t *
playlist_from_file (const char *path)
{
  (void)path;
  return NULL;
}
"""

#: A computed property is the one generated route from Python into plain C,
#: so it carries the live-copy counter out.
BRIDGE_C = """\
#include "studio/playlist/playlist_bridge.h"

int
clip_live (const clip_t *src)
{
  (void)src;
  return desc_live ();
}
"""

BACKING_CMAKE = """\
add_library(backing_core OBJECT playlist_core.c playlist_bridge.c cJSON.c)
target_include_directories(backing_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc
                                               ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(backing_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
"""

COMPOSER_TOML = """
[module.playlist]
kind = "composer"
backing = "playlist"
composes = ["clip"]
depends_on = [{ name = "frame", link = true }]
extra_link_libs = ["backing_core"]

[module.playlist.source]
object = "clip"
struct = "clip_t"
type_name = "Clip"

[[module.playlist.source.fields]]
name = "gain"
type = "double"
default = "1.0"

[[module.playlist.source.fields]]
name = "frame"
object = "frame"
header = "studio/playlist/playlist_core.h"
copy_fn = "desc_copy"
free_fn = "desc_free"
parse_fn = "desc_parse"
format_fn = "desc_format"

[[module.playlist.source.computed]]
name = "live"
type = "int"
fn = "clip_live"

[module.playlist.segment]
type_name = "Track"
struct = "track_t"
sources = "multi"

[[module.playlist.segment.fields]]
name = "dur"
type = "size_t"
default = "2"

[module.playlist.oo]
composer_type_name = "Mix"

[module.playlist.cli]
enabled = true
name = "playlist_cli"

[module.playlist.json]
enabled = true
"""

#: Driven in a subprocess, so a double free is a crash this test sees rather
#: than one that takes pytest down with it.
DRIVE = """\
import gc
import sys

sys.path.insert(0, "src")

import numpy as np

from studio.frame import Frame
from studio.playlist.playlist import Clip, Mix, Track


def live():
    gc.collect()
    return Clip().live


assert live() == 0

# None is the common case: nothing is allocated.
c = Clip(gain=1.0)
assert c.frame is None and live() == 0

# Text binds through parse_fn and reads back through format_fn.
c.frame = '{"n": 3}'
assert c.frame == '{"n": 3}', c.frame
assert live() == 1

# A host object is COPIED through copy_fn: the source owns a snapshot, so
# dropping the host does not reach it.
f = Frame(5)
c2 = Clip(gain=2.0, frame=f)
assert live() == 2
del f
gc.collect()
assert c2.frame == '{"n": 5}', c2.frame

# A refused assignment leaves the source as it was and leaks nothing.
try:
    c2.frame = '{"n": -1}'
    raise AssertionError("expected the parser to refuse")
except ValueError as e:
    assert "desc_parse" in str(e), e
assert c2.frame == '{"n": 5}' and live() == 2
# Text is the WHOLE str: an embedded NUL would hand parse_fn a prefix,
# which it would accept as a different value.
try:
    c2.frame = '{"n": 9}\\x00{"n": 1}'
    raise AssertionError("expected an embedded NUL to be refused")
except ValueError as e:
    assert "embedded null character" in str(e), e
assert c2.frame == '{"n": 5}' and live() == 2
try:
    c2.frame = 42
    raise AssertionError("expected a TypeError")
except TypeError:
    pass
assert c2.frame == '{"n": 5}' and live() == 2

# The backing deep-copies it on create, and the value reaches the kernel.
m = Mix(Track.sum(c2, dur=2))
assert live() == 3
assert np.allclose(m.execute(4), [7, 7])

# segments rebuilds each source through copy_fn: its own copy, freed once.
segs = m.segments
assert segs[0].sources[0].frame == '{"n": 5}'
assert live() == 4
del segs
assert live() == 3

# Clearing, dealloc and the composer's close all free.
c.frame = None
assert c.frame is None and live() == 2
del c, c2
assert live() == 1
m.close()
del m
assert live() == 0, live()

# The single-source Track constructor forwards the field to the source.
t = Track(gain=1.0, frame='{"n": 1}', dur=1)
assert t.sources[0].frame == '{"n": 1}'
del t
assert live() == 0

print("owned pointer: PASSED")
"""


#: cJSON is the project's vendored dependency (the generic record path
#: includes ``cJSON.h`` and the project links it), and jm vendors none. This
#: is the subset the generated record calls, with cJSON's own struct layout
#: and names, so the record face RUNS here -- a compile-only probe cannot see
#: a copy freed twice or a parsed value leaked.
CJSON_H = r"""#ifndef cJSON__h
#define cJSON__h

#include <stddef.h>

typedef int cJSON_bool;

typedef struct cJSON
{
  struct cJSON *next;
  struct cJSON *prev;
  struct cJSON *child;
  int           type;
  char         *valuestring;
  int           valueint;
  double        valuedouble;
  char         *string;
} cJSON;

cJSON     *cJSON_Parse (const char *s);
cJSON     *cJSON_ParseWithLength (const char *s, size_t n);
char      *cJSON_Print (const cJSON *it);
char      *cJSON_PrintUnformatted (const cJSON *it);
void       cJSON_Delete (cJSON *it);
void       cJSON_free (void *p);
cJSON     *cJSON_CreateObject (void);
cJSON     *cJSON_CreateArray (void);
cJSON     *cJSON_CreateNumber (double v);
cJSON     *cJSON_CreateString (const char *s);
cJSON_bool cJSON_AddItemToArray (cJSON *a, cJSON *it);
cJSON_bool cJSON_AddItemToObject (cJSON *o, const char *k, cJSON *it);
cJSON     *cJSON_AddNumberToObject (cJSON *o, const char *k, double v);
cJSON     *cJSON_AddStringToObject (cJSON *o, const char *k, const char *s);
cJSON     *cJSON_AddBoolToObject (cJSON *o, const char *k, cJSON_bool b);
cJSON     *cJSON_AddArrayToObject (cJSON *o, const char *k);
cJSON     *cJSON_AddObjectToObject (cJSON *o, const char *k);
cJSON     *cJSON_GetObjectItemCaseSensitive (const cJSON *o, const char *k);
cJSON     *cJSON_GetObjectItem (const cJSON *o, const char *k);
cJSON     *cJSON_GetArrayItem (const cJSON *a, int i);
int        cJSON_GetArraySize (const cJSON *a);
double     cJSON_GetNumberValue (const cJSON *it);
char      *cJSON_GetStringValue (const cJSON *it);
cJSON_bool cJSON_IsNull (const cJSON *it);
cJSON_bool cJSON_IsString (const cJSON *it);
cJSON_bool cJSON_IsNumber (const cJSON *it);
cJSON_bool cJSON_IsArray (const cJSON *it);
cJSON_bool cJSON_IsObject (const cJSON *it);
cJSON_bool cJSON_IsTrue (const cJSON *it);
cJSON_bool cJSON_IsBool (const cJSON *it);

#define cJSON_ArrayForEach(e, a)                                            \
  for ((e) = (a) ? (a)->child : NULL; (e) != NULL; (e) = (e)->next)

#endif
"""

CJSON_C = r"""#include "cJSON.h"

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { J_NULL = 1, J_FALSE, J_TRUE, J_NUM, J_STR, J_ARR, J_OBJ };

static cJSON *
node (int t)
{
  cJSON *n = calloc (1, sizeof *n);
  if (n)
    n->type = t;
  return n;
}

static char *
dupn (const char *s, size_t n)
{
  char *d = malloc (n + 1);
  if (d)
    {
      memcpy (d, s, n);
      d[n] = 0;
    }
  return d;
}

void
cJSON_Delete (cJSON *c)
{
  while (c)
    {
      cJSON *nx = c->next;
      cJSON_Delete (c->child);
      free (c->valuestring);
      free (c->string);
      free (c);
      c = nx;
    }
}

void
cJSON_free (void *p)
{
  free (p);
}

cJSON *
cJSON_CreateObject (void)
{
  return node (J_OBJ);
}

cJSON *
cJSON_CreateArray (void)
{
  return node (J_ARR);
}

cJSON *
cJSON_CreateNumber (double v)
{
  cJSON *n = node (J_NUM);
  if (n)
    {
      n->valuedouble = v;
      n->valueint    = (int)v;
    }
  return n;
}

cJSON *
cJSON_CreateString (const char *s)
{
  cJSON *n = node (J_STR);
  if (n)
    n->valuestring = dupn (s, strlen (s));
  return n;
}

cJSON_bool
cJSON_AddItemToArray (cJSON *a, cJSON *it)
{
  if (!a || !it)
    return 0;
  if (!a->child)
    a->child = it;
  else
    {
      cJSON *c = a->child;
      while (c->next)
        c = c->next;
      c->next  = it;
      it->prev = c;
    }
  return 1;
}

cJSON_bool
cJSON_AddItemToObject (cJSON *o, const char *k, cJSON *it)
{
  if (!o || !it)
    return 0;
  free (it->string);
  it->string = dupn (k, strlen (k));
  return cJSON_AddItemToArray (o, it);
}

static cJSON *
add (cJSON *o, const char *k, cJSON *it)
{
  if (!cJSON_AddItemToObject (o, k, it))
    {
      cJSON_Delete (it);
      return NULL;
    }
  return it;
}

cJSON *
cJSON_AddNumberToObject (cJSON *o, const char *k, double v)
{
  return add (o, k, cJSON_CreateNumber (v));
}

cJSON *
cJSON_AddStringToObject (cJSON *o, const char *k, const char *s)
{
  return add (o, k, cJSON_CreateString (s));
}

cJSON *
cJSON_AddBoolToObject (cJSON *o, const char *k, cJSON_bool b)
{
  return add (o, k, node (b ? J_TRUE : J_FALSE));
}

cJSON *
cJSON_AddArrayToObject (cJSON *o, const char *k)
{
  return add (o, k, cJSON_CreateArray ());
}

cJSON *
cJSON_AddObjectToObject (cJSON *o, const char *k)
{
  return add (o, k, cJSON_CreateObject ());
}

cJSON *
cJSON_GetObjectItemCaseSensitive (const cJSON *o, const char *k)
{
  for (cJSON *c = o ? o->child : NULL; c; c = c->next)
    if (c->string && !strcmp (c->string, k))
      return c;
  return NULL;
}

cJSON *
cJSON_GetObjectItem (const cJSON *o, const char *k)
{
  return cJSON_GetObjectItemCaseSensitive (o, k);
}

cJSON *
cJSON_GetArrayItem (const cJSON *a, int i)
{
  cJSON *c = a ? a->child : NULL;
  while (c && i-- > 0)
    c = c->next;
  return c;
}

int
cJSON_GetArraySize (const cJSON *a)
{
  int n = 0;
  for (cJSON *c = a ? a->child : NULL; c; c = c->next)
    n++;
  return n;
}

cJSON_bool cJSON_IsNull (const cJSON *it) { return it && it->type == J_NULL; }
cJSON_bool cJSON_IsString (const cJSON *it) { return it && it->type == J_STR; }
cJSON_bool cJSON_IsNumber (const cJSON *it) { return it && it->type == J_NUM; }
cJSON_bool cJSON_IsArray (const cJSON *it) { return it && it->type == J_ARR; }
cJSON_bool cJSON_IsObject (const cJSON *it) { return it && it->type == J_OBJ; }
cJSON_bool cJSON_IsTrue (const cJSON *it) { return it && it->type == J_TRUE; }

cJSON_bool
cJSON_IsBool (const cJSON *it)
{
  return it && (it->type == J_TRUE || it->type == J_FALSE);
}

double
cJSON_GetNumberValue (const cJSON *it)
{
  return cJSON_IsNumber (it) ? it->valuedouble : NAN;
}

char *
cJSON_GetStringValue (const cJSON *it)
{
  return cJSON_IsString (it) ? it->valuestring : NULL;
}

/* -- parse ---------------------------------------------------------------- */

static const char *
ws (const char *p)
{
  while (*p && isspace ((unsigned char)*p))
    p++;
  return p;
}

static char *
pstr (const char **pp)
{
  const char *p = *pp;
  if (*p != '"')
    return NULL;
  p++;
  char  *o = malloc (strlen (p) + 1);
  size_t n = 0;
  if (!o)
    return NULL;
  while (*p && *p != '"')
    {
      if (*p == '\\')
        {
          p++;
          if (!*p)
            break;
          o[n++] = *p == 'n' ? '\n' : *p == 't' ? '\t' : *p;
          p++;
        }
      else
        o[n++] = *p++;
    }
  if (*p != '"')
    {
      free (o);
      return NULL;
    }
  o[n] = 0;
  *pp  = p + 1;
  return o;
}

static cJSON *pval (const char **pp);

static cJSON *
pseq (const char **pp, int obj)
{
  cJSON      *c = node (obj ? J_OBJ : J_ARR);
  const char *p = ws (*pp + 1);
  if (!c)
    return NULL;
  if (*p == (obj ? '}' : ']'))
    {
      *pp = p + 1;
      return c;
    }
  for (;;)
    {
      char *k = NULL;
      if (obj)
        {
          if (!(k = pstr (&p)))
            goto bad;
          p = ws (p);
          if (*p++ != ':')
            {
              free (k);
              goto bad;
            }
        }
      cJSON *v = pval (&p);
      if (!v)
        {
          free (k);
          goto bad;
        }
      v->string = k;
      cJSON_AddItemToArray (c, v);
      p = ws (p);
      if (*p == ',')
        {
          p = ws (p + 1);
          continue;
        }
      if (*p == (obj ? '}' : ']'))
        {
          *pp = p + 1;
          return c;
        }
      goto bad;
    }
bad:
  cJSON_Delete (c);
  return NULL;
}

static cJSON *
pval (const char **pp)
{
  const char *p = ws (*pp);
  cJSON      *c = NULL;
  if (*p == '{' || *p == '[')
    {
      c = pseq (&p, *p == '{');
    }
  else if (*p == '"')
    {
      char *s = pstr (&p);
      if (s && (c = node (J_STR)))
        c->valuestring = s;
      else
        free (s);
    }
  else if (!strncmp (p, "true", 4) && (c = node (J_TRUE)))
    p += 4;
  else if (!strncmp (p, "false", 5) && (c = node (J_FALSE)))
    p += 5;
  else if (!strncmp (p, "null", 4) && (c = node (J_NULL)))
    p += 4;
  else
    {
      char  *end;
      double v = strtod (p, &end);
      if (end != p)
        {
          c = cJSON_CreateNumber (v);
          p = end;
        }
    }
  *pp = p;
  return c;
}

cJSON *
cJSON_Parse (const char *s)
{
  const char *p = s;
  cJSON      *c = pval (&p);
  if (c && *ws (p))
    {
      cJSON_Delete (c);
      return NULL;
    }
  return c;
}

cJSON *
cJSON_ParseWithLength (const char *s, size_t n)
{
  char  *d = dupn (s, n);
  cJSON *c = d ? cJSON_Parse (d) : NULL;
  free (d);
  return c;
}

/* -- print ---------------------------------------------------------------- */

typedef struct
{
  char  *b;
  size_t n, cap;
} buf_t;

static int
put (buf_t *b, const char *s, size_t n)
{
  if (b->n + n + 1 > b->cap)
    {
      size_t cap = (b->cap + n + 1) * 2;
      char  *nb  = realloc (b->b, cap);
      if (!nb)
        return 0;
      b->b   = nb;
      b->cap = cap;
    }
  memcpy (b->b + b->n, s, n);
  b->n += n;
  b->b[b->n] = 0;
  return 1;
}

static int
pq (buf_t *b, const char *s)
{
  int ok = put (b, "\"", 1);
  for (; ok && *s; s++)
    ok = (*s == '"' || *s == '\\') ? put (b, "\\", 1) && put (b, s, 1)
         : *s == '\n'              ? put (b, "\\n", 2)
                                   : put (b, s, 1);
  return ok && put (b, "\"", 1);
}

static int
pr (buf_t *b, const cJSON *it)
{
  char num[64];
  switch (it->type)
    {
    case J_NULL:
      return put (b, "null", 4);
    case J_FALSE:
      return put (b, "false", 5);
    case J_TRUE:
      return put (b, "true", 4);
    case J_NUM:
      snprintf (num, sizeof num, "%.17g", it->valuedouble);
      return put (b, num, strlen (num));
    case J_STR:
      return pq (b, it->valuestring);
    default:
      {
        int obj = it->type == J_OBJ;
        if (!put (b, obj ? "{" : "[", 1))
          return 0;
        for (const cJSON *c = it->child; c; c = c->next)
          {
            if (c != it->child && !put (b, ",", 1))
              return 0;
            if (obj && !(pq (b, c->string) && put (b, ":", 1)))
              return 0;
            if (!pr (b, c))
              return 0;
          }
        return put (b, obj ? "}" : "]", 1);
      }
    }
}

char *
cJSON_PrintUnformatted (const cJSON *it)
{
  buf_t b = { 0 };
  if (!it || !pr (&b, it))
    {
      free (b.b);
      return NULL;
    }
  return b.b;
}

char *
cJSON_Print (const cJSON *it)
{
  return cJSON_PrintUnformatted (it);
}
"""


#: The generic record: `format_fn`'s text nested as JSON when it is JSON and
#: kept as a string when not; read back through `parse_fn` in either
#: spelling, with every parsed copy freed by the record's own teardown.
DRIVE_JSON = """\
import gc
import json
import sys

sys.path.insert(0, "src")

import numpy as np

from studio.playlist.playlist import Clip, Mix, Track


def live():
    gc.collect()
    return Clip().live


m = Mix(
    [
        Track.sum(Clip(gain=1.0, frame='{"n": 2}'), Clip(gain=0.5), dur=2),
        Track.sum(Clip(gain=0.0, frame="n=150"), dur=1),
    ]
)
text = m.to_json()
doc = json.loads(text)
s0 = doc["segments"][0]["sources"]
# JSON text is nested as a value, so the record reads as one document.
assert s0[0]["frame"] == {"n": 2}, s0
# A NULL pointer writes nothing.
assert "frame" not in s0[1], s0
# Text that is not JSON is kept as a string.
assert doc["segments"][1]["sources"][0]["frame"] == "n=150", doc

# Only the kernel's copies outlive the read: every parsed one is freed.
before = live()
m2 = Mix.from_json(text)
assert live() == before + 2, (live(), before)
assert np.allclose(m2.execute(3), [3.5, 3.5, 150.0])
segs = m2.segments
assert segs[0].sources[0].frame == '{"n": 2}'
assert segs[0].sources[1].frame is None
assert segs[1].sources[0].frame == "n=150"
del segs
m2.close()
del m2
assert live() == before, (live(), before)

# A JSON value written as a string is read too.
doc["segments"][0]["sources"][0]["frame"] = '{"n": 7}'
m3 = Mix.from_json(json.dumps(doc))
assert m3.segments[0].sources[0].frame == '{"n": 7}'
m3.close()
del m3
assert live() == before, (live(), before)

# A value the host refuses fails the whole record -- after the first
# segment's copy was parsed, which the teardown must still free.
doc["segments"][1]["sources"][0]["frame"] = "nope"
try:
    Mix.from_json(json.dumps(doc))
    raise AssertionError("expected the record to be refused")
except ValueError:
    pass
assert live() == before, (live(), before)

print("owned pointer record: PASSED")
"""


def _run(cmd: list, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "PYTHONPATH": str(cwd / "src")},
    )


def _ok(r: subprocess.CompletedProcess) -> str:
    assert r.returncode == 0, (r.args, r.stdout[-3000:], r.stderr[-3000:])
    return r.stdout


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """`studio`: a `frame` object publishing its state as a capsule, a `clip`
    generator, and a composer whose source owns a copy of a frame. Built with
    the prefix `jm new` defaults to, so the author-named host functions are
    also shown to survive it unrenamed."""
    import contextlib
    import io

    root = tmp_path_factory.mktemp("g1711") / "studio"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("studio", root)
        object_run(
            root,
            "frame",
            None,
            state_vars=[("n", "int", "0")],
            arg_type="double",
            return_type="double",
        )
        property_run(
            root,
            "frame",
            "_capsule",
            None,
            "capsule",
            False,
            capsule="studio.frame.desc",
        )
        object_run(
            root,
            "clip",
            None,
            state_vars=[("level", "double", "0.0")],
            arg_type="void",
            return_type="float _Complex",
        )
        inc = root / "native" / "inc" / "studio" / "playlist"
        inc.mkdir(parents=True)
        backing = root / "native" / "src" / "backing"
        backing.mkdir(parents=True)
        _textio.write_text(inc / "playlist_core.h", PLAYLIST_H)
        _textio.write_text(backing / "playlist_core.c", PLAYLIST_C)
        _textio.write_text(backing / "playlist_bridge.c", BRIDGE_C)
        _textio.write_text(backing / "CMakeLists.txt", BACKING_CMAKE)
        _textio.write_text(backing / "cJSON.h", CJSON_H)
        _textio.write_text(backing / "cJSON.c", CJSON_C)
        toml = root / "just-makeit.toml"
        text = toml.read_text(encoding="utf-8").replace(
            "[project]\n", '[project]\nc_deps = ["backing"]\n', 1
        )
        _textio.write_text(toml, text + COMPOSER_TOML)
        apply_run(root)
    _ok(
        _run(
            [
                "cmake",
                "-S",
                ".",
                "-B",
                "build",
                "-DCMAKE_BUILD_TYPE=Debug",
                "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
                f"-DPython3_EXECUTABLE={sys.executable}",
            ],
            root,
        )
    )
    _ok(_run(["cmake", "--build", "build", "--parallel", "4"], root))
    return root


def test_every_python_face_owns_and_frees_its_copy(project: Path) -> None:
    out = _ok(_run([sys.executable, "-c", DRIVE], project))
    assert "owned pointer: PASSED" in out


def test_the_stub_names_the_host_class(project: Path) -> None:
    """`object` resolves the class, and the stub imports it: the keyword
    and the setter take it, the property reads text."""
    pyi = (project / "src" / "studio" / "playlist" / "playlist.pyi").read_text(
        encoding="utf-8"
    )
    assert "from studio.frame import Frame" in pyi
    assert "frame: Frame | str | None = ...," in pyi
    assert "def frame(self) -> str | None: ..." in pyi
    assert "def frame(self, value: Frame | str | None) -> None: ..." in pyi


def test_the_json_record_nests_parses_and_frees(project: Path) -> None:
    out = _ok(_run([sys.executable, "-c", DRIVE_JSON], project))
    assert "owned pointer record: PASSED" in out


def _cli(project: Path) -> Path:
    exe = [
        p
        for p in (project / "build").rglob("playlist_cli*")
        if p.is_file() and os.access(p, os.X_OK)
    ]
    assert exe, "the c-face CLI was not built"
    return exe[0]


def test_the_cli_binds_the_text_form(project: Path) -> None:
    out = _ok(
        _run(
            [str(_cli(project)), "--frame", '{"n": 4}', "--file-type", "csv"],
            project,
        )
    )
    # gain 1.0 + frame.n 4, for the default dur of two samples.
    assert out.splitlines() == ["5.000000000,0.000000000"] * 2, out


def test_the_cli_refuses_text_the_host_refuses(project: Path) -> None:
    r = _run([str(_cli(project)), "--frame", "nope"], project)
    assert r.returncode == 2
    assert "bad --frame nope" in r.stderr


def test_the_host_functions_keep_their_names_under_the_prefix(
    project: Path,
) -> None:
    """Author-named, so the `c_prefix` rename never touches them (gh-1591),
    while the pointer type `object` resolved did take the stem."""
    ext = (
        project / "native" / "src" / "playlist" / "playlist_ext.c"
    ).read_text(encoding="utf-8")
    for fn in ("desc_copy", "desc_free", "desc_parse", "desc_format"):
        assert f"{fn}(" in ext
        assert f"studio_{fn}" not in ext
    assert "studio_frame_state_t *" in ext
