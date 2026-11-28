"""gh-2813: an `int` setter on a kind row that declares no `error`.

gh-2302 refuses, on an object, a writable property whose header declares
its setter returning a status while the manifest declares no ``error``: the
binding would call it as a bare statement and an assignment C refused would
succeed. gh-2214 let three other faces declare ``error`` and left the
converse open:

- a ``kind = "handle"`` getter field's ``writable_fn``;
- a ``kind = "capsule"`` module's writable property;
- a ``kind = "composer"`` setting's ``setter_fn``.

`apply` and `status --check` now refuse each, before any write, naming the
row and the two ways out (declare ``error``, or make the setter ``void``),
through the predicate the object face uses.

Driven through `run_cli`: the refusal is a fact about the CLI.
"""

from __future__ import annotations

import importlib
import re
import shutil
import sys
from pathlib import Path

import pytest
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, SRC.as_posix())

from just_makeit import _config as C  # noqa: E402

PKG = "kz"

_HB_H = """\
#ifndef HB_CORE_H
#define HB_CORE_H
typedef struct hb hb_t;
hb_t *hb_open(void);
void hb_close(hb_t *h);
double hb_get_g(const hb_t *h);
{set_g}
#endif
"""

_CB_H = """\
#ifndef CB_CORE_H
#define CB_CORE_H
typedef struct cb_state cb_state_t;
cb_state_t *cb_create(double q);
void cb_destroy(cb_state_t *st);
double cb_get_q(const cb_state_t *st);
{set_q}
#endif
"""

#: The declaration each face's setter has, by what it returns.
_HB_SET = "{ret} hb_set_g(hb_t *h, double v);"
_CB_SET = "{ret} cb_set_q(cb_state_t *st, double v);"


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _handle_module(error: bool) -> dict:
    field = {"name": "g", "type": "double", "writable_fn": "hb_set_g"}
    if error:
        field |= {"error": "ValueError", "error_message": "bad g"}
    return {
        "kind": "handle",
        "backing": "hb",
        "type_name": "Hb",
        "create_fn": "hb_open",
        "close_fn": "hb_close",
        "getters": [{"fn": "hb_get_g", "out": "double", "fields": [field]}],
    }


def _capsule_module(error: bool) -> dict:
    prop = {"name": "q", "type": "double", "writable": True}
    if error:
        prop |= {"error": "ValueError", "error_message": "bad q"}
    return {
        "kind": "capsule",
        "backing": "cb",
        "init_params": [{"name": "q", "type": "double"}],
        "properties": [prop],
    }


def _project(root: Path, face: str, ret: str, error: bool) -> Path:
    """A project with one *face* module whose backing header declares the
    setter returning *ret*; the row declares ``error`` when *error*."""
    _ok("new", PKG, cwd=root)
    proj = root / PKG
    heads = {
        "hb": _HB_H.replace("{set_g}", _HB_SET.format(ret=ret)),
        "cb": _CB_H.replace("{set_q}", _CB_SET.format(ret=ret)),
    }
    name = {"handle": "hb", "capsule": "cb"}[face]
    inc = proj / "native" / "inc" / PKG / name
    inc.mkdir(parents=True, exist_ok=True)
    (inc / f"{name}_core.h").write_text(heads[name], encoding="utf-8")
    cfg = C.load(proj)
    cfg.setdefault("module", {})[name] = (
        _handle_module(error) if face == "handle" else _capsule_module(error)
    )
    C.save(proj, cfg)
    return proj


FACES = {
    "handle": ("hb", "g", "hb_set_g"),
    "capsule": ("cb", "q", "cb_set_q"),
}


@pytest.mark.parametrize("face", sorted(FACES))
def test_an_int_setter_with_no_error_is_refused(tmp_path, face) -> None:
    mod, row, fn = FACES[face]
    proj = _project(tmp_path, face, "int", error=False)
    before = sorted(p.as_posix() for p in proj.rglob("*") if p.is_file())
    r = run_cli("apply", cwd=proj)
    assert r.returncode != 0, r.stdout + r.stderr
    err = " ".join((r.stdout + r.stderr).split())
    assert f"`{mod}.{row}`" in err, err
    assert f"{fn}()" in err and "returning `int`" in err, err
    assert "Declare `error`" in err and "return void" in err, err
    after = sorted(p.as_posix() for p in proj.rglob("*") if p.is_file())
    assert after == before, "a refused apply wrote files"


@pytest.mark.parametrize("face", sorted(FACES))
def test_status_check_refuses_it_too(tmp_path, face) -> None:
    mod, row, _fn = FACES[face]
    proj = _project(tmp_path, face, "int", error=False)
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode != 0, r.stdout + r.stderr
    assert f"`{mod}.{row}`" in " ".join((r.stdout + r.stderr).split())


@pytest.mark.parametrize("face", sorted(FACES))
def test_the_ways_out_apply(tmp_path, face) -> None:
    """`void` setter, or `int` with `error` declared: both apply."""
    for ret, error in (("void", False), ("int", True)):
        root = tmp_path / ret
        root.mkdir()
        _ok("apply", cwd=_project(root, face, ret, error))


def test_a_setter_declared_in_an_included_header_is_found(tmp_path) -> None:
    """The author's prototype may sit in a header the backing one
    includes; it is read there, as the compiler would."""
    proj = _project(tmp_path, "capsule", "void", error=False)
    inc = proj / "native" / "inc" / PKG / "cb"
    (inc / "cb_core.h").write_text(
        _CB_H.replace("{set_q}", '#include "kz/cb/cb_setters.h"'),
        encoding="utf-8",
    )
    (inc / "cb_setters.h").write_text(
        "int cb_set_q(cb_state_t *st, double v);\n", encoding="utf-8"
    )
    r = run_cli("apply", cwd=proj)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "cb_set_q()" in " ".join((r.stdout + r.stderr).split())


# -- composer ----------------------------------------------------------------

_DESTROY_DECL = "void           playlist_destroy (playlist_state_t *state);"


def _composer(tmp_path: Path, ret: str, error: bool) -> Path:
    """composer_seams, given a ``gain`` setting whose setter the header
    declares returning *ret*."""
    sys.path.insert(
        0, str(SRC / "just_makeit" / "examples" / "composer_seams")
    )
    try:
        seams = importlib.import_module("test")
        seams.run(tmp_path)
    finally:
        sys.path.pop(0)
        sys.modules.pop("test", None)
    proj = tmp_path / "studio"
    hdr = next(proj.glob("native/inc/**/playlist/playlist_core.h"))
    text = hdr.read_text(encoding="utf-8")
    assert text.count(_DESTROY_DECL) == 1
    hdr.write_text(
        text.replace(
            _DESTROY_DECL,
            _DESTROY_DECL + "\n"
            f"{ret}            playlist_set_gain (playlist_state_t *state, "
            "int v);\n"
            "int            playlist_gain (const playlist_state_t *state);",
            1,
        ),
        encoding="utf-8",
    )
    cfg = C.load(proj)
    row = {
        "name": "gain",
        "setter_fn": "playlist_set_gain",
        "getter_fn": "playlist_gain",
        "type": "int",
    }
    if error:
        row |= {"error": "ValueError", "error_message": "bad gain"}
    cfg["module"]["playlist"]["settings"] = [row]
    C.save(proj, cfg)
    return proj


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("cmake") is None, reason="no cmake")
def test_a_composer_setting_is_refused_and_has_its_ways_out(tmp_path) -> None:
    (tmp_path / "a").mkdir()
    proj = _composer(tmp_path / "a", "int", error=False)
    r = run_cli("apply", cwd=proj)
    assert r.returncode != 0, r.stdout + r.stderr
    err = " ".join((r.stdout + r.stderr).split())
    assert "`playlist.gain`" in err and "playlist_set_gain()" in err, err
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode != 0, r.stdout + r.stderr
    for ret, error in (("void", False), ("int", True)):
        root = tmp_path / f"{ret}{error}"
        root.mkdir()
        _ok("apply", cwd=_composer(root, ret, error))


def test_the_check_is_the_objects_predicate() -> None:
    """One predicate: the object face and the kind faces ask the same
    question of a declared return (registration-free: the two call
    sites are found in the source)."""
    text = (SRC / "just_makeit" / "_property.py").read_text(encoding="utf-8")
    assert len(re.findall(r"\\bvoid\\b\(\?!", text)) == 1, (
        "the `void`-not-`void *` test is spelled once"
    )
