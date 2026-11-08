"""gh-2538: a read-only property's getter can fail, and the read raises.

Before this, a computed property's binding was
``return <to_py>(<stem>_get_<prop>(self->handle));`` -- the C getter had no
failure path, so a member that does not apply to the object's
configuration had to read as ``NaN`` or ``0``. doppler's one ``Correlator``
class (doppler-dsp/doppler#2752) carries search-only properties that must
raise on a map flavor, never read ``NaN``.

The decided shape: ``getter_status = true`` on a read-only property. The C
getter becomes ``int <stem>_get_<prop>(const state *, T *out)``, and a
non-zero return raises the property's ``error`` (default ``ValueError``,
as a method's ``status_return``) with ``error_message``. ``AttributeError``
joins ``_config.ERROR_CATEGORIES``. Wherever there is no C getter call to
test, the key is refused before anything is written.

Built and run, not read from generated text: whether a read raises is a
fact about the extension, and the only honest way to read it is to read.
"""

from __future__ import annotations

import ast
import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _keys
from just_makeit._bind import parse_header
from just_makeit._context import make_properties_ctx

#: The no-value messages, distinct per object so a hit cannot come from the
#: neighbour.
COR_MSG = "eta is search-only"
ACC_MSG = "pd is search-only"


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


def _stub(fn: str, state_t: str, ctype: str) -> str:
    """The placeholder definition jm appends for a status getter."""
    return (
        f"{fn}(const {state_t} *state, {ctype} *out)\n{{\n"
        "    (void)state; (void)out;\n"
        "    return 0; /* placeholder */\n}"
    )


# ── built and run ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """A project with a status getter on a standalone and a module object,
    reached by both writers: the manifest (`apply`'s replay) on the
    standalone object, the CLI (`jm property --getter-status`) on the
    module one.

    ``mode`` is the configuration: 0 is a search flavor, anything else a
    map one, where the search-only members have no value. ``kind`` is an
    enum-decoded status getter with no ``error`` (so the default class);
    ``gain`` declares nothing and must keep the plain getter.
    """
    if not shutil.which("cmake"):
        pytest.skip("cmake not found")
    root = tmp_path_factory.mktemp("gh2538")
    _ok("new", "rs", "--object", "cor", "--state", "mode:int:0",
        "--arg-type", "float", "--return-type", "float", cwd=root)  # fmt: skip
    proj = root / "rs"
    _ok("module", "dsp", cwd=proj)
    _ok("object", "acc", "--module", "dsp", "--state", "mode:int:0",
        "--arg-type", "float", "--return-type", "float", cwd=proj)  # fmt: skip
    _ok("property", "acc", "pd", "--type", "double", "--getter-status",
        "--error", "AttributeError", "--error-message", ACC_MSG,
        cwd=proj)  # fmt: skip

    # The standalone object's properties arrive by manifest, so `apply`'s
    # replay is the writer under test.
    with open(proj / "just-makeit.toml", "a", encoding="utf-8") as f:
        f.write('\n[[enum]]\nname = "flavor"\nvalues = ["search", "map"]\n')
    with open(proj / "objects" / "cor.toml", "a", encoding="utf-8") as f:
        f.write(
            "\n[[cor.properties]]\n"
            'name = "eta"\ntype = "double"\ngetter_status = true\n'
            f'error = "AttributeError"\nerror_message = "{COR_MSG}"\n'
            "\n[[cor.properties]]\n"
            'name = "kind"\ntype = "int"\nenum = "flavor"\n'
            "getter_status = true\n"
            "\n[[cor.properties]]\n"
            'name = "gain"\ntype = "double"\n'
        )
    _ok("apply", cwd=proj)

    # The getters are jm's stubs; give them bodies.
    cor_c = proj / "native" / "src" / "cor" / "cor_core.c"
    _replace_once(
        cor_c,
        _stub("rs_cor_get_eta", "rs_cor_state_t", "double"),
        "rs_cor_get_eta(const rs_cor_state_t *state, double *out)\n{\n"
        "    if (state->mode != 0)\n        return 3;\n"
        "    *out = 2.5;\n    return 0;\n}",
    )
    _replace_once(
        cor_c,
        _stub("rs_cor_get_kind", "rs_cor_state_t", "int"),
        "rs_cor_get_kind(const rs_cor_state_t *state, int *out)\n{\n"
        "    if (state->mode < 0)\n        return -4;\n"
        "    *out = state->mode;\n    return 0;\n}",
    )
    acc_c = proj / "native" / "src" / "acc" / "acc_core.c"
    _replace_once(
        acc_c,
        _stub("rs_acc_get_pd", "rs_acc_state_t", "double"),
        "rs_acc_get_pd(const rs_acc_state_t *state, double *out)\n{\n"
        "    if (state->mode != 0)\n        return 9;\n"
        "    *out = 0.75;\n    return 0;\n}",
    )
    r = run_cli("status", "--check", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

    for cmd in (
        ["cmake", "-S", ".", "-B", "build",
         f"-DPython3_EXECUTABLE={sys.executable}"],
        ["cmake", "--build", "build"],
    ):  # fmt: skip
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    return proj


_PROBE = """
import json, sys
sys.path.insert(0, "src")
from rs import Cor
from rs.dsp import Acc


def read(obj, name):
    try:
        return ["ok", getattr(obj, name)]
    except Exception as e:
        return [type(e).__name__, str(e)]


out = {}
for mode in (0, 1, -1):
    c = Cor(mode)
    a = Acc(mode)
    out[str(mode)] = {
        "eta": read(c, "eta"),
        "kind": read(c, "kind"),
        "gain": read(c, "gain"),
        "pd": read(a, "pd"),
        "has_eta": hasattr(c, "eta"),
    }
out["docs"] = {
    "eta": Cor.eta.__doc__,
    "kind": Cor.kind.__doc__,
    "pd": Acc.pd.__doc__,
    "gain": Cor.gain.__doc__,
}
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def probe(built) -> dict:
    r = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=built,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_getter_that_writes_a_value_returns_it(probe) -> None:
    got = probe["0"]
    assert got["eta"] == ["ok", 2.5]
    assert got["pd"] == ["ok", 0.75]
    assert got["kind"] == ["ok", "search"]
    assert probe["1"]["kind"] == ["ok", "map"]
    assert got["has_eta"] is True


def test_a_failing_getter_raises_the_declared_exception(probe) -> None:
    """The reported case: a search-only member on a map flavor raises."""
    got = probe["1"]
    assert got["eta"] == ["AttributeError", f"{COR_MSG} (rc=3)"]
    assert got["pd"] == ["AttributeError", f"{ACC_MSG} (rc=9)"]
    # AttributeError is what `hasattr` reads as absence.
    assert got["has_eta"] is False


def test_an_undeclared_error_is_the_default_class(probe) -> None:
    """No `error`: the default a method's `status_return` raises, naming
    the C getter, and the enum decode is never reached."""
    assert probe["-1"]["kind"] == [
        "ValueError",
        "rs_cor_get_kind failed (rc=-4)",
    ]


def test_without_getter_status_the_getter_is_plain(probe) -> None:
    assert probe["1"]["gain"] == ["ok", 0.0]
    assert probe["docs"]["gain"] == "Gain.\n"


def _stub_doc(proj: Path, stub: str, cls: str, member: str) -> str:
    tree = ast.parse((proj / "src" / "rs" / stub).read_text("utf-8"))
    (klass,) = [
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls
    ]
    (getter,) = [
        n
        for n in klass.body
        if isinstance(n, ast.FunctionDef)
        and n.name == member
        and any(
            isinstance(d, ast.Name) and d.id == "property"
            for d in n.decorator_list
        )
    ]
    return ast.get_docstring(getter) or ""


@pytest.mark.parametrize(
    "key, stub, cls, exc, getter",
    [
        ("eta", "cor.pyi", "Cor", "AttributeError", "rs_cor_get_eta"),
        ("kind", "cor.pyi", "Cor", "ValueError", "rs_cor_get_kind"),
        ("pd", "dsp/dsp.pyi", "Acc", "AttributeError", "rs_acc_get_pd"),
    ],
)
def test_both_doc_faces_document_the_raise(
    built, probe, key, stub, cls, exc, getter
) -> None:
    """The `.pyi` and `help()` say what a read raises, in one text: the
    runtime face is the stub's docstring without its indent."""
    runtime = inspect.cleandoc(probe["docs"][key])
    assert f"Raises\n------\n{exc}\n    On read, if ``{getter}``" in runtime
    assert _stub_doc(built, stub, cls, key) == runtime


def test_the_header_declares_the_status_getter(built) -> None:
    h = (built / "native" / "inc" / "rs" / "cor" / "cor_core.h").read_text()
    assert "int rs_cor_get_eta(const rs_cor_state_t *state, double *out);" in h
    assert "int rs_cor_get_kind(const rs_cor_state_t *state, int *out);" in h
    assert "double rs_cor_get_gain(const rs_cor_state_t *state);" in h


# ── refused: no C getter call to test ─────────────────────────────────────────


@pytest.fixture()
def fresh(tmp_path) -> Path:
    _ok("new", "rf", "--object", "o", "--state", "level:double:1.0",
        cwd=tmp_path)  # fmt: skip
    return tmp_path / "rf"


@pytest.mark.parametrize(
    "prop, flags, needle",
    [
        ("p", ["--error", "ValueError"], "or getter_status = true"),
        (
            "p",
            ["--getter-status", "--writable"],
            "a writable property's error is its setter's",
        ),
        ("p", ["--getter-status", "--field"], "field reads the struct member"),
        (
            "p",
            ["--getter-status", "--expr", "self->handle->level"],
            "expr evaluates its C expression inline",
        ),
        (
            "p",
            ["--getter-status", "--type", "list"],
            "a container property reads through",
        ),
        (
            "p",
            ["--getter-status", "--type", "json"],
            "a json property's getter already reports failure",
        ),
        (
            "level",
            ["--getter-status"],
            "accessor of the state field 'level'",
        ),
        (
            "p",
            ["--getter-status", "--error", "AttributeErorr"],
            "'AttributeErorr' is not a known exception category",
        ),
    ],
    ids=[
        "error-without-getter-status",
        "writable",
        "field",
        "expr",
        "container",
        "json",
        "state-field",
        "unknown-exception",
    ],
)
def test_the_cli_refuses_before_writing(fresh, prop, flags, needle) -> None:
    """Refused before the manifest is touched: no `update` line is printed
    for a write the refusal then has to take back."""
    before = (fresh / "objects" / "o.toml").read_text("utf-8")
    # A later --type overrides this one, so a flag set may name its own.
    r = run_cli("property", "o", prop, "--type", "double", *flags, cwd=fresh)
    assert r.returncode == 1, r.stdout + r.stderr
    assert needle in r.stderr, r.stderr
    assert "update" not in r.stdout, r.stdout
    assert (fresh / "objects" / "o.toml").read_text("utf-8") == before


def test_the_manifest_is_refused_too(fresh) -> None:
    """A hand-written row reaches the same check through `apply`, and the
    tree is left as it was."""
    frag = fresh / "objects" / "o.toml"
    with open(frag, "a", encoding="utf-8") as f:
        f.write(
            '\n[[o.properties]]\nname = "p"\ntype = "double"\n'
            "field = true\ngetter_status = true\n"
        )
    ext = fresh / "native" / "src" / "o" / "o_ext.c"
    before = ext.read_text("utf-8")
    r = run_cli("apply", cwd=fresh)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "field reads the struct member" in r.stderr, r.stderr
    assert ext.read_text("utf-8") == before


# ── every writer carries the key ─────────────────────────────────────────────


def test_the_cli_declares_and_stubs_the_status_getter(fresh) -> None:
    _ok("property", "o", "p", "--type", "double", "--getter-status",
        cwd=fresh)  # fmt: skip
    h = (fresh / "native" / "inc" / "rf" / "o" / "o_core.h").read_text()
    c = (fresh / "native" / "src" / "o" / "o_core.c").read_text()
    assert "int rf_o_get_p(const rf_o_state_t *state, double *out);" in h
    assert "\nint\nrf_o_get_p(const rf_o_state_t *state, double *out)\n" in c


def test_apply_declares_the_status_getter_on_a_module_object(tmp_path) -> None:
    """A module object's sacred header is only ever added to (gh-627), by
    `apply`'s additive injection -- the writer a manifest row reaches."""
    _ok("new", "rm", cwd=tmp_path)
    proj = tmp_path / "rm"
    _ok("module", "dsp", cwd=proj)
    _ok("object", "acc", "--module", "dsp", "--state", "mode:int:0",
        cwd=proj)  # fmt: skip
    with open(proj / "objects" / "acc.toml", "a", encoding="utf-8") as f:
        f.write(
            '\n[[acc.properties]]\nname = "pd"\ntype = "double"\n'
            "getter_status = true\n"
        )
    _ok("apply", cwd=proj)
    h = (proj / "native" / "inc" / "rm" / "acc" / "acc_core.h").read_text()
    assert "int rm_acc_get_pd(const rm_acc_state_t *state, double *out);" in h


def test_script_replays_the_key(fresh) -> None:
    _ok("property", "o", "p", "--type", "double", "--getter-status",
        "--error", "AttributeError", cwd=fresh)  # fmt: skip
    out = run_cli("script", cwd=fresh).stdout
    assert "--getter-status" in out, out
    assert "--error AttributeError" in out, out


def test_the_key_is_recognised_on_a_property() -> None:
    cfg = {
        "project": {"name": "rf"},
        "o": {
            "properties": [
                {
                    "name": "p",
                    "type": "double",
                    "getter_status": True,
                    "error": "AttributeError",
                }
            ]
        },
    }
    assert _keys.unknown_keys(cfg) == []


def test_bind_reads_a_status_getter_from_the_header(tmp_path) -> None:
    """`jm bind` reflects a hand-written header: an `int` getter writing
    through `T *out` is a status getter of a T property, not a getter of
    an int one."""
    h = tmp_path / "comp_core.h"
    h.write_text(
        "typedef struct { float gain; } comp_state_t;\n"
        "int comp_get_eta(const comp_state_t *state, double *out);\n"
        "int comp_get_count(const comp_state_t *state);\n"
        "static inline float comp_step(const comp_state_t *state, float x) "
        "{ return x; }\n"
    )
    props = {p["name"]: p for p in parse_header(h)["properties"]}
    assert props["eta"] == {
        "name": "eta",
        "type": "double",
        "writable": False,
        "getter_status": True,
    }
    assert props["count"] == {
        "name": "count",
        "type": "int",
        "writable": False,
    }


# ── without the key, the render is unchanged ─────────────────────────────────


def test_a_plain_getter_renders_as_before() -> None:
    """Pins the no-`getter_status` render of the one builder every face
    uses: the call whose value is returned, the value-returning prototype,
    a one-line doc."""
    ctx = make_properties_ctx(
        "o", "O", [{"name": "p", "type": "double"}], csym="rf_o"
    )
    assert (
        "    return PyFloat_FromDouble(rf_o_get_p(self->handle));\n}"
        in ctx["getset_def"]
    )
    assert "_rc" not in ctx["getset_def"]
    assert '(getter)O_getprop_p, NULL, "P.\\n", NULL }' in ctx["getset_def"]
    assert (
        "double rf_o_get_p(const rf_o_state_t *state);"
        in ctx["property_decls"]
    )


def test_a_status_getter_is_declared_int_by_the_render() -> None:
    """The template-rendered header (`property_decls`) agrees with the
    binding -- the slot a full re-render of `_core.h` writes."""
    ctx = make_properties_ctx(
        "o",
        "O",
        [{"name": "p", "type": "double", "getter_status": True}],
        csym="rf_o",
    )
    assert (
        "int rf_o_get_p(const rf_o_state_t *state, double *out);"
        in ctx["property_decls"]
    )
    assert "int _rc = rf_o_get_p(self->handle, &_got);" in ctx["getset_def"]
    assert '"rf_o_get_p failed",' in ctx["getset_def"]
