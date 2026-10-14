"""gh-2283: a composer module documents every member once, on both faces.

A ``kind = "composer"`` module's runtime docs were fixed strings, while its
``.pyi`` was derived. Measured on v0.105.2 (doppler's ``wfm_compose``, and
this file's fixture):

==========================  ===============================  ==============================
member                      runtime ``__doc__``              ``.pyi``
==========================  ===============================  ==============================
source type (``Clip``)      "Clip — one composable source    "Clip." + a Parameters entry
                            configuration."                  per field
segment / timeline /        a fixed "<T> — ..." sentence     "<T>." (+ Parameters)
composer type
factory (``sine``)          "sine(**kw) -> Clip"             "Return a Clip configured as
                                                             a *sine* source."
``sum`` / ``add`` /         a signature line                 a sentence; ``stream``'s
``execute`` / ``compose``                                    ``realtime`` documented
/ ``close`` / ``stream`` /                                   differently on each face
``to_dict``
``from_json`` / ...         a signature line                 none
==========================  ===============================  ==============================

And neither face read the struct's own ``@brief``, nor what a factory's
discriminant value means.

Now every docstring is defined once (``_composer.type_doc_lines`` /
``member_doc_lines``) and the binding's text is DERIVED from the stub's
(``_docstring.docstring_body``), so the two cannot differ.

GATE: for every class and callable in the generated ``.pyi`` -- read from the
stub, so a member added later is covered without a list to extend -- the
runtime ``__doc__`` of the built module says the same thing, less only the
signature line a ``METH_VARARGS`` callable leads with. Built and imported,
because ``help()`` reads the ``.so``, not the C text.
"""

from __future__ import annotations
from _jminc import INC_DIR  # noqa: E402
from just_makeit import _incpath as INC  # noqa: E402

import ast
import importlib.util
import inspect
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli

_CC = default_cc()
pytestmark = pytest.mark.skipif(_CC is None, reason="no C compiler available")

#: The enum a factory's discriminant reads. `enumerators` is the declared
#: mapping (gh-901) that lets a choice read its enumerator's header doc.
_ENUM = """
[[enum]]
name = "clip_kind"
values = ["sine", "square"]
enumerators = ["CLIP_SINE", "CLIP_SQUARE"]
"""

#: Every composer surface that has a docstring on either face.
_COMPOSER = """\
[module.playlist]
kind = "composer"
backing = "playlist"
composes = ["clip"]

[module.playlist.source]
object = "clip"
struct = "clip_t"
type_name = "Clip"

[[module.playlist.source.fields]]
name = "kind"
type = "int"
enum = "clip_kind"
default = "sine"

[[module.playlist.source.fields]]
name = "gain"
type = "double"
default = "1.0"

[[module.playlist.source.computed]]
name = "duration"
type = "double"
fn = "clip_duration"
doc = "How long the clip lasts."

[module.playlist.source.generates]
generator = "clip"
bridge_fn = "clip_from_source"

[module.playlist.segment]
type_name = "Track"
struct = "track_t"
sources = "multi"

[[module.playlist.segment.fields]]
name = "dur"
type = "size_t"
default = "8"

[module.playlist.timeline]
type_name = "Reel"

[module.playlist.oo]
composer_type_name = "Mix"
factories = ["sine", "square"]
discriminant = "kind"

[module.playlist.composer]
stream = true
to_dict = true

[module.playlist.json]
enabled = true
to_json_fn = "playlist_to_json"

[[module.playlist.serializers]]
name = "describe"
fn = "playlist_describe"
returns = "str"
header = "<<P>>playlist/playlist_ser.h"
doc = "A one-line summary."

[[module.playlist.settings]]
name = "mode"
setter_fn = "playlist_set_mode"
getter_fn = "playlist_get_mode"
type = "int"
doc = "The playback mode."

[[module.playlist.extra_methods]]
name = "draws"
fn = "Mix_draws"
flags = "METH_NOARGS"
returns = "int"
doc = "How many draws so far."
"""

_BACKINGS = {
    "native/inc/<<P>>clip/clip_core.h": """\
#ifndef CLIP_CORE_H
#define CLIP_CORE_H
#include <stddef.h>
#include "<<P>>clib_common.h"
typedef enum {
    CLIP_SINE = 0,   /**< A pure sine at the clip's gain. */
    CLIP_SQUARE = 1, /**< A square wave at the clip's gain. */
} clip_kind_t;
/**
 * @brief One clip: a waveform and its gain.
 *
 * A track sums its clips sample by sample.
 */
typedef struct {
    int kind;    /**< Which waveform. */
    double gain; /**< Linear gain. */
} clip_t;
typedef struct clip_state clip_state_t;
void clip_steps(clip_state_t *s, float _Complex *out, size_t n);
float _Complex clip_step(clip_state_t *s);
void clip_reset(clip_state_t *s);
void clip_destroy(clip_state_t *s);
#endif
""",
    "native/inc/<<P>>playlist/playlist_core.h": """\
#ifndef PLAYLIST_CORE_H
#define PLAYLIST_CORE_H
#include <stddef.h>
#include "<<P>>clib_common.h"
#include "<<P>>clip/clip_core.h"
/** @brief One track: the clips it sums, for `dur` samples. */
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
playlist_state_t *playlist_from_json(const char *json);
playlist_state_t *playlist_from_file(const char *path);
char *playlist_to_json(const track_t *segs, size_t n, int repeat,
                       int continuous);
#endif
""",
    "native/inc/<<P>>playlist/playlist_ser.h": """\
#ifndef PLAYLIST_SER_H
#define PLAYLIST_SER_H
#include <stddef.h>
#include "<<P>>playlist/playlist_core.h"
char *playlist_describe(const track_t *segs, size_t n);
#endif
""",
    "native/src/playlist/playlist.c": """\
#include <stdlib.h>
#include <string.h>
#include "<<P>>playlist/playlist_core.h"
#include "<<P>>playlist/playlist_ser.h"
#include "<<P>>playlist/playlist_bridge.h"
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
playlist_state_t *playlist_from_json(const char *json)
{
    (void)json;
    return calloc(1, sizeof(playlist_state_t));
}
playlist_state_t *playlist_from_file(const char *path)
{
    (void)path;
    return calloc(1, sizeof(playlist_state_t));
}
static char *dup2(void)
{
    char *out = malloc(3);
    if (out) strcpy(out, "{}");
    return out;
}
char *playlist_to_json(const track_t *segs, size_t n, int repeat,
                       int continuous)
{
    (void)segs; (void)n; (void)repeat; (void)continuous;
    return dup2();
}
char *playlist_describe(const track_t *segs, size_t n)
{
    (void)segs; (void)n;
    return dup2();
}
""",
    "native/src/playlist/playlist_ext_extra.c": """\
static PyObject *
Mix_draws(PyObject *self, PyObject *Py_UNUSED(ignored))
{
    (void)self;
    return PyLong_FromLong(0);
}
""",
}


def _layout(text: str, proj: Path) -> str:
    """The fixture's headers in *proj*'s layout (gh-1583)."""
    return text.replace("<<P>>", INC.prefix(proj))


def _import(proj: Path):
    """Compile the binding with its backing and import the ``.so``."""
    import numpy as np

    out = proj / "build-faces"
    out.mkdir(exist_ok=True)
    so = out / f"playlist{sysconfig.get_config_var('EXT_SUFFIX') or '.so'}"
    link = (
        ["-bundle", "-undefined", "dynamic_lookup"]
        if sys.platform == "darwin"
        else ["-shared"]
    )
    srcs = [
        "native/src/playlist/playlist_ext.c",
        "native/src/playlist/playlist.c",
    ]
    r = subprocess.run(
        [_CC, *link, "-fPIC", "-std=c11",
         "-Werror=implicit-function-declaration",
         "-I", str(proj / INC_DIR),
         "-I", sysconfig.get_path("include"),
         "-I", np.get_include(),
         *[str(proj / s) for s in srcs],
         "-o", str(so)],
        capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    spec = importlib.util.spec_from_file_location("playlist", so)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> "tuple[ast.Module, object]":
    root = tmp_path_factory.mktemp("gh2283")
    assert run_cli("new", "fc", "--no-c-prefix", cwd=root).returncode == 0
    proj = root / "fc"
    manifest = proj / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + _ENUM, encoding="utf-8"
    )
    (proj / "modules").mkdir(exist_ok=True)
    (proj / "modules" / "playlist.toml").write_text(
        _layout(_COMPOSER, proj), encoding="utf-8"
    )
    for rel, text in _BACKINGS.items():
        path = proj / _layout(rel, proj)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_layout(text, proj), encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    # The replay `status` diffs against reads the same headers, or every
    # header-derived doc reads as STALE.
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    stub = (proj / "src" / "fc" / "playlist" / "playlist.pyi").read_text(
        encoding="utf-8"
    )
    return ast.parse(stub), _import(proj)


def _runtime_text(name: str, doc: "str | None") -> "str | None":
    """*doc* less the leading ``name(...)`` signature line and its blank."""
    if doc is None:
        return None
    lines = inspect.cleandoc(doc).split("\n")
    if lines[0].startswith(f"{name}(") and (len(lines) == 1 or not lines[1]):
        lines = lines[2:]
    return "\n".join(lines) or None


def _faces(
    tree: ast.Module, mod
) -> "list[tuple[str, str | None, str | None]]":
    """``(where, stub, runtime)`` for every class and callable the stub has.

    Read from the stub, so a member jm starts generating is compared without
    anyone listing it. A dunder is Python's protocol, not a documented member,
    and an annotated attribute carries no docstring of its own (a field's
    doc is its class's ``Parameters`` entry).
    """
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            out.append(
                (
                    node.name,
                    ast.get_docstring(node),
                    _runtime_text(node.name, getattr(mod, node.name).__doc__),
                )
            )
        elif isinstance(node, ast.ClassDef):
            cls = getattr(mod, node.name)
            out.append(
                (
                    node.name,
                    ast.get_docstring(node),
                    _runtime_text("", cls.__doc__),
                )
            )
            for m in node.body:
                if not isinstance(m, ast.FunctionDef) or m.name.startswith(
                    "__"
                ):
                    continue
                out.append(
                    (
                        f"{node.name}.{m.name}",
                        ast.get_docstring(m),
                        _runtime_text(m.name, getattr(cls, m.name).__doc__),
                    )
                )
    return out


def test_every_member_says_the_same_on_both_faces(built) -> None:
    tree, mod = built
    faces = _faces(tree, mod)
    # Armed: the fixture's stub has the four types, two factories and the
    # composer's methods, so an empty walk cannot pass for a clean one.
    assert len(faces) >= 20, faces
    differ = [
        f"{where}:\n--- stub\n{stub}\n--- runtime\n{rt}"
        for where, stub, rt in faces
        if (stub and inspect.cleandoc(stub)) != rt
    ]
    assert not differ, "\n\n".join(differ)


def test_the_struct_brief_is_the_class_summary(built) -> None:
    """The issue's item 2: the type says what its struct's header says."""
    _, mod = built
    doc = inspect.cleandoc(mod.Clip.__doc__)
    assert doc.startswith("One clip: a waveform and its gain.\n"), doc
    assert "A track sums its clips sample by sample." in doc
    assert mod.Track.__doc__.startswith("One track: the clips it sums")


def test_help_lists_the_keywords_the_stub_lists(built) -> None:
    """The reported symptom: ``help(Synth)`` named no keyword."""
    _, mod = built
    doc = mod.Clip.__doc__
    assert "Parameters" in doc
    for line in ("kind : str", "gain : float", "Linear gain."):
        assert line in doc, (line, doc)


def test_a_factory_says_what_it_makes(built) -> None:
    """The issue's item 3: the discriminant value's own doc, read through
    the enum's declared ``enumerators``, and where its keywords are."""
    _, mod = built
    doc = mod.sine.__doc__
    assert "Return a Clip configured as a *sine* source." in doc
    assert "A pure sine at the clip's gain." in doc
    assert 'kind="sine"' in doc and "help(Clip)" in doc
    assert "A square wave" in mod.square.__doc__
