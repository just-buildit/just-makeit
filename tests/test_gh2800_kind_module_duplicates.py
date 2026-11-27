"""gh-2800: a name twice in a module kind's parameter list is refused.

gh-2112's ``_config.duplicate_name_errors`` covered objects, views and
plain-module functions. The handle, capsule and composer kinds render their
own C, and each of their lists is one C declaration too: a handle's
``create_args`` (its ``tp_init``), a handle method's ``args``, a handle
factory's ``init_params``, a capsule's ``init_params`` (its ``create``), a
composer source's ``fields`` (the source type's ``tp_init``), its segment's
``fields`` (the segment object's struct) and each serializer's ``params``.
Every one, declared twice, applied cleanly and failed in the compiler:
``redefinition of 'capacity'``, ``duplicate member 'dur'``.

The first test derives its cases: every wrapper a kind's ``arg_scopes``
names (gh-1525's registry of the wrappers whose C locals a manifest name
becomes) gets a duplicate in its own list, and the check must name that
scope. A new arg-bearing list a kind grows without a row in the check fails
here, unlisted.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from _jmrun import run_cli
from test_gh1525_kind_arg_local_names import _KINDS, _composer_cfg

from just_makeit import _config as C


def _lists(node):
    """Every list in *node*, a manifest dict, at any depth."""
    if isinstance(node, dict):
        for v in node.values():
            yield from _lists(v)
    elif isinstance(node, list):
        yield node
        for v in node:
            yield from _lists(v)


def _home(cfg: dict, params) -> list:
    """The one manifest list a scope's *params* were read from.

    By identity where the accessor hands back the manifest's own rows, by
    the run of names where it hands back tuples (a capsule's
    ``module_init_params``).
    """
    first = params[0]
    names = [p["name"] if isinstance(p, dict) else p[0] for p in params]
    found = [
        rows
        for rows in _lists(cfg)
        if rows
        and all(isinstance(r, dict) for r in rows)
        and [r.get("name") for r in rows] == names
        and (not isinstance(first, dict) or any(r is first for r in rows))
    ]
    assert len(found) == 1, (names, len(found))
    return found[0]


@pytest.mark.parametrize("kind", sorted(_KINDS))
def test_every_kind_arg_scope_is_checked(kind: str) -> None:
    gen, make_cfg, module = _KINDS[kind]
    assert C.duplicate_name_errors(make_cfg()) == [], (
        "the fixture repeats names only across lists, which is legal"
    )
    n = len(gen.arg_scopes(make_cfg(), module))
    assert n, f"{kind}: no arg scope, so nothing was asked"
    unchecked = []
    for i in range(n):
        cfg = make_cfg()
        owner, _fn, params, _declares = gen.arg_scopes(cfg, module)[i]
        rows = _home(cfg, params)
        rows.append(dict(rows[0]))
        errors = C.duplicate_name_errors(cfg)
        name = rows[0]["name"]
        if not any(
            e.startswith(owner) and f"'{name}' is declared twice" in e
            for e in errors
        ):
            unchecked.append((owner, errors))
    assert not unchecked, (
        "a module kind's list is one C declaration, but a name in it twice"
        " is not refused (gh-2800); add it to"
        " _config.duplicate_name_errors:\n  "
        + "\n  ".join(map(str, unchecked))
    )


def test_a_composer_segment_field_twice_is_refused() -> None:
    """Not an arg scope (its values are read from a dict), but the fields
    are members of one struct: ``duplicate member 'fs'``."""
    cfg = _composer_cfg()
    fields = cfg["module"]["wmod"]["segment"]["fields"]
    fields.append(dict(fields[0]))
    assert C.duplicate_name_errors(cfg) == [
        "composer module 'wmod' segment fields: 'fs' is declared twice;"
        " each name is one C member or parameter, so the scaffold would"
        " not compile."
    ]


_HANDLE = """
[module.ring]
kind = "handle"
backing = "ringbuf"
type_name = "Ring"
create_fn = "ringbuf_open"
close_fn = "ringbuf_close"
create_args = [
  { name = "capacity", type = "size_t" },
  { name = "capacity", type = "size_t" },
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


def test_apply_and_status_refuse_the_issues_handle(tmp_path: Path) -> None:
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
            "handle module 'ring' create_args: 'capacity' is declared twice"
            in r.stderr
        ), r.stderr
    assert _snapshot(root) == before, "a refused command wrote to the tree"
