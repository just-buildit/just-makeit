"""gh-2679: a parameter defaulting to a header constant stays optional, named.

A method parameter whose default is a C constant (``LIMIT_NONE``, say
``0xFFFFFFFFu``) is declared ``default = "LIMIT_NONE"``, as a state field's
is (gh-1488): the binding seeds its local with the constant, so the argument
is optional, and the stub signature, which cannot name C, says ``...``.
Three things kept that from being usable:

- no docstring face named a parameter's default at all -- neither the stub's
  ``Parameters`` entry nor the runtime ``__doc__`` -- so a constant reached
  the caller nowhere, and a literal only through the signature;
- ``jm method`` / ``jm function`` refused ``--param name:T=default`` on every
  ``parse_type`` scalar (``uint32_t``, ``size_t``, a complex) although the
  manifest accepted it, so ``jm script`` printed a replay jm rejected;
- ``default_raw``, the constructor's spelling, was reached for on a method
  parameter and ignored with a warning that said only "no effect", leaving
  the parameter required.

GATE: every docstring face of a method (standalone object, module object)
      and of a module function says ``name : T, default <value>`` -- the C
      name for a constant, the Python spelling for a literal; every
      ``parse_type`` scalar takes a CLI default on both commands; the
      project's ``jm script`` replays to the same parameters; and
      ``default_raw`` on a parameter points at ``default``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _jmrun import replay_script, run_cli
from just_makeit import _config as C
from just_makeit import _keys
from just_makeit._types import _CTYPE_META

#: The constants the probe's defaults name. Never defined: no test here
#: compiles, and the binding's seeding is unchanged by gh-2679.
_CP, _K, _LIM = "LIMIT_NONE", "SIZE_MAX", "LIM_MAX"


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"{args}\n{r.stdout}\n{r.stderr}"


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """One project with every face: a standalone object's method, a module
    object's method, and a module function, each with a constant default
    and a literal one."""
    tmp = tmp_path_factory.mktemp("gh2679")
    _ok("new", "nc", cwd=tmp)
    root = tmp / "nc"
    _ok("object", "frame", "--init-param", "n:int:0", cwd=root)
    _ok(
        "method", "frame", "add_run",
        "--param", "a:int",
        "--param", f"cp:uint32_t={_CP}",
        "--param", "g:double=0.5",
        "--arg-type", "void", "--return-type", "int",
        cwd=root,
    )  # fmt: skip
    _ok("module", "dsp", cwd=root)
    _ok("object", "fir", "--module", "dsp", cwd=root)
    _ok(
        "method", "fir", "go", "--module", "dsp",
        "--param", f"k:size_t={_K}",
        "--param", "t:int16_t=-4",
        "--arg-type", "void", "--return-type", "int",
        cwd=root,
    )  # fmt: skip
    _ok(
        "function", "clamp", "--module", "dsp",
        "--param", "x:double",
        "--param", f"lim:uint32_t={_LIM}",
        "--param", "s:int8_t=-3",
        "--return-type", "double",
        cwd=root,
    )  # fmt: skip
    # An undocumented free function collapses to its one-line summary on
    # both faces alike; a Doxygen block is what gives it a Parameters
    # section to carry the default.
    hdr = next(root.glob("native/inc/*/dsp/dsp_core.h"))
    text = hdr.read_text()
    proto = re.search(r"^double \w*clamp\(", text, re.M)
    assert proto, text
    doc = (
        "/**\n * @brief Clamp x.\n * @param x  Value.\n"
        " * @param lim  Limit.\n * @param s  Shift.\n"
        " * @return The clamped value.\n */\n"
    )
    hdr.write_text(text[: proto.start()] + doc + text[proto.start() :])
    _ok("apply", cwd=root)
    return root


def _faces(root: Path) -> "dict[str, str]":
    """Every generated docstring face, by what it is."""

    def one(pattern: str) -> str:
        hits = sorted(root.glob(pattern))
        assert len(hits) == 1, (pattern, hits)
        return hits[0].read_text()

    return {
        "object stub": one("src/nc/frame.pyi"),
        "object runtime": one("native/src/frame/frame_ext.c"),
        "module stub": one("src/nc/dsp/dsp.pyi"),
        "module object runtime": one("native/src/dsp/dsp_ext_fir.c"),
        "function runtime": one("native/src/dsp/dsp_ext.c"),
    }


#: (face, the Parameters type line it must carry). A constant by its C
#: name, a literal in Python -- `-3` and `0.5` are their own spellings.
#: Every name is distinct within its file: the module stub holds both the
#: module object's method and the function, and a line one of them carried
#: would otherwise pass for the other's.
_LINES = [
    ("object stub", f"cp : int, default {_CP}"),
    ("object stub", "g : float, default 0.5"),
    ("object runtime", f"cp : int, default {_CP}"),
    ("object runtime", "g : float, default 0.5"),
    ("module stub", f"k : int, default {_K}"),
    ("module stub", "t : int, default -4"),
    ("module object runtime", f"k : int, default {_K}"),
    ("module object runtime", "t : int, default -4"),
    ("module stub", f"lim : int, default {_LIM}"),
    ("module stub", "s : int, default -3"),
    ("function runtime", f"lim : int, default {_LIM}"),
    ("function runtime", "s : int, default -3"),
]


@pytest.mark.parametrize(("face", "line"), _LINES)
def test_every_face_names_the_default(project, face, line):
    text = _faces(project)[face]
    if face.endswith("runtime"):
        # A C string literal, one docstring line per literal.
        assert f'"{line}\\n"' in text, f"{face}: {line!r} missing"
    else:
        assert re.search(rf"^\s+{re.escape(line)}$", text, re.M), (
            f"{face}: {line!r} missing"
        )


def test_the_signature_stays_optional(project):
    """The constant cannot be a Python name, so `...`; the literal is
    itself. Both keep the argument omittable."""
    stub = _faces(project)["object stub"]
    assert "def add_run(self, a: int, cp: int = ..., g: float = 0.5)" in stub
    mod = _faces(project)["module stub"]
    assert "lim: int = ..., s: int = -3) -> float:" in mod


def test_the_binding_seeds_the_constant(project):
    """What makes it optional: the omitted argument's local starts at the
    constant. Unchanged by gh-2679, held here beside the faces naming it."""
    rt = _faces(project)["object runtime"]
    assert f"long long cp_raw = {_CP};" in rt


def _params(cfg: dict) -> "dict[str, list]":
    """Every method's and function's params, keyed by owner."""
    out = {}
    for comp in ("frame", "fir"):
        for m in cfg[comp].get("methods", []):
            out[f"{comp}.{m['name']}"] = m.get("params", [])
    for f in cfg["module"]["dsp"].get("functions", []):
        out[f"dsp.{f['name']}"] = f.get("params", [])
    return out


def test_jm_script_replays_the_defaults(project, tmp_path):
    script = run_cli("script", cwd=project)
    assert script.returncode == 0, script.stderr
    assert f"--param cp:uint32_t={_CP}" in script.stdout
    replayed = replay_script(script.stdout, tmp_path)
    assert _params(C.load(replayed)) == _params(C.load(project))


#: Every scalar the binding parses through an intermediate -- the types
#: `jm method` and `jm function` refused a default on. Derived, so a type
#: added to the table is covered without being listed.
_PARSE_TYPE = sorted(k for k, m in _CTYPE_META.items() if "parse_type" in m)


@pytest.mark.parametrize("command", ["method", "function"])
def test_every_parse_type_scalar_takes_a_cli_default(tmp_path, command):
    _ok("new", "pt", cwd=tmp_path)
    root = tmp_path / "pt"
    params = []
    for i, ctype in enumerate(_PARSE_TYPE):
        params += ["--param", f"p{i}:{ctype}={_CTYPE_META[ctype]['zero']}"]
    if command == "method":
        _ok("object", "o", cwd=root)
        args = ["method", "o", "m", *params, "--arg-type", "void"]
    else:
        _ok("module", "md", cwd=root)
        args = ["function", "f", "--module", "md", *params]
    _ok(*args, "--return-type", "int", cwd=root)
    cfg = C.load(root)
    rows = (
        cfg["o"]["methods"][-1]["params"]
        if command == "method"
        else cfg["module"]["md"]["functions"][-1]["params"]
    )
    assert [(p["type"], p.get("default")) for p in rows] == [
        (t, _CTYPE_META[t]["zero"]) for t in _PARSE_TYPE
    ]


@pytest.mark.parametrize(
    ("kind", "table"),
    [("param", "methods"), ("function param", "functions")],
)
def test_default_raw_on_a_param_points_at_default(project, kind, table):
    """The constructor's key, on a parameter: the warning says what to write
    instead, rather than only that it does nothing."""
    cfg = C.load(project)
    rows = (
        cfg["frame"]["methods"][0]["params"]
        if table == "methods"
        else cfg["module"]["dsp"]["functions"][0]["params"]
    )
    rows[-1]["default_raw"] = _CP
    hits = [
        u.message()
        for u in _keys.unknown_keys(cfg)
        if u.kind == kind and u.key == "default_raw"
    ]
    assert hits, kind
    assert "write the C constant as `default`" in hits[0], hits[0]
