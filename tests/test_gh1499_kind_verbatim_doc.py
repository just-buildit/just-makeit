"""gh-1499: gh-1493's rule on the ``kind`` modules -- a ``doc`` is verbatim.

gh-1493 made a manifest ``doc`` render as the author laid it out, on the stub
and at runtime alike, for the object faces. The three ``kind``-bearing module
generators build their docs through their own paths, and measured before this
(every ``doc`` a ``KIND_*`` / ``COMPOSER_*`` table accepts, two-paragraph
text, both faces):

========================  ==========================  ==========================
face                      stub                        runtime
========================  ==========================  ==========================
handle module             dropped                     dropped
handle method             reflowed (``override``)     reflowed
handle getter / field     dropped                     dropped
handle create_arg         dropped                     dropped
handle factory / its arg  dropped                     dropped
capsule module / method   dropped                     dropped
capsule property / arg    dropped                     dropped
composer module           dropped                     dropped
composer source / segment raw TOML indent             dropped (getset ``NULL``)
composer computed         dropped                     pasted raw: a two-line doc
                                                      did not compile
composer extra_method     first line only             raw TOML indent
composer serializer       dropped                     dropped
composer setting          dropped                     dropped
========================  ==========================  ==========================

Built and imported, not read from generated text, for the reason gh-1493
gives: the runtime face is what ``help()`` shows, and the honest way to read
it is to ask Python. Each module is compiled from the binding ``jm apply``
wrote plus a small hand-written backing, the way ``test_handle_build`` does.

That includes a composer's ``extra_methods`` row: its body is the author's
``<cname>_ext_extra.c``, which the binding has ``#include``-d since gh-1516,
so its runtime text is read from the built type like every other face
(gh-1534).

GATE: a manifest ``doc`` renders verbatim on both faces of every ``kind``
module -- handle, capsule and composer.
"""

from __future__ import annotations

import ast
import importlib.util
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from _jmrun import run_cli
from test_gh1493_verbatim_doc import _contains_block, _doc, _expected

_CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
pytestmark = pytest.mark.skipif(_CC is None, reason="no C compiler available")


_HANDLE = f"""\
[module.lamp]
kind = "handle"
backing = "lamp"
type_name = "Lamp"
handle_type = "lamp_t"
header = "lamp/lamp.h"
create_fn = "lamp_open"
close_fn = "lamp_close"
doc = {_doc("h_module")}

[[module.lamp.create_args]]
name = "level"
type = "int"
doc = {_doc("h_create_arg")}

[[module.lamp.methods]]
name = "bump"
fn = "lamp_bump"
doc = {_doc("h_method")}

[[module.lamp.getters]]
doc = {_doc("h_getter")}

[[module.lamp.getters.fields]]
name = "level"
getter = "lamp_level"
type = "int"

[[module.lamp.getters]]
fn = "lamp_stats"
out = "lamp_stats_t"

[[module.lamp.getters.fields]]
name = "hits"
type = "int"
doc = {_doc("h_getter_field")}

[[module.lamp.getters.fields]]
name = "peak"
type = "int"

[[module.lamp.getters]]

[[module.lamp.getters.fields]]
name = "floor"
getter = "lamp_level"
type = "int"

[[module.lamp.factories]]
name = "lamp_clone"
create_fn = "lamp_clone"
doc = {_doc("h_factory")}

[[module.lamp.factories.init_params]]
name = "level"
type = "int"
doc = {_doc("h_factory_arg")}
"""

_CAPSULE = f"""\
[module.gadget]
kind = "capsule"
backing = "gadget"
doc = {_doc("c_module")}

[[module.gadget.init_params]]
name = "rate"
type = "double"
doc = {_doc("c_init_param")}

[[module.gadget.methods]]
name = "reset"
doc = {_doc("c_method")}

[[module.gadget.properties]]
name = "rate"
type = "double"
writable = true
doc = {_doc("c_property")}
"""

_COMPOSER = f"""\
[module.playlist]
kind = "composer"
backing = "playlist"
composes = ["clip"]
doc = {_doc("k_module")}

[module.playlist.source]
object = "clip"
struct = "clip_t"
type_name = "Clip"

[[module.playlist.source.fields]]
name = "gain"
type = "double"
default = "1.0"
doc = {_doc("k_source_field")}

[[module.playlist.source.computed]]
name = "duration"
type = "double"
fn = "clip_duration"
doc = {_doc("k_computed")}

[module.playlist.source.generates]
generator = "clip"
bridge_fn = "clip_from_source"

[module.playlist.segment]
type_name = "Track"
struct = "track_t"
sources = "multi"
flat_sources = true

[[module.playlist.segment.fields]]
name = "dur"
type = "size_t"
default = "8"
doc = {_doc("k_segment_field")}

[module.playlist.oo]
composer_type_name = "Mix"

[[module.playlist.serializers]]
name = "describe"
fn = "playlist_describe"
returns = "str"
header = "playlist/playlist_ser.h"
doc = {_doc("k_serializer")}

[[module.playlist.settings]]
name = "mode"
setter_fn = "playlist_set_mode"
getter_fn = "playlist_get_mode"
type = "int"
doc = {_doc("k_setting")}

[[module.playlist.extra_methods]]
name = "draws"
fn = "Mix_draws"
flags = "METH_NOARGS"
returns = "int"
doc = {_doc("k_extra_method")}
"""

#: The backings: just enough C for each binding to link and import.
_BACKINGS = {
    "native/inc/lamp/lamp.h": """\
#ifndef LAMP_H
#define LAMP_H
typedef struct lamp lamp_t;
typedef struct { int hits; int peak; } lamp_stats_t;
lamp_t *lamp_open(int level);
lamp_t *lamp_clone(int level);
void lamp_close(lamp_t *l);
void lamp_bump(lamp_t *l);
int lamp_level(const lamp_t *l);
void lamp_stats(const lamp_t *l, lamp_stats_t *out);
#endif
""",
    "native/src/lamp/lamp.c": """\
#include <stdlib.h>
#include "lamp/lamp.h"
struct lamp { int level, hits; };
lamp_t *lamp_open(int level)
{
    lamp_t *l = calloc(1, sizeof *l);
    if (l) l->level = level;
    return l;
}
lamp_t *lamp_clone(int level) { return lamp_open(level); }
void lamp_close(lamp_t *l) { free(l); }
void lamp_bump(lamp_t *l) { l->hits++; }
int lamp_level(const lamp_t *l) { return l->level; }
void lamp_stats(const lamp_t *l, lamp_stats_t *out)
{
    out->hits = l->hits;
    out->peak = l->level;
}
""",
    "native/inc/gadget/gadget_core.h": """\
#ifndef GADGET_CORE_H
#define GADGET_CORE_H
typedef struct gadget_state gadget_state_t;
gadget_state_t *gadget_create(double rate);
void gadget_destroy(gadget_state_t *s);
void gadget_reset(gadget_state_t *s);
double gadget_get_rate(const gadget_state_t *s);
void gadget_set_rate(gadget_state_t *s, double rate);
#endif
""",
    "native/src/gadget/gadget_core.c": """\
#include <stdlib.h>
#include "gadget/gadget_core.h"
struct gadget_state { double rate; };
gadget_state_t *gadget_create(double rate)
{
    gadget_state_t *s = calloc(1, sizeof *s);
    if (s) s->rate = rate;
    return s;
}
void gadget_destroy(gadget_state_t *s) { free(s); }
void gadget_reset(gadget_state_t *s) { (void)s; }
double gadget_get_rate(const gadget_state_t *s) { return s->rate; }
void gadget_set_rate(gadget_state_t *s, double rate) { s->rate = rate; }
""",
    "native/inc/clip/clip_core.h": """\
#ifndef CLIP_CORE_H
#define CLIP_CORE_H
#include <stddef.h>
#include "clib_common.h"
typedef struct { double gain; } clip_t;
typedef struct clip_state clip_state_t;
void clip_steps(clip_state_t *s, float _Complex *out, size_t n);
float _Complex clip_step(clip_state_t *s);
void clip_reset(clip_state_t *s);
void clip_destroy(clip_state_t *s);
#endif
""",
    "native/inc/playlist/playlist_core.h": """\
#ifndef PLAYLIST_CORE_H
#define PLAYLIST_CORE_H
#include <stddef.h>
#include "clib_common.h"
#include "clip/clip_core.h"
typedef struct {
    clip_t *sources;
    size_t  n_sources;
    size_t  dur;
    double  fs;
} track_t;
typedef struct playlist_state playlist_state_t;
playlist_state_t *playlist_create(const track_t *segs, size_t n, int repeat,
                                  int continuous);
void playlist_destroy(playlist_state_t *s);
size_t playlist_execute(playlist_state_t *s, float _Complex *out, size_t n);
const track_t *playlist_segments(const playlist_state_t *s, size_t *n,
                                 int *repeat, int *continuous);
void playlist_set_mode(playlist_state_t *s, int mode);
int playlist_get_mode(const playlist_state_t *s);
#endif
""",
    "native/inc/playlist/playlist_ser.h": """\
#ifndef PLAYLIST_SER_H
#define PLAYLIST_SER_H
#include <stddef.h>
#include "playlist/playlist_core.h"
char *playlist_describe(const track_t *segs, size_t n);
#endif
""",
    "native/src/playlist/playlist.c": """\
#include <stdlib.h>
#include <string.h>
#include "playlist/playlist_core.h"
#include "playlist/playlist_ser.h"
#include "playlist/playlist_bridge.h"
struct clip_state { double gain; };
struct playlist_state { int mode; };
clip_state_t *clip_from_source(const clip_t *src, double fs)
{
    clip_state_t *s = calloc(1, sizeof *s);
    (void)fs;
    if (s) s->gain = src->gain;
    return s;
}
double clip_duration(const clip_t *src) { return src->gain; }
void clip_steps(clip_state_t *s, float _Complex *out, size_t n)
{
    for (size_t i = 0; i < n; i++) out[i] = (float)s->gain;
}
float _Complex clip_step(clip_state_t *s) { return (float)s->gain; }
void clip_reset(clip_state_t *s) { (void)s; }
void clip_destroy(clip_state_t *s) { free(s); }
playlist_state_t *playlist_create(const track_t *segs, size_t n, int repeat,
                                  int continuous)
{
    (void)segs; (void)n; (void)repeat; (void)continuous;
    return calloc(1, sizeof(playlist_state_t));
}
void playlist_destroy(playlist_state_t *s) { free(s); }
size_t playlist_execute(playlist_state_t *s, float _Complex *out, size_t n)
{
    (void)s; (void)out; (void)n;
    return 0;
}
const track_t *playlist_segments(const playlist_state_t *s, size_t *n,
                                 int *repeat, int *continuous)
{
    (void)s;
    *n = 0; *repeat = 0; *continuous = 0;
    return NULL;
}
void playlist_set_mode(playlist_state_t *s, int mode) { s->mode = mode; }
int playlist_get_mode(const playlist_state_t *s) { return s->mode; }
char *playlist_describe(const track_t *segs, size_t n)
{
    char *out = malloc(3);
    (void)segs; (void)n;
    if (out) strcpy(out, "{}");
    return out;
}
""",
    # The extra_methods row's body. Not in `_SOURCES`: the binding
    # `#include`s it after the generated types, and forward-declares
    # `Mix_draws` with the signature METH_NOARGS implies (gh-1516).
    "native/src/playlist/playlist_ext_extra.c": """\
static PyObject *
Mix_draws(PyObject *self, PyObject *Py_UNUSED(ignored))
{
    (void)self;
    return PyLong_FromLong(0);
}
""",
}

#: module -> the C files that make its extension.
_SOURCES = {
    "lamp": ["native/src/lamp/lamp_ext.c", "native/src/lamp/lamp.c"],
    "gadget": [
        "native/src/gadget/gadget_ext.c",
        "native/src/gadget/gadget_core.c",
    ],
    "playlist": [
        "native/src/playlist/playlist_ext.c",
        "native/src/playlist/playlist.c",
    ],
}


def _import(proj: Path, mod: str):
    """Compile *mod*'s binding with its backing and import the ``.so``."""
    import numpy as np

    out = proj / "build-kinds"
    out.mkdir(exist_ok=True)
    so = out / f"{mod}{sysconfig.get_config_var('EXT_SUFFIX') or '.so'}"
    link = (
        ["-bundle", "-undefined", "dynamic_lookup"]
        if sys.platform == "darwin"
        else ["-shared"]
    )
    r = subprocess.run(
        [_CC, *link, "-fPIC", "-std=c11",
         "-Werror=implicit-function-declaration",
         "-I", str(proj / "native" / "inc"),
         "-I", sysconfig.get_path("include"),
         "-I", np.get_include(),
         *[str(proj / s) for s in _SOURCES[mod]],
         "-o", str(so)],
        capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    spec = importlib.util.spec_from_file_location(mod, so)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> "tuple[Path, dict]":
    root = tmp_path_factory.mktemp("gh1499")
    assert run_cli("new", "vk", cwd=root).returncode == 0
    proj = root / "vk"
    mods = proj / "modules"
    mods.mkdir(exist_ok=True)
    (mods / "lamp.toml").write_text(_HANDLE, encoding="utf-8")
    (mods / "gadget.toml").write_text(_CAPSULE, encoding="utf-8")
    (mods / "playlist.toml").write_text(_COMPOSER, encoding="utf-8")
    for rel, text in _BACKINGS.items():
        (proj / rel).parent.mkdir(parents=True, exist_ok=True)
        (proj / rel).write_text(text, encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    # `status` replays the manifest onto a copy and diffs: a doc the replay
    # drops renders differently there, and the file reads as STALE.
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    return proj, {m: _import(proj, m) for m in _SOURCES}


def _stub(proj: Path, mod: str) -> str:
    text = (proj / "src" / "vk" / mod / f"{mod}.pyi").read_text(
        encoding="utf-8"
    )
    ast.parse(text)
    return text


#: face -> (module, what `help()` reads for it). A getter-level `doc` on a
#: one-field getter is that property's; a create_arg's and an init param's
#: live in their callable's `Parameters`; a source field's reaches the
#: segment's flat proxy of it too.
FACES = {
    "h_module": ("lamp", lambda m: m.__doc__),
    "h_create_arg": ("lamp", lambda m: m.Lamp.__doc__),
    "h_method": ("lamp", lambda m: m.Lamp.bump.__doc__),
    "h_getter": ("lamp", lambda m: m.Lamp.level.__doc__),
    "h_getter_field": ("lamp", lambda m: m.Lamp.hits.__doc__),
    "h_factory": ("lamp", lambda m: m.lamp_clone.__doc__),
    "h_factory_arg": ("lamp", lambda m: m.lamp_clone.__doc__),
    "c_module": ("gadget", lambda m: m.__doc__),
    "c_init_param": ("gadget", lambda m: m.gadget_create.__doc__),
    "c_method": ("gadget", lambda m: m.gadget_reset.__doc__),
    "c_property": (
        "gadget",
        lambda m: m.gadget_get_rate.__doc__ + m.gadget_set_rate.__doc__,
    ),
    "k_module": ("playlist", lambda m: m.__doc__),
    "k_source_field": (
        "playlist",
        lambda m: m.Clip.gain.__doc__ + m.Track.gain.__doc__,
    ),
    "k_computed": ("playlist", lambda m: m.Clip.duration.__doc__),
    "k_segment_field": ("playlist", lambda m: m.Track.dur.__doc__),
    "k_serializer": ("playlist", lambda m: m.Mix.describe.__doc__),
    "k_setting": ("playlist", lambda m: m.Mix.mode.__doc__),
    "k_extra_method": ("playlist", lambda m: m.Mix.draws.__doc__),
}


@pytest.mark.parametrize("face", FACES)
def test_the_stub_renders_the_doc_verbatim(built, face) -> None:
    proj, _ = built
    mod, _read = FACES[face]
    assert _contains_block(_stub(proj, mod), _expected(face)), (
        f"{face}: {mod}.pyi does not carry the doc line for line"
    )


@pytest.mark.parametrize("face", FACES)
def test_the_runtime_renders_the_doc_verbatim(built, face) -> None:
    _, mods = built
    mod, read = FACES[face]
    doc = read(mods[mod])
    assert doc and _contains_block(doc, _expected(face)), (
        f"{face}: help() does not carry the doc line for line:\n{doc}"
    )


def test_a_kind_module_without_docs_keeps_its_runtime_text(built) -> None:
    """The undocumented members say what they always said -- a project
    that declares no `doc` sees no churn on the members it did not touch."""
    _, mods = built
    gad = mods["gadget"]
    assert gad.gadget_destroy.__doc__ == "gadget_destroy(state) -> None"
    # A field of a struct getter, and a one-field getter, with no `doc` at
    # either level: the getset slot stays NULL (the second read "None").
    assert mods["lamp"].Lamp.peak.__doc__ is None
    assert mods["lamp"].Lamp.floor.__doc__ is None


# ── a `doc` with no face is refused, not dropped ─────────────────────────────


def test_a_getter_doc_backing_several_properties_is_refused(tmp_path) -> None:
    """A struct getter backing N properties has no one member its `doc`
    documents -- the rule a header `@brief` already follows there. Refused
    with the fix named, rather than dropped (what it was) or copied onto
    every field (a text the author wrote for none of them)."""
    assert run_cli("new", "rg", cwd=tmp_path).returncode == 0
    proj = tmp_path / "rg"
    (proj / "modules").mkdir(exist_ok=True)
    manifest = _HANDLE.replace(
        'fn = "lamp_stats"\n', f'fn = "lamp_stats"\ndoc = {_doc("x")}\n'
    )
    assert manifest != _HANDLE
    (proj / "modules" / "lamp.toml").write_text(manifest, encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "backs 2 properties" in out and "each field's own `doc`" in out
