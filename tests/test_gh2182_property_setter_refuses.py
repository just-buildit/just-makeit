"""gh-2182: a writable property's setter can refuse a value.

Before this, a writable property without ``field`` rendered its setter as
``<stem>_set_<prop>(self->handle, v); return 0;`` -- whatever the C setter
answered was discarded. A value the core rejected was dropped silently: the
Python assignment succeeded and the property went on reading its old value.
doppler's ``AccTrace.alpha`` (doppler-dsp/doppler#1959) pinned exactly that.

The decided shape: ``error`` / ``error_message`` on a ``writable``
property, spelled as a method's pair. With ``error`` the setter's C function
returns ``int`` and a non-zero return raises the declared exception through
the raise emitter every status-checking method uses. Wherever there is no C
setter call to test, the key is refused before anything is written.

Built and run, not read from generated text: whether an assignment raises is
a fact about the extension, and the only honest way to read it is to assign.
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
from just_makeit import _config as C
from just_makeit import _keys
from just_makeit._context import make_properties_ctx

#: The refused-value messages, distinct per object so a hit cannot come from
#: the neighbour.
TR_MSG = "alpha must be non-negative"
ACC_MSG = "alpha must lie in [0, 1]"


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


# ── built and run ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """A project with a refusing setter on both a standalone and a module
    object, reached by both writers: the manifest (`apply`'s replay) on the
    standalone object, the CLI (`jm property --error`) on the module one.

    The standalone ``alpha`` is ``expr``-backed, so its setter is the
    AUTHOR's to declare (the issue's shape); the module ``alpha`` is
    computed, so jm declares the setter and stubs it -- ``int`` either way.
    ``beta`` declares no ``error``, and must still assign without raising.
    """
    if not shutil.which("cmake"):
        pytest.skip("cmake not found")
    root = tmp_path_factory.mktemp("gh2182")
    _ok("new", "rs", "--object", "tr", "--state", "level:double:0.5",
        "--arg-type", "float", "--return-type", "float", cwd=root)  # fmt: skip
    proj = root / "rs"
    _ok("module", "dsp", cwd=proj)
    _ok("object", "acc", "--module", "dsp", "--state", "level:double:0.25",
        "--arg-type", "float", "--return-type", "float", cwd=proj)  # fmt: skip
    _ok("property", "acc", "alpha", "--type", "double", "--writable",
        "--error", "OverflowError", "--error-message", ACC_MSG,
        cwd=proj)  # fmt: skip

    # The standalone object's properties arrive by manifest, so `apply`'s
    # replay is the writer under test.
    with open(proj / "objects" / "tr.toml", "a", encoding="utf-8") as f:
        f.write(
            "\n[[tr.properties]]\n"
            'name = "alpha"\ntype = "double"\nwritable = true\n'
            'expr = "self->handle->level"\n'
            f'error = "ValueError"\nerror_message = "{TR_MSG}"\n'
            "\n[[tr.properties]]\n"
            'name = "beta"\ntype = "double"\nwritable = true\n'
            'expr = "self->handle->level"\n'
        )
    tr_h = proj / "native" / "inc" / "rs" / "tr" / "tr_core.h"
    _replace_once(
        tr_h,
        "void rs_tr_reset(rs_tr_state_t *state);\n",
        "void rs_tr_reset(rs_tr_state_t *state);\n"
        "int rs_tr_set_alpha(rs_tr_state_t *state, double val);\n"
        "void rs_tr_set_beta(rs_tr_state_t *state, double val);\n",
    )
    with open(
        proj / "native" / "src" / "tr" / "tr_core.c", "a", encoding="utf-8"
    ) as f:
        # Both refuse a negative value and keep the old one; only `alpha`
        # can SAY so.
        f.write(
            "\nint\nrs_tr_set_alpha(rs_tr_state_t *state, double val)\n{\n"
            "    if (val < 0.0)\n        return -2;\n"
            "    state->level = val;\n    return 0;\n}\n"
            "\nvoid\nrs_tr_set_beta(rs_tr_state_t *state, double val)\n{\n"
            "    if (val < 0.0)\n        return;\n"
            "    state->level = val;\n}\n"
        )
    _ok("apply", cwd=proj)

    # The module object's accessors are jm's stubs; give them bodies.
    acc_c = proj / "native" / "src" / "acc" / "acc_core.c"
    _replace_once(
        acc_c,
        "    (void)state;\n    return 0.0; /* placeholder */\n",
        "    return state->level;\n",
    )
    _replace_once(
        acc_c,
        "    (void)state; (void)val;\n    return 0; /* placeholder */\n",
        "    if (val < 0.0 || val > 1.0)\n        return 7;\n"
        "    state->level = val;\n    return 0;\n",
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
from rs import Tr
from rs.dsp import Acc


def attempt(obj, name, value):
    try:
        setattr(obj, name, value)
    except Exception as e:
        return [type(e).__name__, str(e)]
    return None


out = {}
t = Tr(0.5)
out["tr_bad"] = attempt(t, "alpha", -0.5)
out["tr_after_bad"] = t.alpha
out["tr_good"] = attempt(t, "alpha", 0.25)
out["tr_after_good"] = t.alpha
out["beta_bad"] = attempt(t, "beta", -1.0)
out["beta_after"] = t.beta
a = Acc(0.25)
out["acc_bad"] = attempt(a, "alpha", 2.0)
out["acc_after_bad"] = a.alpha
out["acc_good"] = attempt(a, "alpha", 0.75)
out["acc_after_good"] = a.alpha
out["docs"] = {
    "tr": Tr.alpha.__doc__,
    "acc": Acc.alpha.__doc__,
    "beta": Tr.beta.__doc__,
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


def test_a_refused_value_raises_the_declared_exception(probe) -> None:
    """The reported case: the assignment says the core refused it."""
    assert probe["tr_bad"] == ["ValueError", f"{TR_MSG} (rc=-2)"]
    assert probe["acc_bad"] == ["OverflowError", f"{ACC_MSG} (rc=7)"]


def test_a_refused_value_leaves_the_old_one(probe) -> None:
    assert probe["tr_after_bad"] == 0.5
    assert probe["acc_after_bad"] == 0.25


def test_an_accepted_value_is_set(probe) -> None:
    assert probe["tr_good"] is None and probe["tr_after_good"] == 0.25
    assert probe["acc_good"] is None and probe["acc_after_good"] == 0.75


def test_without_error_the_setter_still_discards(probe) -> None:
    """No `error`, no change: the setter has always discarded the return,
    and a project that declares nothing keeps exactly that."""
    assert probe["beta_bad"] is None
    assert probe["beta_after"] == 0.25
    assert probe["docs"]["beta"] == "Beta.\n"


def _stub_doc(proj: Path, stub: str, cls: str, member: str) -> str:
    tree = ast.parse((proj / "src" / "rs" / stub).read_text("utf-8"))
    (klass,) = [
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls
    ]
    getters = [
        n
        for n in klass.body
        if isinstance(n, ast.FunctionDef)
        and n.name == member
        and any(
            isinstance(d, ast.Name) and d.id == "property"
            for d in n.decorator_list
        )
    ]
    (getter,) = getters
    return ast.get_docstring(getter) or ""


@pytest.mark.parametrize(
    "key, stub, cls, exc, msg",
    [
        ("tr", "tr.pyi", "Tr", "ValueError", TR_MSG),
        ("acc", "dsp/dsp.pyi", "Acc", "OverflowError", ACC_MSG),
    ],
)
def test_both_doc_faces_document_the_raise(
    built, probe, key, stub, cls, exc, msg
) -> None:
    """The `.pyi` and `help()` say what an assignment raises, in one text:
    the runtime face is the stub's docstring without its indent."""
    runtime = inspect.cleandoc(probe["docs"][key])
    assert f"Raises\n------\n{exc}\n" in runtime, runtime
    assert msg.split()[0] in runtime
    assert _stub_doc(built, stub, cls, "alpha") == runtime


# ── refused: no C setter call to test ─────────────────────────────────────────


@pytest.fixture()
def fresh(tmp_path) -> Path:
    _ok("new", "rf", "--object", "o", "--state", "level:double:1.0",
        cwd=tmp_path)  # fmt: skip
    return tmp_path / "rf"


@pytest.mark.parametrize(
    "flags, needle",
    [
        (["--error", "ValueError"], "a read-only property has no setter"),
        (
            ["--writable", "--field", "--error", "ValueError"],
            "Drop field and declare rf_o_set_p returning int",
        ),
        (["--writable", "--error-message", "no"], "needs error as well"),
        (
            ["--writable", "--error", "ValueErorr"],
            "'ValueErorr' is not a known exception category",
        ),
    ],
    ids=["read-only", "field", "message-alone", "unknown-exception"],
)
def test_the_cli_refuses_before_writing(fresh, flags, needle) -> None:
    """Refused before the manifest is touched: no `update` line is printed
    for a write the refusal then has to take back."""
    before = (fresh / "objects" / "o.toml").read_text("utf-8")
    r = run_cli("property", "o", "p", "--type", "double", *flags, cwd=fresh)
    assert r.returncode == 1, r.stdout + r.stderr
    assert needle in r.stderr, r.stderr
    assert "update" not in r.stdout, r.stdout
    assert (fresh / "objects" / "o.toml").read_text("utf-8") == before


def test_a_property_named_for_a_state_field_is_refused(fresh) -> None:
    """Its setter is the state field's accessor, which jm declares `void`:
    testing that return would not compile."""
    r = run_cli(
        "property", "o", "level", "--type", "double", "--writable",
        "--error", "ValueError", cwd=fresh,
    )  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    assert "declares returning void" in r.stderr, r.stderr


@pytest.mark.parametrize(
    "verb",
    [
        ("apply",),
        # Not a replay: `jm property` re-renders the object's binding from
        # the manifest as it stands, the hand-written row included, so the
        # check has to live in the render every verb goes through.
        ("property", "o", "q", "--type", "double"),
    ],
    ids=["apply", "another-verb"],
)
def test_the_manifest_is_refused_too(fresh, verb) -> None:
    """A hand-written row reaches the same check, and the tree is left as
    it was."""
    frag = fresh / "objects" / "o.toml"
    with open(frag, "a", encoding="utf-8") as f:
        f.write(
            '\n[[o.properties]]\nname = "p"\ntype = "double"\n'
            'writable = true\nfield = true\nerror = "ValueError"\n'
        )
    ext = fresh / "native" / "src" / "o" / "o_ext.c"
    before = ext.read_text("utf-8")
    r = run_cli(*verb, cwd=fresh)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Drop field and declare rf_o_set_p returning int" in r.stderr
    assert ext.read_text("utf-8") == before


# ── the prototype jm writes says int ─────────────────────────────────────────


@pytest.mark.parametrize("error, ret", [(True, "int"), (False, "void")])
def test_the_declared_setter_returns_what_the_binding_tests(
    fresh, error, ret
) -> None:
    flags = ["--error", "ValueError"] if error else []
    _ok("property", "o", "p", "--type", "double", "--writable", *flags,
        cwd=fresh)  # fmt: skip
    h = (fresh / "native" / "inc" / "rf" / "o" / "o_core.h").read_text()
    c = (fresh / "native" / "src" / "o" / "o_core.c").read_text()
    assert f"{ret} rf_o_set_p(rf_o_state_t *state, double val);" in h
    assert f"\n{ret}\nrf_o_set_p(rf_o_state_t *state, double val)\n" in c


# ── every writer carries the keys ─────────────────────────────────────────────


def test_script_replays_the_keys(fresh) -> None:
    _ok("property", "o", "p", "--type", "double", "--writable",
        "--error", "ValueError", "--error-message", "p is out of range",
        cwd=fresh)  # fmt: skip
    out = run_cli("script", cwd=fresh).stdout
    assert "--error ValueError" in out, out
    assert '--error-message "p is out of range"' in out, out


def test_the_keys_are_recognised_on_a_property() -> None:
    cfg = {
        "project": {"name": "rf"},
        "o": {
            "properties": [
                {
                    "name": "p",
                    "type": "double",
                    "writable": True,
                    "error": "ValueError",
                    "error_message": "no",
                }
            ]
        },
    }
    assert _keys.unknown_keys(cfg) == []


# ── without `error`, the render is unchanged ─────────────────────────────────


def test_a_plain_setter_renders_as_before() -> None:
    """Pins the no-`error` render of the one builder every face uses: the
    call whose return is discarded, a `void` prototype, a one-line doc."""
    ctx = make_properties_ctx(
        "o",
        "O",
        [{"name": "p", "type": "double", "writable": True}],
        csym="rf_o",
    )
    assert (
        '    if (!PyArg_Parse(value, "d", &v)) return -1;\n'
        "    rf_o_set_p(self->handle, v);\n"
        "    return 0;\n"
        "}"
    ) in ctx["getset_def"]
    assert "_rc" not in ctx["getset_def"]
    assert '(setter)O_setprop_p, "P.\\n", NULL }' in ctx["getset_def"]
    assert (
        "void rf_o_set_p(rf_o_state_t *state, double val);"
        in (ctx["property_decls"])
    )
    assert '        """P."""\n    @p.setter' in ctx["property_stubs_pyi"]


def test_a_refusing_setter_is_declared_int_by_the_render() -> None:
    """The template-rendered header (`property_decls`) agrees with the
    binding -- the slot a full re-render of `_core.h` writes."""
    ctx = make_properties_ctx(
        "o",
        "O",
        [
            {
                "name": "p",
                "type": "double",
                "writable": True,
                "error": "ValueError",
            }
        ],
        csym="rf_o",
    )
    assert (
        "int rf_o_set_p(rf_o_state_t *state, double val);"
        in (ctx["property_decls"])
    )
    assert "int _rc = rf_o_set_p(self->handle, v);" in ctx["getset_def"]
    assert '"rf_o_set_p failed",' in ctx["getset_def"]


def test_the_loader_keeps_the_keys(fresh) -> None:
    """The manifest the CLI wrote reads back with both keys."""
    _ok(
        "property",
        "o",
        "p",
        "--type",
        "double",
        "--writable",
        "--error",
        "ValueError",
        "--error-message",
        "no",
        cwd=fresh,
    )
    (p,) = C.properties(C.load(fresh), "o")
    assert (p["error"], p["error_message"]) == ("ValueError", "no")
