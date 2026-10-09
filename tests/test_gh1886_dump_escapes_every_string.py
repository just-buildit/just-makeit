"""gh-1886: every string `_dump` writes goes through the one escaper.

`jm method o greet --param 's:const char *="hi"'` exited 0, and then every
`apply` and every `status` failed:

    error: just-makeit generated a manifest it cannot read back:
           Unclosed inline table

The CLI's own write was fine -- tomlkit escapes -- but `apply` replays the
manifest through `_dump`, and `_dump` wrote a param default as
``default = "{p["default"]}"``. A C string literal is the documented spelling
of a `const char *` default, so the one spelling that should have worked was
the one that could not.

gh-844 had built the escaper (`_toml_basic_string`) and routed the prose keys
through it. The hand-quoted f-string it replaced was still the house style
for every other key -- 130-odd sites across `_dump` and its helpers: every
`default`, `name`, `type`, `header`, `expr`, `condition`, `help`, every
string inside an array, and the `impl` heredocs, which doubled no backslash at
all, so `printf("a\\n")` in a C body read back with a real newline in it.
Fixing the four sites the issue listed would have left the rest.

Three gates, because they fail on different things:

* **The round trip** writes a value holding ``"``, ``\\`` and a newline into
  each string the fixtures carry, one at a time, and reads it back equal.
  This is the property the issue is about, asked of every key.
* **The reach ratchet** says the fixtures are not a sample. Every call to an
  escaper inside `_dump`'s call graph -- found in the source, not listed --
  must run while the fixtures are dumped, or it names the line. A key whose
  branch no fixture enters cannot be checked by the round trip, so it fails
  here instead.
* **The source gate** refuses the raw shape itself: an f-string, or a ``+``
  chain, in the serializer that interpolates between two ``"``. The ratchet
  can only ask the fixtures to reach an escaper CALL; a key quoted by hand has
  none, so this is what keeps a new one from arriving unseen.

The CLI trigger closes the loop: the issue's own commands, ending in an
`apply` that succeeds and a build whose suite passes.
"""

from __future__ import annotations

import ast
import copy
import shutil
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402

from _compilers import default_cc  # noqa: E402
from _jmrun import run_cli  # noqa: E402

#: A quote, a backslash and a newline: the three characters a hand-quoted
#: TOML string mishandles, in the order that makes each one matter -- the
#: backslash sits before an `s`, so a writer that does not double it emits
#: an escape TOML has no meaning for.
HOSTILE = 'a"b\\s\nc'

#: A value that chooses which grammar the rest of its table is written in.
#: Replacing it does not test that value's escaping -- it makes `_dump` write
#: a different table, so the one key it is excluded from is the switch.
_GRAMMAR_KEYS = frozenset({"kind"})


def _row(**kw: object) -> dict:
    return dict(kw)


#: The ordinary object, carrying every key `_dump` writes for one.
_OBJECT = {
    "module": "filt",
    "arg_type": "float",
    "return_type": "float",
    "mutable": "false",
    "no_state": "false",
    "no_step": "false",
    "no_reset": "false",
    "opaque_state": "false",
    "process_global": "false",
    "header_only": "false",
    "core_macro": "O_CORE",
    "core_header": "o/o_core.h",
    "step_delegates_to_steps": "true",
    "serializable": "true",
    "streamable": "true",
    "async_stream": "false",
    "stream_block_default": "1024",
    "class_name": "Obj",
    "create_fn": "o_create_custom",
    "create_error": "ValueError",
    "create_error_message": "could not create",
    "doc": "An object.",
    "depends_on": [_row(name="dep", link=True, test_only=True), "plain"],
    "extra_link_libs": ["m"],
    "extra_include_dirs": ["vendor"],
    "core_args": ["n"],
    "impl": "return x;",
    "create_impl": "obj->n = n;",
    "reset_impl": "state->n = 0;",
    "destroy_impl": "free(state);",
    "destroy": _row(
        name="o_close",
        aliases=["close"],
        returns="int",
        error="OSError",
        error_message="close failed",
        exit="close",
    ),
    "array_args": [_row(name="taps", type="float")],
    "state": [
        _row(
            name="n",
            type="int",
            default="0",
            opaque=True,
            no_ctor=True,
            controllable=True,
            doc="A count.",
        )
    ],
    "init_params": [
        _row(
            name="taps",
            type="float[]",
            default="0.0",
            default_raw="TAPS",
            real_type="float",
            real_create_fn="o_create",
            create_fn="o_make",
            doc="The taps.",
            capsule="p.cap",
            header="p/cap.h",
            derived=["ny", "nx"],
            c_type="o_mode_t",
            example_value="1",
            object="other",
            str_hint="pass an array",
            rank=1,
        )
    ],
    "init_groups": [_row(group="geom", prefix="in")],
    "init_post_parse": "if (n < 0) n = 0;",
    "methods": [
        _row(
            name="run",
            doc="Run it.",
            arg_type="float",
            return_type="float",
            count_default="16",
            count_name="n_out",
            multi_output=["float"],
            params=[
                _row(
                    name="s",
                    type="const char *",
                    default="NULL",
                    role="variant",
                    capsule="p.cap",
                    header="p/cap.h",
                )
            ],
            out_type="float",
            result_fields=[_row(name="r", type="int", doc="A result.")],
            py_return_type="float",
            record_name="Rec",
        ),
        _row(
            name="feed",
            arg_type="float",
            return_type="void",
            extra_args=[_row(name="g", type="double", default="1.0")],
        ),
    ],
    "properties": [
        _row(
            name="level",
            doc="The level.",
            type="double",
            enum="mode",
            buf_field="buf",
            len_field="n_buf",
            valid_field="ok",
            expr="state->level",
            value_type="double",
            count_fn="o_count",
            key_fn="o_key",
            value_fn="o_value",
            capsule="p.level",
        )
    ],
    "extra_methods": [
        _row(
            name="peek",
            fn="Obj_peek",
            flags="METH_NOARGS",
            args="",
            returns="float",
            doc="Peek.",
            # Not a key: kept by the save so `_keys` can name it (gh-1997).
            nargs=0,
        )
    ],
    "warnings": [
        _row(
            after="__init__",
            condition="state->n > 4",
            category="UserWarning",
            message="many",
        )
    ],
    "views": [
        _row(
            class_name="View",
            create_fn="o_view_create",
            doc="A view.",
            init_params=[
                _row(
                    name="k",
                    type="int",
                    default="2",
                    doc="K.",
                    derived=["a", "b"],
                )
            ],
            exclude_properties=["level"],
            exclude_methods=["run"],
            create_error="ValueError",
            create_error_message="bad view",
            warnings=[
                _row(
                    after="__init__",
                    condition="k > 2",
                    category="UserWarning",
                    message="k",
                )
            ],
            methods=[_row(name="vrun", arg_type="float", return_type="float")],
            properties=[_row(name="vlevel", type="double")],
        )
    ],
}

#: A composer module: every sub-table `_dump_composer_subtables` writes.
_COMPOSER = {
    "kind": "composer",
    "backing": "wfm",
    "capsule_name": "p.wfm",
    "package": "pk",
    "header": "wfm/wfm.h",
    "composes": ["nco"],
    "depends_on": ["nco"],
    "init_params": [_row(name="fs", type="double", default="1.0")],
    "properties": [_row(name="rate", type="double")],
    "source": _row(
        object="wfm_synth",
        struct="wfm_source_t",
        type_name="Synth",
        fields=[
            _row(
                name="freq",
                type="double",
                enum="wfm_type",
                default="0",
                c_ptr="sync.bits",
                c_len="sync.len",
                aliases=["f"],
                coerce="hex",
                coerce_str_fn="wfm_bits_parse",
                object="frame",
                copy_fn="frame_copy",
                doc="Frequency.",
            )
        ],
        computed=[_row(name="dur", type="double", fn="wfm_dur", doc="D.")],
        ranged=[_row(name="freq", flag="WFM_RANGE_FREQ")],
        generates=_row(
            generator="nco",
            bridge_fn="wfm_bridge",
            bridge_error_fn="wfm_bridge_why",
            state_type="nco_state_t",
            steps_fn="nco_steps",
            step_fn="nco_step",
            reset_fn="nco_reset",
            destroy_fn="nco_destroy",
            header="nco/nco_core.h",
            output_type="float _Complex",
        ),
    ),
    "segment": _row(
        type_name="Seg",
        struct="wfm_seg_t",
        sources="wfm_source_t",
        sources_member="srcs",
        count_member="n_srcs",
        fields=[_row(name="gap", type="double")],
        ranged=[_row(name="gap", flag="WFM_RANGE_GAP")],
    ),
    "timeline": _row(type_name="Timeline", loop=["a", "b"]),
    "oo": _row(
        factories=["mk"],
        emit="emit_fn",
        discriminant="kind",
        composer_type_name="Composer",
    ),
    "composer": _row(realtime={"clk_new": "wfm_clk_new"}),
    "json": _row(
        enabled=True,
        to_json_fn="wfm_to_json",
        from_json_fn="wfm_from_json",
        from_file_fn="wfm_from_file",
        header="cjson/cJSON.h",
        include_dir="vendor/cjson",
        to_json_trailing=["seed"],
    ),
    "serializers": [
        _row(
            name="to_sigmf",
            fn="wfm_to_sigmf",
            returns="str",
            header="wfm/sigmf.h",
            params=[_row(name="path", type="const char *")],
        )
    ],
    "settings": [
        _row(
            name="gain",
            setter_fn="wfm_set_gain",
            getter_fn="wfm_get_gain",
            type="double",
            enum="mode",
        )
    ],
}

#: A handle module, with `capsule` -- an accepted key no branch writes, so
#: `_module_leftover_lines` is entered.
_HANDLE = {
    "kind": "handle",
    "backing": "ring",
    "type_name": "Ring",
    "create_fn": "ring_open",
    "init_fn": "ring_init",
    "close_fn": "ring_close",
    "handle_type": "ring_t *",
    "optional_backend": "RING",
    "serializable": "ring_state",
    "capsule": "p.ring",
    "create_args": [
        _row(name="size", type="int", enum="mode", default="8", kwonly=True)
    ],
    "create_post": [_row(fn="ring_start", when="size", arg="size")],
    "methods": [
        _row(
            name="read",
            fn="ring_read",
            returns="int",
            args=[_row(name="n", type="int", default="1", kwonly=True)],
        )
    ],
    "getters": [
        _row(
            fn="ring_stat",
            out="ring_stat_t",
            fields=[_row(name="used", type="size_t", getter="ring_used")],
        )
    ],
}

#: Every shape `_dump` writes, across the fixtures below. One manifest cannot
#: hold them all -- an `[[app]]` row names a function OR an object, and a
#: reserved section no branch renders is written by `_dump_generic` in
#: whichever of its four shapes it has -- so the later ones carry the other
#: branches.
FIXTURES: dict[str, dict] = {
    "full": {
        "project": {
            "name": "p",
            "version": "0.1.0",
            "c_format_command": "clang-format",
            "platforms": ["linux"],
            "bench": {"suite": "s", "runs": 3, "fast": True, "tags": ["a"]},
        },
        "enum": [_row(name="mode", values=["fast", "slow"])],
        "group": [
            _row(
                name="geom",
                fields=[_row(name="x", type="double", default="0.0")],
            )
        ],
        "module": {
            "filt": {
                "objects": ["o"],
                "package": "pk",
                "doc": "Filters.",
                "extra_types": ["T"],
                "extra_link_libs": ["m"],
                "extra_include_dirs": ["inc"],
                "reexports": {"sub": ["A"]},
                "functions": [
                    _row(
                        name="hello",
                        doc="Say it.",
                        return_type="double",
                        out_type="double",
                        out_size="n",
                        max_results_param="m",
                        result_fields=[_row(name="r", type="int")],
                        params=[
                            _row(
                                name="s",
                                type="const char *",
                                default="NULL",
                                capsule="p.cap",
                                header="p/cap.h",
                                out=True,
                            )
                        ],
                        inline=True,
                        why=True,
                        impl_file="native/legacy.c",
                    )
                ],
            },
            "dsp.filters": {"objects": ["o2"]},
            "core": {"functions_in_core": True},
            "cap": {
                "kind": "capsule",
                "backing": "buf",
                "capsule_name": "p.buf",
                "header": "buf/buf.h",
                "depends_on": [_row(name="nco", link=True, test_only=True)],
                "init_params": [_row(name="n", type="int", default="4")],
                "methods": [
                    _row(name="push", arg_type="float", return_type="int")
                ],
                "properties": [_row(name="size", type="size_t")],
            },
            "wfm": _COMPOSER,
            "ring": _HANDLE,
        },
        "o": _OBJECT,
        "app": [
            {
                "target": "cli",
                "name": "tool",
                "function": "hello",
                "module": "filt",
                "flags": [
                    _row(
                        name="gain", type="double", default="1.0", help="Gain."
                    )
                ],
                "commands": [
                    _row(
                        name="run",
                        help="Run.",
                        flags=[
                            _row(
                                name="n",
                                type="int",
                                default="1",
                                help="How many.",
                            )
                        ],
                    )
                ],
            }
        ],
        "codec": {"blue": {"entries": ["v"], "label": "Blue"}},
    },
    "object_app": {
        "project": {"name": "p", "version": "0.1.0"},
        "o": {"arg_type": "float", "return_type": "float"},
        "app": [
            {
                "target": "console",
                "name": "tool",
                "object": "o",
                "module": "filt",
            }
        ],
        "template": [_row(name="t", body="x")],
        "codec": {"label": "Blue"},
    },
    # Alone, because a bare key appended after any table would bind to it.
    "generic_scalar": {"template": "x"},
}


# ---------------------------------------------------------------------------
# The round trip


def _string_leaves(node: object, path: tuple = ()) -> "list[tuple]":
    """Every path in *node* that ends at a string, in document order."""
    if isinstance(node, dict):
        return [
            p
            for k, v in node.items()
            if k not in _GRAMMAR_KEYS
            for p in _string_leaves(v, path + (k,))
        ]
    if isinstance(node, list):
        return [
            p
            for i, v in enumerate(node)
            for p in _string_leaves(v, path + (i,))
        ]
    return [path] if isinstance(node, str) else []


def _get(node: object, path: tuple) -> object:
    for key in path:
        node = node[key]  # type: ignore[index]
    return node


def _with(cfg: dict, path: tuple, value: str) -> dict:
    out = copy.deepcopy(cfg)
    _get(out, path[:-1])[path[-1]] = value  # type: ignore[index]
    return out


_CASES = [
    pytest.param(name, path, id=f"{name}:{'/'.join(map(str, path))}")
    for name, cfg in FIXTURES.items()
    for path in _string_leaves(cfg)
]


def test_the_round_trip_is_armed() -> None:
    """A sweep over no leaves passes. Name the sites the issue reported, so
    an emptied fixture cannot read as green."""
    ids = {p.id for p in _CASES}
    for want in (
        "full:o/methods/0/params/0/default",  # the issue's trigger
        "full:module/filt/functions/0/params/0/default",  # function face
        "full:module/cap/init_params/0/default",  # capsule init_params
        "full:module/wfm/source/fields/0/default",  # composer fields
        "full:module/ring/create_args/0/default",  # handle create_args
        "full:o/state/0/default",  # [[state]]
        "full:app/0/flags/0/default",  # [[app.flags]]
    ):
        assert want in ids, want
    assert len(_CASES) > 200, len(_CASES)


@pytest.mark.parametrize("fixture,path", _CASES)
def test_every_string_survives_dump(fixture: str, path: tuple) -> None:
    """The value written is the value read back.

    `_dump` re-parses its own output and raises "cannot read back" on bad
    TOML (gh-844), so a raw quote or backslash fails here inside `_dump`. A
    raw newline is the same. What `_dump`'s own check cannot see is a value
    that parses but MEANS something else -- `\\s` is not a TOML escape, but
    `\\n` is, and a C body's `"a\\n"` used to come back holding a newline --
    so the comparison is the half that catches that.

    The one difference allowed is gh-192's: the ``\"\"\"`` form of a
    multi-line value reads back with one trailing newline, which the next
    save strips again, so repeated saves are byte-stable. That form only
    appears for a value that has a newline in it, which this one does.
    """
    cfg = _with(FIXTURES[fixture], path, HOSTILE)
    text = C._dump(cfg)
    got = _get(tomllib.loads(text), path)
    if got == HOSTILE + "\n":
        assert '"""' in text, text
        return
    assert got == HOSTILE, f"{'/'.join(map(str, path))}: {got!r}"


# ---------------------------------------------------------------------------
# The reach ratchet


def _escapers() -> "set[str]":
    """gh-844's definition of an escaper, read off the module.

    `test_gh844_toml_escaping` checks each of these against every character
    TOML forbids; this file checks that every key reaches one.
    """
    return {
        name
        for name in dir(C)
        if (
            name.startswith("_toml_") or name in ("_str_assign", "_doc_assign")
        )
        and callable(getattr(C, name))
    }


def _module_tree() -> "tuple[str, dict[str, ast.FunctionDef]]":
    src = Path(C.__file__).read_text(encoding="utf-8")
    funcs = {
        n.name: n
        for n in ast.parse(src).body
        if isinstance(n, ast.FunctionDef)
    }
    return src, funcs


def _call_graph(roots: "tuple[str, ...]") -> "set[str]":
    """The module-level `_config` functions *roots* reach, by name.

    Derived, so a helper `_dump` starts calling tomorrow is in it tomorrow.
    A name merely mentioned (passed as a value) counts as reached, which is
    the conservative reading.
    """
    _src, funcs = _module_tree()
    seen: "set[str]" = set()
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += [
            n.id
            for n in ast.walk(funcs[name])
            if isinstance(n, ast.Name) and n.id in funcs and n.id not in seen
        ]
    return seen


def _escaper_call_sites() -> "list[tuple[int, int, str, str]]":
    """``(first line, last line, function, source)`` of every escaper call
    in `_dump`'s call graph, outside the escapers themselves."""
    src, funcs = _module_tree()
    escapers = _escapers()
    sites = []
    for name in sorted(_call_graph(("_dump",)) - escapers):
        for node in ast.walk(funcs[name]):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in escapers
            ):
                seg = ast.get_source_segment(src, node) or ""
                sites.append(
                    (node.lineno, node.end_lineno or node.lineno, name, seg)
                )
    return sites


def test_the_fixtures_reach_every_escaper_call(monkeypatch) -> None:
    """The round trip is only as good as the manifests it dumps.

    Every escaper is wrapped to record the line that called it, the
    fixtures are dumped, and each call site must have recorded a line inside
    its span. A site no fixture reaches is a key whose escaping nothing here
    checks: add the row that enters its branch.
    """
    here = Path(C.__file__).resolve()
    hit: "set[int]" = set()
    for name in _escapers():
        real = getattr(C, name)

        def _record(*args, _real=real, **kwargs):
            caller = sys._getframe(1)
            if Path(caller.f_code.co_filename).resolve() == here:
                hit.add(caller.f_lineno)
            return _real(*args, **kwargs)

        monkeypatch.setattr(C, name, _record)
    for cfg in FIXTURES.values():
        C._dump(copy.deepcopy(cfg))
    sites = _escaper_call_sites()
    assert len(sites) > 20, "no escaper call sites found -- gate not armed"
    missed = [
        f"{fn}:{lo}: {seg}"
        for lo, hi, fn, seg in sites
        if not any(lo <= ln <= hi for ln in hit)
    ]
    assert not missed, "no fixture reaches:\n" + "\n".join(missed)


# ---------------------------------------------------------------------------
# The source gate


def _parts(node: ast.AST) -> "list[object]":
    """*node* as the literal text and the interpolations it concatenates.

    A string literal contributes its text; an f-string its literal pieces
    and one marker per ``{...}``; a ``+`` chain both sides in order. Anything
    else is a value computed at run time, so a marker.
    """
    if isinstance(node, ast.JoinedStr):
        return [
            v.value if isinstance(v, ast.Constant) else None
            for v in node.values
        ]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _parts(node.left) + _parts(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return [None]


def _quotes_by_hand(node: ast.AST) -> bool:
    """True when a run-time value lands between two ``"`` of TOML text.

    Tracks whether the text so far is inside a basic string; an escaped
    quote (``\\"``) inside one does not end it.
    """
    inside = False
    for part in _parts(node):
        if part is None:
            if inside:
                return True
            continue
        i = 0
        text = str(part)
        while i < len(text):
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == '"':
                inside = not inside
            i += 1
    return False


def test_no_serializer_string_is_quoted_by_hand() -> None:
    """The shape every one of the 130-odd sites had, refused at the source.

    The serializer is every `_config` function reached from the three that
    write manifest text -- `_dump`, `_write_doc` and `stamp_jm_version` --
    less the escapers, which are where quoting is supposed to happen.
    """
    src, funcs = _module_tree()
    serializer = _call_graph(("_dump", "_write_doc", "stamp_jm_version"))
    assert {"_dump", "_method_dump_lines", "_inline_field"} <= serializer
    offenders = []
    for name in sorted(serializer - _escapers()):
        for node in ast.walk(funcs[name]):
            if isinstance(node, (ast.JoinedStr, ast.BinOp)) and (
                _quotes_by_hand(node)
            ):
                seg = ast.get_source_segment(src, node) or ""
                offenders.append(f"{name}:{node.lineno}: {seg}")
    assert not offenders, (
        "a serializer string quoted by hand -- route the value through "
        "`_toml_basic_string` (or `_toml_string_array` / `_toml_value`):\n"
        + "\n".join(sorted(set(offenders)))
    )


def test_the_source_gate_is_armed() -> None:
    """The detector must see the shape it exists to refuse, and not the
    shapes that only look like it: a quote it wrote itself, and an escaped
    quote inside a string that closes before the value."""
    for bad in (
        r"""f'k = "{v}"'""",
        r"""'k = "' + v + '"'""",
        r"""f'"{x}"'""",
        r'''f'k = """\n{v}\n"""' ''',
    ):
        assert _quotes_by_hand(ast.parse(bad.strip(), mode="eval").body), bad
    for good in (
        r"""f"k = {_toml_basic_string(v)}" """,
        r"""'no_generate = "true"'""",
        r"""'k = "a\\"b" # ' + v""",
    ):
        tree = ast.parse(good.strip(), mode="eval").body
        assert not _quotes_by_hand(tree), good


# ---------------------------------------------------------------------------
# The issue's trigger, end to end

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None

#: The spelling `_types._join_fmt_with_optional`'s refusal recommends for an
#: empty-string default (gh-1271). It failed the same way: `_dump` wrote
#: `default = """"`, an unterminated multi-line string.
_EMPTY_DEFAULT = """
[[module.m.functions]]
name = "blank"
return_type = "int"
params = [{ name = "s", type = "const char *", default = '""' }]
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """The issue's commands, on both faces, plus the documented spelling."""
    tmp = tmp_path_factory.mktemp("gh1886")
    assert run_cli("new", "t9", "--object", "o", cwd=tmp).returncode == 0
    root = tmp / "t9"
    out = run_cli(
        "method", "o", "greet", "--param", 's:const char *="hi"', cwd=root
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert run_cli("module", "m", cwd=root).returncode == 0
    out = run_cli(
        "function",
        "hello",
        "--module",
        "m",
        "--param",
        's:const char *="hi"',
        cwd=root,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    frag = root / "modules" / "m.toml"
    frag.write_text(
        frag.read_text(encoding="utf-8") + _EMPTY_DEFAULT, encoding="utf-8"
    )
    return root


def test_apply_reads_back_what_it_writes(project: Path) -> None:
    out = run_cli("apply", cwd=project)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "cannot read back" not in out.stdout + out.stderr
    out = run_cli("status", cwd=project)
    assert "cannot read back" not in out.stdout + out.stderr, out.stdout


def test_each_default_reaches_c_as_the_literal_declared(project: Path) -> None:
    """Not merely readable: the C local each binding seeds is the literal
    the author wrote, quotes and all."""
    assert run_cli("apply", cwd=project).returncode == 0
    srcs = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (project / "native" / "src").rglob("*_ext*.c")
    )
    assert srcs.count('const char * s = "hi";') == 2, srcs
    assert 'const char * s = "";' in srcs, srcs


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_the_project_builds_and_its_suite_passes(project: Path) -> None:
    """What "works" means for a default: it compiles, and the generated
    tests call the binding with it."""
    assert run_cli("apply", cwd=project).returncode == 0
    out = run_cli("test", cwd=project)
    assert out.returncode == 0, out.stdout + out.stderr
