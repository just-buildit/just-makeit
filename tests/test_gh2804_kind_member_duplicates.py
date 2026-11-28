"""gh-2804: a member name twice in a module kind is refused, not compiled.

gh-2800 refused a name twice in a handle's, capsule's or composer's
PARAMETER lists. A kind's member lists were not walked, and several of them
render one C function per name, so a repeat applied cleanly and failed in
the compiler -- measured by building the bundled ``composites`` and
``composer_seams`` examples with each repeat:

- a handle getter field, in one ``getters`` table or across two
  (``redefinition of 'Ring_get_fill_fraction'``);
- a handle method (``redefinition of 'Ring_pop'``) and factory
  (``redefinition of 'ring_ring_new'``);
- a capsule method (``redefinition of '_fn_cb_reset'``), found by the
  derived test below and then built;
- a composer source ``computed`` property named twice
  (``redefinition of 'Clip_get_duration'``), and a serializer
  (``redefinition of 'Mix_to_json'``).

A computed property named after a stored source field was refused, but by
a ``ValueError`` raised mid-render, as a traceback.

The first test derives its cases from the rendered C: every list of named
rows in a kind's fixture gets a repeat, and wherever the render then
defines one C function twice, ``duplicate_name_errors`` must refuse it. A
member list a kind grows later is held here without being listed.
"""

from __future__ import annotations

import copy
import hashlib
from collections import Counter
from pathlib import Path

import pytest
from _jmrun import run_cli
from test_gh1525_kind_arg_local_names import _FUNC, _KINDS

from just_makeit import _composer
from just_makeit import _config as C


def _with_members(kind: str) -> dict:
    """The gh-1525 fixture of *kind*, plus the member lists it lacks: a
    handle's ``getters`` (a struct table and a scalar one), a capsule's
    ``properties``, and a composer's ``settings``, ``extra_methods`` and
    source ``computed`` properties (gh-2838: every table the kind renders,
    held by ``test_the_fixture_declares_every_table_a_kind_renders``)."""
    gen, make_cfg, module = _KINDS[kind]
    cfg = make_cfg()
    sec = cfg["module"][module]
    # Build wiring, not source: a row here renders no C, but a table the
    # schema renders is declared, so the gate below can say it was tried.
    sec["depends_on"] = [{"name": "zzdep", "link": True}]
    if kind == "handle":
        sec["getters"] = [
            {
                "fn": "hb_stats",
                "out": "hb_stats_t",
                "fields": [
                    {"name": "used", "type": "size_t"},
                    {"name": "free", "type": "size_t"},
                ],
            },
            {
                "fn": "hb_gain",
                "out": "float",
                "fields": [{"name": "gain", "type": "float"}],
            },
        ]
    elif kind == "capsule":
        sec["properties"] = [
            {"name": "gain", "type": "double", "writable": True},
            {"name": "rate", "type": "double"},
        ]
    elif kind == "composer":
        sec["source"]["computed"] = [
            {"name": "span", "type": "double", "fn": "wc_span"},
            {"name": "rate", "type": "double", "fn": "wc_rate"},
        ]
        sec["settings"] = [
            {
                "name": "mode",
                "type": "int",
                "setter_fn": "wc_set_mode",
                "getter_fn": "wc_get_mode",
            },
            {
                "name": "depth",
                "type": "int",
                "setter_fn": "wc_set_depth",
                "getter_fn": "wc_get_depth",
            },
        ]
        sec["extra_methods"] = [
            {"name": "draws", "fn": "Wc_draws", "flags": "METH_NOARGS"},
            {"name": "peaks", "fn": "Wc_peaks", "flags": "METH_NOARGS"},
        ]
    return cfg


def _named_lists(node, path=()):
    """The path of every list of named rows under *node*, at any depth."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _named_lists(v, (*path, k))
    elif isinstance(node, list):
        if node and all(
            isinstance(r, dict) and isinstance(r.get("name"), str)
            for r in node
        ):
            yield path
        for i, v in enumerate(node):
            yield from _named_lists(v, (*path, i))


def _at(node, path):
    for step in path:
        node = node[step]
    return node


def _twice(text: str) -> "set[str]":
    """Every C function the rendered *text* defines more than once."""
    seen = Counter(m.group(1) for m in _FUNC.finditer(text))
    return {name for name, n in seen.items() if n > 1}


@pytest.mark.parametrize("kind", sorted(_KINDS))
def test_every_list_that_renders_a_function_twice_is_refused(kind) -> None:
    gen, _make, module = _KINDS[kind]
    base = _with_members(kind)
    assert C.duplicate_name_errors(base) == [], "the fixture is legal"
    assert not _twice(gen.render_ext(base, module)), "the fixture renders"
    redefining, unchecked = [], []
    for path in _named_lists(base["module"][module]):
        cfg = copy.deepcopy(base)
        rows = _at(cfg["module"][module], path)
        rows.append(dict(rows[-1]))
        try:
            twice = _twice(gen.render_ext(cfg, module))
        except (ValueError, NotImplementedError):
            # Refused by the renderer itself: not a compiler's finding.
            continue
        if not twice:
            continue
        redefining.append(path)
        name = rows[-1]["name"]
        errors = C.duplicate_name_errors(cfg)
        if not any(f"'{name}' is declared twice" in e for e in errors):
            unchecked.append((path, sorted(twice)))
    assert not unchecked, (
        "a repeat in these lists renders a C function twice, so the"
        " scaffold does not compile, but duplicate_name_errors does not"
        " refuse it (gh-2804); add it to _config._kind_duplicate_errors:\n  "
        + "\n  ".join(map(str, unchecked))
    )
    assert redefining, f"{kind}: no list rendered a repeat; not armed"


def test_the_fixture_declares_every_table_a_kind_renders() -> None:
    """gh-2838: the gate above walks the lists the fixture HAS. A table a
    kind renders and the fixture omits was never checked, so the tables come
    from the schema -- ``_keys.KIND_TABLE_VOCAB``, whose entry is the promise
    that a row in the table is rendered (gh-1517) -- and each one naming
    its rows (a ``name``, or a getter's ``fields``) must be in the fixture.
    """
    from just_makeit import _keys

    missing = []
    for (kind, table), vocab in _keys.KIND_TABLE_VOCAB.items():
        keys = _keys.KIND_KEYS[vocab]
        if "name" not in keys and "fields" not in keys:
            continue
        if kind not in _KINDS:
            continue
        module = _KINDS[kind][2]
        rows = _with_members(kind)["module"][module].get(table)
        if not rows:
            missing.append((kind, table))
    assert not missing, (
        "_with_members declares no row for these tables a kind renders, so"
        " a repeat in them is never tried:\n  "
        + "\n  ".join(map(str, missing))
    )


def test_a_getter_field_repeated_across_tables_is_refused() -> None:
    """Two ``getters`` tables share the type's one getset."""
    cfg = _with_members("handle")
    getters = cfg["module"]["hmod"]["getters"]
    getters[1]["fields"][0]["name"] = "used"
    assert C.duplicate_name_errors(cfg) == [
        "handle module 'hmod' getters fields: 'used' is declared twice;"
        " each name is one C member or parameter, so the scaffold would"
        " not compile."
    ]


@pytest.mark.parametrize("name", ["zqb", "rate"])
def test_a_computed_property_repeating_a_member_is_refused(name) -> None:
    """A stored field (refused mid-render before) or another computed."""
    cfg = _with_members("composer")
    cfg["module"]["wmod"]["source"]["computed"][0]["name"] = name
    assert C.duplicate_name_errors(cfg) == [
        f"composer module 'wmod' source fields and computed: '{name}' is"
        " declared twice; each name is one C member or parameter, so the"
        " scaffold would not compile."
    ]


def test_the_renderer_keeps_only_the_reserved_fs() -> None:
    """``fs`` is not a row in either list, so the renderer still refuses
    a computed property named for it; a field is the table's question."""
    cfg = _with_members("composer")
    cfg["module"]["wmod"]["source"]["computed"][0]["name"] = "fs"
    with pytest.raises(ValueError, match="collides with the source's fs"):
        _composer.render_source_type(cfg, "wmod")


_HANDLE = """
[module.ring]
kind = "handle"
backing = "ringbuf"
type_name = "Ring"
create_fn = "ringbuf_open"
close_fn = "ringbuf_close"
create_args = [{ name = "capacity", type = "size_t" }]

[[module.ring.getters]]
fn = "ringbuf_stats"
out = "ringbuf_stats_t"
fields = [
  { name = "used", type = "size_t" },
  { name = "used", type = "size_t" },
]
"""


def _snapshot(root: Path) -> "dict[str, str]":
    return {
        p.relative_to(root).as_posix(): hashlib.sha1(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_apply_and_status_refuse_the_issues_getter(tmp_path: Path) -> None:
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    root = tmp_path / "p"
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + _HANDLE, encoding="utf-8"
    )
    before = _snapshot(root)
    for verb in ("apply", "status"):
        r = run_cli(verb, cwd=root)
        assert r.returncode == 1, (verb, r.stdout + r.stderr)
        assert "Traceback" not in r.stderr, r.stderr
        assert (
            "handle module 'ring' getters fields: 'used' is declared twice"
            in r.stderr
        ), r.stderr
    assert _snapshot(root) == before, "a refused command wrote to the tree"
