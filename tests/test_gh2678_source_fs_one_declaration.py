"""gh-2678: a composer source type's ``fs`` is declared once, for every face.

A ``kind = "composer"`` source type (doppler's ``Synth``) takes a trailing
``fs`` its source fields do not declare. Two places gave it a default:

- the ``tp_init`` C, where jm wrote ``double fs = 1e6;``;
- the class docstring and ``.pyi``, which borrowed the SEGMENT's ``fs``
  field -- ``fs : float, default 1.0`` and that field's sentence.

So ``Synth`` documented ``default 1.0`` and ran at 1e6, while the composed
object's own ``[[wfm_synth.init_params]] fs`` -- the declaration the value is
handed to -- was read by neither. Now ``_composer.source_fs`` is the one row
the default, the docstring and the getset doc all render from: the object's
``fs`` init param, or jm's documented fallback when it has none.

GATE (compiled and imported, because the bug was a runtime value disagreeing
with documentation): for every class the generated ``.pyi`` defines, every
``name : T, default V`` its docstring states with a numeric ``V`` is the
attribute a bare instance carries. Read from the stub, so a field or type
added later is checked without a list to extend. Plus: each type's ``fs``
says its own declaration's sentence, and the fallback row agrees with the
C it renders.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest
import _cbuild as cbuild
from _jminc import INC_DIR
from _jmrun import run_cli

from just_makeit import _composer
from just_makeit import _incpath as INC
from just_makeit._report import Refusal

_CC = cbuild.compiler_for()

#: The composed object's own declaration: what the source's `fs` must say.
_OBJ_FS_DEFAULT = "1000000.0"
_OBJ_FS_DOC = "Clip sample rate in Hz; the generator runs at this rate."
#: The segment's `fs`: a different value and sentence, true of the segment.
_SEG_FS_DOC = "Track sample rate in Hz; 1.0 means cycles per sample."

_COMPOSER = """\
[module.playlist]
kind = "composer"
backing = "playlist"
composes = ["clip"]

[module.playlist.source]
object = "clip"
struct = "clip_src_t"
type_name = "Clip"

[[module.playlist.source.fields]]
name = "level"
type = "double"
default = "0.5"

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

[[module.playlist.segment.fields]]
name = "fs"
type = "double"
default = "1.0"
doc = "<<SEG_DOC>>"

[module.playlist.oo]
composer_type_name = "Mix"
"""

_BACKINGS = {
    "native/inc/<<P>>playlist/playlist_core.h": """\
#ifndef PLAYLIST_CORE_H
#define PLAYLIST_CORE_H
#include <stddef.h>
#include "<<P>>clib_common.h"
#include "<<P>>clip/clip_core.h"
/** @brief One clip's configuration. */
typedef struct {
    double level;
} clip_src_t;
/** @brief One track: the clips it sums, for `dur` samples. */
typedef struct {
    clip_src_t *sources;
    size_t      n_sources;
    size_t      dur;
    double      fs;
} track_t;
typedef struct playlist_state playlist_state_t;
playlist_state_t *playlist_create(const track_t *segs, size_t n, int repeat,
                                  int continuous);
void playlist_destroy(playlist_state_t *s);
size_t playlist_execute(playlist_state_t *s, float _Complex *out, size_t n);
const track_t *playlist_segments(const playlist_state_t *s, size_t *n,
                                 int *repeat, int *continuous);
#endif
""",
    "native/src/playlist/playlist.c": """\
#include <stdlib.h>
#include "<<P>>playlist/playlist_core.h"
#include "<<P>>playlist/playlist_bridge.h"
struct playlist_state { int unused; };
clip_state_t *clip_from_source(const clip_src_t *src, double fs)
{
    (void)src;
    return clip_create(fs);
}
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
""",
}


def _layout(text: str, proj: Path) -> str:
    """The fixture's headers in *proj*'s layout (gh-1583)."""
    return text.replace("<<P>>", INC.prefix(proj))


def _import(proj: Path):
    """Compile the composer binding, its backing and the generator object."""
    out = proj / "build-gh2678"
    out.mkdir(exist_ok=True)
    srcs = [
        "native/src/playlist/playlist_ext.c",
        "native/src/playlist/playlist.c",
        "native/src/clip/clip_core.c",
    ]
    so = cbuild.build(
        "ext",
        [proj / s for s in srcs],
        cbuild.output(out, "playlist", "ext"),
        std="c11",
        warnings=["error=implicit-function-declaration"],
        includes=[proj / INC_DIR],
        numpy=True,
    ).path
    spec = importlib.util.spec_from_file_location("playlist", so)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> "tuple[ast.Module, object]":
    if _CC is None:
        pytest.fail("no C compiler: this gate needs one to build the .so")
    root = tmp_path_factory.mktemp("gh2678")
    assert run_cli("new", "gp", "--no-c-prefix", cwd=root).returncode == 0
    proj = root / "gp"
    r = run_cli(
        "object",
        "clip",
        "--arg-type",
        "void",
        "--return-type",
        "float _Complex",
        "--init-param",
        f"fs:double:{_OBJ_FS_DEFAULT}",
        cwd=proj,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    # The init param's own `doc`, which the CLI has no flag for.
    frag = proj / "objects" / "clip.toml"
    text = frag.read_text(encoding="utf-8")
    anchor = f'default = "{_OBJ_FS_DEFAULT}"\n'
    assert text.count(anchor) == 1, text
    frag.write_text(
        text.replace(anchor, anchor + f'doc = "{_OBJ_FS_DOC}"\n'),
        encoding="utf-8",
    )
    (proj / "modules").mkdir(exist_ok=True)
    (proj / "modules" / "playlist.toml").write_text(
        _layout(_COMPOSER, proj).replace("<<SEG_DOC>>", _SEG_FS_DOC),
        encoding="utf-8",
    )
    for rel, body in _BACKINGS.items():
        path = proj / _layout(rel, proj)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_layout(body, proj), encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    stub = (proj / "src" / "gp" / "playlist" / "playlist.pyi").read_text(
        encoding="utf-8"
    )
    return ast.parse(stub), _import(proj)


#: A numpy-style parameter line stating a default: ``name : T, default V``.
_DEFAULT_RE = re.compile(r"^(\w+) : [^,]+, default (\S+)$")


def _documented_defaults(
    tree: ast.Module,
) -> "list[tuple[str, str, float]]":
    """``(class, name, value)`` for every numeric default a class documents.

    Read from the stub's class docstrings, so every type and every field is
    covered without a list. Only a parameter the stub also declares as an
    attribute of the class: a segment documents its source's fields as
    constructor keywords that build ``sources[0]``, not attributes of its
    own. A non-numeric default (a quoted enum choice, ``None``) is not a
    number a bare attribute can be compared to.
    """
    out = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        attrs = {
            m.target.id
            for m in node.body
            if isinstance(m, ast.AnnAssign) and isinstance(m.target, ast.Name)
        }
        for line in (ast.get_docstring(node) or "").splitlines():
            m = _DEFAULT_RE.match(line.strip())
            if not m or m.group(1) not in attrs:
                continue
            try:
                value = float(m.group(2))
            except ValueError:
                continue
            out.append((node.name, m.group(1), value))
    return out


def test_every_documented_default_is_the_bare_attribute(built) -> None:
    tree, mod = built
    cases = _documented_defaults(tree)
    # Armed: both types' `fs`, the source field and the segment field are
    # all read, so an empty or partial walk cannot pass for a clean one.
    named = {(c, n) for c, n, _v in cases}
    assert {("Clip", "fs"), ("Clip", "level"), ("Track", "fs")} <= named
    differ = []
    for cls, name, value in cases:
        got = getattr(getattr(mod, cls)(), name)
        if got != value:
            differ.append(f"{cls}.{name}: documented {value}, bare {got}")
    assert not differ, "\n".join(differ)


def _param_doc(tree: ast.Module, cls: str, name: str) -> str:
    """The prose under ``name : ...`` in *cls*'s ``Parameters`` section."""
    node = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls
    )
    lines = (ast.get_docstring(node) or "").splitlines()
    start = next(
        i for i, ln in enumerate(lines) if ln.startswith(f"{name} : ")
    )
    body = []
    for ln in lines[start + 1 :]:
        if not ln.startswith("    "):
            break
        body.append(ln.strip())
    return " ".join(body)


def test_each_fs_says_its_own_declaration(built) -> None:
    tree, mod = built
    # The source's `fs` is the composed object's init param...
    assert _param_doc(tree, "Clip", "fs") == _OBJ_FS_DOC
    assert (mod.Clip.fs.__doc__ or "").strip() == _OBJ_FS_DOC
    assert mod.Clip().fs == float(_OBJ_FS_DEFAULT)
    # ...and the segment's is the segment's.
    assert _param_doc(tree, "Track", "fs") == _SEG_FS_DOC
    assert mod.Track().fs == 1.0


def _cfg(init_params: "list[dict] | None") -> dict:
    """A composer whose source names object ``g``, with *init_params*."""
    cfg: dict = {
        "project": {"name": "p"},
        "module": {
            "m": {
                "kind": "composer",
                "source": {
                    "object": "g",
                    "struct": "g_src_t",
                    "type_name": "Src",
                },
                "segment": {"type_name": "Seg", "struct": "seg_t"},
            }
        },
    }
    if init_params is not None:
        cfg["g"] = {"init_params": init_params}
    return cfg


@pytest.mark.parametrize(
    "init_params, default",
    [
        ([{"name": "fs", "type": "double", "default": "8000.0"}], "8000.0"),
        ([{"name": "gain", "type": "double", "default": "2.0"}], "1e6"),
        (None, "1e6"),
    ],
    ids=["declared", "object-without-fs", "no-object-table"],
)
def test_the_rendered_default_is_the_documented_one(
    init_params, default
) -> None:
    """With or without a declaration, the C default and the docstring agree:
    both render from `source_fs`, including jm's fallback row."""
    cfg = _cfg(init_params)
    assert _composer.source_fs(cfg, "m")["default"] == default
    c = _composer.render_source_type(cfg, "m")
    assert f"    double fs = {default};\n" in c
    doc = _composer.type_doc_lines(cfg, "m")["Src"]
    assert any(ln.strip() == f"fs : float, default {default}" for ln in doc)


def test_a_declared_fs_without_a_default_is_refused() -> None:
    cfg = _cfg([{"name": "fs", "type": "double", "required": True}])
    with pytest.raises(Refusal, match=r"\[\[g\.init_params\]\] `fs`"):
        _composer.source_fs(cfg, "m")
