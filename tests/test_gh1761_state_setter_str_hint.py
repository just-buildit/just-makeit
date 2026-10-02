"""gh-1761: an array state field's setter may say where text goes.

gh-1756 let an array param carry a ``str_hint``, appended to gh-1700's
refusal of a ``str``. The one other array argument jm converts with a
declaration behind it is an array STATE field's ``set_<name>``: it goes
through the same ``jm_array_arg``, so it refused a ``str`` --
``sync must be an array of numbers, not str`` -- and could not say more.
(Since gh-1824 that refusal is the key's opt-in: a field without it leaves a
``str`` to numpy, and the setter's length check sees one element.)

The issue proposed the key on ``[[<obj>.properties]]``. Measured on main, no
property setter converts an array: ``jm property`` refuses a ``T[N]`` type,
and a property is a scalar, a container, a capsule or a read-only
``buf_field`` view. The setter that does is the state field's, so the key
lives on the row that declares it::

    [[frame.state]]
    name = "sync"
    type = "uint8_t[8]"
    str_hint = "build bits from text with field_bits()"

    TypeError: sync must be an array of numbers, not str: build bits from
    text with field_bits()

ONE mechanism, gh-1756's: `_config.state_str_hints` reads each row through
`_coerce.str_hint` and the setter passes it to `_coerce.array_arg`. A
``str_hint`` on a property is refused at load and names the state row.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _jmrun import run_cli  # noqa: E402
from just_makeit import _coerce  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _context as Ctx  # noqa: E402
from just_makeit._keys import STATE_KEYS  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)

SYNC_HINT = "build bits from text with field_bits()"
#: Quotes, a backslash and a `%`: the hint is a C literal passed as a `%s`
#: ARGUMENT, so each must reach Python unchanged.
W_HINT = 'use w("0.5") \\ not text, 100% of the time'

_FRAG = ("native", "src", "m", "m_ext_r.c")


def _jm(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}\n{r.stdout}\n{r.stderr}"
    return r


def _sub(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


def _scaffold(root: Path) -> Path:
    """A standalone object and a module object, each with array state."""
    _jm("new", "jmp", cwd=root)
    p = root / "jmp"
    for args in (
        ("object", "fld", "--no-step",
         "--state", "sync:uint8_t[8]", "--state", "taps:float[4]"),
        ("module", "m"),
        ("object", "r", "--module", "m", "--no-step",
         "--state", "w:float[4]"),
    ):  # fmt: skip
        _jm(*args, cwd=p)
    return p


def _declare_hints(p: Path) -> None:
    """Add a ``str_hint`` to ``sync`` and ``w``, by hand, in TOML."""
    _sub(
        p / "objects" / "fld.toml",
        'name = "sync"\ntype = "uint8_t[8]"\n',
        f'name = "sync"\ntype = "uint8_t[8]"\n'
        f"str_hint = {json.dumps(SYNC_HINT)}\n",
    )
    _sub(
        p / "objects" / "r.toml",
        'name = "w"\ntype = "float[4]"\n',
        f'name = "w"\ntype = "float[4]"\nstr_hint = {json.dumps(W_HINT)}\n',
    )


def _exts(p: Path) -> dict:
    return {
        f.relative_to(p).as_posix(): f.read_text("utf-8")
        for f in sorted((p / "native" / "src").rglob("*_ext*.c"))
    }


@pytest.fixture(scope="module")
def plain(tmp_path_factory) -> Path:
    p = _scaffold(tmp_path_factory.mktemp("gh1761_plain"))
    # Rendered the way `project`'s module fragment is below, so the two
    # trees differ only by the declaration.
    p.joinpath(*_FRAG).unlink()
    _jm("apply", cwd=p)
    return p


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    p = _scaffold(tmp_path_factory.mktemp("gh1761"))
    _declare_hints(p)
    # A module object's binding fragment is sacred once written, so a hint
    # declared afterwards reaches it only by a re-render (or a hand edit,
    # which `test_a_fragment_predating_the_hint_is_named` covers). Deleting
    # it makes `apply` render it from the manifest: the module-object path.
    p.joinpath(*_FRAG).unlink()
    _jm("apply", cwd=p)
    return p


# -- the render --------------------------------------------------------------


def _state_ctx(str_hints=None) -> str:
    kw = {} if str_hints is None else {"str_hints": str_hints}
    return Ctx.make_state_ctx(
        "fld",
        "Fld",
        [("sync", "uint8_t[8]", ""), ("taps", "float[4]", "")],
        csym="fld",
        **kw,
    )["getter_setter_methods_c"]


def test_no_hint_renders_exactly_as_before():
    """Absent, empty and a hint for another field render identically."""
    base = _state_ctx()
    assert _state_ctx({}) == base
    assert _state_ctx({"gain": "x"}) == base
    assert (
        'jm_array_arg(in_obj, NPY_UINT8, NPY_ARRAY_C_CONTIGUOUS, "sync")'
        in base
    )


def test_a_hint_changes_only_its_own_setter():
    base, hinted = _state_ctx(), _state_ctx({"sync": SYNC_HINT})
    flags = "NPY_ARRAY_C_CONTIGUOUS"
    old = _coerce.array_arg("in_obj", "NPY_UINT8", flags, "sync")
    new = _coerce.array_arg("in_obj", "NPY_UINT8", flags, "sync", SYNC_HINT)
    assert hinted == base.replace(old, new)
    assert hinted.count("jm_array_arg_hint(") == 1


def test_every_hinted_setter_and_no_other_changes(plain, project):
    """Declaring two hints changes exactly two lines, on both object kinds."""
    before, after = _exts(plain), _exts(project)
    assert before.keys() == after.keys()
    changed = {}
    for name in before:
        old, new = before[name].splitlines(), after[name].splitlines()
        assert len(old) == len(new), name
        diff = [(o, n) for o, n in zip(old, new) if o != n]
        if diff:
            changed[name] = diff
    assert sorted(changed) == [
        "native/src/fld/fld_ext.c",
        "native/src/m/m_ext_r.c",
    ], changed
    for diff in changed.values():
        assert len(diff) == 1, diff
        (old, new), = diff  # fmt: skip
        assert "jm_array_arg(in_obj," in old
        assert "jm_array_arg_hint(in_obj," in new
    # `taps` shares an object with `sync` and keeps the plain call.
    assert (
        'jm_array_arg(in_obj, NPY_FLOAT, NPY_ARRAY_C_CONTIGUOUS, "taps")'
        in after["native/src/fld/fld_ext.c"]
    )


def test_status_check_agrees(project):
    """The replay `status` compares against renders the hint too."""
    r = run_cli("status", "--check", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_view_shares_the_hint(tmp_path):
    """A view is a second class over the same state (gh-504): same setter."""
    p = _scaffold(tmp_path)
    _jm("view", "r", "Peek", "--module", "m", "--create-fn", "r_open",
        cwd=p)  # fmt: skip
    _declare_hints(p)
    frag = p / "native" / "src" / "m" / "m_ext_peek.c"
    frag.unlink()
    _jm("apply", cwd=p)
    text = frag.read_text("utf-8")
    assert "Peek_set_w(" in text
    assert (
        _coerce.array_arg(
            "in_obj", "NPY_FLOAT", "NPY_ARRAY_C_CONTIGUOUS", "w", W_HINT
        )
        in text
    )


@pytest.mark.parametrize("module", [None, "m"])
def test_the_replay_entry_point_renders_and_persists(tmp_path, module):
    """`_object.run` with `state_str_hints`, as `jm apply` replays it.

    `_apply._object_kwargs` hands the hint to `_object.run`, which renders a
    standalone object through `_init.run` and persists it, for both kinds,
    through `add_component` -- so the replay's own manifest and binding
    carry it, not only the files re-rendered later from the real manifest.
    """
    from just_makeit import _object

    _jm("new", "jmp", cwd=tmp_path)
    p = tmp_path / "jmp"
    if module:
        _jm("module", module, cwd=p)
    _object.run(
        p, "fld", module,
        state_vars=[("sync", "uint8_t[8]", "")],
        no_step=True,
        state_str_hints={"sync": SYNC_HINT},
    )  # fmt: skip
    assert C.state_str_hints(C.load(p), "fld") == {"sync": SYNC_HINT}
    ext = (
        p / "native" / "src" / module / f"{module}_ext_fld.c"
        if module
        else p / "native" / "src" / "fld" / "fld_ext.c"
    )
    want = _coerce.array_arg(
        "in_obj", "NPY_UINT8", "NPY_ARRAY_C_CONTIGUOUS", "sync", SYNC_HINT
    )
    assert want in ext.read_text("utf-8")


def test_regenerate_keeps_the_hint(tmp_path):
    """`jm regenerate` re-renders the glue from the manifest (`_glue`)."""
    p = _scaffold(tmp_path)
    _declare_hints(p)
    ext = p / "native" / "src" / "fld" / "fld_ext.c"
    ext.unlink()
    r = run_cli("regenerate", "fld", cwd=p, stdin="y\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "jm_array_arg_hint(in_obj, NPY_UINT8" in ext.read_text("utf-8")


# -- the build ---------------------------------------------------------------

_CASES = r"""
import json
from jmp import Fld
from jmp.m import R
cases = {
    "sync": lambda: Fld().set_sync("01010101"),
    "taps unhinted": lambda: Fld().set_taps("1.5"),
    "sync bytes ok": lambda: Fld().set_sync(b"\x01" * 8),
    "w": lambda: R().set_w("0.5"),
    "w floats ok": lambda: R().set_w([0.5] * 4),
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
class TestTheSetter:
    """The asked message, from the running extension."""

    @pytest.mark.parametrize(
        "case, field, hint",
        [("sync", "sync", SYNC_HINT), ("w", "w", W_HINT)],
    )
    def test_the_hint_follows_the_refusal(self, results, case, field, hint):
        assert results[case] == {
            "err": "TypeError",
            "msg": f"{field} must be an array of numbers, not str: {hint}",
        }

    def test_a_field_without_the_key_leaves_a_str_to_numpy(self, results):
        """gh-1824: no blanket refusal. numpy makes ``"1.5"`` ONE float,
        and the setter's own length check is what refuses it for a
        ``float[4]`` -- not a refusal of ``str``."""
        assert results["taps unhinted"] == {
            "err": "ValueError",
            "msg": "taps requires exactly 4 elements, got 1",
        }

    @pytest.mark.parametrize("case", ["sync bytes ok", "w floats ok"])
    def test_an_array_still_converts(self, results, case):
        assert results[case] == {"ok": True}


# -- the manifest: refused at load, kept on save -----------------------------


def _load_with(tmp_path: Path, tail: str):
    """`load` a one-object manifest ending in *tail* (TOML text)."""
    (tmp_path / C.FILENAME).write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n\n'
        '[fld]\narg_type = "void"\nreturn_type = "void"\n\n' + tail,
        encoding="utf-8",
    )
    return C.load(tmp_path)


def _state(**row) -> str:
    body = "\n".join(f"{k} = {json.dumps(v)}" for k, v in row.items())
    return f"[[fld.state]]\n{body}\n"


def test_a_state_hint_loads(tmp_path):
    cfg = _load_with(
        tmp_path, _state(name="s", type="uint8_t[8]", str_hint="x")
    )
    assert C.state_str_hints(cfg, "fld") == {"s": "x"}


@pytest.mark.parametrize("value", [1, True, "", ["a"]])
def test_a_non_string_hint_is_refused(tmp_path, capsys, value):
    with pytest.raises(SystemExit):
        _load_with(
            tmp_path, _state(name="s", type="uint8_t[8]", str_hint=value)
        )
    err = capsys.readouterr().err
    assert "[[fld.state]] s: str_hint" in err
    assert "must be a non-empty string" in err


@pytest.mark.parametrize("stype", ["int", "double"])
def test_a_hint_on_a_scalar_field_is_refused(tmp_path, capsys, stype):
    with pytest.raises(SystemExit):
        _load_with(tmp_path, _state(name="n", type=stype, str_hint="x"))
    err = capsys.readouterr().err
    assert "[[fld.state]] n: str_hint is read only by an array" in err


def test_a_hint_on_an_opaque_field_is_refused(tmp_path, capsys):
    """An opaque field has no ``set_<name>`` at all."""
    with pytest.raises(SystemExit):
        _load_with(
            tmp_path,
            _state(name="o", type="uint8_t[8]", opaque=True, str_hint="x"),
        )
    err = capsys.readouterr().err
    assert "str_hint is never shown on an opaque state field" in err


@pytest.mark.parametrize(
    "tail, where",
    [
        (
            '[[fld.properties]]\nname = "g"\ntype = "double"\n'
            'writable = true\nstr_hint = "x"\n',
            "[[fld.properties]] g",
        ),
        (
            '[[fld.views]]\nclass_name = "V"\ncreate_fn = "fld_create_v"\n'
            '[[fld.views.properties]]\nname = "g"\ntype = "double"\n'
            'str_hint = "x"\n',
            "[[fld.views.properties]] g",
        ),
    ],
)
def test_a_hint_on_a_property_names_the_state_row(
    tmp_path, capsys, tail, where
):
    with pytest.raises(SystemExit):
        _load_with(tmp_path, tail)
    err = capsys.readouterr().err
    assert f"{where}: str_hint is never shown on a property" in err
    assert "declare the hint on its [[fld.state]] row" in err


def test_the_key_survives_a_save(tmp_path):
    """A mutating command rewrites the fragment; the hint comes back."""
    p = _scaffold(tmp_path)
    _declare_hints(p)
    _jm("method", "fld", "again", "--param", "c:float[]",
        "--return-type", "int64_t", cwd=p)  # fmt: skip
    cfg = C.load(p)
    assert C.state_str_hints(cfg, "fld") == {"sync": SYNC_HINT}
    assert C.state_str_hints(cfg, "r") == {"w": W_HINT}


def test_every_state_key_survives_the_dumper():
    """`_dump` writes every `STATE_KEYS` key -- the set, not one member.

    `jm split-objects` and `jm migrate-to-fragments` rewrite a section
    through `_dump` rather than tomlkit, so a key it forgets is dropped
    there with no error. It had forgotten gh-1493's `doc` already.
    """
    rep = {
        "name": "k",
        "type": "uint8_t[8]",
        "default": "0",
        "doc": 'Gain "k".',
        "opaque": True,
        "no_ctor": True,
        "controllable": True,
        "str_hint": W_HINT,
    }
    assert set(rep) == set(STATE_KEYS), "give the new key a representative"
    cfg = {
        "project": {"name": "x", "version": "0.1.0"},
        "fld": {"arg_type": "void", "return_type": "void", "state": [rep]},
    }
    assert C.tomllib.loads(C._dump(cfg))["fld"]["state"] == [rep]


# -- a sacred fragment: jm says when it predates a declared hint -------------


def test_a_fragment_predating_the_hint_is_named(tmp_path):
    """`_docsync`'s gh-1756 marker reads the setter's PyMethodDef row."""
    p = _scaffold(tmp_path)
    _jm("apply", cwd=p)
    _sub(
        p / "objects" / "r.toml",
        'name = "w"\ntype = "float[4]"\n',
        'name = "w"\ntype = "float[4]"\nstr_hint = "pass floats"\n',
    )
    out = _jm("apply", cwd=p)
    text = out.stdout + out.stderr
    assert "m_ext_r.c" in text, text
    assert "set_w: the manifest declares a str_hint" in text, text
