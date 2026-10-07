"""gh-2009: a kind-module row that crosses as a scalar refuses what is not one.

A capsule module's ``init_params`` and a handle module's ``create_args`` parse
one C scalar per row; both ended in ``_CTYPE_META[type]["fmt"]``. So a row
typed ``"float[]"`` was not refused: ``jm apply`` died with a bare
``KeyError: 'float[]'`` from inside the renderer, naming neither the module
nor the row. Measured on main, the same lookup crashed five more rows the
same way -- a capsule's ``properties``, a handle factory's ``init_params``,
a handle getter's ``fields``, and, with a spelling jm does not know, a handle
method's scalar ``args`` and its ``returns``.

Every one of those lookups now goes through ``_capsule.scalar_meta``, which
refuses with one ``error:`` line naming the module, the table and the row;
on a constructor row an array is pointed at an object's
``[[<obj>.init_params]]``, where a constructor array is supported. Nothing is
written: ``apply`` renders into a throwaway tree first.

Two halves:

* **every face**, through ``jm apply``: exit 1, exactly one ``error:`` line
  naming the row, the project byte-identical -- the issue's two fragments
  verbatim, and each other row the lookup reached;
* **the placement**: no ``_CTYPE_META[...]`` subscript is left in the two
  generators, read from their AST, so a new row cannot index the table
  directly and bring the ``KeyError`` back.

GATE: a capsule / handle row typed as something no scalar parse converts is
refused, naming the row, and leaves the project untouched.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import io
from pathlib import Path

import pytest

from _jmrun import run_cli
from test_capsule_apply import _capsule_module
from test_handle_apply import _ring_module

from just_makeit import _capsule
from just_makeit import _config as C
from just_makeit import _types as T
from just_makeit._new import run as new_run

SRC = Path(_capsule.__file__).parent

#: An array in each spelling a manifest may write one, and a spelling that
#: names no type at all.
ARRAYS = ("float[]", "double _Complex[][]", "int32_t[8]")
UNKNOWN = "nope_t"

_CAPSULE = ("ddc_fn", _capsule_module)
_HANDLE = ("ringbuf", _ring_module)


def _append(table: str):
    """Add the row to the module's *table*."""

    def add(m: dict, row: dict) -> None:
        m[table] = list(m.get(table) or []) + [row]

    return add


#: face -> (module, factory, how the row is added, the row for a type, what
#: the refusal names, is it a constructor row, the types it must refuse).
#: A method's argument and return take an array legitimately (one array arg,
#: an array return), so only the unknown spelling reaches the scalar parse.
FACES = {
    "capsule init_params": (
        *_CAPSULE,
        _append("init_params"),
        lambda t: {"name": "zz", "type": t},
        "capsule module 'ddc_fn' init_params row 'zz'",
        True,
        (*ARRAYS, UNKNOWN),
    ),
    "capsule properties": (
        *_CAPSULE,
        _append("properties"),
        lambda t: {"name": "zz", "type": t, "writable": True},
        "capsule module 'ddc_fn' properties row 'zz'",
        False,
        (*ARRAYS, UNKNOWN),
    ),
    "handle create_args": (
        *_HANDLE,
        _append("create_args"),
        lambda t: {"name": "zz", "type": t},
        "handle module 'ringbuf' create_args row 'zz'",
        True,
        (*ARRAYS, UNKNOWN),
    ),
    "handle factory init_params": (
        *_HANDLE,
        _append("factories"),
        lambda t: {
            "name": "zzf",
            "create_fn": "ringbuf_zz",
            "init_params": [{"name": "zz", "type": t}],
        },
        "handle module 'ringbuf' factory 'zzf' init_params row 'zz'",
        True,
        (*ARRAYS, UNKNOWN),
    ),
    "handle getter field": (
        *_HANDLE,
        _append("getters"),
        lambda t: {
            "fn": "ringbuf_zz",
            "out": "zz_t",
            "fields": [
                {"name": "zz", "type": t, "writable_fn": "ringbuf_set_zz"}
            ],
        },
        "handle module 'ringbuf' getter 'ringbuf_zz' fields row 'zz'",
        False,
        (*ARRAYS, UNKNOWN),
    ),
    # A getter row whose fields each name their own `getter` has no `fn`.
    "handle per-field getter field": (
        *_HANDLE,
        _append("getters"),
        lambda t: {
            "fields": [{"name": "zz", "type": t, "getter": "ringbuf_get_zz"}],
        },
        "handle module 'ringbuf' getter fields row 'zz'",
        False,
        (*ARRAYS, UNKNOWN),
    ),
    "handle method arg": (
        *_HANDLE,
        _append("methods"),
        lambda t: {
            "name": "zzm",
            "fn": "ringbuf_zz",
            "args": [{"name": "zz", "type": t}],
        },
        "handle module 'ringbuf' method 'zzm' args row 'zz'",
        False,
        (UNKNOWN,),
    ),
    "handle method arg beside an array": (
        *_HANDLE,
        _append("methods"),
        lambda t: {
            "name": "zzm",
            "fn": "ringbuf_zz",
            "args": [
                {"name": "x", "type": "float[]"},
                {"name": "zz", "type": t},
            ],
        },
        "handle module 'ringbuf' method 'zzm' args row 'zz'",
        False,
        (UNKNOWN,),
    ),
    "handle out_len_fn method arg": (
        *_HANDLE,
        _append("methods"),
        lambda t: {
            "name": "zzm",
            "fn": "ringbuf_zz",
            "returns": "float[]",
            "out_len_fn": "ringbuf_zz_len",
            "args": [{"name": "zz", "type": t}],
        },
        "handle module 'ringbuf' method 'zzm' args row 'zz'",
        False,
        (UNKNOWN,),
    ),
    "handle method returns": (
        *_HANDLE,
        _append("methods"),
        lambda t: {"name": "zzm", "fn": "ringbuf_zz", "returns": t},
        "handle module 'ringbuf' method 'zzm' returns",
        False,
        (UNKNOWN,),
    ),
}

_CASES = [(face, t) for face, spec in sorted(FACES.items()) for t in spec[-1]]


def _tree(root: Path) -> dict:
    """Every file under *root*, by relative path, with its bytes."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _errors(r) -> list:
    return [ln for ln in r.stderr.splitlines() if ln.startswith("error:")]


def _assert_refused(r, before: dict, root: Path, where: str) -> str:
    """Exit 1, one `error:` line naming *where*, the tree unchanged."""
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    errors = _errors(r)
    assert len(errors) == 1, r.stderr
    assert errors[0].startswith(f"error: {where}: "), errors[0]
    assert _tree(root) == before, "a refused apply changed the project"
    return errors[0]


@pytest.mark.parametrize("face, ctype", _CASES)
def test_a_row_that_is_not_a_scalar_is_refused(tmp_path, face, ctype):
    mod, factory, add, row, where, ctor, _types = FACES[face]
    root = tmp_path / "proj"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run("proj", root, ["widget"], [("gain", "float", "0.0f")])
    cfg = C.load(root)
    m = factory()
    add(m, copy.deepcopy(row(ctype)))
    cfg.setdefault("module", {})[mod] = m
    C.save(root, cfg)
    before = _tree(root)
    err = _assert_refused(run_cli("apply", cwd=root), before, root, where)
    if ctype in ARRAYS:
        assert "is an array" in err, err
        assert (_capsule.CTOR_ARRAY_HOME in err) == ctor, err
    else:
        assert f"unknown type '{ctype}'" in err, err


#: The issue's two triggers, verbatim.
ISSUE_FRAGMENTS = {
    "capsule": (
        '[module.cap]\nkind = "capsule"\nbacking = "eng"\n'
        'init_params = [{name = "h", type = "float[]"}]\n',
        "capsule module 'cap' init_params row 'h'",
    ),
    "handle": (
        '[module.h]\nkind = "handle"\nhandle_type = "h_t"\n'
        'create_fn = "h_open"\nclose_fn = "h_close"\n'
        'create_args = [{name = "h", type = "float[]"}]\n',
        "handle module 'h' create_args row 'h'",
    ),
}


@pytest.mark.parametrize("kind", sorted(ISSUE_FRAGMENTS))
def test_the_issue_fragment_is_refused(tmp_path, kind):
    """`jm apply frag.toml`: on main, `KeyError: 'float[]'` and no row."""
    fragment, where = ISSUE_FRAGMENTS[kind]
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    frag = tmp_path / "frag.toml"
    frag.write_text(fragment, encoding="utf-8")
    before = _tree(root)
    err = _assert_refused(
        run_cli("apply", str(frag), cwd=root), before, root, where
    )
    assert _capsule.CTOR_ARRAY_HOME in err, err


@pytest.mark.parametrize("ctype", sorted(T._CTYPE_META))
def test_every_scalar_jm_converts_still_passes(ctype):
    """The refusal takes nothing a row could convert before."""
    assert _capsule.scalar_meta(ctype, "x") is T._CTYPE_META[ctype]


@pytest.mark.parametrize("name", ["_capsule.py", "_handle.py"])
def test_no_generator_indexes_the_type_table_directly(name):
    """The placement half: every lookup goes through `scalar_meta`.

    A direct ``_CTYPE_META[...]`` is the line that raised the ``KeyError``.
    Read from the AST, so a comment or a docstring naming the table is not a
    finding, and a reformatted subscript still is.
    """
    tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
    direct = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "_CTYPE_META"
    ]
    assert not direct, (
        f"{name} indexes _CTYPE_META directly at line(s) {direct}; go "
        f"through _capsule.scalar_meta, which refuses a type it lacks"
    )
