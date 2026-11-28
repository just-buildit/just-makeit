"""gh-2214: a kind module's setter can refuse a value.

gh-2182 let a writable OBJECT property declare ``error`` /
``error_message``. Three other faces call a C setter the author wrote and
discarded what it answered, so assigning a value the core refused succeeded
in Python and the attribute went on reading its old value:

- a ``kind = "handle"`` getter field's ``writable_fn``;
- a ``kind = "capsule"`` module's writable property
  (``<backing>_set_<prop>``);
- a ``kind = "composer"`` setting's ``setter_fn`` -- both the attribute and
  the post-construction keyword ``create`` applies.

Each now takes the object face's pair on its own row: with ``error`` the
C setter returns ``int`` and a non-zero return raises the declared
exception through the raise emitter every status-checking member uses.

Built and run, not read from generated text: whether an assignment raises
is a fact about the extension, and the only honest way to read it is to
assign.
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli
from test_gh2346_composer_capsule import _cmake, _same_second_as_the_build

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, SRC.as_posix())

from just_makeit import _config as C  # noqa: E402
from just_makeit import _capsule, _composer, _handle  # noqa: E402
from just_makeit._report import Refusal  # noqa: E402
from _cmake import PYTHON3_ARG

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

PKG = "kz"
#: Distinct per face, so a hit cannot come from the neighbour.
HB_MSG = "g must be non-negative"
CB_MSG = "q must be non-negative"
MIX_MSG = "gain must be non-negative"


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


# ── handle + capsule, one project over one hand-written backing ──────────────

_HB_H = """\
#ifndef HB_CORE_H
#define HB_CORE_H
typedef struct hb hb_t;
hb_t *hb_open(void);
void hb_close(hb_t *h);
double hb_get_g(const hb_t *h);
int hb_set_g(hb_t *h, double v);
double hb_get_u(const hb_t *h);
void hb_set_u(hb_t *h, double v);
#endif
"""

_CB_H = """\
#ifndef CB_CORE_H
#define CB_CORE_H
typedef struct cb_state cb_state_t;
cb_state_t *cb_create(double q);
void cb_destroy(cb_state_t *st);
double cb_get_q(const cb_state_t *st);
int cb_set_q(cb_state_t *st, double v);
#endif
"""

#: Each refusing setter returns a distinct non-zero code and keeps the old
#: value; `hb_set_u` refuses too, but declares nothing, so it cannot say so.
_BACKING_C = f"""\
#include "{PKG}/hb/hb_core.h"
#include "{PKG}/cb/cb_core.h"
#include <stdlib.h>
struct hb {{ double g, u; }};
struct cb_state {{ double q; }};
hb_t *hb_open(void)
{{
    hb_t *h = calloc(1, sizeof *h);
    if (h) {{ h->g = 0.5; h->u = 0.5; }}
    return h;
}}
void hb_close(hb_t *h) {{ free(h); }}
double hb_get_g(const hb_t *h) {{ return h->g; }}
int hb_set_g(hb_t *h, double v)
{{
    if (v < 0.0) return -3;
    h->g = v;
    return 0;
}}
double hb_get_u(const hb_t *h) {{ return h->u; }}
void hb_set_u(hb_t *h, double v) {{ if (v >= 0.0) h->u = v; }}
cb_state_t *cb_create(double q)
{{
    cb_state_t *st = calloc(1, sizeof *st);
    if (st) st->q = q;
    return st;
}}
void cb_destroy(cb_state_t *st) {{ free(st); }}
double cb_get_q(const cb_state_t *st) {{ return st->q; }}
int cb_set_q(cb_state_t *st, double v)
{{
    if (v < 0.0) return 9;
    st->q = v;
    return 0;
}}
"""


def _handle_module() -> dict:
    return {
        "kind": "handle",
        "backing": "hb",
        "type_name": "Hb",
        "create_fn": "hb_open",
        "close_fn": "hb_close",
        "getters": [
            {
                "fn": "hb_get_g",
                "out": "double",
                "fields": [
                    {
                        "name": "g",
                        "type": "double",
                        "writable_fn": "hb_set_g",
                        "error": "ValueError",
                        "error_message": HB_MSG,
                    }
                ],
            },
            {
                "fn": "hb_get_u",
                "out": "double",
                "fields": [
                    {"name": "u", "type": "double", "writable_fn": "hb_set_u"}
                ],
            },
        ],
        "extra_link_libs": ["backing_core"],
    }


def _capsule_module() -> dict:
    return {
        "kind": "capsule",
        "backing": "cb",
        "init_params": [{"name": "q", "type": "double"}],
        "properties": [
            {
                "name": "q",
                "type": "double",
                "writable": True,
                "error": "OverflowError",
                "error_message": CB_MSG,
            }
        ],
        "extra_link_libs": ["backing_core"],
    }


@pytest.fixture(scope="module")
def kinds(tmp_path_factory) -> Path:
    """The handle and the capsule, applied, configured and built."""
    if _NO_TOOLCHAIN:
        pytest.skip("no cmake / C compiler")
    root = tmp_path_factory.mktemp("gh2214")
    _ok("new", PKG, cwd=root)
    proj = root / PKG
    for name, text in (("hb", _HB_H), ("cb", _CB_H)):
        inc = proj / "native" / "inc" / PKG / name
        inc.mkdir(parents=True, exist_ok=True)
        (inc / f"{name}_core.h").write_text(text, encoding="utf-8")
    backing = proj / "native" / "src" / "backing"
    backing.mkdir(parents=True, exist_ok=True)
    (backing / "backing.c").write_text(_BACKING_C, encoding="utf-8")
    (backing / "CMakeLists.txt").write_text(
        "add_library(backing_core OBJECT backing.c)\n"
        "target_include_directories(backing_core PUBLIC"
        " ${CMAKE_SOURCE_DIR}/native/inc)\n",
        encoding="utf-8",
    )
    cfg = C.load(proj)
    cfg["project"]["c_deps"] = ["backing"]
    cfg.setdefault("module", {}).update(
        {"hb": _handle_module(), "cb": _capsule_module()}
    )
    C.save(proj, cfg)
    _ok("apply", cwd=proj)
    _cmake(
        proj,
        "-S",
        ".",
        "-B",
        "build",
        PYTHON3_ARG,
        "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
    )
    _cmake(proj, "--build", "build", "--parallel", "4")
    return proj


_KINDS_PROBE = f"""
import json, sys
sys.path.insert(0, "src")
hbm = __import__("{PKG}.hb.hb", fromlist=["Hb"])
cb = __import__("{PKG}.cb.cb", fromlist=["cb"])


def attempt(call):
    try:
        call()
    except Exception as e:
        return [type(e).__name__, str(e)]
    return None


out = {{}}
h = hbm.Hb()
out["hb_bad"] = attempt(lambda: setattr(h, "g", -1.0))
out["hb_after_bad"] = h.g
out["hb_good"] = attempt(lambda: setattr(h, "g", 2.0))
out["hb_after_good"] = h.g
out["hb_undeclared"] = attempt(lambda: setattr(h, "u", -1.0))
out["hb_undeclared_after"] = h.u
out["hb_doc"] = type(h).g.__doc__
st = cb.cb_create(0.5)
out["cb_bad"] = attempt(lambda: cb.cb_set_q(st, -1.0))
out["cb_after_bad"] = cb.cb_get_q(st)
out["cb_good"] = attempt(lambda: cb.cb_set_q(st, 2.0))
out["cb_after_good"] = cb.cb_get_q(st)
out["cb_doc"] = cb.cb_set_q.__doc__
print(json.dumps(out))
"""


def _probe(proj: Path, src: str) -> dict:
    r = subprocess.run(
        [sys.executable, "-c", src],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def kinds_probe(kinds) -> dict:
    return _probe(kinds, _KINDS_PROBE)


@pytest.mark.slow
def test_a_handle_field_setter_refusal_raises(kinds_probe) -> None:
    """The handle face: the declared class and message, the rc appended,
    and the field keeps reading its old value."""
    assert kinds_probe["hb_bad"] == ["ValueError", f"{HB_MSG} (rc=-3)"]
    assert kinds_probe["hb_after_bad"] == 0.5
    assert kinds_probe["hb_good"] is None
    assert kinds_probe["hb_after_good"] == 2.0


@pytest.mark.slow
def test_a_handle_field_without_error_is_unchanged(kinds_probe) -> None:
    """No `error`, no raise: the shape every existing handle has."""
    assert kinds_probe["hb_undeclared"] is None
    assert kinds_probe["hb_undeclared_after"] == 0.5


@pytest.mark.slow
def test_a_capsule_property_setter_refusal_raises(kinds_probe) -> None:
    """The capsule face: the setter is a free function over the state."""
    assert kinds_probe["cb_bad"] == ["OverflowError", f"{CB_MSG} (rc=9)"]
    assert kinds_probe["cb_after_bad"] == 0.5
    assert kinds_probe["cb_good"] is None
    assert kinds_probe["cb_after_good"] == 2.0


@pytest.mark.slow
def test_the_runtime_docs_say_what_assignment_raises(kinds_probe) -> None:
    """The documented class is the raised class, from the same pair."""
    assert _raises(kinds_probe["hb_doc"], "ValueError", HB_MSG)
    assert _raises(kinds_probe["cb_doc"], "OverflowError", CB_MSG)


def _raises(doc: str, category: str, message: str) -> bool:
    """Whether *doc* has a ``Raises`` entry for *category* quoting
    *message*, however the renderer wrapped it."""
    flat = " ".join(doc.split())
    return f"Raises ------ {category} " in flat and f"``{message}``" in flat


def test_the_stubs_say_what_assignment_raises(kinds) -> None:
    """The ``.pyi`` face, from the same pair as the runtime one."""
    hb = next(kinds.glob("src/**/hb.pyi")).read_text(encoding="utf-8")
    cbs = next(kinds.glob("src/**/cb.pyi")).read_text(encoding="utf-8")
    assert _raises(hb, "ValueError", HB_MSG)
    assert _raises(cbs, "OverflowError", CB_MSG)


# ── composer, on the bundled composer_seams project ──────────────────────────

_DESTROY_DECL = "void           playlist_destroy (playlist_state_t *state);"
_DESTROY = "void\nplaylist_destroy (playlist_state_t *state)\n{\n"


@pytest.fixture(scope="module")
def composer(tmp_path_factory) -> Path:
    """composer_seams, given a ``gain`` setting whose setter refuses a
    negative value, applied and rebuilt."""
    if _NO_TOOLCHAIN:
        pytest.skip("no cmake / C compiler")
    tmp = tmp_path_factory.mktemp("gh2214c")
    sys.path.insert(
        0, str(SRC / "just_makeit" / "examples" / "composer_seams")
    )
    try:
        seams = importlib.import_module("test")
        seams.run(tmp)
    finally:
        sys.path.pop(0)
        sys.modules.pop("test", None)
    proj = tmp / "studio"
    hdr = next(proj.glob("native/inc/**/playlist/playlist_core.h"))
    text = hdr.read_text(encoding="utf-8")
    assert text.count(_DESTROY_DECL) == 1
    hdr.write_text(
        text.replace(
            _DESTROY_DECL,
            _DESTROY_DECL + "\n"
            "int            playlist_set_gain (playlist_state_t *state, "
            "int v);\n"
            "int            playlist_gain (const playlist_state_t *state);",
            1,
        ),
        encoding="utf-8",
    )
    core = proj / "native" / "src" / "backing" / "playlist_core.c"
    c = core.read_text(encoding="utf-8")
    assert c.count(_DESTROY) == 1
    c = c.replace(
        _DESTROY,
        "static int jm_gain = 1;\n"
        "int\nplaylist_set_gain (playlist_state_t *state, int v)\n{\n"
        "  (void) state;\n  if (v < 0)\n    return 5;\n"
        "  jm_gain = v;\n  return 0;\n}\n"
        "int\nplaylist_gain (const playlist_state_t *state)\n{\n"
        "  (void) state;\n  return jm_gain;\n}\n\n" + _DESTROY,
        1,
    )
    core.write_text(c, encoding="utf-8")
    cfg = C.load(proj)
    cfg["module"]["playlist"]["settings"] = [
        {
            "name": "gain",
            "setter_fn": "playlist_set_gain",
            "getter_fn": "playlist_gain",
            "type": "int",
            "error": "ValueError",
            "error_message": MIX_MSG,
        }
    ]
    C.save(proj, cfg)
    _ok("apply", cwd=proj)
    _same_second_as_the_build(proj)
    _cmake(proj, "--build", "build", "--parallel", "4", "--clean-first")
    return proj


_MIX_PROBE = """
import json, sys
sys.path.insert(0, "src")
from studio.playlist.playlist import Mix, Track


def attempt(call):
    try:
        call()
    except Exception as e:
        return [type(e).__name__, str(e)]
    return None


out = {}
m = Mix([Track(dur=4)])
out["good"] = attempt(lambda: setattr(m, "gain", 3))
out["after_good"] = m.gain
out["bad"] = attempt(lambda: setattr(m, "gain", -1))
out["after_bad"] = m.gain
out["ctor_bad"] = attempt(lambda: Mix([Track(dur=4)], gain=-2))
out["after_ctor_bad"] = m.gain
out["doc"] = Mix.gain.__doc__
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def composer_probe(composer) -> dict:
    return _probe(composer, _MIX_PROBE)


@pytest.mark.slow
def test_a_composer_setting_refusal_raises(composer_probe) -> None:
    """The attribute face: raises, and the setting keeps its old value."""
    assert composer_probe["good"] is None
    assert composer_probe["after_good"] == 3
    assert composer_probe["bad"] == ["ValueError", f"{MIX_MSG} (rc=5)"]
    assert composer_probe["after_bad"] == 3


@pytest.mark.slow
def test_a_composer_setting_refused_at_construction_raises(
    composer_probe,
) -> None:
    """The keyword face: ``create`` applies the setting after create_fn,
    through the same setter, so a refusal there raises too."""
    assert composer_probe["ctor_bad"] == ["ValueError", f"{MIX_MSG} (rc=5)"]
    assert composer_probe["after_ctor_bad"] == 3


@pytest.mark.slow
def test_a_composer_setting_documents_its_refusal(
    composer, composer_probe
) -> None:
    """Both faces, and the stub's attribute is a property that can carry
    a docstring at all."""
    assert _raises(composer_probe["doc"], "ValueError", MIX_MSG)
    pyi = next(composer.glob("src/**/playlist.pyi")).read_text(
        encoding="utf-8"
    )
    assert _raises(pyi, "ValueError", MIX_MSG)
    assert "    @gain.setter\n" in pyi


# ── refusals: `error` where no setter return can be tested ───────────────────


def _cfg(module: dict) -> dict:
    return {
        "project": {"name": PKG, "version": "0.1.0"},
        "module": {"m": module},
    }


def test_a_handle_field_error_without_writable_fn_is_refused() -> None:
    mod = _handle_module()
    del mod["getters"][0]["fields"][0]["writable_fn"]
    with pytest.raises(Refusal, match="writable_fn"):
        _handle.render_ext(_cfg(mod), "m")


def test_a_capsule_error_on_a_read_only_property_is_refused() -> None:
    mod = _capsule_module()
    del mod["properties"][0]["writable"]
    with pytest.raises(Refusal, match="writable"):
        _capsule.render_ext(_cfg(mod), "m")


@pytest.mark.parametrize(
    "row",
    [
        {"error_message": "no category"},
        {"error": "NotAnError"},
    ],
)
def test_a_malformed_pair_is_refused_on_every_face(row) -> None:
    """The object face's rules, through its own refusal, on each kind."""
    h = _handle_module()
    h["getters"][0]["fields"][0].pop("error")
    h["getters"][0]["fields"][0].pop("error_message")
    h["getters"][0]["fields"][0].update(row)
    with pytest.raises(Refusal):
        _handle.render_ext(_cfg(h), "m")
    cap = _capsule_module()
    cap["properties"][0].pop("error")
    cap["properties"][0].pop("error_message")
    cap["properties"][0].update(row)
    with pytest.raises(Refusal):
        _capsule.render_ext(_cfg(cap), "m")
    st = {
        "name": "gain",
        "setter_fn": "s",
        "getter_fn": "g",
        "type": "int",
        **row,
    }
    with pytest.raises(Refusal):
        _composer._settings_getset_c(_cfg({"settings": [st]}), "m", "c", "o")
