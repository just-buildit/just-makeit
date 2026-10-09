"""gh-2045 / gh-2046 / gh-2036 / gh-2047: `_dump` writes back every key it is
given, as the type it is.

`_dump` is the writer behind every whole-manifest rewrite -- a brand-new
fragment, `jm split-objects`, `jm migrate-to-fragments`, a replay's scratch
tree, an install without tomlkit. It was thirty hand-written emitters, one per
table, each writing the keys it had been taught, and on origin/main:

* gh-2045: a method's and a module function's params lost `doc`, `enum`,
  `out`, `rank`, `elements_per_sample` and `str_hint`;
* gh-2046: `streamable = true` came back `streamable = "True"`, which
  `_truthy` reads as false, so `stream()` silently went away;
* gh-2036: an `[[<obj>.array_args]]` row's `dtype` came back as `type`;
* gh-2047: a root scalar was written after the tables, binding to the last
  one, and a key needing quotes was written bare.

The census that measured those found the same class one table over: an
object's `records`, `fragment` and `*_impl_file`, a method's `status_errors`
and `releases`, a plain module's `platforms`, an `[[enum]]` row's
`enumerators`. Now there is one emitter; it writes every key a table holds, and
reads its text back, refusing one that means anything else.

GATE: every key a `_keys` vocabulary accepts survives `_dump`, as the type
      it is, in either layout. Four parts, registration-free where the
      source can say it:

1. **Placement, held to `_keys`.** Every vocabulary in `_keys.KIND_KEYS` has a
   place in a manifest here, and `_keys.unknown_keys` reports a probe key
   planted there under that very kind: the place is where `_keys` reads the
   vocabulary, not a guess. Every `C.RESERVED_SECTIONS` section has one too.
2. **The writer: every key, every type.** For each vocabulary, each key it
   accepts and each TOML value type, `_dump`'s text reads back as the
   manifest given -- and a key that needs quoting (gh-2047) does too.
3. **load -> _dump -> load, both layouts.** The manifest is written by an
   INDEPENDENT writer (tomlkit), `C.load`ed, rewritten through `_dump` --
   saved into an empty root (central), and by `jm migrate-to-fragments`
   (fragments) -- and loaded again: equal, for every example value of every
   key that `C.load` accepts.
4. The four issues' own shapes, by name. Their end-to-end trigger -- a real
   verb rewriting the manifest, then `status --check` -- is the
   `central-keys` shape of `test_gh2057_verb_leaves_what_apply_writes`, which
   runs `split-objects` and `migrate-to-fragments` on it.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import tomlkit

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _keys  # noqa: E402
from just_makeit import _migrate  # noqa: E402
from just_makeit._report import Refusal  # noqa: E402

tomllib = C.tomllib

_PROJECT = {"name": "p", "version": "0.1.0"}
_OBJECT = {"arg_type": "float", "return_type": "float"}
_RUN = {"name": "run", "arg_type": "float", "return_type": "float"}
_HANDLE = {"kind": "handle", "backing": "ring"}
_CAPSULE = {"kind": "capsule", "backing": "buf"}
_COMPOSER = {"kind": "composer", "backing": "wfm"}
_GEOM = [{"name": "geom", "fields": [{"name": "x", "type": "double"}]}]
#: An app with its own rows, which `[[app]]` (gh-2074) nests one level down.
_APP = {
    "target": "c",
    "name": "t",
    "flags": [{"name": "g", "type": "double"}],
    "commands": [{"name": "run", "flags": [{"name": "n", "type": "int"}]}],
}


def _obj(**tables: object) -> dict:
    return {"project": dict(_PROJECT), "o": {**_OBJECT, **tables}}


def _mod(name: str, data: dict) -> dict:
    return {"project": dict(_PROJECT), "module": {name: data}}


def _handle(**tables: object) -> dict:
    return _mod("h", {**_HANDLE, **tables})


def _capsule(**tables: object) -> dict:
    return _mod("c", {**_CAPSULE, **tables})


def _composer(**tables: object) -> dict:
    return _mod("w", {**_COMPOSER, **tables})


#: `_keys` vocabulary -> (the least row of it, where such a row lives).
#: Arrays where a row may be one, because `str_hint`, `rank` and
#: `elements_per_sample` are refused at load on anything else.
PLACES = {
    "object": (dict(_OBJECT), lambda r: {"project": dict(_PROJECT), "o": r}),
    "state": (
        {"name": "buf", "type": "float[8]"},
        lambda r: _obj(state=[r]),
    ),
    "init_param": (
        {"name": "taps", "type": "float[]"},
        lambda r: _obj(init_params=[r]),
    ),
    "init_group": (
        {"group": "geom"},
        lambda r: {**_obj(init_groups=[r]), "group": copy.deepcopy(_GEOM)},
    ),
    "array_arg": (
        {"name": "taps", "type": "float32"},
        lambda r: _obj(array_args=[r]),
    ),
    "method": (dict(_RUN), lambda r: _obj(methods=[r])),
    "param": (
        {"name": "x", "type": "float[]"},
        lambda r: _obj(methods=[{**_RUN, "params": [r]}]),
    ),
    "property": (
        {"name": "lvl", "type": "double"},
        lambda r: _obj(properties=[r]),
    ),
    "record": (
        {"name": "iq_t", "fields": [{"name": "i", "type": "int16_t"}]},
        lambda r: _obj(records=[r]),
    ),
    "record field": (
        {"name": "i", "type": "int16_t"},
        lambda r: _obj(records=[{"name": "iq_t", "fields": [r]}]),
    ),
    "extra_method": (
        {"name": "peek", "fn": "o_peek"},
        lambda r: _obj(extra_methods=[r]),
    ),
    "function": (
        {"name": "f"},
        lambda r: _mod("m", {"objects": [], "functions": [r]}),
    ),
    "function param": (
        {"name": "x", "type": "double[]"},
        lambda r: _mod(
            "m", {"objects": [], "functions": [{"name": "f", "params": [r]}]}
        ),
    ),
    "handle module": (dict(_HANDLE), lambda r: _mod("h", r)),
    "capsule module": (dict(_CAPSULE), lambda r: _mod("c", r)),
    "composer module": (dict(_COMPOSER), lambda r: _mod("w", r)),
    "handle method": (
        {"name": "read", "fn": "ring_read"},
        lambda r: _handle(methods=[r]),
    ),
    "capsule method": (
        {"name": "push", "arg_type": "float", "return_type": "int"},
        lambda r: _capsule(methods=[r]),
    ),
    "kind getter": (
        {"fn": "ring_stat", "out": "ring_stat_t"},
        lambda r: _handle(getters=[r]),
    ),
    "kind getter field": (
        {"name": "used", "type": "size_t"},
        lambda r: _handle(getters=[{"fn": "ring_stat", "fields": [r]}]),
    ),
    "kind factory": (
        {"name": "mk", "create_fn": "ring_mk"},
        lambda r: _handle(factories=[r]),
    ),
    "kind create_arg": (
        {"name": "size", "type": "int"},
        lambda r: _handle(create_args=[r]),
    ),
    "kind create_post": (
        {"fn": "ring_start"}, lambda r: _handle(create_post=[r]),
    ),
    "kind method arg": (
        {"name": "n", "type": "float[]"},
        lambda r: _handle(
            methods=[{"name": "read", "fn": "ring_read", "args": [r]}]
        ),
    ),
    "kind depends_on": ({"name": "nco"}, lambda r: _handle(depends_on=[r])),
    "kind property": (
        {"name": "size", "type": "size_t"},
        lambda r: _capsule(properties=[r]),
    ),
    "kind init_param": (
        {"name": "n", "type": "int"},
        lambda r: _capsule(init_params=[r]),
    ),
    "composer extra_method": (
        {"name": "peek", "fn": "w_peek"},
        lambda r: _composer(extra_methods=[r]),
    ),
    "kind serializer": (
        {"name": "to_x", "fn": "w_to_x"},
        lambda r: _composer(serializers=[r]),
    ),
    "kind setting": (
        {"name": "gain", "setter_fn": "w_set"},
        lambda r: _composer(settings=[r]),
    ),
    "[module.X.source]": ({"object": "s"}, lambda r: _composer(source=r)),
    "[module.X.source.generates]": (
        {"generator": "nco"},
        lambda r: _composer(source={"object": "s", "generates": r}),
    ),
    "[module.X.segment]": (
        {"type_name": "Seg"}, lambda r: _composer(segment=r),
    ),
    "[module.X.timeline]": (
        {"type_name": "T"}, lambda r: _composer(timeline=r),
    ),
    "[module.X.oo]": ({"emit": "e"}, lambda r: _composer(oo=r)),
    "[module.X.composer]": ({"stream": True}, lambda r: _composer(composer=r)),
    "[module.X.json]": ({"enabled": True}, lambda r: _composer(json=r)),
    "[module.X.cli]": ({"enabled": True}, lambda r: _composer(cli=r)),
    "composer field": (
        {"name": "gap", "type": "double"},
        lambda r: _composer(segment={"type_name": "Seg", "fields": [r]}),
    ),
    "composer source field": (
        {"name": "freq", "type": "double"},
        lambda r: _composer(source={"object": "s", "fields": [r]}),
    ),
    "composer computed": (
        {"name": "dur", "type": "double", "fn": "w_dur"},
        lambda r: _composer(source={"object": "s", "computed": [r]}),
    ),
}  # fmt: skip

#: A second least row, for keys `C.load` takes only beside their siblings:
#: a macro family is `core_macro`, `core_args` and `core_header` together, on
#: a header-only object (gh-1310).
SIBLINGS = {
    "object": {
        **_OBJECT,
        "header_only": "true",
        "core_macro": "O_CORE",
        "core_args": ["n"],
        "core_header": "p/o/o_family.h",
    },
}

#: Vocabularies `_keys` registers and walks nowhere, so no manifest place
#: reads them. Each keeps a key set the writer is still held to through
#: another vocabulary.
UNWALKED = {
    # gh-1190: a composer has no `methods` table (`methods` is refused on
    # one); the vocabulary is the capsule method's, which is placed.
    "composer method": "capsule method",
}

#: Every top-level section that is not a component, holding a key no
#: vocabulary names. `app` in both spellings: one table through schema 8,
#: one row per app from gh-2074.
RESERVED = {
    "project": lambda v: {"project": {**_PROJECT, "probe": v}},
    "module": lambda v: _mod("m", {"objects": [], "probe": v}),
    "app": lambda v: {
        "project": dict(_PROJECT),
        "app": {**_APP, "probe": v},
    },
    "app rows": lambda v: {
        "project": dict(_PROJECT),
        "app": [{**_APP, "probe": v}, {**_APP, "name": "u"}],
    },
    "enum": lambda v: {
        "project": dict(_PROJECT),
        "enum": [{"name": "mode", "values": ["a"], "probe": v}],
    },
    "codec": lambda v: {
        "project": dict(_PROJECT), "codec": {"x": {"probe": v}},
    },
    "group": lambda v: {
        "project": dict(_PROJECT),
        "group": [{"name": "g", "fields": [], "probe": v}],
    },
    "template": lambda v: {
        "project": dict(_PROJECT),
        "template": {"t": {"params": [], "probe": v}},
    },
}  # fmt: skip

#: One value of every TOML type. The array is ``["linux"]`` because
#: `platforms` refuses any other string and every other array takes any.
EXAMPLES = {
    "str": "text",
    "true": True,
    "false": False,
    "int": 7,
    "float": 2.5,
    "array": ["linux"],
    "rows": [{"name": "a", "n": 1}],
    "table": {"k": "v", "on": True},
}

#: A key TOML cannot read bare: a dot, a space and a quote (gh-2047).
ODD_KEY = 'a.b "c"'


def _heredoc(kind: str, cfg: dict, key: str, value: object) -> dict:
    """*cfg* as a heredoc reads back: gh-192's one trailing newline on an
    object's C body. The one difference `_dump` makes on purpose, read from
    where it is declared rather than restated."""
    if (
        kind == "object"
        and isinstance(value, str)
        and f"<obj>.{key}" in C._HEREDOC_SHAPES
    ):
        cfg = copy.deepcopy(cfg)
        cfg["o"][key] = value + "\n"
    return cfg


# ---------------------------------------------------------------------------
# 1. Placement


def test_every_vocabulary_has_a_place() -> None:
    """A vocabulary added to `_keys` without a place here fails, rather than
    going unchecked by everything below."""
    assert set(PLACES) | set(UNWALKED) == set(_keys.KIND_KEYS)
    for kind, stands_in in UNWALKED.items():
        assert _keys.KIND_KEYS[kind] == _keys.KIND_KEYS[stands_in], kind


@pytest.mark.parametrize("kind", sorted(PLACES))
def test_each_place_is_where_keys_reads_that_kind(kind: str) -> None:
    """The oracle: a probe key planted in the row is reported by
    `_keys.unknown_keys` as an unknown key of exactly this vocabulary."""
    minimal, place = PLACES[kind]
    found = _keys.unknown_keys(place({**minimal, "gh2045_probe": 1}))
    assert [u.kind for u in found if u.key == "gh2045_probe"] == [kind]


def test_an_unwalked_vocabulary_really_is_unwalked() -> None:
    """If `_keys` starts reading a composer's methods, it needs a place."""
    found = _keys.unknown_keys(
        _composer(methods=[{"name": "m", "gh2045_probe": 1}])
    )
    assert not [u for u in found if u.kind in UNWALKED]


def test_every_reserved_section_has_a_place() -> None:
    assert {k.split()[0] for k in RESERVED} == set(C.RESERVED_SECTIONS)


# ---------------------------------------------------------------------------
# 2. The writer: every key, every type


def _reads_back(cfg: dict, want: dict) -> "str | None":
    try:
        got = tomllib.loads(C._dump(copy.deepcopy(cfg)))
    except ValueError as exc:
        return f"refused: {exc}"
    return None if got == want else f"read back {got!r}"


@pytest.mark.parametrize("kind", sorted(PLACES))
def test_every_key_of_every_type_reads_back(kind: str) -> None:
    minimal, place = PLACES[kind]
    bad = []
    for key in sorted(_keys.KIND_KEYS[kind]) + [ODD_KEY]:
        for label, value in EXAMPLES.items():
            cfg = place({**minimal, key: copy.deepcopy(value)})
            why = _reads_back(cfg, _heredoc(kind, cfg, key, value))
            if why:
                bad.append(f"{key} = <{label}>: {why}")
    assert not bad, f"{kind}:\n" + "\n".join(bad)


@pytest.mark.parametrize("section", sorted(RESERVED))
def test_a_reserved_section_keeps_any_key(section: str) -> None:
    """No vocabulary names these keys, and none has to: a key is written
    because the table holds it."""
    bad = []
    for label, value in EXAMPLES.items():
        for key in ("probe", ODD_KEY):
            cfg = _rekey(RESERVED[section](copy.deepcopy(value)), "probe", key)
            why = _reads_back(cfg, cfg)
            if why:
                bad.append(f"{key} = <{label}>: {why}")
    assert not bad, f"{section}:\n" + "\n".join(bad)


def _rekey(node: object, old: str, new: str) -> object:
    """*node* with every *old* key renamed *new*."""
    if isinstance(node, dict):
        return {
            (new if k == old else k): _rekey(v, old, new)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_rekey(v, old, new) for v in node]
    return node


# ---------------------------------------------------------------------------
# 3. load -> _dump -> load, in both layouts


def _write_central(root: Path, cfg: dict) -> None:
    """*cfg* as a central manifest, by tomlkit: a writer that is not the one
    under test, so a fault in `_dump` cannot also be in the original."""
    root.mkdir()
    (root / C.FILENAME).write_text(tomlkit.dumps(cfg), encoding="utf-8")


def _loads(root: Path) -> "dict | None":
    """`C.load`, or None where it refuses the manifest -- a value of a type
    the key does not take, which is the reader's business, not this test's.
    The save-side name gate counts as the reader."""
    try:
        cfg = C.load(root)
        C.require_declared_names(cfg)
    except (
        SystemExit,
        Refusal,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ):
        return None
    return cfg


@pytest.mark.parametrize("kind", sorted(PLACES))
def test_load_dump_load_in_both_layouts(
    kind: str, tmp_path: Path, capsys
) -> None:
    minimal, place = PLACES[kind]
    rows = [minimal] + ([SIBLINGS[kind]] if kind in SIBLINGS else [])
    bad, untested, n = [], [], 0
    for key in sorted(_keys.KIND_KEYS[kind]):
        loaded = 0
        for (label, value), least in (
            (example, row) for row in rows for example in EXAMPLES.items()
        ):
            if loaded and least is not minimal:
                break
            n += 1
            src = tmp_path / f"src{n}"
            _write_central(src, place({**least, key: copy.deepcopy(value)}))
            before = _loads(src)
            if before is None:
                continue
            loaded += 1
            want = _heredoc(kind, before, key, value)
            central = tmp_path / f"central{n}"
            central.mkdir()
            C.save(central, before)
            if C.load(central) != want:
                bad.append(f"central: {key} = <{label}>")
            _migrate.run(src)
            assert (src / "just-makeit.toml").read_text().startswith("include")
            if C.load(src) != want:
                bad.append(f"fragments: {key} = <{label}>")
        if not loaded:
            untested.append(key)
    capsys.readouterr()
    assert not bad, f"{kind}:\n" + "\n".join(bad)
    assert not untested, (
        f"{kind}: `C.load` refused every example for {untested}, so nothing"
        " here round-trips them -- give the place's least row what they need"
    )


# ---------------------------------------------------------------------------
# 4. The issues, by name


@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize(
    "key",
    sorted(
        {
            "streamable", "async_stream", "serializable", "process_global",
            "header_only", "no_reset", "opaque_state", "mutable", "no_state",
            "no_step", "step_delegates_to_steps",
        }
    ),
)  # fmt: skip
def test_gh2046_a_boolean_stays_a_boolean(key: str, value: bool) -> None:
    """`_truthy` reads the same answer before and after, because the value
    read back IS the value: `true`, never `"True"`."""
    cfg = _obj(**{key: value})
    back = tomllib.loads(C._dump(cfg))["o"][key]
    assert back is value
    assert C._truthy(back) is C._truthy(value)


def test_gh2046_an_integer_stays_an_integer() -> None:
    back = tomllib.loads(C._dump(_obj(stream_block_default=4096)))
    assert back["o"]["stream_block_default"] == 4096


@pytest.mark.parametrize("spelling", ["type", "dtype"])
def test_gh2036_array_args_row_is_written_as_spelt(spelling: str) -> None:
    """Byte for byte: the text `_dump` writes, read and written again, is
    the same text, and it keeps the author's spelling."""
    cfg = _obj(array_args=[{"name": "taps", spelling: "float32"}])
    text = C._dump(cfg)
    assert f'{spelling} = "float32"' in text
    assert C._dump(tomllib.loads(text)) == text
    assert tomllib.loads(text) == cfg


@pytest.mark.parametrize("face", ["method", "function"])
def test_gh2045_a_param_keeps_every_key(face: str) -> None:
    """The issue's list, on both faces."""
    param = {
        "name": "x",
        "type": "float[]",
        "doc": "The x.",
        "enum": "mode",
        "out": True,
        "rank": 1,
        "elements_per_sample": 2,
        "str_hint": "pass an array",
    }
    if face == "method":
        cfg = _obj(methods=[{**_RUN, "params": [param]}])
        got = tomllib.loads(C._dump(cfg))["o"]["methods"][0]["params"][0]
    else:
        cfg = _mod(
            "m",
            {"objects": [], "functions": [{"name": "f", "params": [param]}]},
        )
        got = tomllib.loads(C._dump(cfg))["module"]["m"]["functions"][0][
            "params"
        ][0]
    assert got == param


def test_gh2047_a_root_scalar_stays_at_the_root() -> None:
    """Written after a table, TOML binds it to that table."""
    cfg = {"project": dict(_PROJECT), "template": "x"}
    assert tomllib.loads(C._dump(cfg)) == cfg
    assert tomllib.loads(C._dump_generic("template", "x")) == {"template": "x"}


def test_gh2047_a_key_that_needs_quotes_keeps_its_meaning() -> None:
    """A re-exported sub-package named with a dot is one key, not a nested
    table; a codec key with a space is not a parse error."""
    cfg = _mod(
        "m",
        {"objects": [], "reexports": {"dsp.filters": ["Fir"], "a b": ["X"]}},
    )
    cfg["codec"] = {"blue": {"odd key": 1, 'q"uote': "v"}}
    assert tomllib.loads(C._dump(cfg)) == cfg


def test_a_value_toml_cannot_hold_is_refused_by_name() -> None:
    """The self-check: TOML has no null, so a None inside an array cannot
    be written -- and `_dump` says where, instead of writing ``"None"``."""
    with pytest.raises(ValueError, match=r"o\.core_args\[1\]"):
        C._dump(_obj(core_args=["n", None]))


def test_what_is_not_written_is_named() -> None:
    """`_declared`'s three, and nothing else: a ``_`` key, a None value, and
    a group's expansion (its declaration is what persists, gh-999)."""
    cfg = _obj(
        doc=None,
        _doc_blocks=["x"],
        init_params=[{"name": "geom_x", "type": "double", "_group": "geom"}],
        init_groups=[{"group": "geom"}],
    )
    assert tomllib.loads(C._dump(cfg)) == _obj(init_groups=[{"group": "geom"}])


def test_a_name_beginning_with_an_underscore_is_written() -> None:
    """A ``_`` KEY is run-time state; a ``_`` NAME is the author's. `_x` is a
    valid object name and `_m` a valid module id, and both are kept."""
    cfg = {
        "project": dict(_PROJECT),
        "module": {"_m": {"objects": ["_x"]}},
        "_x": dict(_OBJECT),
    }
    assert tomllib.loads(C._dump(cfg)) == cfg
