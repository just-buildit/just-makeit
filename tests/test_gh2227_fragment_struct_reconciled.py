"""gh-2227: `apply` reconciles a module fragment's object struct.

A module object's binding fragment is sacred: `apply` transplants the members
it lacks into it and keeps the rest (gh-770). Its ``typedef struct {...}
<C>Object;`` was never revisited, so a member apply added that writes a field
jm declares did not compile -- a borrow's ``self->_jm_borrowed``, a released
call's ``self->_jm_inflight``, a retained owner (gh-2327) -- and a lending
object could not be given the ``_jm_parked`` its teardown needs (gh-2187).
Any verb on the module re-rendered the struct and healed it, so only a hand
edit plus `apply` broke.

`_object.reconcile_struct_fields` makes the struct's jm fields the render's:
missing ones added, ones jm no longer renders removed, every author line kept.
`tp_dealloc`, which releases what those fields hold, is held to the render
as every verb already holds it.

Gates (the issue's): each row that adds a jm field, hand-added to the
manifest and then `jm apply`, builds; a field jm stops rendering is removed;
an author-added struct member survives.
"""

# gh-1591: this file's hand-written C spells jm's bare derived names, so its
# projects opt out of the prefix `jm new` now defaults to.

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

_TT_H = "#ifndef TT_H\n#define TT_H\ntypedef struct tt tt_t;\n#endif\n"

#: Each row: what is appended to `objects/o.toml`, and the jm field it adds.
ROWS = {
    # The issue's trigger: a borrow writes `_jm_borrowed`, and an object
    # that lends parks its state in `_jm_parked` (gh-2187).
    "borrow": (
        '\n[[o.methods]]\nname = "wait"\nborrow = true\n'
        'arg_type = "void"\nreturn_type = "sample"\n'
        'params = [{ name = "n", type = "size_t" }]\n',
        ("_jm_borrowed", "_jm_parked"),
    ),
    # A released call is counted in `_jm_inflight` (gh-2272).
    "nogil": (
        '\n[[o.methods]]\nname = "work"\nreturn_type = "int"\nnogil = true\n',
        ("_jm_inflight",),
    ),
    # A retained capsule param keeps its owner (gh-2327).
    "retain": (
        '\n[[o.methods]]\nname = "attach"\nreturn_type = "int"\n'
        "status_return = true\n"
        'params = [{ name = "t", type = "tt_t *", capsule = "p.tt", '
        'header = "p/tt.h", retain = true }]\n',
        ("_jm_retain_attach_t",),
    ),
}


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _project(tmp_path: Path) -> Path:
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    (proj / "native/inc/p/tt.h").write_text(_TT_H, encoding="utf-8")
    _ok("module", "dsp", cwd=proj)
    _ok("object", "o", "--module", "dsp", cwd=proj)
    # The borrow row's element (gh-1405).
    _ok("record", "o", "sample", "--type", "float _Complex", cwd=proj)
    return proj


def _frag(proj: Path) -> Path:
    return proj / "native/src/dsp/dsp_ext_o.c"


def _struct(proj: Path) -> str:
    text = _frag(proj).read_text(encoding="utf-8")
    m = re.search(r"typedef struct \{\n(.*?)\} OObject;", text, re.S)
    assert m, text
    return m.group(1)


def _append(proj: Path, toml: str) -> None:
    frag = proj / "objects" / "o.toml"
    frag.write_text(frag.read_text(encoding="utf-8") + toml, encoding="utf-8")


def _build(proj: Path) -> None:
    build = subprocess.run(
        ["make", f"PYTHON={sys.executable}"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert build.returncode == 0, build.stderr[-3000:]


@pytest.mark.parametrize("row", sorted(ROWS))
def test_a_hand_added_member_gets_its_fields(tmp_path, row):
    proj = _project(tmp_path)
    toml, fields = ROWS[row]
    _append(proj, toml)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stderr
    struct = _struct(proj)
    for f in fields:
        assert re.search(rf"\b{f}\s*;", struct), (f, struct)
    assert "object struct fields +" in r.stdout, r.stdout
    # And dealloc releases what a field holds (the retained owner).
    if row == "retain":
        text = _frag(proj).read_text(encoding="utf-8")
        dealloc = text[text.index("O_dealloc(OObject *self)") :]
        assert (
            "Py_XDECREF(self->_jm_retain_attach_t);"
            in dealloc[: dealloc.index("\n}\n")]
        )


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
@pytest.mark.parametrize("row", sorted(ROWS))
def test_a_hand_added_member_builds(tmp_path, row):
    proj = _project(tmp_path)
    _append(proj, ROWS[row][0])
    _ok("apply", cwd=proj)
    _build(proj)


def test_an_author_member_survives_and_a_dropped_field_goes(tmp_path):
    proj = _project(tmp_path)
    _append(proj, ROWS["retain"][0])
    _ok("apply", cwd=proj)
    frag = _frag(proj)
    text = frag.read_text(encoding="utf-8")
    assert text.count("} OObject;") == 1, text
    frag.write_text(
        text.replace(
            "} OObject;", "    int mine;  /* the author's */\n} OObject;"
        ),
        encoding="utf-8",
    )
    # The param is no longer retained: jm stops rendering its field.
    toml = proj / "objects" / "o.toml"
    t = toml.read_text(encoding="utf-8")
    assert t.count(", retain = true") == 1, t
    toml.write_text(t.replace(", retain = true", ""), encoding="utf-8")
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stderr
    struct = _struct(proj)
    assert "_jm_retain_attach_t" not in struct, struct
    assert "    int mine;  /* the author's */\n" in struct, struct
    assert "object struct fields -_jm_retain_attach_t" in r.stdout, r.stdout


def test_an_unchanged_struct_is_not_rewritten(tmp_path):
    """No jm field moved: apply leaves the fragment's bytes alone."""
    proj = _project(tmp_path)
    _append(proj, ROWS["retain"][0])
    _ok("apply", cwd=proj)
    before = _frag(proj).read_bytes()
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stderr
    assert "object struct fields" not in r.stdout, r.stdout
    assert _frag(proj).read_bytes() == before
