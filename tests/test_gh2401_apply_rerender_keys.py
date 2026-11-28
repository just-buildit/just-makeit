"""gh-2401: `apply` re-creates a standalone binding with every manifest key.

A standalone object's ``<obj>_ext.c`` and ``.pyi`` have two renderers.
`jm apply`'s replay writes them through `_init.run` (the scaffold `jm
object` runs), and every member verb -- `jm property`, `jm warning`, `jm
error`, `jm remove` of a member -- re-renders them through
`_glue.component_ctx`. Each takes its inputs as an explicit list, so a key
one list names and the other does not is a key whose fate depends on which
verb last touched the file:

* ``init_post_parse`` reached `_glue`'s list and not the replay's: the
  `_object.run` -> `_init.run` forward dropped it, so a deleted binding came
  back from `apply` without the snippet (the issue).
* ``[[<obj>.state]] controllable`` reached the replay's list and not
  `_glue`'s: every member verb rewrote ``steps()`` without the per-call
  override, calling a ``<obj>_steps()`` the header declares with one more
  argument, so the project stopped compiling.

Neither was visible to `status --check`: it renders through the same
builders it compares against, so a key one of them drops is dropped on both
sides.

GATE: for every key `_keys` says a ``[<obj>]`` table, a ``[[<obj>.state]]``
row or a ``[[<obj>.init_params]]`` row may carry -- derived, so a key added
there without a row here fails `test_every_object_key_has_a_row` -- set it
on a standalone object and require

(a) the binding and stub `jm apply` writes when they are missing are
    byte-identical to the ones `_glue.regenerate_standalone` writes from the
    same manifest (the member verbs' render); and
(b) unless the row says why the key cannot reach them, the key CHANGES that
    render: `_glue`'s render with it differs from `_glue`'s render of the
    same manifest without it. This is the oracle that does not inherit an
    omission: (a) alone holds when both renderers drop the key, which is
    exactly how ``controllable`` survived.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, NamedTuple

import pytest

from _jmrun import run_cli
from just_makeit import _config as C
from just_makeit import _glue
from just_makeit import _keys

EXT = Path("native/src/o/o_ext.c")
#: The two files a standalone object's binding is: what `apply` writes when
#: they are missing, and what every member verb re-renders.
FILES = (EXT, Path("src/p/o.pyi"))

Edit = Callable[[dict], None]


class Row(NamedTuple):
    """How to set one key on object ``o``, given the whole manifest.

    ``prep`` sets the companions the key needs to mean anything (a
    `streamable` object needs a block producer); ``put`` sets the key
    itself. ``neutral`` is the reason the key cannot change either file,
    or "" when it must. ``flags`` are `jm object` flags for a key that is
    structural -- it decides the files the object is scaffolded with, so
    flipping it in a manifest is a different object, not an edit.
    ``files`` are author files the key names, and ``verbs`` the jm commands
    that declare what it refers to; ``argv`` replaces the base `jm object`
    command for a key the base object's shape excludes. ``refused`` is the sentence `apply`
    must refuse a standalone object carrying the key with.
    """

    put: Edit
    prep: Edit = lambda c: None
    neutral: str = ""
    flags: tuple = ()
    files: "dict[str, str]" = {}
    verbs: tuple = ()
    refused: str = ""
    argv: tuple = ()


def _set(**kw) -> Edit:
    return lambda c: c["o"].update(kw)


def _both(*edits: Edit) -> Edit:
    def edit(c: dict) -> None:
        for e in edits:
            e(c)

    return edit


def _state(**kw) -> Edit:
    return lambda c: c["o"]["state"][0].update(kw)


def _ip(**kw) -> Edit:
    return lambda c: c["o"]["init_params"][0].update(kw)


def _append(table: str, **row) -> Edit:
    return lambda c: c["o"].setdefault(table, []).append(dict(row))


def _on_last(table: str, **kw) -> Edit:
    """Set *kw* on the last row of *table* -- the one a ``prep`` added."""
    return lambda c: c["o"][table][-1].update(kw)


_SOURCE = _set(arg_type="void", return_type="float _Complex")
_STREAM = _both(_SOURCE, _set(streamable="true"))
_ERROR = _set(create_error="ValueError", create_error_message="refused")
_SERIAL = _set(serializable="true")
_BODY = "the lifecycle bodies land in the sacred `_core.c`"
_WIRING = "build wiring: the component's CMakeLists, not its binding"
_CORE_H = "shapes the sacred `_core.h` alone"
_IDENTITY = "the row's identity; every row has one"
_BODY_FILE = {"body.c": "void f (void)\n{\n  return;\n}\n"}
#: A macro family is all three keys or none, so the three rows are one.
_FAMILY = Row(
    _set(core_macro="O_CORE", core_args=["o"], core_header="p/o_fam.h"),
    flags=("--header-only",),
    files={"native/inc/p/o_fam.h": "#define O_CORE(id)\n"},
    neutral=_CORE_H,
)


def _body(key: str, value: str) -> Row:
    files = _BODY_FILE if key.endswith("_file") else {}
    return Row(_set(**{key: value}), neutral=_BODY, files=files)


OBJECT_ROWS: dict[str, Row] = {
    "arg_type": Row(_set(arg_type="double")),
    "return_type": Row(_set(return_type="double")),
    "class_name": Row(_set(class_name="PN")),
    "doc": Row(_set(doc="A documented object.")),
    "mutable": Row(_set(mutable="true"), neutral=_CORE_H),
    "no_state": Row(_set(no_state="true", state=[])),
    "no_step": Row(_set(no_step="true")),
    "no_reset": Row(_set(no_reset="true")),
    "opaque_state": Row(
        _set(opaque_state="true"),
        argv=(
            "object",
            "o",
            "--opaque-state",
            "--no-step",
            "--init-param",
            "poly:uint64_t:0",
        ),
        neutral="the struct's visibility in the sacred `_core.h`",
    ),
    "header_only": Row(
        _set(header_only="true"), flags=("--header-only",), neutral=_CORE_H
    ),
    "core_macro": _FAMILY,
    "core_args": _FAMILY,
    "core_header": _FAMILY,
    "fragment": Row(
        _set(fragment="sacred"),
        neutral="who owns a MODULE object's binding fragment; a"
        " standalone binding is always jm's",
    ),
    "process_global": Row(_set(process_global=True)),
    "no_python_bench": Row(
        _set(no_python_bench=True), neutral="the Python benchmark"
    ),
    "serializable": Row(_SERIAL),
    "set_state_status_errors": Row(
        _set(
            set_state_status_errors=[
                {"status": "O_EBAD", "error": "ValueError", "message": "x"}
            ]
        ),
        prep=_SERIAL,
    ),
    "streamable": Row(_set(streamable="true"), prep=_SOURCE),
    "stream_block_default": Row(_set(stream_block_default="64"), prep=_STREAM),
    "async_stream": Row(_set(async_stream="true"), prep=_STREAM),
    "step_delegates_to_steps": Row(
        _set(step_delegates_to_steps="true"), neutral=_CORE_H
    ),
    "array_args": Row(
        _set(array_args=[{"name": "taps", "type": "float32"}]),
        prep=_set(init_params=[]),
    ),
    "create_fn": Row(_set(create_fn="o_make")),
    "create_error": Row(_ERROR),
    "create_error_message": Row(
        _set(create_error_message="refused"),
        prep=_set(create_error="ValueError"),
    ),
    "create_why": Row(_set(create_why=True), prep=_ERROR),
    "init_post_parse": Row(
        _set(init_post_parse="    if (poly == 0)\n        poly = 7;\n")
    ),
    "depends_on": Row(_set(depends_on=["m"]), neutral=_WIRING),
    "extra_link_libs": Row(_set(extra_link_libs=["m"]), neutral=_WIRING),
    "extra_include_dirs": Row(
        _set(extra_include_dirs=["inc"]), neutral=_WIRING
    ),
    "module": Row(
        _set(module="dsp"),
        verbs=(("module", "dsp"),),
        neutral="routes the object into a module, which has no"
        " standalone binding at all",
    ),
    "impl": _body("impl", "    return x;"),
    "impl_file": _body("impl_file", "body.c::f"),
    "create_impl": _body("create_impl", "    (void)s;"),
    "create_impl_file": _body("create_impl_file", "body.c::f"),
    "reset_impl": _body("reset_impl", "    (void)s;"),
    "reset_impl_file": _body("reset_impl_file", "body.c::f"),
    "destroy_impl": _body("destroy_impl", "    (void)s;"),
    "destroy_impl_file": _body("destroy_impl_file", "body.c::f"),
    "replace": Row(_set(replace={"a": "b"}), neutral=_BODY),
    "state": Row(_append("state", name="bias", type="double")),
    "methods": Row(
        _append(
            "methods", name="scale", arg_type="double", return_type="double"
        )
    ),  # fmt: skip
    "properties": Row(_append("properties", name="level", type="double")),
    "records": Row(
        _append(
            "records",
            name="pair",
            type="o_pair_t",
            fields=[{"name": "a", "type": "double"}],
        ),
        neutral="a record no member references declares nothing in the"
        " binding",
    ),
    "init_params": Row(
        _append("init_params", name="seed", type="int", default="0")
    ),
    "init_groups": Row(
        _append("init_groups", group="gains", prefix="rx"),
        prep=lambda c: c.setdefault("group", []).append(
            {"name": "gains", "fields": [{"name": "g", "type": "double"}]}
        ),
    ),
    "views": Row(
        _append("views", class_name="OView", create_fn="o_view"),
        refused="views are a module-object feature",
    ),
    "warnings": Row(
        _append(
            "warnings", category="UserWarning", message="w", condition="loud"
        ),
        prep=_append("state", name="loud", type="bool"),
    ),
    "destroy": Row(_set(destroy={"returns": "int"})),
    "extra_methods": Row(_append("extra_methods", name="hello", fn="o_hi")),
}

STATE_ROWS: dict[str, Row] = {
    "name": Row(_state(name="level"), neutral=_IDENTITY),
    "type": Row(_state(type="double")),
    "default": Row(_state(default="2.0"), neutral=_CORE_H),
    # A state field documents the class only as a constructor parameter.
    "doc": Row(_state(doc="The gain."), prep=_set(init_params=[])),
    # A pointer field is opaque or not a state type at all, so the row
    # without the key is no row.
    "opaque": Row(
        _append("state", name="h", type="float *", opaque=True),
        prep=_set(create_impl="    s->h = NULL;"),
        neutral="an opaque field is a struct member the binding never names",
    ),
    "no_ctor": Row(_state(no_ctor=True), prep=_set(init_params=[])),
    "controllable": Row(_state(controllable=True)),
    "str_hint": Row(
        _on_last("state", str_hint="use bits()"),
        prep=_append("state", name="w", type="uint8_t[8]", default="0"),
    ),
}

#: The two keys are one dispatch: the real-input constructor and its type.
_REAL = Row(
    _on_last(
        "init_params", real_type="float[]", real_create_fn="o_create_real"
    ),
    prep=_append("init_params", name="taps", type="float _Complex[]"),
)
_CLK_H = {"native/inc/p/clk.h": "typedef struct o_clk o_clk_t;\n"}


def _param(**row) -> Edit:
    """A ``prep`` adding one init param after the base object's two."""
    return _append("init_params", **row)


INIT_PARAM_ROWS: dict[str, Row] = {
    "name": Row(_ip(name="taps"), neutral=_IDENTITY),
    "type": Row(_ip(type="int64_t")),
    "default": Row(_ip(default="5")),
    "default_raw": Row(
        _on_last("init_params", default_raw="UINT32_MAX"),
        prep=_param(name="cap", type="uint32_t"),
    ),
    "doc": Row(_ip(doc="The polynomial.")),
    "optional": Row(
        _on_last("init_params", optional=True),
        prep=_param(name="w", type="float[]", create_fn="o_create_w"),
    ),
    # `optional` IS the dispatch to `create_fn`; neither is valid alone.
    "create_fn": Row(
        _on_last("init_params", optional=True, create_fn="o_create_w"),
        prep=_param(name="w", type="float[]"),
    ),
    "required": Row(_ip(required=True)),
    "str_hint": Row(
        _on_last("init_params", str_hint="use arrays"),
        prep=_param(name="buf", type="float[]"),
    ),
    "example_value": Row(_ip(example_value="3")),
    # A foreign pointer is a capsule or nothing, so the row without the key
    # is no row at all.
    "capsule": Row(
        _param(
            name="clock", type="o_clk_t *", capsule="p.clk", header="p/clk.h"
        ),
        files=_CLK_H,
    ),
    "header": Row(
        _on_last("init_params", header="p/clk.h"),
        prep=_param(name="clock", type="o_clk_t *", capsule="p.clk"),
        files=_CLK_H,
        neutral="the include the sacred `_core.h` needs for the"
        " `create()` prototype",
    ),
    "object": Row(
        _param(name="src", object="b"),
        # Without the key: the same pointer under another capsule name, so
        # what the reference derives is what must show.
        prep=lambda c: None,
        verbs=(
            ("object", "b"),
            (
                "property",
                "b",
                "handle",
                "--type",
                "capsule",
                "--capsule",
                "p.b",
            ),
        ),
    ),  # fmt: skip
    "real_type": _REAL,
    "real_create_fn": _REAL,
    "derived": Row(
        _on_last("init_params", derived=["ny", "nx"]),
        prep=_param(name="ref", type="float _Complex[][]"),
        neutral="names the extents in the `create()` prototype; the"
        " binding passes them by position",
    ),
    "rank": Row(
        _on_last("init_params", rank=1),
        prep=_param(name="r", type="float[]"),
    ),
    "elements_per_sample": Row(
        _on_last("init_params", elements_per_sample=2),
        prep=_param(name="k", type="float[]"),
    ),
    "c_type": Row(
        _on_last("init_params", c_type="o_mode_t"),
        prep=_param(name="mode", type="string_enum:a,b", default="a"),
        neutral="the parameter's type in the `create()` prototype",
    ),
}


def _rows() -> dict[str, Row]:
    return (
        dict(OBJECT_ROWS)
        | {f"state.{k}": r for k, r in STATE_ROWS.items()}
        | {f"init_params.{k}": r for k, r in INIT_PARAM_ROWS.items()}
    )


def test_every_object_key_has_a_row() -> None:
    """The rows are the keys `_keys` recognises, no more, no fewer."""
    assert set(OBJECT_ROWS) == set(_keys.OBJECT_KEYS)
    assert set(STATE_ROWS) == set(_keys.STATE_KEYS)
    assert set(INIT_PARAM_ROWS) == set(_keys.INIT_PARAM_KEYS)


_OBJECT = (
    "object", "o", "--state", "gain:float:1.0",
    "--init-param", "poly:uint64_t:0", "--init-param", "length:uint32_t:0",
)  # fmt: skip


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout + r.stderr)


def _scaffold(root: Path, argv: tuple = _OBJECT) -> Path:
    """`jm new p`, then the standalone object ``o`` by *argv*."""
    root.mkdir(parents=True)
    _ok("new", "p", "--no-c-prefix", cwd=root)
    proj = root / "p"
    _ok(*argv, cwd=proj)
    return proj


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The shared scaffold for every row with no ``flags``."""
    return _scaffold(tmp_path_factory.mktemp("gh2401") / "base")


def _project(base: Path, dst: Path, row: Row, *edits: Edit) -> Path:
    """*row*'s project at *dst*, with *edits* applied to its manifest."""
    if row.flags or row.argv:
        argv = row.argv or (*_OBJECT, *row.flags)
        base = _scaffold(dst.with_name(dst.name + "-new"), argv)
    shutil.copytree(base, dst)
    for argv in row.verbs:
        _ok(*argv, cwd=dst)
    for rel, text in row.files.items():
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        (dst / rel).write_text(text, encoding="utf-8")
    cfg = C.load(dst)
    for edit in edits:
        edit(cfg)
    C.save(dst, cfg)
    return dst


def _read(proj: Path) -> "dict[Path, str]":
    return {f: (proj / f).read_text(encoding="utf-8") for f in FILES}


def _glue_render(proj: Path) -> "dict[Path, str]":
    """What the member verbs write: `_glue.regenerate_standalone`.

    Called in this process, as every member verb calls it. No verb renders
    the binding and nothing else, so this is the render itself rather than
    a verb whose own member would change the files under comparison.
    """
    _glue.regenerate_standalone(proj, C.load(proj), "o", "p")
    return _read(proj)


@pytest.mark.parametrize("key", sorted(_rows()))
def test_apply_recreates_the_binding_the_member_verbs_render(
    base: Path, tmp_path: Path, key: str
) -> None:
    row = _rows()[key]
    proj = _project(base, tmp_path / "apply", row, row.prep, row.put)
    if row.refused:
        r = run_cli("apply", cwd=proj)
        out = r.stdout + r.stderr
        assert r.returncode != 0 and row.refused in out, out
        return
    shutil.copytree(proj, tmp_path / "glue")
    for f in FILES:
        (proj / f).unlink()
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    applied = _read(proj)
    rendered = _glue_render(tmp_path / "glue")
    for f in FILES:
        assert applied[f] == rendered[f], (
            f"`{key}`: the {f.name} `jm apply` re-creates is not the one"
            " the member verbs render from the same manifest -- one"
            " renderer drops the key"
        )
    if row.neutral:
        return
    without = _glue_render(_project(base, tmp_path / "without", row, row.prep))
    assert rendered != without, (
        f"`{key}` changes nothing either renderer writes: both drop it, or"
        " its row needs the reason it cannot reach the binding"
    )


def test_object_run_forwards_every_parameter_init_run_takes() -> None:
    """`_object.run`'s standalone branch hands `_init.run` all it can take.

    The forward is a hand-written keyword list, and ``init_post_parse_impl``
    was the one name missing from it: accepted by both functions, passed by
    `apply`'s replay, dropped between them. Derived from the two
    signatures, so a parameter added to both and not forwarded fails here.
    """
    import ast
    import inspect

    from just_makeit import _init, _object

    fn = ast.parse(inspect.getsource(_object.run)).body[0]
    assert isinstance(fn, ast.FunctionDef)
    calls = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_init"
    ]
    assert len(calls) == 1, "expected the one standalone `_init.run` call"
    call = calls[0]
    forwarded = {k.arg for k in call.keywords} | {
        a.id for a in call.args if isinstance(a, ast.Name)
    }
    takes = set(inspect.signature(_init.run).parameters)
    params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    missing = sorted((params & takes) - forwarded)
    assert not missing, (
        f"`_object.run` takes {missing} and `_init.run` takes them too, but"
        " the standalone branch does not forward them"
    )


def test_the_replay_manifest_carries_init_post_parse(
    base: Path, tmp_path: Path
) -> None:
    """The standalone peer of gh-1971's module-path persistence.

    `_init.run` composes the replay's scratch manifest, which every later
    render of the replay reads; the key reaches it only if `_init.run`
    persists it. Reached through the Python API because `replay_out` is
    `status`'s internal handle on the replay (gh-949), with no flag.
    """
    import contextlib
    import io

    from just_makeit import _apply

    row = OBJECT_ROWS["init_post_parse"]
    proj = _project(base, tmp_path / "p", row, row.put)
    out = tmp_path / "replay"
    with contextlib.redirect_stdout(io.StringIO()):
        _apply.run(proj, replay_out=out)
    assert C.init_post_parse(C.load(out), "o") == C.init_post_parse(
        C.load(proj), "o"
    )
