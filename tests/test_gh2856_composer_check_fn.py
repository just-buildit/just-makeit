"""A composer source's whole-struct validator, ``check_fn``, is called by
every face that writes the source without a field's ``set_fn`` (gh-2856).

doppler's ``ofdm`` rate is two enum fields, ``constellation`` and
``code_rate``, given whole or both ``auto``. gh-2753's ``set_fn`` keeps a
live setter's pair whole, but the constructor, ``from_json`` and the record
assigned fields directly and called no project code, so
``Synth(type="ofdm", constellation="bpsk")`` built half a pair that only
the first ``steps()`` refused (``bridge_error_fn``).

Decisions (the issue is the spec; ``create_why`` was rejected there):

1. ``[module.X.source] check_fn = "<fn>"`` names
   ``const char *fn(const <source.struct> *)``: ``NULL`` is whole, a
   sentence is raised as ``ValueError``. Its prototype is a seam in
   ``<cname>_bridge.h`` (``_composer.seam_fns``), so ``_csym`` reads it as
   author-named (gh-1694).
2. ONE emitter, ``_composer.check_c``, renders every call: the source
   constructor (so a factory and the inline ``Segment`` too), each plain
   setter, the generic JSON record (``from_json`` / ``from_file``), a
   delegated ``from_json_fn`` / ``from_file_fn`` (every source of the state
   it built, ``_check_state_c``) and the c-face CLI (flags, and
   ``--from-file``).
3. A field naming ``set_fn`` calls only ``set_fn``. A plain setter writes
   to a COPY, checks it, and commits only on ``NULL``: a refused
   assignment leaves the source as it was. An owned buffer's
   ``_replace_<name>`` checks its copy for the setter and not for
   ``__init__``, which checks once after every field.
4. Refused at load: a ``check_fn`` that is not a string, and one on any
   composer table but ``[module.X.source]``.

A composer source has no ``__setstate__``: it is not picklable, so the
"``__setstate__`` when serializable" face does not exist. The compiled
proof pins that, so a pickle face added later must come here.

GATE: one compiled project, two composer modules (generic JSON + CLI, and
delegated JSON), whose ``check_fn`` refuses half a ``num``/``den`` pair, a
negative or inverted ``gain`` range and a ``bits`` run past 4. Every face
raises the sentence; every refused setter leaves the source unchanged; a
``set_fn`` field is not checked after its writer.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from _cmake import PYTHON3_ARG
from _jmrun import run_cli

from just_makeit import _composer
from just_makeit import _config as C
from just_makeit import _csym
from just_makeit import _keys
from just_makeit._report import Refusal

sys.path.insert(0, str(Path(__file__).parent))
import test_gh2710_composer_field_get_fn as base  # noqa: E402
from test_gh1711_composer_owned_pointer import CJSON_C, CJSON_H  # noqa: E402

PKG = "chkp"
HALF = "num and den are one rate: give both, or neither"
GAIN = "gain is a level: >= 0, and a range runs low to high"
BITS = "bits holds at most 4"
TAG = "tag 13 is reserved"
MODE = "hi mode needs a rate"
IQ = "an iq run may not start at 13"
#: doppler's trigger is two enum fields; this one has no set_fn, so its
#: plain setter and the constructor are what hold the struct whole.
ENUMS = [{"name": "mode", "values": ["auto", "lo", "hi"]}]


def _source(type_name: str) -> dict:
    return {
        "struct": "wsrc_t",
        "type_name": type_name,
        "check_fn": "win_check",
        "fields": [
            {"name": "num", "type": "int", "default": "0"},
            {
                "name": "den",
                "type": "int",
                "default": "0",
                "set_fn": "win_set_den",
            },
            {"name": "gain", "type": "double", "default": "0.0"},
            {"name": "bits", "type": "uint8_t*", "bytes": True},
            {"name": "mode", "type": "int", "enum": "mode", "default": "auto"},
            {"name": "iq", "type": "float _Complex*", "complex": True},
            # gh-2864: an owned pointer whose live count is the leak oracle.
            {
                "name": "tag",
                "type": "wtag_t *",
                "copy_fn": "tag_copy",
                "free_fn": "tag_free",
                "parse_fn": "tag_parse",
                "format_fn": "tag_format",
            },
        ],
        "computed": [{"name": "live", "type": "int", "fn": "tag_live"}],
        "ranged": [{"name": "gain", "flag": "R_GAIN"}],
    }


def _segment(type_name: str) -> dict:
    return {
        "type_name": type_name,
        "struct": "wseg_t",
        "sources": "multi",
        "flat_sources": True,
        "fields": [
            {"name": "fs", "type": "double", "default": "1e6"},
            {"name": "count", "type": "size_t", "default": "100"},
        ],
    }


def _module() -> dict:
    """The generic composer: jm's own JSON record and the c-face CLI."""
    return {
        "kind": "composer",
        "backing": "win",
        "source": _source("Src"),
        "segment": _segment("Seg"),
        "oo": {"composer_type_name": "Win"},
        "json": {"enabled": True},
        # gh-2863: no `name`, so the default program name is built
        # beside the extension of the same module.
        "cli": {"enabled": True},
        "extra_link_libs": ["backing_core"],
    }


def _delegated() -> dict:
    """The same source behind the project's own JSON readers."""
    return {
        "kind": "composer",
        "backing": "wdl",
        "header": f"{PKG}/win/win_core.h",
        "source": _source("DSrc"),
        "segment": _segment("DSeg"),
        "oo": {"composer_type_name": "Dl"},
        "json": {
            "enabled": True,
            "to_json_fn": "wdl_to_json",
            "from_json_fn": "wdl_from_json",
            "from_file_fn": "wdl_from_file",
        },
        "extra_link_libs": ["backing_core"],
    }


def _cfg(m: "dict | None" = None, mod: str = "win") -> dict:
    return {
        "project": {"name": PKG},
        "module": {mod: m or _module()},
        "enum": ENUMS,
    }


def _body(text: str, head: str) -> str:
    """The C function whose definition starts with *head*."""
    at = text.index(head)
    return text[at : text.index("\n}\n", at)]


class TestTheKey:
    def test_recognised_and_written_back(self) -> None:
        cfg = _cfg()
        assert "check_fn" in _keys.COMPOSER_SOURCE_KEYS
        assert _keys.unknown_keys(cfg) == []
        assert 'check_fn = "win_check"' in C._dump(cfg)

    def test_an_author_named_key(self) -> None:
        """jm derives no part of the name (gh-1694)."""
        assert "check_fn" in _csym.AUTHOR_NAMED_KEYS
        assert "win_check" in _composer.seam_fns(_cfg(), "win")

    def test_declared_in_the_bridge(self) -> None:
        h = _composer.render_bridge_h(_cfg(), "win")
        assert "const char *win_check(const wsrc_t *);" in h
        # The only seam: the bridge exists for it alone.
        m = _module()
        del m["source"]["fields"][1]["set_fn"]
        m["source"]["fields"] = [
            f for f in m["source"]["fields"] if f["name"] != "tag"
        ]
        del m["source"]["computed"]
        assert _composer.seam_fns(_cfg(m), "win") == ["win_check"]
        ext = _composer.render_ext(_cfg(m), "win")
        assert '#include "win/win_bridge.h"' in ext
        assert "extern " not in ext
        cli = _composer.render_cli(_cfg(m), "win")
        assert '#include "win/win_bridge.h"' in cli

    def test_undeclared_renders_as_before(self) -> None:
        m = _module()
        del m["source"]["check_fn"]
        cfg = _cfg(m)
        for text in (
            _composer.render_source_type(cfg, "win"),
            _composer.render_json_funcs(cfg, "win"),
            _composer.render_cli(cfg, "win"),
        ):
            assert "_chk" not in text and "win_check" not in text
        src = _composer.render_source_type(cfg, "win")
        assert "Src_replace_bits(SrcObject *self, PyObject *value)\n" in src
        assert "    self->src.num = _v;\n" in src


class TestEveryFaceCallsIt:
    def test_the_constructor_after_every_field(self) -> None:
        init = _body(
            _composer.render_source_type(_cfg(), "win"), "Src_init(SrcObject"
        )
        at = init.index("const char *_chk = win_check(&_n);")
        assert at > init.index("_attach_bytes(&_n.bits, &_n.n_bits, bits)")
        assert at > init.index("_attach_tag(&_n, tag)")
        assert at > init.index("_n.gain = _lo;")

    def test_the_cli_flags_and_from_file(self) -> None:
        cli = _composer.render_cli(_cfg(), "win")
        flags = cli.index("win_check(&src);")
        assert cli.index("src.num = num;") < flags < cli.index("seg.sources")
        assert 'fprintf(stderr, "%s\\n", _chk);' in cli
        assert (
            "free(src.bits);\n                tag_free((wtag_t *)src.tag);\n"
            "                return 2;"
        ) in cli
        assert cli.index("c = win_from_file(from_file);") < cli.index(
            "win_check(&_cs[_ci].sources[_ck]);"
        )

    def test_the_cli_default_name_is_not_the_extension(self) -> None:
        """gh-2863: the default was the module id, the extension's target."""
        from just_makeit import _targets

        cfg = _cfg()
        assert _composer.cli_program(cfg, "win") == "win_cli"
        assert "add_executable(win_cli win_cli.c)" in (
            _composer.render_cmake(cfg, "win")
        )
        assert _targets.collisions(cfg) == {}
        assert "win_cli" in _targets.from_manifest(cfg)

    def test_a_cli_name_jm_already_emits_is_refused(self) -> None:
        m = _module()
        m["cli"]["name"] = "win"
        with pytest.raises(Refusal, match="CMP0002"):
            _composer.render_cmake(_cfg(m), "win")

    def test_the_constructor_docstring_names_the_refusal(self) -> None:
        docs = _composer.type_doc_lines(_cfg(), "win")
        for t in ("Src", "Seg"):
            text = "\n".join(docs[t])
            assert "Raises" in text and "``win_check``" in text, text


class TestInitIsTransactional:
    """gh-2864: `__init__` converts into a copy and commits it whole."""

    def _init(self, m: "dict | None" = None) -> str:
        return _body(
            _composer.render_source_type(_cfg(m), "win"), "Src_init(SrcObject"
        )

    def test_every_field_lands_in_the_copy(self) -> None:
        init = self._init()
        assert "self->src." not in init.split("free(self->src.bits);")[0]
        for line in (
            "wsrc_t _n = self->src;",
            "_n.bits = NULL;",
            "_n.n_bits = 0;",
            "_n.tag = NULL;",
            "_n.num = num;",
        ):
            assert line in init, line

    def test_commit_after_the_check_frees_the_old_members(self) -> None:
        init = self._init()
        order = [
            "win_check(&_n);",
            "free(self->src.bits);",
            "tag_free((wtag_t *)self->src.tag);",
            "self->src = _n;",
            "self->fs = fs;",
            "return 0;",
        ]
        at = [init.index(s) for s in order]
        assert at == sorted(at), init

    def test_a_refusal_frees_the_copy(self) -> None:
        init = self._init()
        # each attach's refusal, and the check's.
        assert init.count("free(_n.bits);") == 4, init
        assert init.count("free(_n.iq);") == 4, init
        assert init.count("tag_free((wtag_t *)_n.tag);") == 4, init
        assert "if (!_attach_tag(&_n, tag)) {\n        free(_n.bits);" in init

    def test_the_same_without_a_check_fn(self) -> None:
        m = _module()
        del m["source"]["check_fn"]
        init = self._init(m)
        assert "_chk" not in init
        assert init.index("self->src = _n;") > init.index("_attach_tag(&_n")

    def test_an_absent_range_clears_its_flag(self) -> None:
        assert (
            "_n.ranged &= ~(unsigned)R_GAIN;"
            in self._init().split("} else {\n        _n.gain = (double)0.0;")[
                1
            ]
        )


class TestTheSettersAndReaders:
    """gh-2856: the setters, the record and a delegated reader."""

    def test_a_plain_setter_checks_a_copy_and_commits_on_null(self) -> None:
        src = _composer.render_source_type(_cfg(), "win")
        for name, store in (
            ("num", "_c.num = _v;"),
            ("gain", "_c.gain = _lo;"),
        ):
            body = _body(src, f"Src_set_{name}(SrcObject")
            order = [
                "wsrc_t _c = self->src;",
                store,
                "const char *_chk = win_check(&_c);",
                "PyErr_SetString(PyExc_ValueError, _chk);",
                "self->src = _c;",
            ]
            at = [body.index(s) for s in order]
            assert at == sorted(at), body
            assert "self->src.gain" not in body.split("_chk")[0]

    def test_a_buffer_setter_checks_its_copy(self) -> None:
        src = _composer.render_source_type(_cfg(), "win")
        rep = _body(src, "Src_replace_bits(SrcObject")
        order = [
            "_attach_bytes(&_t.bits",
            "const char *_chk = win_check(&_t);",
            "free(_t.bits);\n            return 0;",
            "free(self->src.bits);",
        ]
        at = [rep.index(s) for s in order]
        assert at == sorted(at), rep
        assert "return Src_replace_bits(self, value) ? 0 : -1;" in src

    def test_a_set_fn_field_calls_set_fn_only(self) -> None:
        body = _body(
            _composer.render_source_type(_cfg(), "win"), "Src_set_den(Src"
        )
        assert "win_set_den(&self->src, _v);" in body
        assert "win_check" not in body

    def test_the_generic_record(self) -> None:
        rec = _body(
            _composer.render_json_funcs(_cfg(), "win"),
            "_json_parse_source(const cJSON",
        )
        assert rec.index("win_check(src);") > rec.index("src->num")

    def test_a_delegated_reader_checks_the_sources_it_built(self) -> None:
        text = _composer.render_composer_type(_cfg(_delegated(), "wdl"), "wdl")
        for fn in ("Dl_from_json(PyObject", "Dl_from_file(PyObject"):
            body = _body(text, fn)
            order = [
                "wdl_state_t *st = ",
                "wdl_segments(st, &_cn, &_cr, &_cc);",
                "win_check(&_cs[_ci].sources[_ck]);",
                "wdl_destroy(st);\n                        return NULL;",
                "type->tp_alloc(type, 0);",
            ]
            at = [body.index(s) for s in order]
            assert at == sorted(at), body


class TestRefusals:
    @pytest.mark.parametrize("value", [3, True, ["win_check"]])
    def test_not_a_name(self, value: object) -> None:
        m = _module()
        m["source"]["check_fn"] = value
        errs = _composer.check_fn_errors(_cfg(m))
        assert len(errs) == 1 and "is a string" in errs[0], errs
        with pytest.raises(Refusal, match="is a string"):
            _composer.render_bridge_h(_cfg(m), "win")

    @pytest.mark.parametrize(
        "where",
        ["segment", "generates", "field", "module"],
    )
    def test_not_on_the_source(self, where: str) -> None:
        m = _module()
        del m["source"]["check_fn"]
        path = {
            "segment": "module.win.segment",
            "generates": "module.win.source.generates",
            "field": "module.win.source.fields[]",
            "module": "module.win",
        }[where]
        if where == "segment":
            m["segment"]["check_fn"] = "w"
        elif where == "generates":
            m["source"]["generates"] = {"check_fn": "w"}
        elif where == "field":
            m["source"]["fields"][0]["check_fn"] = "w"
        else:
            m["check_fn"] = "w"
        errs = _composer.check_fn_errors(_cfg(m))
        assert errs == [
            "composer `win`: `check_fn` is read on [module.win.source] "
            "only -- it validates the whole source struct -- and was found "
            f"at [{path}] (gh-2856)."
        ], errs

    def test_refused_when_the_manifest_loads(self, tmp_path: Path) -> None:
        assert run_cli("new", PKG, cwd=tmp_path).returncode == 0
        proj = tmp_path / PKG
        cfg = C.load(proj)
        m = _module()
        m["segment"]["check_fn"] = "win_check"
        cfg.setdefault("module", {})["win"] = m
        C.save(proj, cfg)
        with pytest.raises(Refusal, match=r"found at \[module.win.segment\]"):
            C.load(proj)
        r = run_cli("apply", cwd=proj)
        assert r.returncode != 0
        assert "[module.win.segment]" in r.stdout + r.stderr


# -- the compiled proof ------------------------------------------------------

_HEADER = """\
#ifndef WIN_CORE_H
#define WIN_CORE_H

#include <complex.h>
#include <stddef.h>
#include <stdint.h>

#define R_GAIN 1u

/* An owned pointer: tag_live counts the ones alive. */
typedef struct
{
  int v;
} wtag_t;

typedef struct
{
  int      num;
  int      den;
  double   gain;
  double   gain_hi;
  unsigned ranged;
  uint8_t *bits;
  size_t   n_bits;
  int      mode;
  float _Complex *iq;
  size_t   n_iq;
  wtag_t  *tag;
} wsrc_t;

wtag_t *tag_copy (const wtag_t *t);
void    tag_free (wtag_t *t);

typedef struct
{
  wsrc_t *sources;
  size_t  n_sources;
  double  fs;
  size_t  count;
} wseg_t;

typedef struct win_state win_state_t;
win_state_t *win_create (const wseg_t *s, size_t n, int r, int c);
size_t win_execute (win_state_t *st, float _Complex *o, size_t m);
const wseg_t *win_segments (const win_state_t *st, size_t *n, int *r,
                            int *c);
void win_destroy (win_state_t *st);
/* The CLI's --from-file: "half" in the path builds half a pair. */
win_state_t *win_from_file (const char *path);

/* The delegated module's backing: the same state, the project's readers. */
typedef struct win_state wdl_state_t;
wdl_state_t *wdl_create (const wseg_t *s, size_t n, int r, int c);
size_t wdl_execute (wdl_state_t *st, float _Complex *o, size_t m);
const wseg_t *wdl_segments (const wdl_state_t *st, size_t *n, int *r,
                            int *c);
void wdl_destroy (wdl_state_t *st);
wdl_state_t *wdl_from_json (const char *json);
wdl_state_t *wdl_from_file (const char *path);
char *wdl_to_json (const wseg_t *s, size_t n, int r, int c);

#endif
"""

_SOURCE = f"""\
#include "{PKG}/win/win_core.h"
#include <stdlib.h>
#include <string.h>

struct win_state
{{
  wseg_t *segs;
  size_t  n;
}};

/* A deep copy: the sources' bits belong to the caller. */
win_state_t *
win_create (const wseg_t *s, size_t n, int r, int c)
{{
  (void)r;
  (void)c;
  win_state_t *st = calloc (1, sizeof *st);
  st->segs        = calloc (n, sizeof *st->segs);
  for (size_t i = 0; i < n; i++)
    {{
      st->segs[i]         = s[i];
      st->segs[i].sources = calloc (s[i].n_sources, sizeof (wsrc_t));
      for (size_t k = 0; k < s[i].n_sources; k++)
        {{
          wsrc_t *d = &st->segs[i].sources[k];
          *d        = s[i].sources[k];
          if (d->n_bits)
            {{
              d->bits = malloc (d->n_bits);
              memcpy (d->bits, s[i].sources[k].bits, d->n_bits);
            }}
          d->tag = d->tag ? tag_copy (d->tag) : NULL;
          if (d->n_iq)
            {{
              d->iq = malloc (d->n_iq * sizeof *d->iq);
              memcpy (d->iq, s[i].sources[k].iq, d->n_iq * sizeof *d->iq);
            }}
        }}
    }}
  st->n = n;
  return st;
}}

size_t
win_execute (win_state_t *st, float _Complex *o, size_t m)
{{
  (void)st;
  (void)o;
  (void)m;
  return 0;
}}

const wseg_t *
win_segments (const win_state_t *st, size_t *n, int *r, int *c)
{{
  *n = st->n;
  *r = 0;
  *c = 0;
  return st->segs;
}}

void
win_destroy (win_state_t *st)
{{
  for (size_t i = 0; i < st->n; i++)
    {{
      for (size_t k = 0; k < st->segs[i].n_sources; k++)
        {{
          free (st->segs[i].sources[k].bits);
          tag_free (st->segs[i].sources[k].tag);
          free (st->segs[i].sources[k].iq);
        }}
      free (st->segs[i].sources);
    }}
  free (st->segs);
  free (st);
}}

/* One segment of one num/den source: whole, or half a pair. */
static win_state_t *
one (int half)
{{
  wsrc_t src;
  wseg_t seg;
  memset (&src, 0, sizeof src);
  memset (&seg, 0, sizeof seg);
  src.num       = 1;
  src.den       = half ? 0 : 2;
  seg.sources   = &src;
  seg.n_sources = 1;
  seg.fs        = 1e6;
  seg.count     = 4;
  return win_create (&seg, 1, 0, 0);
}}

win_state_t *
win_from_file (const char *path)
{{
  return one (strstr (path, "half") != NULL);
}}

wdl_state_t *
wdl_create (const wseg_t *s, size_t n, int r, int c)
{{
  return win_create (s, n, r, c);
}}

size_t
wdl_execute (wdl_state_t *st, float _Complex *o, size_t m)
{{
  return win_execute (st, o, m);
}}

const wseg_t *
wdl_segments (const wdl_state_t *st, size_t *n, int *r, int *c)
{{
  return win_segments (st, n, r, c);
}}

void
wdl_destroy (wdl_state_t *st)
{{
  win_destroy (st);
}}

wdl_state_t *
wdl_from_json (const char *json)
{{
  return one (strstr (json, "half") != NULL);
}}

wdl_state_t *
wdl_from_file (const char *path)
{{
  return one (strstr (path, "half") != NULL);
}}

char *
wdl_to_json (const wseg_t *s, size_t n, int r, int c)
{{
  char *t = malloc (3);
  (void)s;
  (void)n;
  (void)r;
  (void)c;
  if (t)
    strcpy (t, "{{}}");
  return t;
}}
"""

#: The project's seams, written against the prototypes jm declares.
_CHECK = f"""\
#include "{PKG}/win/win_bridge.h"

#include <stdio.h>
#include <stdlib.h>

const char *
win_check (const wsrc_t *s)
{{
  if ((s->num == 0) != (s->den == 0))
    return "{HALF}";
  if (s->gain < 0.0 || ((s->ranged & R_GAIN) && s->gain_hi < s->gain))
    return "{GAIN}";
  if (s->n_bits > 4)
    return "{BITS}";
  if (s->tag && s->tag->v == 13)
    return "{TAG}";
  if (s->mode == 2 && s->num == 0)
    return "{MODE}";
  if (s->n_iq && crealf (s->iq[0]) == 13.0f)
    return "{IQ}";
  return NULL;
}}

static int live = 0;

wtag_t *
tag_copy (const wtag_t *t)
{{
  wtag_t *c = malloc (sizeof *c);
  *c        = *t;
  live++;
  return c;
}}

void
tag_free (wtag_t *t)
{{
  if (t)
    live--;
  free (t);
}}

wtag_t *
tag_parse (const char *s)
{{
  wtag_t t;
  char  *end;
  t.v = (int)strtol (s, &end, 10);
  if (!*s || *end)
    return NULL;
  return tag_copy (&t);
}}

char *
tag_format (const wtag_t *t)
{{
  char *s = malloc (16);
  snprintf (s, 16, "%d", t->v);
  return s;
}}

int
tag_live (const wsrc_t *s)
{{
  (void)s;
  return live;
}}

/* Keeps the pair whole -- except at 99, where it stores den alone and
 * leaves half a pair on purpose: that the write is not refused is the
 * proof jm does not check after a set_fn. */
const char *
win_set_den (wsrc_t *s, int den)
{{
  if (den < 0)
    return "den must be >= 0";
  s->den = den;
  if (den == 99)
    return NULL;
  if (den == 0)
    s->num = 0;
  else if (s->num == 0)
    s->num = 1;
  return NULL;
}}
"""

_BACKING_CMAKE = """\
add_library(backing_core OBJECT win_core.c win_check.c cJSON.c)
target_include_directories(backing_core PUBLIC ${CMAKE_SOURCE_DIR}/native/inc
                                               ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(backing_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
# The test's cJSON stand-in is not the code under test.
set_source_files_properties(cJSON.c PROPERTIES COMPILE_OPTIONS "-w")
"""

#: Each block is one face of the issue; a failure names it.
_DRIVER = f"""\
import json, pickle, resource, sys
import numpy as np
sys.path.insert(0, "src")
from {PKG}.win.win import Src, Seg, Win
from {PKG}.wdl.wdl import DSrc, Dl

HALF = {HALF!r}
GAIN = {GAIN!r}
BITS = {BITS!r}
TAG = {TAG!r}
MODE = {MODE!r}
IQ = {IQ!r}


def refused(fn, why):
    try:
        fn()
    except ValueError as e:
        assert str(e) == why, (str(e), why)
    else:
        raise AssertionError("not refused: " + why)


# 1. The constructor: the whole struct, after every field.
Src()
Src(num=1, den=2, gain=(0.5, 1.0), bits=b"\\x01")
refused(lambda: Src(num=1), HALF)
refused(lambda: Src(den=2), HALF)  # the constructor does not call set_fn
refused(lambda: Src(gain=-1.0), GAIN)
refused(lambda: Src(gain=(2.0, 1.0)), GAIN)
refused(lambda: Src(bits=b"\\x01" * 5), BITS)
refused(lambda: DSrc(num=1), HALF)
# ...and the inline Segment builds its source through it.
refused(lambda: Seg(num=1), HALF)
seg = Seg(num=1, den=2)
assert (seg.num, seg.den) == (1, 2), "the flat read of a whole source"

# 2. A plain setter writes a copy, checks it, and commits on NULL: a
# refused assignment leaves the source as it was.
s = Src(num=1, den=2, gain=0.5, bits=b"\\x01\\x00")
refused(lambda: setattr(s, "num", 0), HALF)
assert (s.num, s.den) == (1, 2), (s.num, s.den)
refused(lambda: setattr(s, "gain", -3.0), GAIN)
refused(lambda: setattr(s, "gain", (2.0, 1.0)), GAIN)
assert s.gain == 0.5, s.gain
refused(lambda: setattr(s, "bits", b"\\x01" * 5), BITS)
assert s.bits == b"\\x01\\x00", s.bits
s.num = 3
s.gain = (1.0, 2.0)
s.bits = b"\\x01"
assert (s.num, s.gain, s.bits) == (3, (1.0, 2.0), b"\\x01")

# 3. A set_fn field calls set_fn only.
t = Src()
t.den = 4
assert (t.num, t.den) == (1, 4), "set_fn fixed the sibling"
refused(lambda: setattr(t, "den", -1), "den must be >= 0")
assert (t.num, t.den) == (1, 4)
u = Src()
u.den = 99
assert (u.num, u.den) == (0, 99), "no check after set_fn"

# 4. The record: the generic from_json and from_file.
w = Win([Seg.sum(Src(num=1, den=2, bits=b"\\x01"), count=4)])
text = w.to_json()
assert Win.from_json(text).segments[0].sources[0].den == 2
doc = json.loads(text)
doc["segments"][0]["sources"][0]["den"] = 0
refused(lambda: Win.from_json(json.dumps(doc)), HALF)
with open("half.json", "w") as f:
    json.dump(doc, f)
refused(lambda: Win.from_file("half.json"), HALF)
doc["segments"][0]["sources"][0].update(den=2, bits=[1] * 5)
refused(lambda: Win.from_json(json.dumps(doc)), BITS)

# 5. A delegated reader: every source of the state it built.
assert Dl.from_json("{{}}").segments[0].sources[0].den == 2
refused(lambda: Dl.from_json('{{"half": 1}}'), HALF)
refused(lambda: Dl.from_file("half.spec"), HALF)
assert Dl.from_file("whole.spec").segments[0].sources[0].den == 2

# 6. No __setstate__: a source is not picklable, so that face is absent.
for cls in (Src, Seg):
    assert "__setstate__" not in dir(cls), cls
    try:
        pickle.dumps(cls())
    except TypeError:
        pass
    else:
        raise AssertionError(f"{{cls.__name__}} pickled: check __setstate__")
# 7. gh-2864: a refused re-__init__ -- by check_fn, by a coercion, by an
# owned pointer's parser -- leaves the source exactly as it was, and frees
# what it converted (`live` counts the tags alive); a successful one frees
# the buffers it replaces.
def state(x):
    return (x.num, x.den, x.gain, x.bits, x.tag, x.fs)


r = Src(num=1, den=2, gain=(0.5, 1.0), bits=b"\\x01\\x00", tag="7", fs=8.0)
before, base = state(r), r.live
for kw, err, why in [
    (dict(num=5), ValueError, HALF),
    (dict(num=1, den=2, bits=b"\\x01" * 5, tag="9"), ValueError, BITS),
    (dict(num=1, den=2, tag="13"), ValueError, TAG),
    (dict(num=1, den=2, bits=b"\\x01", tag="x"), ValueError, None),
    (dict(num=1, den=2, tag="9", gain=(1.0, "x")), TypeError, None),
    (dict(num=1, den=2, tag="9", bits=7), TypeError, None),
]:
    try:
        r.__init__(**kw)
    except err as e:
        assert why is None or str(e) == why, (kw, str(e))
    else:
        raise AssertionError("re-init not refused: %r" % (kw,))
    assert state(r) == before, (kw, state(r), before)
    assert r.live == base, ("a refused re-init leaked or freed", kw, r.live)
r.__init__(num=3, den=4, bits=b"\\x01", tag="8")
assert state(r) == (3, 4, 0.0, b"\\x01", "8", 1e6), state(r)
assert r.live == base, ("the replaced tag was not freed", r.live, base)
r.__init__()
assert (r.bits, r.tag, r.live) == (None, None, base - 1), (r.tag, r.live)
# A refused setter frees its copy too.
r.tag = "5"
try:
    r.tag = "13"
except ValueError as e:
    assert str(e) == TAG, e
assert (r.tag, r.live) == ("5", base), (r.tag, r.live)

# 8. gh-2864: a Segment's re-__init__ builds its source before it commits
# a scalar of its own.
sg = Seg(num=1, den=2, count=5, fs=3.0, tag="6")
sbefore, sbase = (sg.fs, sg.count, sg.num, sg.den), sg.sources[0].live
for kw, err, why in [
    (dict(num=1, count=7, fs=9.0), ValueError, HALF),
    (dict(num=1, den=2, count=7, fs=9.0, tag="13"), ValueError, TAG),
    (dict(num=1, den=2, count="x", fs=9.0), TypeError, None),
]:
    try:
        sg.__init__(**kw)
    except err as e:
        assert why is None or str(e) == why, (kw, str(e))
    else:
        raise AssertionError("segment re-init not refused: %r" % (kw,))
    assert (sg.fs, sg.count, sg.num, sg.den) == sbefore, (kw, sbefore)
    assert sg.sources[0].live == sbase, ("leaked", kw)
sg.__init__(num=1, den=2, count=7, fs=9.0)
assert (sg.fs, sg.count, sg.sources[0].fs) == (9.0, 7, 9.0)

# 9. An enum field with no set_fn, and a complex field: the constructor and
# the plain setter, the state unchanged after a refusal.
refused(lambda: Src(mode="hi"), MODE)
assert Src(num=1, den=2, mode="hi").mode == "hi"
e = Src(mode="lo")
refused(lambda: setattr(e, "mode", "hi"), MODE)
assert e.mode == "lo", e.mode
one = np.array([1 + 2j], dtype=np.complex64)
bad = np.array([13, 1], dtype=np.complex64)
refused(lambda: Src(iq=bad), IQ)
c = Src(iq=one)
refused(lambda: setattr(c, "iq", bad), IQ)
assert np.array_equal(c.iq, one), c.iq
c.__init__(num=1, den=2, iq=one)
try:
    c.__init__(iq=bad)
except ValueError as err:
    assert str(err) == IQ, err
assert np.array_equal(c.iq, one) and (c.num, c.den) == (1, 2)


# 10. No refusal leaks what it converted: each refused write of a 4 MiB
# buffer, repeated, leaves the peak RSS where it was.
def peak():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r if sys.platform == "darwin" else r * 1024


BIG_BITS = b"\x01" * (4 << 20)
BIG_IQ = np.full(1 << 19, 13, dtype=np.complex64)
host = Src(num=1, den=2, bits=b"\x01", iq=one)
faces = [
    ("bits ctor", lambda: Src(bits=BIG_BITS), BITS),
    ("bits setter", lambda: setattr(host, "bits", BIG_BITS), BITS),
    ("bits re-init", lambda: host.__init__(num=1, den=2, bits=BIG_BITS), BITS),
    ("iq ctor", lambda: Src(iq=BIG_IQ), IQ),
    ("iq setter", lambda: setattr(host, "iq", BIG_IQ), IQ),
    ("iq re-init", lambda: host.__init__(num=1, den=2, iq=BIG_IQ), IQ),
]
for label, fn, why in faces:
    for _ in range(2):
        refused(fn, why)
    before_rss = peak()
    for _ in range(32):
        refused(fn, why)
    grown = peak() - before_rss
    assert grown < 32 << 20, (label, "leaked", grown)
print("CHECK_FN OK")
"""


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


def _build(tmp: Path, generic: str, delegated: str, cli: dict) -> Path:
    """The test project with the generic composer at module id *generic*
    (its CLI table *cli*) and the delegated one at *delegated*, built
    warning-clean so an undeclared call (implicit declaration) is red."""
    assert run_cli("new", PKG, cwd=tmp).returncode == 0
    proj = tmp / PKG
    inc = proj / "native" / "inc" / PKG / "win"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "win_core.h").write_text(_HEADER, encoding="utf-8")
    backing = proj / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    (backing / "win_core.c").write_text(_SOURCE, encoding="utf-8")
    # The seams include the generic module's bridge, wherever its id puts
    # it (gh-2427: beside the module's own core).
    bridge = _composer.bridge_h(generic, {"project": {"name": PKG}})
    (backing / "win_check.c").write_text(
        _CHECK.replace(f"{PKG}/win/win_bridge.h", f"{PKG}/{bridge}", 1),
        encoding="utf-8",
    )
    (backing / "cJSON.h").write_text(CJSON_H, encoding="utf-8")
    (backing / "cJSON.c").write_text(CJSON_C, encoding="utf-8")
    (backing / "CMakeLists.txt").write_text(_BACKING_CMAKE, encoding="utf-8")
    cfg = C.load(proj)
    cfg["project"]["c_deps"] = ["backing"]
    cfg["project"]["status_allow"] = ["CMakeLists.txt:backing_core"]
    m = _module()
    m["cli"] = cli
    cfg.setdefault("module", {})[generic] = m
    cfg["module"][delegated] = _delegated()
    cfg["enum"] = ENUMS
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    _run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            PYTHON3_ARG,
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        proj,
    )
    _run(["cmake", "--build", "build", "--parallel", "4"], proj)
    return proj


def _exe(project: Path, name: str) -> Path:
    return next(
        p
        for p in (project / "build").rglob(name)
        if p.is_file() and p.stat().st_mode & 0o111
    )


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    if base._NO_TOOLCHAIN:
        pytest.skip("no cmake / C compiler")
    # gh-2863: no `name`, so the default program name is built beside the
    # extension of the same module.
    return _build(
        tmp_path_factory.mktemp("gh2856"), "win", "wdl", {"enabled": True}
    )


#: gh-2863: two dotted composers sharing the leaf `win`, the generic one's
#: CLI named `win` -- the leaf, which is the `.so` basename and no longer a
#: target. Under leaf targets this was three targets named `win`.
_DOTTED_DRIVER = f"""\
import sys
sys.path.insert(0, "src")
from {PKG}.dsp.win.win import Src
from {PKG}.aux.win.win import Dl
try:
    Src(num=1)
except ValueError as e:
    assert str(e) == {HALF!r}, e
else:
    raise AssertionError("not refused")
try:
    Dl.from_json('{{"half": 1}}')
except ValueError as e:
    assert str(e) == {HALF!r}, e
else:
    raise AssertionError("not refused")
print("DOTTED OK")
"""


@pytest.mark.slow
@pytest.mark.skipif(base._NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_dotted_composers_sharing_a_leaf_build(tmp_path: Path) -> None:
    """gh-2863 for a dotted id: each composer's extension target is its
    cname (`dsp_win`, `aux_win`) with OUTPUT_NAME `win`, so two of them and
    a CLI named for the leaf configure, build and import side by side."""
    proj = _build(
        tmp_path, "dsp.win", "aux.win", {"enabled": True, "name": "win"}
    )
    cm = (proj / "native" / "src" / "dsp_win" / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    assert "Python3_add_library(dsp_win MODULE" in cm
    assert "    OUTPUT_NAME win\n" in cm
    assert "add_executable(win dsp_win_cli.c)" in cm
    driver = proj / "drive_dotted.py"
    driver.write_text(_DOTTED_DRIVER, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(driver)], cwd=proj, capture_output=True, text=True
    )
    assert r.returncode == 0 and "DOTTED OK" in r.stdout, r.stdout + r.stderr
    r = subprocess.run(
        [str(_exe(proj, "win")), "--num", "1"],
        cwd=proj,
        capture_output=True,
        text=True,
    )
    assert (r.returncode, r.stderr.strip()) == (2, HALF), r.stderr


def test_a_dotted_cli_name_is_checked_against_the_real_target() -> None:
    """gh-2863: the count names the target each kind emits -- the cname --
    so `dsp_win` is refused and the leaf `win` is not."""
    from just_makeit import _targets

    cfg = {
        "project": {"name": PKG},
        "module": {"dsp.win": _module()},
        "enum": ENUMS,
    }
    cfg["module"]["dsp.win"]["cli"] = {"enabled": True, "name": "dsp_win"}
    with pytest.raises(Refusal, match="CMP0002"):
        _composer.cli_program(cfg, "dsp.win")
    cfg["module"]["dsp.win"]["cli"]["name"] = "win"
    assert _composer.cli_program(cfg, "dsp.win") == "win"
    assert "dsp_win" in _targets.from_manifest(cfg)
    assert "Python3_add_library(dsp_win MODULE" in _composer.render_cmake(
        cfg, "dsp.win"
    )


@pytest.mark.parametrize("kind", ["capsule", "handle", "composer"])
def test_every_kind_names_its_extension_target_by_cname(kind: str) -> None:
    """gh-2863: the three kinds share `_capsule.extension_cmake`, so each
    emits the cname `_targets.emitted` counts, and keeps the leaf `.so`."""
    from just_makeit import _capsule, _handle

    m = {"kind": kind, "backing": "x", "type_name": "H"}
    if kind == "composer":
        m = _module()
    cfg = {"project": {"name": PKG}, "module": {"dsp.x": m}, "enum": ENUMS}
    render = {
        "capsule": _capsule.render_cmake,
        "handle": _handle.render_cmake,
        "composer": _composer.render_cmake,
    }[kind]
    text = render(cfg, "dsp.x")
    assert "Python3_add_library(dsp_x MODULE WITH_SOABI dsp_x_ext.c)" in text
    assert (
        "set_target_properties(dsp_x PROPERTIES\n    OUTPUT_NAME x\n" in text
    )
    assert "Python3_add_library(x " not in text


@pytest.mark.slow
@pytest.mark.skipif(base._NO_TOOLCHAIN, reason="no cmake / C compiler")
class TestEndToEnd:
    def test_every_python_face_raises_the_sentence(
        self, project: Path
    ) -> None:
        driver = project / "drive_gh2856.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(driver)],
            cwd=project,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0 and "CHECK_FN OK" in r.stdout, (
            r.stdout + r.stderr
        )

    @pytest.mark.parametrize(
        ("args", "why"),
        [
            (["--num", "1", "--den", "2"], ""),
            (["--num", "1"], HALF),
            (["--den", "2"], HALF),
            (["--num", "1", "--den", "2", "--bits", "11111"], BITS),
            (["--from-file", "whole.spec"], ""),
            (["--from-file", "half.spec"], HALF),
            (["--mode", "hi"], MODE),
        ],
        ids=[
            "whole",
            "half",
            "set_fn-field",
            "bits",
            "file",
            "half-file",
            "enum",
        ],
    )
    def test_the_cli(self, project: Path, args: list, why: str) -> None:
        exe = _exe(project, "win_cli")
        r = subprocess.run(
            [str(exe), "--out", str(project / "out.bin"), *args],
            cwd=project,
            capture_output=True,
            text=True,
        )
        if why:
            assert r.returncode == 2, (r.returncode, r.stderr)
            assert r.stderr.strip() == why, r.stderr
        else:
            assert r.returncode == 0, (r.returncode, r.stderr)

    def test_the_manifest_round_trips(self, project: Path) -> None:
        """`apply` again changes nothing, `status --check` is clean, and
        the key survives a save."""
        frag = project / "modules" / "win.toml"
        before = frag.read_text(encoding="utf-8")
        assert before.count('check_fn = "win_check"') == 1, before
        assert run_cli("apply", cwd=project).returncode == 0
        r = run_cli("status", "--check", cwd=project)
        assert r.returncode == 0, r.stdout + r.stderr
        assert frag.read_text(encoding="utf-8") == before
        C.save(project, C.load(project))
        assert frag.read_text(encoding="utf-8") == before
