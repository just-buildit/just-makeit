"""A C refusal's reason reaches the Python exception (gh-1706).

C functions that refuse often know why, and a common C convention is a
trailing ``const char **why`` out-param that receives a static sentence. jm
had no way to carry it: a module function's ``check_return`` raised
``RuntimeError: <fn> failed``, and a composer's delegated JSON factory raised
``ValueError: <fn> failed`` -- while the C CLI over the same reader named the
retired key the user had to change (doppler-dsp/doppler#1614).

Two declarations, one emitter:

- a module function's ``why = true`` (``jm function --why``) appends
  ``const char **why`` LAST to the C signature, and the binding passes
  ``&_why``;
- ``[module.X.json] from_json_why`` / ``from_file_why`` say the delegated
  reader takes one.

Both raise through ``_context._diagnostics.reason_raise_c``, which the
composer bridge's ``bridge_error_fn`` (gh-1307) now uses too: a reason is a
``ValueError`` carrying the C sentence, and a refusal with none keeps the
site's old text.

The end-to-end tests build real generated projects and assert the MESSAGE,
not just the class.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from test_composer_codegen import _cfg as _composer_cfg  # noqa: E402

from just_makeit import _composer  # noqa: E402
from just_makeit import _keys  # noqa: E402
from just_makeit import _render as R  # noqa: E402
from just_makeit import _script  # noqa: E402
from just_makeit._context._diagnostics import reason_raise_c  # noqa: E402
from just_makeit._stubs import _fn_stub  # noqa: E402

_SPEC = [{"name": "spec", "type": "const char *"}]


# -- the emitter ---------------------------------------------------------------


def test_without_a_reason_the_fallback_is_unchanged():
    fb = 'PyErr_SetString(PyExc_OSError,\n    "f failed");\n'
    assert reason_raise_c(fb, indent=8) == (
        '        PyErr_SetString(PyExc_OSError,\n            "f failed");\n'
    )


def test_a_reason_is_raised_as_value_error_before_the_fallback():
    out = reason_raise_c('PyErr_SetString(PyExc_OSError, "f");\n', "_why")
    assert out == (
        "        if (_why)\n"
        "            PyErr_SetString(PyExc_ValueError, _why);\n"
        "        else\n"
        '            PyErr_SetString(PyExc_OSError, "f");\n'
    )


# -- a module function: the C side ---------------------------------------------


def test_prototype_and_stub_take_why_last():
    decl = R.fn_c_decl("p_rate", [("spec", "const char *")], "int", why=True)
    assert decl == "int p_rate(const char * spec, const char **why);\n"
    stub = R.fn_c_stub("p_rate", [("spec", "const char *")], "int", why=True)
    assert "p_rate(const char * spec, const char **why)" in stub
    assert "(void)why;" in stub


def test_why_follows_the_self_sized_output():
    # A reason-naming C API puts the out-param last: f(spec, out, &why).
    for fn in (R.fn_c_decl, R.fn_c_stub):
        text = fn(
            "p_bits",
            [("n", "int")],
            "size_t",
            out_type="uint8_t",
            variable_output=True,
            why=True,
        )
        assert "p_bits(int n, uint8_t *out, const char **why)" in text


def test_inline_stub_takes_why():
    text = R.fn_c_inline_stub("p_rate", [("x", "int")], "int", why=True)
    assert "p_rate(int x, const char **why)" in text
    assert "(void)why;" in text


def test_without_why_the_prototype_is_unchanged():
    assert R.fn_c_decl("p_rate", [("spec", "const char *")], "int") == (
        "int p_rate(const char * spec);\n"
    )


# -- a module function: the binding ---------------------------------------------


def _wrap(**kw):
    return R._py_wrapper_for_function(
        "rate", _SPEC, kw.pop("return_type", "int"), c_name="p_rate", **kw
    )


def test_status_form_passes_and_raises_the_reason():
    src = _wrap(check_return=True, why=True)
    i_decl = src.index("const char *_why = NULL;")
    i_call = src.index("int _rc = p_rate(spec, &_why);")
    i_raise = src.index("PyErr_SetString(PyExc_ValueError, _why);")
    i_fallback = src.index('"p_rate failed (rc=%d)"')
    assert i_decl < i_call < i_raise < i_fallback


def test_count_form_passes_and_raises_the_reason():
    src = R._py_wrapper_for_function(
        "bits",
        [{"name": "n", "type": "int"}],
        "size_t",
        out_type="uint8_t",
        variable_output=True,
        out_size="n",
        check_return=True,
        why=True,
        c_name="p_bits",
    )
    assert "(uint8_t *)PyArray_DATA((PyArrayObject *)_out), &_why);" in src
    i_check = src.index("if (_n == 0) {")
    block = src[i_check : src.index("return NULL;", i_check)]
    assert "Py_DECREF(_out);" in block
    assert "PyErr_SetString(PyExc_ValueError, _why);" in block


def test_str_form_passes_and_raises_the_reason():
    src = R._py_wrapper_for_function(
        "name",
        [{"name": "n", "type": "int"}],
        "size_t",
        out_type="str",
        variable_output=True,
        out_size="n",
        check_return=True,
        why=True,
        c_name="p_name",
    )
    assert "p_name(n, _buf, &_why);" in src
    i_check = src.index("if (_n == 0) {")
    block = src[i_check : src.index("return NULL;", i_check)]
    assert "free(_buf);" in block
    assert "PyErr_SetString(PyExc_ValueError, _why);" in block


def test_why_without_check_return_is_refused():
    with pytest.raises(ValueError, match="nothing in its binding raises"):
        _wrap(why=True)


def test_python_surface_never_shows_why():
    fn = {
        "name": "rate",
        "return_type": "int",
        "check_return": True,
        "why": True,
        "params": _SPEC,
    }
    assert "why" not in _fn_stub(fn)
    ctx = R.make_functions_ctx(
        "m", "M", [fn], {}, owner={"project": {"name": "p"}}
    )
    table = ctx["module_methods_def"]
    assert "why" not in table[table.index('{"rate"') :]


# -- the manifest: recognised, replayed ------------------------------------------


def test_keys_are_recognised():
    cfg = {
        "module": {
            "m": {
                "functions": [
                    {"name": "rate", "check_return": True, "why": True}
                ]
            }
        }
    }
    assert _keys.unknown_keys(cfg) == []
    assert {"from_json_why", "from_file_why"} <= _keys.COMPOSER_JSON_KEYS


def test_script_replays_the_flag():
    flags = "".join(
        _script._function_flags(
            {"name": "rate", "check_return": True, "why": True}, "m"
        )
    )
    assert "--why" in flags


def test_cli_declares_it(tmp_path, monkeypatch):
    from just_makeit import _cli_function
    from just_makeit._config import load
    from just_makeit._module import run as mod_run
    from just_makeit._new import run as new_run

    dest = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", dest)
        mod_run(dest, "m")
        monkeypatch.chdir(dest)
        _cli_function.run(
            [
                "rate",
                "--module",
                "m",
                "--param",
                "spec:const char *",
                "--return-type",
                "int",
                "--check-return",
                "--why",
            ]
        )
    fn = load(dest)["module"]["m"]["functions"][0]
    assert fn["why"] is True


def test_the_manifest_writer_keeps_the_keys():
    """``_dump`` is the writer ``jm split-objects`` and a new manifest go
    through; its function block was hand-enumerated and wrote neither
    ``check_return`` nor ``why``, so the refusal handling vanished."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        import tomli as tomllib

    from just_makeit import _config as C

    fn = {"name": "rate", "return_type": "int", "check_return": True}
    fn["why"] = True
    cfg = {
        "project": {"name": "p", "version": "0.1.0"},
        "module": {"m": {"objects": [], "functions": [fn]}},
    }
    back = tomllib.loads(C._dump(cfg))["module"]["m"]["functions"][0]
    assert back["check_return"] is True
    assert back["why"] is True


def test_the_c_app_passes_null_for_why():
    from just_makeit import _app

    cfg = {"project": {"name": "p", "version": "0.1.0"}}
    fn = {
        "name": "rate",
        "return_type": "int",
        "check_return": True,
        "why": True,
        "params": [{"name": "n", "type": "int"}],
    }
    ctx = _app._build_fn_ctx(cfg, "m", "rate", "rate_app", fn)
    assert "rate(n, NULL);" in ctx["call_and_print"]


# -- a composer's JSON factory ----------------------------------------------------


def _json_cfg(**json):
    cfg = _composer_cfg()
    cfg["module"]["wfm_compose"]["json"].update(json)
    return cfg


def test_from_json_why_passes_and_raises_the_reason():
    s = _composer.render_composer_type(
        _json_cfg(from_json_fn="wfm_read", from_json_why=True), "wfm_compose"
    )
    fn = s[s.index("_from_json(PyObject *cls") :]
    fn = fn[: fn.index("\n}\n")]
    assert "wfm_read(json, &_why);" in fn
    assert "PyErr_SetString(PyExc_ValueError, _why);" in fn
    assert 'PyErr_SetString(PyExc_ValueError, "wfm_read failed");' in fn


def test_from_file_why_passes_and_raises_the_reason():
    s = _composer.render_composer_type(
        _json_cfg(from_file_fn="wfm_load", from_file_why=True), "wfm_compose"
    )
    fn = s[s.index("_from_file(PyObject *cls") :]
    fn = fn[: fn.index("\n}\n")]
    assert "wfm_load(PyBytes_AS_STRING(pathobj), &_why);" in fn
    assert "PyErr_SetString(PyExc_ValueError, _why);" in fn
    assert 'PyErr_SetString(PyExc_OSError, "wfm_load failed");' in fn
    # The from_json factory beside it did not opt in.
    assert "&_why" not in s[s.index("_from_json(PyObject *cls") :][:600]


def test_the_c_cli_passes_and_prints_the_reason():
    cfg = _json_cfg(from_file_why=True)
    cfg["module"]["wfm_compose"]["cli"] = {"enabled": True, "name": "g"}
    c = _composer.render_cli(cfg, "wfm_compose")
    assert "wfm_compose_from_file(from_file, &why);" in c
    assert '"%s\\n", why ? why : "failed to build composer"' in c


def test_the_generated_json_path_refuses_the_keys():
    cfg = _composer_cfg()
    cfg["module"]["wfm_compose"]["json"] = {
        "enabled": True,
        "from_json_why": True,
    }
    with pytest.raises(ValueError, match="no to_json_fn"):
        _composer.render_composer_type(cfg, "wfm_compose")


def test_the_json_table_writer_keeps_the_keys():
    from just_makeit import _config as C

    js = {
        "enabled": True,
        "to_json_fn": "w",
        "from_json_why": True,
        "from_file_why": True,
    }
    lines = C._dump(
        {"module": {"x": {"kind": "composer", "json": js}}}
    ).splitlines()
    assert "from_json_why = true" in lines
    assert "from_file_why = true" in lines


# -- end to end: build, import, read the message ------------------------------------


def _skip_reason() -> str | None:
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()


def _build(dest: Path) -> None:
    build = dest / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(dest),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build), "--parallel", "4"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]} failed:\n{r.stdout}\n{r.stderr}"


def _run_py(dest: Path, code: str) -> str:
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=dest,
        env={**os.environ, "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    return out.stdout


_FUNCTIONS = """
[[module.m.functions]]
name = "rate"
return_type = "int"
check_return = true
why = true

[[module.m.functions.params]]
name = "spec"
type = "const char *"

[[module.m.functions]]
name = "bits"
return_type = "size_t"
out_type = "uint8_t"
variable_output = true
out_size = "n > 0 ? (size_t)n : 1"
check_return = true
why = true

[[module.m.functions.params]]
name = "n"
type = "int"
"""

_RATE_BODY = """
    if (spec[0] == '\\0') {
        if (why) *why = "RATE must not be empty";
        return -1;
    }
    if (spec[0] == '?')
        return -2; /* a refusal that names no reason */
    return 0;"""

_BITS_BODY = """
    if (n < 0) {
        if (why) *why = "LEN, the output length in bits, must be > 0";
        return 0;
    }
    if (n == 0)
        return 0; /* no reason */
    for (int i = 0; i < n; i++) out[i] = (uint8_t)(i & 1);
    return (size_t)n;"""

_FN_CHECK = r"""
from p import m

assert m.rate("12") is None
try:
    m.rate("")
except ValueError as e:
    assert str(e) == "RATE must not be empty", repr(e)
else:
    raise AssertionError("no raise")
try:
    m.rate("?")
except RuntimeError as e:
    assert str(e) == "p_rate failed (rc=-2)", repr(e)
else:
    raise AssertionError("no raise")

assert m.bits(4).tolist() == [0, 1, 0, 1]
try:
    m.bits(-1)
except ValueError as e:
    assert str(e) == "LEN, the output length in bits, must be > 0", repr(e)
else:
    raise AssertionError("no raise")
try:
    m.bits(0)
except RuntimeError as e:
    assert str(e) == "p_bits failed (returned 0)", repr(e)
else:
    raise AssertionError("no raise")
print("OK")
"""


def _implement(path: Path, body: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index("{\n", text.index("<<IMPLEMENT")) + 2
    end = text.rindex("}")
    path.write_text(text[:start] + body.lstrip("\n") + "\n" + text[end:])


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
def test_a_function_raises_its_reason_end_to_end(tmp_path):
    """Declared in the manifest and materialized by ``apply``, so the replay
    forwarding ``why`` is on the path: dropped there, the prototype has no
    ``why`` and the implemented body below does not compile."""
    from just_makeit._apply import run as apply_run
    from just_makeit._module import run as mod_run
    from just_makeit._new import run as new_run

    dest = tmp_path / "p"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("p", dest)
        mod_run(dest, "m")
        manifest = dest / "just-makeit.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + _FUNCTIONS,
            encoding="utf-8",
        )
        apply_run(dest)
    _implement(dest / "native/src/m/rate.c", _RATE_BODY)
    _implement(dest / "native/src/m/bits.c", _BITS_BODY)
    _build(dest)
    assert _run_py(dest, _FN_CHECK).strip() == "OK"


_STEPS = (
    Path(__file__).parent.parent
    / "src/just_makeit/examples/composer_seams/.steps"
)

_JSON_TABLE = """
[module.playlist.json]
enabled = true
to_json_fn = "playlist_to_json"
from_json_fn = "playlist_from_json_why"
from_json_why = true
from_file_fn = "playlist_from_file_why"
from_file_why = true
"""

_JSON_DECLS = """
char *playlist_to_json (const track_t *segs, size_t n, int repeat,
                        int continuous);
playlist_state_t *playlist_from_json_why (const char *json, const char **why);
playlist_state_t *playlist_from_file_why (const char *path, const char **why);

#endif /* PLAYLIST_CORE_H */
"""

_JSON_C = r"""
#include "studio/playlist/playlist_core.h"
#include <stdlib.h>
#include <string.h>

char *
playlist_to_json (const track_t *segs, size_t n, int repeat, int continuous)
{
  char *s = malloc (3);
  (void)segs; (void)n; (void)repeat; (void)continuous;
  if (s)
    strcpy (s, "{}");
  return s;
}

static playlist_state_t *
one_track (void)
{
  clip_t  c = { 1.0 };
  track_t t = { &c, 1, 4, 1.0 };
  return playlist_create (&t, 1, 0, 0);
}

playlist_state_t *
playlist_from_json_why (const char *json, const char **why)
{
  if (strstr (json, "\"old_key\""))
    {
      if (why)
        *why = "\"old_key\" is retired: write \"new_key\"";
      return NULL;
    }
  if (strcmp (json, "{}") != 0)
    return NULL; /* refused, and no reason given */
  return one_track ();
}

playlist_state_t *
playlist_from_file_why (const char *path, const char **why)
{
  size_t n = strlen (path);
  if (n > 4 && strcmp (path + n - 4, ".old") == 0)
    {
      if (why)
        *why = "this scene uses a retired layout";
      return NULL;
    }
  return NULL; /* cannot open: no reason */
}
"""

_COMPOSER_CHECK = r"""
import sys
from studio.playlist.playlist import Mix

assert Mix.from_json("{}") is not None
try:
    Mix.from_json('{"old_key": 1}')
except ValueError as e:
    assert str(e) == '"old_key" is retired: write "new_key"', repr(e)
else:
    raise AssertionError("no raise")
try:
    Mix.from_json("[]")
except ValueError as e:
    assert str(e) == "playlist_from_json_why failed", repr(e)
else:
    raise AssertionError("no raise")
try:
    Mix.from_file("scene.old")
except ValueError as e:
    assert str(e) == "this scene uses a retired layout", repr(e)
else:
    raise AssertionError("no raise")
try:
    Mix.from_file("scene.json")
except OSError as e:
    assert str(e) == "playlist_from_file_why failed", repr(e)
else:
    raise AssertionError("no raise")
print("OK")
"""


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
def test_a_composer_json_factory_raises_its_reason_end_to_end(tmp_path):
    """The composer_seams example's project, with a delegated JSON face whose
    reader names its refusal -- doppler-dsp/doppler#1614's shape."""
    from just_makeit import _incpath as INC
    from just_makeit._apply import run as apply_run
    from just_makeit._new import run as new_run
    from just_makeit._object import run as object_run

    with contextlib.redirect_stdout(io.StringIO()):
        new_run("studio", tmp_path / "studio")
        proj = tmp_path / "studio"
        object_run(
            proj,
            "clip",
            None,
            state_vars=[("level", "double", "0.0")],
            arg_type="void",
            return_type="float _Complex",
        )
    inc = INC.header_root(proj) / "playlist"
    inc.mkdir(parents=True, exist_ok=True)
    header = (_STEPS / "02_playlist_core.h").read_text(encoding="utf-8")
    tail = "#endif /* PLAYLIST_CORE_H */\n"
    assert header.endswith(tail), "the example's header changed shape"
    (inc / "playlist_core.h").write_text(
        header[: -len(tail)] + _JSON_DECLS, encoding="utf-8"
    )
    backing = proj / "native/src/backing"
    backing.mkdir(parents=True, exist_ok=True)
    shutil.copy(_STEPS / "02_playlist_core.c", backing / "playlist_core.c")
    shutil.copy(_STEPS / "05_playlist_bridge.c", backing / "playlist_bridge.c")
    (backing / "playlist_json.c").write_text(_JSON_C, encoding="utf-8")
    cmake = (_STEPS / "02_CMakeLists.txt").read_text(encoding="utf-8")
    assert "playlist_bridge.c)" in cmake
    (backing / "CMakeLists.txt").write_text(
        cmake.replace(
            "playlist_bridge.c)", "playlist_bridge.c\n  playlist_json.c)"
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(_STEPS / "03_manifest.py")],
        cwd=proj,
        check=True,
        capture_output=True,
    )
    manifest = proj / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + _JSON_TABLE, encoding="utf-8"
    )
    with contextlib.redirect_stdout(io.StringIO()):
        apply_run(proj)
    shutil.copy(
        _STEPS / "05b_playlist_ext_extra.c",
        proj / "native/src/playlist/playlist_ext_extra.c",
    )
    _build(proj)
    assert _run_py(proj, _COMPOSER_CHECK).strip() == "OK"
