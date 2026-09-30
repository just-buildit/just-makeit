"""gh-1756: an array param may say where text goes when it refuses a ``str``.

gh-1700 routed every array-argument conversion through ``jm_array_arg``,
which refuses a ``str`` correctly and says only::

    TypeError: sync must be an array of numbers, not str

An owner whose rule is "an object takes bits and a module helper makes them"
wants that refusal to name the helper. ``str_hint`` is one optional literal
per param, appended to the refusal of a ``str``::

    [[frame.init_params]]
    name = "sync"
    type = "uint8_t[]"
    str_hint = "build bits from text with field_bits()"

    TypeError: sync must be an array of numbers, not str: build bits from
    text with field_bits()

ONE mechanism: ``_coerce.array_arg`` takes the hint, read through the one
accessor ``_coerce.str_hint``, and emits the helper's hinted entry point
``jm_array_arg_hint``; ``jm_array_arg`` is that with ``NULL``. So a param
declaring no hint renders its call site byte-for-byte as before.

The central test BUILDS a project with the key on an init param, a method
param and a module-function param -- the last with quotes and a backslash,
which must reach Python unchanged through a C string literal -- and calls
each with a ``str``. The rest pin the load-time refusals and the save.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _jmrun import run_cli  # noqa: E402
from just_makeit import _coerce, _docsync  # noqa: E402
from just_makeit import _config as C  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)

INIT_HINT = "build bits from text with field_bits()"
METHOD_HINT = "pass peek() an array; parse text with bits()"
VO_HINT = "grab() takes the bytes themselves"
#: Every character `_C_ESCAPES` rewrites that a TOML basic string can hold
#: on one line, plus a `%` -- the hint is a `%s` ARGUMENT, not the format,
#: so it must reach Python verbatim rather than be read as a directive.
FUNC_HINT = 'use bits("0101") \\ not text, 100% of the time'


def _jm(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}\n{r.stdout}\n{r.stderr}"
    return r


def _sub(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


def _toml_str(s: str) -> str:
    """*s* as a TOML basic string -- the same escapes JSON uses."""
    return json.dumps(s)


def _scaffold(root: Path) -> Path:
    """A project with an array param on each face, no hints declared."""
    _jm("new", "jmp", cwd=root)
    p = root / "jmp"
    for args in (
        (
            "object", "fld", "--no-state", "--no-step",
            "--arg-type", "void", "--return-type", "void",
            "--init-param", "sync:uint8_t[]",
            "--init-param", "taps:float[]:[]",
        ),
        ("method", "fld", "peek", "--param", "b:uint8_t[]",
         "--return-type", "int64_t"),
        # A variable-output method's params are parsed by their own path.
        ("method", "fld", "grab", "--param", "g:uint8_t[]",
         "--variable-output", "--out-type", "uint8_t",
         "--return-type", "size_t"),
        ("module", "m"),
        ("function", "peekb", "--module", "m", "--param", "b:int8_t[]",
         "--return-type", "int64_t"),
    ):  # fmt: skip
        _jm(*args, cwd=p)
    return p


def _declare_hints(p: Path) -> None:
    """Add a ``str_hint`` to each face's array param, by hand, in TOML."""
    fld = p / "objects" / "fld.toml"
    _sub(
        fld,
        'name = "sync"\ntype = "uint8_t[]"\n',
        f'name = "sync"\ntype = "uint8_t[]"\nstr_hint = {_toml_str(INIT_HINT)}\n',
    )
    _sub(
        fld,
        'name = "b"\ntype = "uint8_t[]"\n',
        f'name = "b"\ntype = "uint8_t[]"\nstr_hint = {_toml_str(METHOD_HINT)}\n',
    )
    _sub(
        fld,
        'name = "g"\ntype = "uint8_t[]"\n',
        f'name = "g"\ntype = "uint8_t[]"\nstr_hint = {_toml_str(VO_HINT)}\n',
    )
    _sub(
        p / "modules" / "m.toml",
        'name = "b"\ntype = "int8_t[]"',
        f'name = "b"\ntype = "int8_t[]"\nstr_hint = {_toml_str(FUNC_HINT)}',
    )


def _exts(p: Path) -> dict:
    return {
        f.relative_to(p).as_posix(): f.read_text("utf-8")
        for f in sorted((p / "native" / "src").rglob("*_ext.c"))
    }


@pytest.fixture(scope="module")
def plain(tmp_path_factory) -> Path:
    return _scaffold(tmp_path_factory.mktemp("gh1756_plain"))


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    p = _scaffold(tmp_path_factory.mktemp("gh1756"))
    _declare_hints(p)
    _jm("apply", cwd=p)
    return p


# -- the render: one entry point, and nothing moves without the key ----------


def test_no_hint_keeps_the_four_argument_call():
    assert _coerce.array_arg("s_obj", "NPY_UINT8", "F", "s") == (
        'jm_array_arg(s_obj, NPY_UINT8, F, "s")'
    )
    assert _coerce.array_arg("s_obj", "NPY_UINT8", "F", "s", "") == (
        _coerce.array_arg("s_obj", "NPY_UINT8", "F", "s")
    )


def test_a_hint_is_escaped_into_one_c_literal():
    call = _coerce.array_arg("b_obj", "NPY_INT8", "F", "b", FUNC_HINT)
    assert call == (
        'jm_array_arg_hint(b_obj, NPY_INT8, F, "b", '
        '"use bits(\\"0101\\") \\\\ not text, 100% of the time")'
    )


def test_every_hinted_call_site_and_no_other_changes(plain, project):
    """Declaring the key changes exactly the four hinted calls."""
    before, after = _exts(plain), _exts(project)
    assert before.keys() == after.keys()
    changed = []
    for name in before:
        old, new = before[name].splitlines(), after[name].splitlines()
        assert len(old) == len(new), name
        changed += [(o, n) for o, n in zip(old, new) if o != n]
    assert len(changed) == 4, changed
    for old, new in changed:
        assert "jm_array_arg(" in old and "jm_array_arg_hint(" in new
    # ...and no hint in the unhinted tree at all: `taps` shares an object
    # with `sync` and keeps the four-argument call.
    # (The helper's own definition names the entry point; a CALL passes an
    # `<param>_obj`.)
    call = re.compile(r"\bjm_array_arg_hint\(\w+_obj,")
    assert not any(call.search(t) for t in before.values())
    assert sum(len(call.findall(t)) for t in after.values()) == 4
    assert (
        'jm_array_arg(taps_obj, NPY_FLOAT, NPY_ARRAY_C_CONTIGUOUS, "taps")'
        in (after["native/src/fld/fld_ext.c"])
    )


def test_the_gh1734_marker_still_reads_a_hinted_call(project):
    """A fragment calling only the hinted entry point still has the helper."""
    call = _coerce.array_arg("s_obj", "NPY_UINT8", "F", "s", INIT_HINT)
    assert _docsync._ARRAY_ARG_RE.search(call)
    assert _docsync._ARRAY_ARG_HINT_RE.search(call)
    assert not _docsync._ARRAY_ARG_HINT_RE.search(
        _coerce.array_arg("s_obj", "NPY_UINT8", "F", "s")
    )


# -- the build: every face, called ------------------------------------------

_CASES = r"""
import json, sys
from jmp import Fld
from jmp.m import peekb
cases = {
    "init": lambda: Fld("0101"),
    "init unhinted": lambda: Fld([1, 0], taps="1.5"),
    "init bytes ok": lambda: Fld(b"\x01\x00"),
    "method": lambda: Fld([1]).peek("01"),
    "vo method": lambda: Fld([1]).grab("01"),
    "function": lambda: peekb("01"),
    "function bytes ok": lambda: peekb(b"\x01"),
}
out = {}
for name, call in cases.items():
    try:
        call()
        out[name] = {"ok": True}
    except Exception as e:
        out[name] = {"err": type(e).__name__, "msg": str(e)}
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def results(project) -> dict:
    for c in (
        project / "native" / "src" / "fld" / "fld_core.c",
        project / "native" / "src" / "m" / "peekb.c",
    ):
        assert c.exists(), c
    build = subprocess.run(
        ["make"], cwd=project, capture_output=True, text=True
    )
    assert build.returncode == 0, build.stdout[-3000:] + build.stderr[-3000:]
    ext = sysconfig.get_config_var("EXT_SUFFIX")
    so = list(project.rglob(f"fld{ext}"))
    assert so, "extension module was not built"
    run = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(so[0].parent.parent)!r})\n"
         + _CASES],
        cwd=project,
        capture_output=True,
        text=True,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="needs cmake and a C compiler")
class TestEveryFace:
    """The asked message, per face, from the running extension."""

    @pytest.mark.parametrize(
        "case, param, hint",
        [
            ("init", "sync", INIT_HINT),
            ("method", "b", METHOD_HINT),
            ("vo method", "g", VO_HINT),
            ("function", "b", FUNC_HINT),
        ],
    )
    def test_the_hint_follows_the_refusal(self, results, case, param, hint):
        assert results[case] == {
            "err": "TypeError",
            "msg": f"{param} must be an array of numbers, not str: {hint}",
        }

    def test_a_param_without_the_key_is_unchanged(self, results):
        assert results["init unhinted"] == {
            "err": "TypeError",
            "msg": "taps must be an array of numbers, not str",
        }

    @pytest.mark.parametrize("case", ["init bytes ok", "function bytes ok"])
    def test_a_byte_buffer_still_converts(self, results, case):
        assert results[case] == {"ok": True}


# -- the manifest: refused at load, kept on save -----------------------------


#: A declared record element (gh-1405): an `iq16_t[]` param is acquired by
#: the record's dtype, which demands an ndarray of it before any conversion.
_RECORD = (
    '[[fld.records]]\nname = "iq16_t"\n'
    'fields = [{ name = "i", type = "int16_t" },'
    ' { name = "q", type = "int16_t" }]\n\n'
)


def _load_with(tmp_path: Path, row: dict, table: str = "init_params"):
    """`load` a one-object manifest whose param *row* sits in *table*.

    Written as TOML text rather than through `C.save`, which would quote a
    non-string value on the way out and hide the case under test.
    """
    body = "\n".join(f"{k} = {json.dumps(v)}" for k, v in row.items())
    if table == "init_params":
        tail = f"[[fld.init_params]]\n{body}\n"
    else:
        strict = "strict = true\n" if table == "strict" else ""
        tail = (
            f'[[fld.methods]]\nname = "peek"\nreturn_type = "int"\n{strict}'
            f"\n[[fld.methods.params]]\n{body}\n"
        )
    (tmp_path / C.FILENAME).write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n\n'
        '[fld]\narg_type = "void"\nreturn_type = "void"\n\n' + _RECORD + tail,
        encoding="utf-8",
    )
    return C.load(tmp_path)


@pytest.mark.parametrize("value", [1, True, "", ["a"]])
def test_a_non_string_hint_is_refused_at_load(tmp_path, capsys, value):
    with pytest.raises(SystemExit):
        _load_with(
            tmp_path, {"name": "s", "type": "uint8_t[]", "str_hint": value}
        )
    err = capsys.readouterr().err
    assert "[[fld.init_params]] s: str_hint" in err
    assert "must be a non-empty string" in err


@pytest.mark.parametrize("ptype", ["int", "double", "path"])
def test_a_hint_on_a_scalar_is_refused_at_load(tmp_path, capsys, ptype):
    with pytest.raises(SystemExit):
        _load_with(tmp_path, {"name": "n", "type": ptype, "str_hint": "x"})
    assert "str_hint is read only by an array" in capsys.readouterr().err


@pytest.mark.parametrize(
    "row, table",
    [
        ({"name": "y", "type": "float[]", "out": True}, "params"),
        ({"name": "y", "type": "float[]", "mutable": True}, "params"),
        ({"name": "x", "type": "float[]"}, "strict"),
        ({"name": "rows", "type": "iq16_t[]"}, "params"),
    ],
)
def test_a_hint_that_could_never_show_is_refused(tmp_path, capsys, row, table):
    """Each refuses a non-ndarray before ``jm_array_arg`` runs."""
    with pytest.raises(SystemExit):
        _load_with(tmp_path, row | {"str_hint": "x"}, table)
    assert "str_hint is never shown on" in capsys.readouterr().err


def test_a_record_param_without_a_hint_still_loads(tmp_path):
    """The record refusal is about the KEY, not the record param."""
    cfg = _load_with(tmp_path, {"name": "rows", "type": "iq16_t[]"}, "params")
    assert cfg["fld"]["methods"][0]["params"][0]["type"] == "iq16_t[]"


def test_a_function_and_a_handle_arg_are_checked_too(tmp_path, capsys):
    cfg = {
        "module": {
            "m": {
                "functions": [
                    {
                        "name": "f",
                        "params": [{"name": "n", "type": "int",
                                    "str_hint": "x"}],
                    }
                ]
            },
            "h": {
                "kind": "handle",
                "methods": [
                    {"name": "g", "args": [{"name": "k", "type": "int",
                                            "str_hint": "x"}]}
                ],
            },
        }
    }  # fmt: skip
    errs = _coerce.str_hint_errors(cfg)
    assert [e.split(":")[0] for e in errs] == [
        "[[module.m.functions.params]] n",
        "[[module.h.methods.args]] k",
    ]


def test_the_key_survives_a_save(tmp_path):
    """A mutating command rewrites the fragments; every hint comes back.

    Its own tree, so the mutation cannot reach the shared ``project``.
    """
    project = _scaffold(tmp_path)
    _declare_hints(project)
    _jm("method", "fld", "again", "--param", "c:float[]",
        "--return-type", "int64_t", cwd=project)  # fmt: skip
    cfg = C.load(project)
    assert C.init_params(cfg, "fld")[0][16] == INIT_HINT
    assert _coerce.str_hint(cfg["fld"]["init_params"][0]) == INIT_HINT
    peek = next(m for m in cfg["fld"]["methods"] if m["name"] == "peek")
    assert peek["params"][0]["str_hint"] == METHOD_HINT
    fn = cfg["module"]["m"]["functions"][0]
    assert fn["params"][0]["str_hint"] == FUNC_HINT


def test_the_init_param_tuple_round_trips():
    """The CLI / view path persists slot 16 as the key."""
    row = {"name": "s", "type": "uint8_t[]", "str_hint": INIT_HINT}
    tup = C._project_init_params({}, [row])[0]
    assert _coerce.str_hint(tup) == INIT_HINT
    assert C.init_param_tuple_to_dict(tup) == row


# -- a sacred fragment: jm says when it predates a declared hint -------------

_FRAG = ("native", "src", "m", "m_ext_r.c")


def _module_object(tmp_path) -> Path:
    """A module object whose method ``scale`` takes ``w: float[]``."""
    _jm("new", "q", cwd=tmp_path)
    proj = tmp_path / "q"
    _jm("module", "m", cwd=proj)
    _jm("object", "r", "--module", "m", "--no-state", "--no-step", cwd=proj)
    _jm("method", "r", "scale", "--module", "m", "--param", "w:float[]",
        "--return-type", "float", cwd=proj)  # fmt: skip
    _jm("apply", cwd=proj)
    return proj


def test_a_fragment_predating_a_declared_hint_is_named(tmp_path):
    proj = _module_object(tmp_path)
    _sub(
        proj / "objects" / "r.toml",
        'name = "w"\ntype = "float[]"',
        'name = "w"\ntype = "float[]"\nstr_hint = "pass floats"',
    )
    out = _jm("apply", cwd=proj)
    text = out.stdout + out.stderr
    assert "m_ext_r.c" in text, text
    assert "scale: the manifest declares a str_hint" in text, text
    # ...and the fragment is not ALSO blamed for predating the converter:
    # its plain call is still gh-1700's helper.
    assert "through jm_array_arg and this" not in text, text


def test_a_fragment_calling_the_hinted_entry_point_is_current(tmp_path):
    proj = _module_object(tmp_path)
    _sub(
        proj / "objects" / "r.toml",
        'name = "w"\ntype = "float[]"',
        'name = "w"\ntype = "float[]"\nstr_hint = "pass floats"',
    )
    frag = proj.joinpath(*_FRAG)
    _sub(
        frag,
        'jm_array_arg(w_obj, NPY_FLOAT, NPY_ARRAY_C_CONTIGUOUS, "w")',
        _coerce.array_arg(
            "w_obj", "NPY_FLOAT", "NPY_ARRAY_C_CONTIGUOUS", "w", "pass floats"
        ),
    )
    out = _jm("apply", cwd=proj)
    assert "no longer matches" not in out.stdout + out.stderr


def _handle_ext(arg: dict) -> str:
    from just_makeit import _handle

    cfg = {
        "project": {"name": "p"},
        "module": {
            "hd": {
                "kind": "handle",
                "backing": "b",
                "header": "b/b.h",
                "type_name": "H",
                "close_fn": "b_close",
                "create_fn": "b_open",
                "create_args": [],
                "methods": [
                    {
                        "name": "send",
                        "fn": "b_send",
                        "returns": "size_t",
                        "args": [arg],
                    }
                ],
            }
        },
    }
    return _handle.render_ext(cfg, "hd")


def test_a_handle_method_arg_carries_its_hint():
    """The kind module with an array arg, which no build test reaches."""
    plain = _handle_ext({"name": "iq", "type": "uint8_t[]"})
    hinted = _handle_ext(
        {"name": "iq", "type": "uint8_t[]", "str_hint": "use iq()"}
    )
    flags = "NPY_ARRAY_C_CONTIGUOUS"
    want = _coerce.array_arg("x_obj", "NPY_UINT8", flags, "iq", "use iq()")
    assert want in hinted
    old = _coerce.array_arg("x_obj", "NPY_UINT8", flags, "iq")
    assert plain == hinted.replace(want, old)
