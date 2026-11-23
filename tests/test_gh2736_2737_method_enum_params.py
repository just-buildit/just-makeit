"""A method parameter's enum, on every face (gh-2736, gh-2737).

gh-2736: a method parameter declared ``type = "int"`` plus
``enum = "<name>"`` annotated ``str`` in both ``.pyi`` producers, while an
init param's ``string_enum:`` annotated ``Literal[...]`` of its names. A
caller deriving values from the stub (doppler's mutator gate) could not
find the legal ones, and a type checker accepted a typo the binding
refuses. Decision: every enum-backed parameter -- a method's in both
producers (``_stubs._obj_stub`` for a module object,
``_context._methods._stub_params`` for a standalone one), a module
function's, a handle's -- annotates ``Literal[...]`` through the one
``EnumAnn``, spelled by ``_types.enum_literal``, which ``_stubs._py``
spells an init param's with too. Each stub imports ``Literal`` when its
text uses it (``_stubs._uses_literal``), in place of a list of the
surfaces that can.

gh-2737: the init_param's enum spellings on a method or function
parameter (``type = "string_enum:a,b"`` or ``type = "enum:<name>"``)
passed ``apply``'s load check and reached ``_CTYPE_META`` in EVERY method
shape and every module function -- a ``KeyError`` naming only the string,
after a header warning had printed it as a C type. The ``enum`` key
spelling already worked in every shape, the issue's array-arg +
``variable_output`` + ``pass_capacity`` + ``error_negative`` + ``why`` one
included. Decision: docs/types.md has always said a method param spells
its enum with the ``enum`` key and that the init_param spelling there is
refused; ``apply`` now refuses it at load, and the one tuple-parse slot
(``scalar_arg_c``) at render, with one sentence naming the row that
works (``_types.param_enum_spelling_error``).

GATES: both producers' signature and ``Parameters`` line, for a module
object and a standalone one, plus a module function; the refusal on each
face and both spellings, with the project left as it was; and a compiled
project with gh-2737's shape that calls the method by choice name and
reads back the decoded int the kernel received.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _types as T  # noqa: E402
from just_makeit._context._parse import scalar_arg_c  # noqa: E402
from just_makeit._report import Refusal  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

PKG = "wl"
CHOICES = ["none", "fcs", "data"]
LIT = 'Literal["none", "fcs", "data"]'
ENUM = {"name": "wlan_mode", "values": CHOICES}
MODE = {"name": "mode", "type": "int", "enum": "wlan_mode", "default": "data"}

#: gh-2737's method, as the issue declares it, the mode by the `enum` key.
MPDU = {
    "name": "mpdu",
    "arg_type": "uint8_t",
    "return_type": "uint8_t",
    "variable_output": True,
    "pass_capacity": True,
    "count_type": "int64_t",
    "error_negative": True,
    "why": True,
    "error": "ValueError",
    "error_message": "mpdu refused",
    "params": [MODE],
}

#: gh-2736's method: a mutator taking its mode by name.
SET_DEN = {
    "name": "set_den",
    "arg_type": "void",
    "return_type": "int",
    "status_return": True,
    "params": [{k: v for k, v in MODE.items() if k != "default"}],
}


def _project(tmp: Path, methods: "list[dict]") -> Path:
    """A module ``m`` holding object ``o``, a standalone object ``s`` and a
    module function ``f``; *methods* on both objects, ``mode`` on ``f``."""
    assert run_cli("new", PKG, "--module", "m", cwd=tmp).returncode == 0
    root = tmp / PKG
    for args in (
        ("object", "o", "--module", "m"),
        ("object", "s"),
        ("function", "f", "--module", "m", "--param", "x:int"),
    ):
        r = run_cli(*args, cwd=root)
        assert r.returncode == 0, r.stdout + r.stderr
    cfg = C.load(root)
    cfg.setdefault("enum", []).append(dict(ENUM))
    for obj in ("o", "s"):
        cfg[obj]["methods"] = [dict(m) for m in methods]
    (fn,) = cfg["module"]["m"]["functions"]
    fn["params"] = [dict(MODE)]
    C.save(root, cfg)
    return root


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


#: Each stub writer, by the file it writes: the module producer
#: (`_stubs`) for the module object and the function, the standalone one
#: (`_context._methods` through `_render.render_component_pyi`).
STUBS = {
    "module": f"src/{PKG}/m/m.pyi",
    "standalone": f"src/{PKG}/s.pyi",
}


@pytest.fixture(scope="module")
def applied(tmp_path_factory) -> Path:
    root = _project(tmp_path_factory.mktemp("gh2736"), [MPDU, SET_DEN])
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


def _signature(pyi: str, cls: "str | None", name: str) -> ast.FunctionDef:
    """The ``def <name>`` of *cls* (a module-level one when None)."""
    tree = ast.parse(pyi)
    scope = tree.body
    if cls is not None:
        scope = next(
            n.body
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == cls
        )
    return next(
        n for n in scope if isinstance(n, ast.FunctionDef) and n.name == name
    )


def _ann(fn: ast.FunctionDef, arg: str) -> str:
    a = next(a for a in fn.args.args + fn.args.kwonlyargs if a.arg == arg)
    assert a.annotation is not None
    return ast.unparse(a.annotation)


_PY_LIT = "Literal['none', 'fcs', 'data']"


@pytest.mark.parametrize(
    "stub,cls,method",
    [
        ("module", "O", "mpdu"),
        ("module", "O", "set_den"),
        ("standalone", "S", "mpdu"),
        ("standalone", "S", "set_den"),
        ("module", None, "f"),
    ],
)
def test_the_signature_names_the_choices(applied, stub, cls, method):
    """gh-2736: each producer annotates the choices, and imports them."""
    pyi = _read(applied, STUBS[stub])
    assert _ann(_signature(pyi, cls, method), "mode") == _PY_LIT, pyi
    imports = [ln for ln in pyi.splitlines() if ln.startswith("from typing")]
    assert any("Literal" in ln for ln in imports), imports


@pytest.mark.parametrize(
    "stub,cls,method",
    [
        ("module", "O", "mpdu"),
        ("standalone", "S", "mpdu"),
    ],
)
def test_the_parameters_line_says_what_the_signature_says(
    applied, stub, cls, method
):
    """The doc's type line is the same annotation, with the default."""
    pyi = _read(applied, STUBS[stub])
    doc = ast.get_docstring(_signature(pyi, cls, method)) or ""
    assert f"mode : {LIT}, default 'data'" in doc, doc


@pytest.mark.parametrize(
    "rel", ["native/src/m/m_ext_o.c", "native/src/s/s_ext.c"]
)
def test_the_runtime_doc_says_it_too(applied, rel):
    """The ``PyMethodDef`` doc comes from the builder the stub does."""
    text = _read(applied, rel).replace('\\"', '"')
    assert f"mode : {LIT}, default 'data'" in text, rel


def test_the_header_prototype_takes_the_c_int(applied):
    """gh-2737: the prototype jm writes passes the decoded int."""
    for rel in ("native/inc/wl/o/o_core.h", "native/inc/wl/s/s_core.h"):
        text = _read(applied, rel)
        line = next(ln for ln in text.splitlines() if "_mpdu(" in ln)
        assert ", int mode," in line, (rel, line)
        assert "string_enum" not in text and "enum:" not in text, rel


# -- the init_param spellings, refused --------------------------------------

SPELLINGS = ["string_enum:none,fcs,data", "enum:wlan_mode"]


@pytest.mark.parametrize("ptype", SPELLINGS)
@pytest.mark.parametrize("face", ["o", "s", "f"])
def test_apply_refuses_the_init_param_spelling(tmp_path, face, ptype):
    """gh-2737: a sentence naming the row that works, not a traceback, and
    the project as it was."""
    root = _project(tmp_path, [MPDU])
    cfg = C.load(root)
    row = {"name": "mode", "type": ptype}
    if face == "f":
        cfg["module"]["m"]["functions"][0]["params"] = [row]
    else:
        cfg[face]["methods"][0]["params"] = [row]
    C.save(root, cfg)
    before = {
        p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }
    r = run_cli("apply", cwd=root)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "Traceback" not in out and "KeyError" not in out, out
    # Refused at load, before anything is rendered: the issue's header
    # warning printed the spelling as the C type of a prototype.
    assert f"{ptype} mode" not in out, out
    assert f"param 'mode': `type = \"{ptype}\"` is the init_param" in out
    assert '{ name = "mode", type = "int", enum = "' in out, out
    after = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    assert after == before


@pytest.mark.parametrize("ptype", SPELLINGS)
def test_the_load_check_names_every_row(ptype):
    """`manifest_type_errors` reports each row before anything renders --
    all of them at once, where a render stops at the first."""
    row = {"name": "mode", "type": ptype}
    cfg = {
        "o": {"methods": [{"name": "mpdu", "params": [row]}]},
        "s": {
            "views": [
                {
                    "class_name": "V",
                    "methods": [{"name": "g", "params": [row]}],
                }
            ]
        },
        "module": {"m": {"functions": [{"name": "f", "params": [row]}]}},
    }
    errs = C.manifest_type_errors(cfg)
    want = T.param_enum_spelling_error("mode", ptype)
    assert errs == [
        f"module 'm' function 'f': {want}",
        f"'o' method 'mpdu': {want}",
        f"'s' view 'V' method 'g': {want}",
    ], errs


@pytest.mark.parametrize("ptype", SPELLINGS)
def test_the_render_slot_refuses_it_too(ptype):
    """For a path that renders without `apply`'s load check, the one
    tuple-parse slot says the same sentence."""
    with pytest.raises(Refusal) as e:
        scalar_arg_c("mode", ptype, "", fail="return NULL;")
    assert str(e.value) == T.param_enum_spelling_error("mode", ptype)


# -- compiled: gh-2737's shape, called by choice name -----------------------

#: The kernel's body: `none` refuses with a sentence, otherwise the output
#: is the mode the binding decoded, then the input -- so the caller reads
#: back exactly the int C received.
_KERNEL_MAX = "    (void)state;\n    return n_in + 1;\n"
_KERNEL = """\
    (void)state;
    if (mode == 0) {
        *why = "mode none carries no body";
        return -1;
    }
    if (max_out < n_in + 1)
        return -1;
    out[0] = (uint8_t)mode;
    for (size_t i = 0; i < n_in; i++)
        out[i + 1] = in[i];
    return (int64_t)(n_in + 1);
"""


def _implement(root: Path, obj: str) -> None:
    """Write the kernel into the scaffolded ``<obj>_core.c``."""
    path = root / "native" / "src" / obj / f"{obj}_core.c"
    text = path.read_text(encoding="utf-8")
    for fn, body in (
        (f"wl_{obj}_mpdu_max_out(", _KERNEL_MAX),
        (f"wl_{obj}_mpdu(", _KERNEL),
    ):
        head = text.index(f"\n{fn}")
        start = text.index("{\n", head) + 2
        end = text.index("\n}\n", start) + 1
        text = text[:start] + body + text[end:]
    path.write_text(text, encoding="utf-8")


_DRIVER = """\
import sys
import numpy as np
sys.path.insert(0, "src")
from wl import S
from wl.m import O

body = np.array([7, 8], dtype=np.uint8)
for cls in (O, S):
    obj = cls()
    assert obj.mpdu(body, mode="fcs").tolist() == [1, 7, 8]
    assert obj.mpdu(body, "data").tolist() == [2, 7, 8]
    assert obj.mpdu(body).tolist() == [2, 7, 8], "the default choice"
    try:
        obj.mpdu(body, mode="nope")
    except ValueError as e:
        assert "invalid mode 'nope' (choices: none, fcs, data)" in str(e), e
    else:
        raise AssertionError("an unknown choice must be refused")
    try:
        obj.mpdu(body, mode="none")
    except ValueError as e:
        assert "mode none carries no body" in str(e), e
    else:
        raise AssertionError("the kernel's refusal must raise")
print("GH2737 OK")
"""


def _run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout[-4000:], r.stderr[-4000:])


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_call_decodes_the_name_to_its_int(tmp_path):
    root = _project(tmp_path, [MPDU])
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    for obj in ("o", "s"):
        _implement(root, obj)
    _run(
        [
            "cmake",
            "-S",
            ".",
            "-B",
            "build",
            f"-DPython3_EXECUTABLE={sys.executable}",
            "-DCMAKE_C_FLAGS=-Wall -Wextra -Werror",
        ],
        root,
    )
    _run(["cmake", "--build", "build", "--parallel", "4"], root)
    driver = root / "drive_gh2737.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    r2 = subprocess.run(
        [sys.executable, str(driver)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert r2.returncode == 0 and "GH2737 OK" in r2.stdout, (
        r2.stdout + r2.stderr
    )
