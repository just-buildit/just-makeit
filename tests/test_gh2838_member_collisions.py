"""gh-2838: two DIFFERENT members of one kind that derive one name.

gh-2804 refused a name twice inside ONE list. A kind's members come from
several lists, and each list turns a name into a C function with its own
template (a handle method ``<Type>_<name>``, a getter field
``<Type>_get_<name>``), so a method ``get_used`` beside a getter field
``used`` defined ``Ring_get_used`` twice, and ``apply`` exited 0 -- measured
by building the bundled ``composites`` example with the method added
(``conflicting types for 'Ring_get_used'``).

The first test derives every such pair from the rendered C. For each named
list it probes the template its names go through (add a sentinel row, see
which function appears), then gives a row of that list the name that makes
its function equal each function the kind's render already defines. Where the
render then defines a function twice, ``duplicate_name_errors`` must refuse
it. A list or a template a kind grows later is held without being listed.

The rest hold the handle's Python members (a name two ways is one type-dict
key, the second silently replacing the first), the composer's
``extra_methods`` repeat (two ``PyMethodDef`` rows under one key, which
built), and the refusal through ``apply``.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections import Counter
from pathlib import Path

import pytest
from _jmrun import run_cli
from test_gh1525_kind_arg_local_names import _KINDS
from test_gh2804_kind_member_duplicates import (
    _at,
    _named_lists,
    _with_members,
)

from just_makeit import _config as C
from just_makeit import _handle

_SENTINEL = "zqzqzq"


#: A function definition: a return-type line, then ``name(``. Unlike the
#: gh-1525 pattern it reads a parameter list holding a macro call
#: (``Py_UNUSED(ignored)``), which is how ``close`` and the state members
#: are written.
_DEF = re.compile(r"^(?:static\s+)?[A-Za-z_][\w \*]*\n(\w+)\(", re.M)


def _defined(text: str) -> "set[str]":
    return set(_DEF.findall(text))


def _twice(text: str) -> "set[str]":
    return {n for n, c in Counter(_DEF.findall(text)).items() if c > 1}


def _render(kind: str, cfg: dict) -> str:
    gen, _make, module = _KINDS[kind]
    return gen.render_ext(cfg, module)


def _name_for(kind: str, cfg: dict, path: tuple) -> "tuple[str, str] | None":
    """The ``(prefix, suffix)`` the list at *path* wraps a name in, found by
    adding a sentinel row and reading the function that appears."""
    _gen, _make, module = _KINDS[kind]
    probe = copy.deepcopy(cfg)
    rows = _at(probe["module"][module], path)
    rows.append({**rows[-1], "name": _SENTINEL})
    try:
        new = _defined(_render(kind, probe)) - _defined(_render(kind, cfg))
    except (ValueError, NotImplementedError):
        return None
    hits = sorted(f for f in new if _SENTINEL in f)
    return tuple(hits[0].split(_SENTINEL, 1)) if hits else None  # type: ignore


#: A composer publishes four types and writes a dozen members of its own
#: (``execute``, ``compose``, the ``segments`` / ``repeat`` / ``continuous``
#: getters, ``_capsule``, ...), several of them conditional on the module's
#: keys. The table of them belongs beside the renderer, in
#: ``_composer.py``, which another change holds; until it has one, the
#: composer's serializers, settings and fields can still be named for a
#: function the composer writes itself. strict: the day ``_composer`` grows
#: ``member_collisions`` and ``_kind_duplicate_errors`` calls it, this
#: XPASSes and the mark must go.
_COMPOSER_PENDING = pytest.mark.xfail(
    strict=True,
    reason="gh-2838: the composer's own members are not tabled in"
    " _composer.py yet (see the module comment above)",
)


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param(k, marks=_COMPOSER_PENDING) if k == "composer" else k
        for k in sorted(_KINDS)
    ],
)
def test_every_pair_of_members_deriving_one_function_is_refused(kind) -> None:
    _gen, _make, module = _KINDS[kind]
    base = _with_members(kind)
    assert C.duplicate_name_errors(base) == [], "the fixture is legal"
    have = _defined(_render(kind, base))
    unchecked, armed = [], 0
    for path in _named_lists(base["module"][module]):
        wrap = _name_for(kind, base, path)
        if wrap is None:
            continue
        pre, post = wrap
        for fn in sorted(have):
            if not (fn.startswith(pre) and fn.endswith(post)):
                continue
            name = fn[len(pre) : len(fn) - len(post)]
            if not name.isidentifier():
                continue
            cfg = copy.deepcopy(base)
            rows = _at(cfg["module"][module], path)
            if any(r["name"] == name for r in rows):
                continue  # the same list: gh-2804's question
            rows.append({**rows[-1], "name": name})
            try:
                twice = _twice(_render(kind, cfg))
            except (ValueError, NotImplementedError):
                continue  # refused by the renderer itself
            if fn not in twice:
                continue
            armed += 1
            if not C.duplicate_name_errors(cfg):
                unchecked.append((path, name, fn))
    assert not unchecked, (
        "a row named for a function another member renders defines it"
        " twice, so the scaffold does not compile, but"
        " duplicate_name_errors does not refuse it (gh-2838); add the"
        " members to the kind's collision check:\n  "
        + "\n  ".join(map(str, unchecked))
    )
    if kind == "handle":
        assert armed >= 4, f"{kind}: only {armed} pairs tried; not armed"


def test_a_handle_member_set_is_every_function_the_render_defines() -> None:
    """``_handle.members`` is the one derivation of a handle's functions, so
    a function the renderer writes that it does not list is a member a
    collision could hide behind."""
    cfg = _with_members("handle")
    sec = cfg["module"]["hmod"]
    sec["context_manager"] = True
    sec["serializable"] = True
    sec["capsule"] = "hb_handle"
    sec["factories"] = sec.get("factories") or []
    sec["getters"][1]["fields"][0]["writable_fn"] = "hb_set_gain"
    text = _render("handle", cfg)
    tname = C.handle_type_name(cfg, "hmod")
    leaf = C.module_paths("hmod").leaf
    derived = {fn for _w, fn, _py, _s in _handle.members(cfg, "hmod") if fn}
    ours = {
        f for f in _defined(text) if f.startswith((f"{tname}_", f"{leaf}_"))
    }
    assert ours - derived == set(), "functions members() does not list"
    for fn in (f"{tname}_set_gain", f"{tname}_enter", f"{tname}_get_state"):
        assert fn in derived and fn in ours


def _handle_with(*extra_methods: str) -> dict:
    cfg = _with_members("handle")
    for n in extra_methods:
        cfg["module"]["hmod"]["methods"].append(
            {"name": n, "fn": "hb_x", "returns": "double"}
        )
    return cfg


@pytest.mark.parametrize(
    "name, clash",
    [
        ("get_used", "C function 'Hb_get_used'"),
        ("close", "C function 'Hb_close'"),
        ("used", "Python member 'used'"),
    ],
)
def test_a_handle_method_clashing_with_another_member_is_refused(
    name, clash
) -> None:
    cfg = _handle_with(name)
    tname = C.handle_type_name(cfg, "hmod")
    errors = C.duplicate_name_errors(cfg)
    assert len(errors) == 1, errors
    assert clash.replace("Hb", tname) in errors[0], errors
    assert f"method '{name}'" in errors[0]


def test_a_method_and_a_getter_field_naming_one_function() -> None:
    """The issue's case, with the message it earns."""
    cfg = _handle_with("get_used")
    tname = C.handle_type_name(cfg, "hmod")
    assert C.duplicate_name_errors(cfg) == [
        "handle module 'hmod': method 'get_used' and getter field 'used'"
        f" both define the C function '{tname}_get_used'; the scaffold"
        " would not compile."
    ]


def test_names_that_do_not_meet_stay_legal() -> None:
    cfg = _handle_with("fill", "get_total", "set_total")
    assert C.duplicate_name_errors(cfg) == []


def test_a_factory_may_share_a_name_with_a_method() -> None:
    """A factory is a module function and a method a class member."""
    cfg = _handle_with("reset")
    cfg["module"]["hmod"]["factories"].append(
        {"name": "reset", "create_fn": "hb_reset", "init_params": []}
    )
    assert C.duplicate_name_errors(cfg) == []


# ── composer extra_methods ────────────────────────────────────────────────


def _composer_extra(*rows: dict) -> dict:
    cfg = _with_members("composer")
    cfg["module"]["wmod"]["extra_methods"] = list(rows)
    return cfg


def _row(name: str, **kw) -> dict:
    return {"name": name, "fn": f"Wc_{name}", "flags": "METH_NOARGS", **kw}


def test_a_composer_extra_method_repeated_is_refused() -> None:
    errors = C.duplicate_name_errors(
        _composer_extra(_row("draws"), _row("draws"))
    )
    assert len(errors) == 1, errors
    assert "'draws' is declared twice" in errors[0]
    assert "second row is unreachable" in errors[0]


def test_an_extra_method_repeating_a_serializer_is_refused() -> None:
    cfg = _composer_extra(_row("draws"))
    name = cfg["module"]["wmod"]["serializers"][0]["name"]
    cfg["module"]["wmod"]["extra_methods"].append(_row(name))
    errors = C.duplicate_name_errors(cfg)
    assert len(errors) == 1 and f"'{name}' is declared twice" in errors[0]


def test_extra_methods_on_different_types_may_share_a_name() -> None:
    cfg = _composer_extra(_row("draws"), _row("draws", type="Source"))
    assert C.duplicate_name_errors(cfg) == []


# ── through the CLI ───────────────────────────────────────────────────────


def _snapshot(root: Path) -> "dict[str, str]":
    return {
        p.relative_to(root).as_posix(): hashlib.sha1(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


_RING = """
[module.ring]
kind = "handle"
backing = "ringbuf"
type_name = "Ring"
create_fn = "ringbuf_open"
close_fn = "ringbuf_close"
create_args = [{ name = "capacity", type = "size_t" }]
methods = [{ name = "get_used", fn = "ringbuf_used", returns = "size_t" }]

[[module.ring.getters]]
fn = "ringbuf_stats"
out = "ringbuf_stats_t"
fields = [{ name = "used", type = "size_t" }]
"""


def test_apply_and_status_refuse_the_issues_method(tmp_path: Path) -> None:
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    root = tmp_path / "p"
    manifest = root / "just-makeit.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + _RING, encoding="utf-8"
    )
    before = _snapshot(root)
    for verb in ("apply", "status"):
        r = run_cli(verb, cwd=root)
        assert r.returncode == 1, (verb, r.stdout + r.stderr)
        assert "Traceback" not in r.stderr, r.stderr
        assert (
            "method 'get_used' and getter field 'used' both define the C"
            " function 'Ring_get_used'" in r.stderr
        ), r.stderr
    assert _snapshot(root) == before, "a refused command wrote to the tree"
