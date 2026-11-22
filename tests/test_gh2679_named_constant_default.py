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
  the parameter required. That half is now a load refusal, gated by
  ``test_gh2709_default_raw_param_refused.py``.

GATE: every docstring face of a method (standalone object, module object)
      and of a module function says ``name : T, default <value>`` -- the C
      name for a constant, the Python spelling for a literal; every
      ``parse_type`` scalar takes a CLI default on both commands; the
      project's ``jm script`` replays to the same parameters.

gh-2703 widened the faces to every table a defaulted parameter is declared
in: an array param (``default None``, what an omitted array is), a handle's
``create_args`` and method ``args``, a composer's source and segment
``fields`` and its serializer ``params`` -- the last had no ``Parameters``
section on either face. Every writer appends the clause through
``_docstring.defaulted_ann``.

GATE: every table that takes a ``default``, derived from the manifest
      vocabulary, names it on both faces or is listed with why it cannot;
      a table listed because its binding ignores the default is held to
      that, so honouring it later turns this red.
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
    text = hdr.read_text(encoding="utf-8")
    proto = re.search(r"^double \w*clamp\(", text, re.M)
    assert proto, text
    doc = (
        "/**\n * @brief Clamp x.\n * @param x  Value.\n"
        " * @param lim  Limit.\n * @param s  Shift.\n"
        " * @return The clamped value.\n */\n"
    )
    hdr.write_text(
        text[: proto.start()] + doc + text[proto.start() :], encoding="utf-8"
    )
    _ok("apply", cwd=root)
    return root


def _faces(root: Path) -> "dict[str, str]":
    """Every generated docstring face, by what it is."""

    def one(pattern: str) -> str:
        hits = sorted(root.glob(pattern))
        assert len(hits) == 1, (pattern, hits)
        return hits[0].read_text(encoding="utf-8")

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


# ── gh-2703: every kind's writers ───────────────────────────────────────────
#
# gh-2679 held the three faces above. A parameter with a default is declared
# in more tables than those -- an array param, a handle's create and method
# args, a composer's fields and serializer params -- and each kind has its
# own writer for its docstrings. The cases below are keyed by the TABLE a
# row is declared in, and `test_every_param_table_is_covered` derives the
# tables from the manifest vocabulary (`_keys`), so a table that takes a
# `default` and is added later fails here until its faces are held.

#: Handle method arg constant, composer serializer constant: never defined.
_STEP, _RATE = "LAMP_STEP", "RATE_DEFAULT"

_KINDS = f"""
[[enum]]
name = "shape"
values = ["sine", "square"]

[module.lamp]
kind = "handle"
backing = "lamp"
type_name = "Lamp"
handle_type = "lamp_t"
header = "kc/lamp/lamp.h"
create_fn = "lamp_open"
close_fn = "lamp_close"

[[module.lamp.create_args]]
name = "level"
type = "int"
default = "3"

[[module.lamp.create_args]]
name = "label"
type = "string"
default = "raw"

[[module.lamp.create_args]]
name = "lit"
type = "bool"
default = "true"

[[module.lamp.methods]]
name = "bump"
fn = "lamp_bump"
doc = "Bump it."
args = [
    {{ name = "by", type = "int", default = "{_STEP}" }},
    {{ name = "gain", type = "double", default = "0.5" }},
]
returns = "int"

[[module.lamp.factories]]
name = "lamp_clone"
create_fn = "lamp_clone"
doc = "Clone."

[[module.lamp.factories.init_params]]
name = "lvl"
type = "int"
default = "4"
doc = "Level."

[module.gadget]
kind = "capsule"
backing = "gadget"

[[module.gadget.init_params]]
name = "hz"
type = "double"
default = "2.0"
doc = "Rate."

[module.mix]
kind = "composer"
backing = "mix"
composes = ["clip"]

[module.mix.source]
object = "clip"
struct = "clip_t"
type_name = "Clip"

[[module.mix.source.fields]]
name = "shape"
type = "int"
enum = "shape"
default = "sine"

[[module.mix.source.fields]]
name = "amp"
type = "double"
default = "1.5"

[module.mix.segment]
type_name = "Track"
struct = "track_t"
sources = "multi"

[[module.mix.segment.fields]]
name = "dur"
type = "size_t"
default = "8"

[module.mix.timeline]
type_name = "Reel"

[module.mix.oo]
composer_type_name = "Mix"
factories = ["sine", "square"]
discriminant = "shape"

[[module.mix.serializers]]
name = "describe"
fn = "mix_describe"
returns = "str"
params = [
    {{ name = "rate", type = "double", default = "{_RATE}" }},
    {{ name = "tag", type = "int", enum = "shape", default = "square" }},
]
"""


@pytest.fixture(scope="module")
def kinds(tmp_path_factory) -> Path:
    """A project with a defaulted parameter in every table but gh-2679's:
    an object's array init param, method param and function param, and
    the handle, capsule and composer modules' parameter tables."""
    tmp = tmp_path_factory.mktemp("gh2703")
    _ok("new", "kc", cwd=tmp)
    root = tmp / "kc"
    _ok(
        "object", "arr", "--init-param", "n:int:0",
        "--init-param", "w:float[]:[]",
        cwd=root,
    )  # fmt: skip
    # `jm method` / `jm function` take no array default (gh-2657 made the
    # manifest's `default = "[]"` the spelling), so it is set below.
    _ok(
        "method", "arr", "feed",
        "--param", "xs:float[]", "--param", "g:double=0.5",
        "--arg-type", "void", "--return-type", "int",
        cwd=root,
    )  # fmt: skip
    _ok("module", "am", cwd=root)
    _ok(
        "function", "tot", "--module", "am",
        "--param", "ys:double[]", "--param", "k:int=2",
        "--return-type", "double",
        cwd=root,
    )  # fmt: skip
    _ok("object", "clip", cwd=root)
    hdr = next(root.glob("native/inc/*/am/am_core.h"))
    text = hdr.read_text(encoding="utf-8")
    proto = re.search(r"^double \w*tot\(", text, re.M)
    assert proto, text
    doc = (
        "/**\n * @brief Total.\n * @param ys  Values.\n"
        " * @param k  Scale.\n * @return The total.\n */\n"
    )
    hdr.write_text(
        text[: proto.start()] + doc + text[proto.start() :], encoding="utf-8"
    )
    cfg = C.load(root)
    cfg["arr"]["doc"] = "An arr."
    (xs,) = [
        p for p in cfg["arr"]["methods"][0]["params"] if p["name"] == "xs"
    ]
    xs["default"] = "[]"
    fn = cfg["module"]["am"]["functions"][0]
    (ys,) = [p for p in fn["params"] if p["name"] == "ys"]
    ys["default"] = "[]"
    C.save(root, cfg)
    with open(root / "just-makeit.toml", "a", encoding="utf-8") as fh:
        fh.write(_KINDS)
    _ok("apply", cwd=root)
    return root


def _kind_faces(root: Path) -> "dict[str, str]":
    """Every face the `kinds` project's cases read, by what it is."""

    def one(pattern: str) -> str:
        hits = sorted(root.glob(pattern))
        assert len(hits) == 1, (pattern, hits)
        return hits[0].read_text(encoding="utf-8")

    return {
        "object stub": one("src/kc/arr.pyi"),
        "object runtime": one("native/src/arr/arr_ext.c"),
        "module stub": one("src/kc/am/am.pyi"),
        "function runtime": one("native/src/am/am_ext.c"),
        "handle stub": one("src/kc/lamp/lamp.pyi"),
        "handle runtime": one("native/src/lamp/lamp_ext.c"),
        "composer stub": one("src/kc/mix/mix.pyi"),
        "composer runtime": one("native/src/mix/mix_ext.c"),
    }


#: (table, face, the Parameters type line it must carry). A table is
#: `(kind, path)`: an object's by its row vocabulary, a kind module's by
#: where its rows sit in `[module.X]`.
_KIND_LINES = [
    (("object", "init_param"), face, "w : NDArray[np.float32], default None")
    for face in ("object stub", "object runtime")
] + [
    (table, face, line)
    for table, faces, line in [
        (
            ("object", "param"),
            ("object stub", "object runtime"),
            "xs : npt.NDArray[np.float32], default None",
        ),
        (
            ("object", "function param"),
            ("module stub", "function runtime"),
            "ys : npt.NDArray[np.float64], default None",
        ),
        (
            ("handle", "create_args"),
            ("handle stub", "handle runtime"),
            "level : int, default 3",
        ),
        (
            ("handle", "create_args"),
            ("handle stub", "handle runtime"),
            'label : str, default ``"raw"``',
        ),
        # A number is spelled as a method param's is: C's `true` is
        # Python's `True` (it read `default true`).
        (
            ("handle", "create_args"),
            ("handle stub", "handle runtime"),
            "lit : bool, default True",
        ),
        (
            ("handle", "methods.args"),
            ("handle stub", "handle runtime"),
            f"by : int, default {_STEP}",
        ),
        (
            ("handle", "methods.args"),
            ("handle stub", "handle runtime"),
            "gain : float, default 0.5",
        ),
        (
            ("composer", "source.fields"),
            ("composer stub", "composer runtime"),
            "amp : float, default 1.5",
        ),
        (
            ("composer", "source.fields"),
            ("composer stub", "composer runtime"),
            'shape : str, default ``"sine"``',
        ),
        (
            ("composer", "segment.fields"),
            ("composer stub", "composer runtime"),
            "dur : int, default 8",
        ),
        (
            ("composer", "serializers.params"),
            ("composer stub", "composer runtime"),
            f"rate : float, default {_RATE}",
        ),
        (
            ("composer", "serializers.params"),
            ("composer stub", "composer runtime"),
            'tag : str, default ``"square"``',
        ),
    ]
    for face in faces
]

#: Tables that take a `default` and whose faces can carry no default
#: clause, each with why: `(stub, its required-argument spelling)` when
#: the binding makes the argument required, checked below; `None` when jm
#: renders no row of the table at all.
_NO_DEFAULT_FACE = {
    # The binding parses these all-required: the default is accepted and
    # read by nothing, so a docstring naming it would describe an argument
    # the caller cannot omit. Filed gh-2719 (factory) and gh-1913 (capsule).
    ("handle", "factories.init_params"): ("lamp/lamp.pyi", "lvl: int"),
    ("capsule", "init_params"): ("gadget/gadget.pyi", "hz: float"),
    # Not rows jm renders: `args` there is the author's own Python
    # signature text (gh-1190), and a capsule method declares one
    # `arg_type` / `return_type` pair, its `args` read by nothing (gh-2719).
    ("composer", "extra_methods.args"): None,
    ("capsule", "methods.args"): None,
}


def _param_tables() -> "set[tuple[str, str]]":
    """Every table whose rows are parameters that take a `default`.

    An object's by vocabulary: every one named for a parameter (``param``,
    ``arg``) or a constructor ``field`` that takes ``default``, other than
    a kind module's own. A kind module's by where its rows sit, read off
    the walk `_keys.unknown_keys` makes: a probe key planted in each table
    and each nested list is reported with the vocabulary that checks it,
    which is the one way to learn what a nested list holds without
    restating the walk.
    """
    vocab = {
        v
        for v, keys in _keys.KIND_KEYS.items()
        if "default" in keys and v.endswith(("param", "arg", "field"))
    }
    out = {
        ("object", v) for v in vocab if not v.startswith(("kind ", "composer"))
    }
    for (kind, tbl), v in _keys.KIND_TABLE_VOCAB.items():
        if "default" in _keys.KIND_KEYS[v]:
            out.add((kind, tbl))
        for key in _keys.KIND_KEYS[v]:
            probe = {"module": {"m": {"kind": kind, tbl: [
                {"name": "r", key: [{"name": "p", "zz_probe": 1}]}
            ]}}}  # fmt: skip
            for u in _keys.unknown_keys(probe):
                if u.key == "zz_probe" and u.kind in vocab:
                    out.add((kind, f"{tbl}.{key}"))
    for (kind, outer, inner), v in _keys.KIND_NESTED_VOCAB.items():
        if v in vocab:
            out.add((kind, f"{outer}.{inner}"))
    return out


def test_every_param_table_is_covered():
    """A table that takes a default is held by a case, or says why not."""
    covered = {t for t, _f, _l in _KIND_LINES}
    assert _param_tables() == covered | set(_NO_DEFAULT_FACE), (
        "a parameter table that takes `default` has no case in _KIND_LINES"
    )
    assert not covered & set(_NO_DEFAULT_FACE)


@pytest.mark.parametrize(("table", "face", "line"), _KIND_LINES)
def test_every_kind_names_the_default(kinds, table, face, line):
    text = _kind_faces(kinds)[face]
    if face.endswith("runtime"):
        lit = line.replace('"', '\\"')
        assert f'"{lit}\\n"' in text, f"{table} {face}: {line!r} missing"
    else:
        assert re.search(rf"^\s+{re.escape(line)}$", text, re.M), (
            f"{table} {face}: {line!r} missing"
        )


@pytest.mark.parametrize(
    "table", [t for t, sig in _NO_DEFAULT_FACE.items() if sig]
)
def test_a_default_the_binding_ignores_is_not_documented(kinds, table):
    """The reason `_NO_DEFAULT_FACE` gives, held: the argument is required
    in the signature, so no face may claim a default. When the binding
    learns to honour it, this goes red, and the table needs a case."""
    stub, sig = _NO_DEFAULT_FACE[table]
    text = (kinds / "src/kc" / stub).read_text(encoding="utf-8")
    assert re.search(rf"\b{re.escape(sig)}[,)]", text), (table, sig)
    name = sig.split(":")[0]
    assert not re.search(rf"^\s+{name} : .*, default", text, re.M), table


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
