"""gh-1525: a handle / capsule / composer arg may not share a name with a C
identifier the wrapper jm generates around it declares.

GATE: every C identifier a handle, capsule or composer wrapper declares beside a manifest-named arg is refused as that arg's name, on apply, before any file is written.

gh-1512 made that rule for methods, module functions and init params
(:func:`~just_makeit._builtins.param_name_clash`). The three module kinds
build their wrappers with emitters of their own and never called it, so a
handle with ``create_args = [{name = "kwlist"}]`` rendered ``static char
*kwlist[] ...; size_t kwlist = 0;`` and a method arg ``self`` rendered
``double self;`` inside ``Ring_push(RingObject *self, ...)`` -- neither
compiles, and ``jm apply`` exited 0.

Those emitters also declare unprefixed locals the object face never does (a
capsule create's ``w`` and ``cap``, a handle method's ``r`` / ``n_in`` /
``view``, a composer source's trailing ``fs``). Each kind names them next to
its emitter (``arg_scopes``) and hands them to the same rule as
``declares``. The first test does not trust those lists: it renders every
arg-bearing shape of each kind with SENTINEL arg names, pulls every
identifier each wrapper declares out of the C, and requires the rule to
claim each one -- the gh-1512 derivation, extended to the three kinds. It
also requires every wrapper that turns a sentinel into a C local to be one
``arg_scopes`` names, so a new arg-bearing wrapper cannot arrive unchecked.

The rest drive ``jm apply`` on a real project: the issue's two collisions
and one per kind are refused with nothing written, and the shapes that must
stay legal still apply.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _jmrun import run_cli

from just_makeit import _capsule, _composer, _handle
from just_makeit import _config as C
from just_makeit._builtins import param_name_clash

#: Sentinel arg names: nothing jm emits is spelled like these, so every other
#: identifier a wrapper declares is jm's own.
_SENTINEL = re.compile(r"zq[a-z]\b")


def _handle_cfg() -> dict:
    """One handle declaring every arg-bearing shape `_emit_method` has, a
    constructor over every create-arg coercion, and a factory."""
    methods = [
        # (a) scalar + path args -> scalar; -> None; -> status raise.
        {
            "name": "a_ret",
            "fn": "h_a",
            "returns": "double",
            "args": [
                {"name": "zqa", "type": "double"},
                {"name": "zqb", "type": "path"},
            ],
        },
        {
            "name": "a_void",
            "fn": "h_v",
            "args": [{"name": "zqa", "type": "float"}],
        },
        {
            "name": "a_err",
            "fn": "h_e",
            "returns": "int",
            "error": "OSError",
            "args": [{"name": "zqa", "type": "double"}],
        },
        # (b) array-in, bare and with trailing scalars.
        {
            "name": "b_bare",
            "fn": "h_b",
            "returns": "size_t",
            "args": [{"name": "zqa", "type": "float[]"}],
        },
        {
            "name": "b_scal",
            "fn": "h_bs",
            "returns": "size_t",
            "args": [
                {"name": "zqa", "type": "float[]"},
                {"name": "zqb", "type": "float"},
                {"name": "zqc", "type": "float", "default": "1.0f"},
            ],
        },
        # (c) int-in -> array-out.
        {
            "name": "c_pop",
            "fn": "h_c",
            "returns": "float[]",
            "args": [{"name": "zqa", "type": "size_t"}],
        },
        # (d) array-in + writable array-out, bare and with scalars.
        {
            "name": "d_bare",
            "fn": "h_d",
            "returns": "float[]",
            "args": [
                {"name": "zqa", "type": "float[]"},
                {"name": "zqb", "type": "float[]", "writable": True},
            ],
        },
        {
            "name": "d_scal",
            "fn": "h_ds",
            "returns": "float[]",
            "args": [
                {"name": "zqa", "type": "float[]"},
                {"name": "zqb", "type": "float[]", "writable": True},
                {"name": "zqc", "type": "float"},
                {"name": "zqd", "type": "float", "default": "0.0f"},
            ],
        },
        # (e) scalars / string -> handle-length array.
        {
            "name": "e_len",
            "fn": "h_el",
            "returns": "float[]",
            "out_len_fn": "h_len",
            "args": [
                {"name": "zqa", "type": "double"},
                {"name": "zqb", "type": "uint64_t"},
                {"name": "zqc", "type": "string"},
            ],
        },
        # (f) scalars / string -> handle-length bytes.
        {
            "name": "f_bytes",
            "fn": "h_f",
            "returns": "bytes",
            "out_len_fn": "h_len",
            "args": [
                {"name": "zqa", "type": "double"},
                {"name": "zqb", "type": "string"},
            ],
        },
    ]
    return {
        "project": {"name": "p", "version": "0.1.0"},
        "enum": [{"name": "mode", "values": ["one", "two"]}],
        "module": {
            "hmod": {
                "kind": "handle",
                "backing": "hb",
                "type_name": "Hb",
                "create_fn": "hb_open",
                "close_fn": "hb_close",
                "create_args": [
                    {"name": "zqa", "type": "size_t"},
                    {"name": "zqb", "type": "path"},
                    {"name": "zqc", "type": "bytes"},
                    {"name": "zqd", "type": "string"},
                    {
                        "name": "zqe",
                        "type": "int",
                        "enum": "mode",
                        "default": "one",
                    },
                ],
                "factories": [
                    {
                        "name": "HbFrom",
                        "create_fn": "hb_restore",
                        "init_params": [
                            {"name": "zqa", "type": "bytes"},
                            {"name": "zqb", "type": "path"},
                            {"name": "zqc", "type": "double"},
                        ],
                    }
                ],
                "methods": methods,
            }
        },
    }


def _capsule_cfg() -> dict:
    return {
        "project": {"name": "p", "version": "0.1.0"},
        "module": {
            "cmod": {
                "kind": "capsule",
                "backing": "cb",
                "capsule_name": "p.cb_state",
                "init_params": [
                    {"name": "zqa", "type": "double"},
                    {"name": "zqb", "type": "int"},
                ],
                "methods": [
                    {
                        "name": "execute",
                        "arg_type": "float[]",
                        "return_type": "float[]",
                        "caller_out": True,
                    },
                    {"name": "reset"},
                ],
            }
        },
    }


def _composer_module() -> dict:
    """A composer whose source fields and serializer params are sentinels,
    over every source-field coercion (enum, scalar, ranged, bytes with an
    alias and bit-pattern coercion)."""
    return {
        "kind": "composer",
        "backing": "wc",
        "capsule_name": "p.wc_state",
        "composes": ["wc_synth"],
        "source": {
            "object": "wc_synth",
            "struct": "wc_source_t",
            "type_name": "Synth",
            "fields": [
                {
                    "name": "zqa",
                    "type": "int",
                    "enum": "wtype",
                    "default": "tone",
                },
                {"name": "zqb", "type": "double", "default": "0.0"},
                {"name": "zqc", "type": "uint32_t", "default": "1"},
                {"name": "zqd", "type": "double", "default": "0.0"},
                {
                    "name": "zqe",
                    "type": "uint8_t*",
                    "bytes": True,
                    "aliases": ["pattern"],
                    "coerce": "bit_pattern",
                },
            ],
            "ranged": [{"name": "zqd", "flag": "WC_RANGE_D"}],
        },
        "segment": {
            "type_name": "Segment",
            "struct": "wc_segment_t",
            "fields": [
                {"name": "fs", "type": "double", "default": "1e6"},
                {"name": "num_samples", "type": "size_t", "default": "1024"},
            ],
            "sources": "multi",
        },
        "timeline": {"type_name": "Timeline", "loop": ["once", "repeat"]},
        "oo": {
            "factories": ["tone"],
            "emit": "ctypes",
            "discriminant": "zqa",
            "composer_type_name": "Composer",
        },
        "serializers": [
            {
                "name": "to_meta",
                "fn": "wc_meta_json",
                "returns": "str",
                "params": [
                    {
                        "name": "zqa",
                        "type": "int",
                        "enum": "wtype",
                        "default": "tone",
                    },
                    {"name": "zqb", "type": "double", "default": "1e6"},
                ],
            }
        ],
    }


def _composer_cfg() -> dict:
    return {
        "project": {"name": "p", "version": "0.1.0"},
        "enum": [{"name": "wtype", "values": ["tone", "noise"]}],
        "module": {"wmod": _composer_module()},
    }


#: A C function definition at column 0: return type line(s), then
#: ``name(signature)``, then a body closed by ``}`` at column 0.
_FUNC = re.compile(
    r"^(?:static\s+)?[A-Za-z_][\w \*]*\n(\w+)\(([^)]*)\)\n\{\n(.*?)^\}",
    re.M | re.S,
)
#: A block-scope declaration: a type, then the declared name, then ``=``,
#: ``;``, ``[`` or ``,``. Anchored at four-plus spaces so file-scope code and
#: prose in comments are not read as locals.
_DECL = re.compile(
    r"^ {4,}(?:static\s+|const\s+)*"
    r"(?!return\b|else\b|goto\b|case\b)[A-Za-z_]\w*"
    r"(?:\s+[A-Za-z_]\w*)*[\s\*]+\**(\w+)\s*(?:=|;|\[|,)",
    re.M,
)
#: The later declarators of a multi-declarator line --
#: ``PyObject *x_obj, *out_obj;`` declares both.
_MORE = re.compile(
    r"^ {4,}[A-Za-z_][\w ]*\*?\s*\w+((?:\s*,\s*\**\s*\w+)+)\s*;", re.M
)


def _declared(text: str) -> "dict[str, set[str]]":
    """Every function's declared identifiers: its C params and its locals."""
    out = {}
    for m in _FUNC.finditer(text):
        name, sig, body = m.groups()
        ids = set(_DECL.findall(body))
        for more in _MORE.findall(body):
            ids |= set(re.findall(r"(\w+)\s*(?=,|$)", more + ","))
        ids |= {
            re.split(r"[\s\*]+", a.strip())[-1]
            for a in sig.split(",")
            if a.strip() and a.strip() != "void"
        }
        out[name] = ids
    return out


_KINDS = {
    "handle": (_handle, _handle_cfg, "hmod"),
    "capsule": (_capsule, _capsule_cfg, "cmod"),
    "composer": (_composer, _composer_cfg, "wmod"),
}


@pytest.mark.parametrize("kind", sorted(_KINDS))
def test_every_local_a_kind_wrapper_declares_is_reserved(kind: str):
    """The derivation: each identifier jm declares beside a manifest-named
    arg is one an arg of that name would be refused as."""
    gen, make_cfg, module = _KINDS[kind]
    cfg = make_cfg()
    functions = _declared(gen.render_ext(cfg, module))
    scopes = gen.arg_scopes(cfg, module)

    # Armed: every scope's wrapper was found, and between them they declare
    # a sentinel as a local. A regex that matched nothing passes the rest.
    missing = [fn for _, fn, _, _ in scopes if fn not in functions]
    assert not missing, (missing, sorted(functions))
    assert any(
        {p for p in functions[fn] if _SENTINEL.fullmatch(p)}
        for _, fn, _, _ in scopes
    ), "no wrapper declared a sentinel: the walk is not reading the C"

    # Every wrapper that makes a manifest name one of its locals is a scope
    # the rule is asked about; one it is not asked about is unchecked.
    scoped = {fn for _, fn, _, _ in scopes}
    unscoped = sorted(
        fn
        for fn, ids in functions.items()
        if fn not in scoped and any(_SENTINEL.fullmatch(i) for i in ids)
    )
    assert not unscoped, (
        f"{kind}: these wrappers declare a manifest-named C local, but"
        f" {gen.__name__}.arg_scopes does not name them, so nothing checks"
        f" their arg names (gh-1525): {unscoped}"
    )

    unclaimed = []
    for _owner, fn, params, declares in scopes:
        for ident in sorted(functions[fn]):
            if _SENTINEL.fullmatch(ident):
                continue
            why = param_name_clash(
                ident,
                list(params) + [(ident, "double")],
                declares=declares,
            )
            if why is None:
                unclaimed.append(f"{fn}: {ident}")
    assert not unclaimed, (
        f"a generated {kind} wrapper declares a C identifier that an arg of"
        " the same name would NOT be refused as -- the tree would not"
        f" compile. Add it to {gen.__name__}.arg_scopes (gh-1525):\n  "
        + "\n  ".join(unclaimed)
    )


# -- the refusal, through `jm apply` ---------------------------------------


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    return tmp_path / "p"


def _add(root: Path, cfg: dict) -> None:
    """Declare *cfg*'s enums and modules in the project's manifest."""
    real = C.load(root)
    real.setdefault("enum", []).extend(cfg.get("enum", []))
    real.setdefault("module", {}).update(cfg["module"])
    C.save(root, real)


def _ring(create_arg: str, method_arg: str) -> dict:
    """The issue's handle: one create arg, one scalar method arg."""
    return {
        "project": {},
        "module": {
            "ring": {
                "kind": "handle",
                "backing": "ringbuf",
                "type_name": "Ring",
                "create_fn": "ringbuf_open",
                "close_fn": "ringbuf_close",
                "create_args": [{"name": create_arg, "type": "size_t"}],
                "methods": [
                    {
                        "name": "push",
                        "fn": "ringbuf_push",
                        "returns": "size_t",
                        "args": [{"name": method_arg, "type": "double"}],
                    }
                ],
            }
        },
    }


def _capsule_with(name: str) -> dict:
    cfg = _capsule_cfg()
    cfg["module"]["cmod"]["init_params"][0]["name"] = name
    return cfg


def _composer_field(name: str) -> dict:
    cfg = _composer_cfg()
    cfg["module"]["wmod"]["source"]["fields"][1]["name"] = name
    return cfg


def _composer_serializer_param(name: str) -> dict:
    cfg = _composer_cfg()
    cfg["module"]["wmod"]["serializers"][0]["params"][1]["name"] = name
    return cfg


def _b_scalar(name: str) -> dict:
    cfg = _ring("capacity", "gain")
    cfg["module"]["ring"]["methods"] = [
        {
            "name": "push_gain",
            "fn": "ringbuf_push_gain",
            "returns": "size_t",
            "args": [
                {"name": "x", "type": "float[]"},
                {"name": name, "type": "float"},
            ],
        }
    ]
    return cfg


_REFUSED = [
    (
        _ring("kwlist", "gain"),
        "handle module 'ring' create_args: param 'kwlist'",
        "keyword table (kwlist)",
    ),
    (
        _ring("capacity", "self"),
        "handle module 'ring' method 'push': param 'self'",
        "'self' is a C parameter",
    ),
    (
        _b_scalar("n_in"),
        "handle module 'ring' method 'push_gain': param 'n_in'",
        "'n_in' is a local the generated wrapper declares",
    ),
    (
        _capsule_with("w"),
        "capsule module 'cmod' init_params: param 'w'",
        "'w' is a local the generated wrapper declares",
    ),
    (
        _composer_field("fs"),
        "composer module 'wmod' source field: param 'fs'",
        "'fs' is a local the generated wrapper declares",
    ),
    (
        _composer_serializer_param("segs"),
        "composer module 'wmod' serializer 'to_meta': param 'segs'",
        "'segs' is a local the generated wrapper declares",
    ),
]


@pytest.mark.parametrize(
    "cfg, owner, why",
    _REFUSED,
    ids=[
        "handle-create-kwlist",
        "handle-method-self",
        "handle-b-n_in",
        "capsule-w",
        "composer-field-fs",
        "composer-serializer-segs",
    ],
)
def test_apply_refuses_a_colliding_arg_before_any_c(project, cfg, owner, why):
    _add(project, cfg)
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    r = run_cli("apply", cwd=project)
    assert r.returncode == 1, r.stdout
    assert f"{owner} collides with the generated C binding" in r.stderr, (
        r.stderr
    )
    assert why in r.stderr, r.stderr
    after = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert after == before, "a refused apply wrote to the tree"


@pytest.mark.parametrize(
    "cfg",
    [
        _ring("capacity", "gain"),
        # A result local is only reserved on a shape that declares one: `r`
        # is free on a method that returns nothing.
        _ring("r", "gain"),
        # `fs` is a SEGMENT field in doppler's composer, and only the source
        # type's constructor declares a local of that name.
        _composer_cfg(),
        _capsule_cfg(),
    ],
    ids=["handle", "handle-create-r", "composer", "capsule"],
)
def test_names_that_do_not_collide_still_apply(project, cfg):
    _add(project, cfg)
    r = run_cli("apply", cwd=project)
    assert r.returncode == 0, r.stderr
