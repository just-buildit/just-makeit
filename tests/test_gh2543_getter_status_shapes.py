"""gh-2543: ``getter_status`` on a writable property and on a buffer view.

gh-2538 let a read-only scalar property's getter fail: the C getter returns
an ``int`` status and writes the value through ``*out``, and a non-zero
status raises the property's ``error``. Two shapes doppler's merged
``Correlator`` needs (doppler-dsp/doppler#2752 step 4c) were refused:

- a WRITABLE property (``keep_surface``). Its ``error`` / ``error_message``
  are the setter's (gh-2182), so the getter's failure is named by its own
  pair, ``getter_error`` / ``getter_error_message``;
- a BUFFER VIEW (``buf_field`` / ``len_field`` / ``valid_field``,
  ``last_corr``). Before the view is built the binding calls
  ``int <stem>_<prop>_status(const <stem>_state_t *)``; non-zero raises
  ``error``, and on zero ``valid_field`` still decides ``None``.

A container property with ``getter_status`` stays refused.

Built and run on a standalone object and a module object, each reached by
a different writer -- the manifest (`apply`'s replay) and the CLI.
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

#: Distinct per object, so a hit cannot come from the neighbour.
MSG = {
    "cor": {"keep": "cor keep is continuous-only", "last": "cor last off"},
    "acc": {"keep": "acc keep is continuous-only", "last": "acc last off"},
}
SET_MSG = "keep must not be negative"


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout + r.stderr


#: The states every object here carries: ``mode`` picks the configuration
#: (0 serves both members, anything else neither), ``corr`` / ``ncorr`` are
#: the buffer and its length, ``has`` the view's validity flag.
_STATES = ("--state", "mode:int:0", "--state", "corr:double[4]",
           "--state", "ncorr:size_t:4", "--state", "has:int:1",
           "--arg-type", "float", "--return-type", "float")  # fmt: skip


def _row(obj: str) -> str:
    return (
        f"\n[[{obj}.properties]]\n"
        'name = "keep"\ntype = "int"\nwritable = true\n'
        'getter_status = true\ngetter_error = "AttributeError"\n'
        f'getter_error_message = "{MSG[obj]["keep"]}"\n'
        f'error = "ValueError"\nerror_message = "{SET_MSG}"\n'
        f"\n[[{obj}.properties]]\n"
        'name = "last"\ntype = "double[]"\nbuf_field = "corr"\n'
        'len_field = "ncorr"\nvalid_field = "has"\ngetter_status = true\n'
        f'error = "AttributeError"\nerror_message = "{MSG[obj]["last"]}"\n'
    )


def _bodies(c_file: Path, stem: str) -> None:
    """Replace jm's three stubs with bodies that fail off ``mode == 0``."""
    st = f"{stem}_state_t"
    _replace_once(
        c_file,
        f"{stem}_get_keep(const {st} *state, int *out)\n{{\n"
        "    (void)state; (void)out;\n    return 0; /* placeholder */\n}",
        f"{stem}_get_keep(const {st} *state, int *out)\n{{\n"
        "    if (state->mode != 0)\n        return 3;\n"
        "    *out = 7;\n    return 0;\n}",
    )
    _replace_once(
        c_file,
        f"{stem}_set_keep({st} *state, int val)\n{{\n"
        "    (void)state; (void)val;\n    return 0; /* placeholder */\n}",
        f"{stem}_set_keep({st} *state, int val)\n{{\n"
        "    (void)state;\n    return val < 0 ? 2 : 0;\n}",
    )
    _replace_once(
        c_file,
        f"{stem}_last_status(const {st} *state)\n{{\n"
        "    (void)state;\n    return 0; /* placeholder */\n}",
        f"{stem}_last_status(const {st} *state)\n{{\n"
        "    return state->mode != 0 ? 5 : 0;\n}",
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """``cor`` is standalone and gets its rows by manifest (`apply`);
    ``acc`` is a module object and gets them by `jm property`."""
    if not shutil.which("cmake"):
        pytest.skip("cmake not found")
    root = tmp_path_factory.mktemp("gh2543")
    _ok("new", "rs", "--object", "cor", *_STATES, cwd=root)
    proj = root / "rs"
    _ok("module", "dsp", cwd=proj)
    _ok("object", "acc", "--module", "dsp", *_STATES, cwd=proj)
    _ok("property", "acc", "keep", "--type", "int", "--writable",
        "--getter-status", "--getter-error", "AttributeError",
        "--getter-error-message", MSG["acc"]["keep"],
        "--error", "ValueError", "--error-message", SET_MSG,
        cwd=proj)  # fmt: skip
    _ok("property", "acc", "last", "--type", "double[]",
        "--buf-field", "corr", "--len-field", "ncorr", "--valid-field",
        "has", "--getter-status", "--error", "AttributeError",
        "--error-message", MSG["acc"]["last"], cwd=proj)  # fmt: skip
    with open(proj / "objects" / "cor.toml", "a", encoding="utf-8") as f:
        f.write(_row("cor"))
    _ok("apply", cwd=proj)

    _bodies(proj / "native" / "src" / "cor" / "cor_core.c", "rs_cor")
    _bodies(proj / "native" / "src" / "acc" / "acc_core.c", "rs_acc")
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
        v = getattr(obj, name)
        return ["ok", v.tolist() if hasattr(v, "tolist") else v]
    except Exception as e:
        return [type(e).__name__, str(e)]


def probe_has(obj, name):
    # hasattr swallows AttributeError only; any other class escapes it, and
    # is recorded so the assertion names it rather than the probe crashing.
    try:
        return hasattr(obj, name)
    except Exception as e:
        return type(e).__name__


def write(obj, name, value):
    try:
        setattr(obj, name, value)
        return ["ok"]
    except Exception as e:
        return [type(e).__name__, str(e)]


out = {}
for cls in (Cor, Acc):
    k = cls.__name__.lower()
    for mode, has in ((0, 1), (0, 0), (1, 1)):
        o = cls(mode, 4, has)
        out[f"{k}/{mode}/{has}"] = {
            "keep": read(o, "keep"),
            "last": read(o, "last"),
            "has_keep": probe_has(o, "keep"),
            "has_last": probe_has(o, "last"),
            "set_ok": write(o, "keep", 1),
            "set_bad": write(o, "keep", -1),
        }
    out[k + "/docs"] = {"keep": cls.keep.__doc__, "last": cls.last.__doc__}
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


OBJS = ["cor", "acc"]


@pytest.mark.parametrize("obj", OBJS)
def test_a_passing_read_returns_the_value(probe, obj) -> None:
    got = probe[f"{obj}/0/1"]
    assert got["keep"] == ["ok", 7]
    assert got["last"] == ["ok", [0.0, 0.0, 0.0, 0.0]]
    assert got["has_keep"] is True
    assert got["has_last"] is True


@pytest.mark.parametrize("obj", OBJS)
def test_valid_field_still_decides_none(probe, obj) -> None:
    """A zero status leaves the view's own `None` exactly as it was."""
    assert probe[f"{obj}/0/0"]["last"] == ["ok", None]


@pytest.mark.parametrize("obj", OBJS)
def test_a_failing_read_raises_the_declared_exception(probe, obj) -> None:
    got = probe[f"{obj}/1/1"]
    assert got["keep"] == ["AttributeError", f"{MSG[obj]['keep']} (rc=3)"]
    assert got["last"] == ["AttributeError", f"{MSG[obj]['last']} (rc=5)"]
    # AttributeError is what `hasattr` reads as absence.
    assert got["has_keep"] is False
    assert got["has_last"] is False


@pytest.mark.parametrize("obj", OBJS)
def test_the_setter_keeps_its_own_error(probe, obj) -> None:
    """`error` stays the setter's on a writable property (gh-2182)."""
    got = probe[f"{obj}/1/1"]
    assert got["set_ok"] == ["ok"]
    assert got["set_bad"] == ["ValueError", f"{SET_MSG} (rc=2)"]


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
    "obj, stub, cls",
    [("cor", "cor.pyi", "Cor"), ("acc", "dsp/dsp.pyi", "Acc")],
)
def test_both_doc_faces_name_the_exception(built, probe, obj, stub, cls):
    """The `.pyi` and `help()` agree, standalone and in a module, and name
    the read's exception -- and, on the writable one, the setter's."""
    docs = probe[f"{obj}/docs"]
    keep = inspect.cleandoc(docs["keep"])
    assert f"AttributeError\n    On read, if ``rs_{obj}_get_keep``" in keep, (
        keep
    )
    assert f"ValueError\n    On assignment, if ``rs_{obj}_set_keep``" in keep
    last = inspect.cleandoc(docs["last"])
    assert (
        f"AttributeError\n    On read, if ``rs_{obj}_last_status``" in last
    ), last
    assert _stub_doc(built, stub, cls, "keep") == keep
    assert _stub_doc(built, stub, cls, "last") == last


def _stub_ann(proj: Path, stub: str, cls: str, member: str) -> str:
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
    return ast.unparse(getter.returns)


@pytest.mark.parametrize(
    "stub, cls", [("cor.pyi", "Cor"), ("dsp/dsp.pyi", "Acc")]
)
def test_a_valid_field_view_is_annotated_optional(built, probe, stub, cls):
    """`valid_field` makes the read `None` while the flag is clear (asserted
    above at runtime), so the stub must say `| None` -- standalone and in a
    module alike."""
    assert _stub_ann(built, stub, cls, "last") == "NDArray[np.float64] | None"


def test_a_view_without_valid_field_is_not_optional() -> None:
    """No `valid_field`, no `None`: the annotation stays the bare array."""
    from just_makeit._context import make_properties_ctx

    def ann(**extra: str) -> str:
        p = {"name": "v", "type": "double[]", "buf_field": "b", **extra}
        pyi = make_properties_ctx("o", "O", [p], csym="rf_o")[
            "property_stubs_pyi"
        ]
        return pyi.split("def v(self) -> ", 1)[1].split(":\n", 1)[0]

    assert ann() == "NDArray[np.float64]"
    assert ann(valid_field="ok") == "NDArray[np.float64] | None"


@pytest.mark.parametrize("obj", OBJS)
def test_the_header_declares_both(built, obj) -> None:
    h = (built / "native" / "inc" / "rs" / obj / f"{obj}_core.h").read_text()
    st = f"rs_{obj}_state_t"
    assert f"int rs_{obj}_get_keep(const {st} *state, int *out);" in h
    assert f"int rs_{obj}_set_keep({st} *state, int val);" in h
    assert f"int rs_{obj}_last_status(const {st} *state);" in h


def test_the_cli_declares_and_stubs_the_status_function(fresh) -> None:
    """`jm property --buf-field --getter-status` leaves a project that links:
    the status function is declared AND given a placeholder body."""
    _ok("property", "o", "v", "--type", "double[]", "--buf-field", "buf",
        "--getter-status", cwd=fresh)  # fmt: skip
    h = (fresh / "native" / "inc" / "rf" / "o" / "o_core.h").read_text()
    c = (fresh / "native" / "src" / "o" / "o_core.c").read_text()
    assert "int rf_o_v_status(const rf_o_state_t *state);" in h
    assert "\nint\nrf_o_v_status(const rf_o_state_t *state)\n{" in c


def test_apply_declares_both_on_a_module_object(tmp_path) -> None:
    """A module object's sacred header is only ever added to (gh-627), by
    `apply`'s additive injection -- the writer a manifest row reaches."""
    _ok("new", "rm", cwd=tmp_path)
    proj = tmp_path / "rm"
    _ok("module", "dsp", cwd=proj)
    _ok("object", "acc", "--module", "dsp", *_STATES, cwd=proj)
    with open(proj / "objects" / "acc.toml", "a", encoding="utf-8") as f:
        f.write(_row("acc"))
    _ok("apply", cwd=proj)
    h = (proj / "native" / "inc" / "rm" / "acc" / "acc_core.h").read_text()
    st = "rm_acc_state_t"
    assert f"int rm_acc_get_keep(const {st} *state, int *out);" in h
    assert f"int rm_acc_last_status(const {st} *state);" in h


def test_script_replays_the_getter_error(built) -> None:
    out = run_cli("script", cwd=built).stdout
    assert "--getter-error AttributeError" in out, out
    assert "--getter-error-message" in out, out


# -- refused ------------------------------------------------------------------


@pytest.fixture()
def fresh(tmp_path) -> Path:
    _ok("new", "rf", "--object", "o", "--state", "level:double:1.0",
        cwd=tmp_path)  # fmt: skip
    return tmp_path / "rf"


@pytest.mark.parametrize(
    "flags, needle",
    [
        (
            ["--writable", "--getter-status"],
            "so getter_status needs getter_error",
        ),
        (
            ["--writable", "--getter-status", "--getter-error-message", "x"],
            "so getter_status needs getter_error",
        ),
        (
            ["--getter-status", "--getter-error", "AttributeError"],
            "getter_error is redundant",
        ),
        (
            ["--getter-status", "--getter-error-message", "x"],
            "getter_error_message is redundant",
        ),
        (
            ["--writable", "--getter-error", "AttributeError"],
            "without getter_status the getter cannot fail",
        ),
        (
            ["--writable", "--getter-status", "--getter-error", "Nope"],
            "getter_error 'Nope' is not a known exception category",
        ),
        (
            ["--type", "list", "--getter-status"],
            "a container property reads through",
        ),
    ],
    ids=[
        "writable-without-getter-error",
        "writable-with-only-the-message",
        "read-only-getter-error",
        "read-only-getter-error-message",
        "getter-error-without-getter-status",
        "unknown-getter-error",
        "container-stays-refused",
    ],
)
def test_the_cli_refuses_before_writing(fresh, flags, needle) -> None:
    before = (fresh / "objects" / "o.toml").read_text("utf-8")
    r = run_cli("property", "o", "p", "--type", "double", *flags, cwd=fresh)
    assert r.returncode == 1, r.stdout + r.stderr
    assert needle in r.stderr, r.stderr
    assert (fresh / "objects" / "o.toml").read_text("utf-8") == before


def test_the_manifest_is_refused_too(fresh) -> None:
    """A hand-written row reaches the same check through `apply`."""
    with open(fresh / "objects" / "o.toml", "a", encoding="utf-8") as f:
        f.write(
            '\n[[o.properties]]\nname = "p"\ntype = "double"\n'
            'writable = true\ngetter_status = true\nerror = "ValueError"\n'
        )
    ext = fresh / "native" / "src" / "o" / "o_ext.c"
    before = ext.read_text("utf-8")
    r = run_cli("apply", cwd=fresh)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "so getter_status needs getter_error" in r.stderr, r.stderr
    assert ext.read_text("utf-8") == before
