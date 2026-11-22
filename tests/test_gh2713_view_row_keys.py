"""gh-2713: an unread key on a ``[[<obj>.views]]`` row is reported.

A view (gh-504) is a second Python class over an object's C core, declared
as a row of the object's ``views`` array. :func:`_keys.unknown_keys` walked
the object, and since gh-2709 the view's method rows, but not the view row
itself, nor the init-param and property rows it declares. A misspelt key
there -- ``exclude_propertys`` -- was accepted, kept, and did nothing, and
jm said nothing about it.

The row now has a vocabulary, ``_keys.VIEW_KEYS`` (``KIND_KEYS["view"]``),
and is walked like an object row: an unknown key is one advisory warning
and the command goes ahead, exactly as on the object (the module docstring
of ``_keys`` says why it is not refused). The view's init-params and
properties are held to the vocabularies the object's own are, because the
same readers read them.

The vocabulary is held to the source rather than to a list: every key any
reader takes off a view row is found by walking ``src/just_makeit``'s
syntax trees for the names a view row is bound to, so a key a reader gains
without being declared goes red, and so does a declared key nothing reads.

GATE: every key the view row's readers do not take is reported, naming the
row; the vocabulary is exactly what the readers read; and ``jm apply``
reports the issue's trigger once and exits 0.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from _jmrun import run_cli, warning_lines

import just_makeit
from just_makeit import _keys
from just_makeit._keys import KIND_KEYS, VIEW_KEYS, unknown_keys

SRC = Path(just_makeit.__file__).parent

#: Spellings no vocabulary contains: the issue's typo, and one more.
_NOBODYS = ("exclude_propertys", "clas_name")

#: Every key some table reads and a view row does not, plus `_NOBODYS`.
_NOT_READ_HERE = sorted(
    (set().union(*KIND_KEYS.values()) - VIEW_KEYS) | set(_NOBODYS)
)

#: The calls that hand back view rows: a list of them (``views``), the
#: ``(parent, view)`` pairs a module registers (``module_views``), and one
#: row by class name (``_find_view``).
_LIST_CALLS = ("views", "module_views")
_ROW_CALLS = ("_find_view",)


def _cfg(view: dict) -> dict:
    """A manifest whose module object ``o`` declares the one *view* row."""
    return {
        "project": {"name": "p"},
        "module": {"m": {"objects": ["o"]}},
        "o": {"arg_type": "float", "return_type": "float", "views": [view]},
    }


_VIEW = {"class_name": "V", "create_fn": "o_create_v"}


@pytest.mark.parametrize("key", _NOT_READ_HERE)
def test_a_key_the_row_does_not_read_is_reported_naming_the_row(key):
    (found,) = unknown_keys(_cfg({**_VIEW, key: 1}))
    assert (found.kind, found.where, found.key) == ("view", "o.views(V)", key)
    assert found.message().startswith(f"o.views(V): unknown view key `{key}`")


def test_a_key_the_row_reads_is_not_reported():
    assert unknown_keys(_cfg({key: [] for key in VIEW_KEYS})) == []


@pytest.mark.parametrize(
    "sub, kind",
    [("init_params", "init_param"), ("properties", "property")],
)
def test_a_views_own_rows_are_held_to_the_objects_vocabulary(sub, kind):
    """The view's init-params and properties are read by the object's
    readers (`C.view_init_params`, the property renderer), so they take the
    object's keys and nothing else."""
    good = {key: "x" for key in KIND_KEYS[kind]}
    assert unknown_keys(_cfg({**_VIEW, sub: [{**good, "name": "a"}]})) == []
    (found,) = unknown_keys(
        _cfg({**_VIEW, sub: [{"name": "a", "type": "double", "dco": "x"}]})
    )
    assert (found.kind, found.where, found.key) == (
        kind,
        "o.views(V).a",
        "dco",
    )


# ---------------------------------------------------------------------------
# The vocabulary, read off the source


def _call_name(node: ast.AST) -> "str | None":
    """The bare name a call is made through, ``C.views(...)`` -> ``views``."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _const_str(node: ast.AST) -> "str | None":
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_views_table(node: ast.AST) -> bool:
    """``body.get("views")`` / ``body["views"]``, optionally ``or []``."""
    if isinstance(node, ast.BoolOp):
        return any(_is_views_table(v) for v in node.values)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
    ):
        return _const_str(node.args[0]) == "views"
    if isinstance(node, ast.Subscript):
        return _const_str(node.slice) == "views"
    return False


def _view_names(scope: ast.AST) -> "set[str]":
    """The names *scope* binds to a view row.

    A parameter named ``view``; a name assigned from ``_find_view``; and the
    target of a loop or comprehension over the rows -- a ``views`` /
    ``module_views`` call (the pair's second element), a name assigned from
    one, or a table's ``views`` array. Iterated to a fixed point, so a loop
    over a list bound later in the walk still counts.
    """
    names: set = set()
    lists: set = set()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = scope.args
        names |= {a.arg for a in args.args + args.kwonlyargs}
        names &= {"view"}
    nodes = list(ast.walk(scope))
    while True:
        before = (len(names), len(lists))
        for n in nodes:
            if (
                isinstance(n, ast.Assign)
                and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name)
            ):
                call = _call_name(n.value)
                if call in _ROW_CALLS:
                    names.add(n.targets[0].id)
                elif call in _LIST_CALLS:
                    lists.add(n.targets[0].id)
            elif isinstance(n, (ast.For, ast.comprehension)):
                it = n.iter
                if not (
                    _call_name(it) in _LIST_CALLS
                    or _is_views_table(it)
                    or (isinstance(it, ast.Name) and it.id in lists)
                ):
                    continue
                target = n.target
                if isinstance(target, ast.Tuple):
                    target = target.elts[-1]
                if isinstance(target, ast.Name):
                    names.add(target.id)
        if (len(names), len(lists)) == before:
            return names


def _key_read(n: ast.AST) -> "tuple[str, str] | None":
    """``(name, key)`` for ``name["key"]``, ``name.get("key")`` (or
    ``setdefault`` / ``pop``) and ``"key" in name``, else None."""
    if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name):
        key = _const_str(n.slice)
        return (n.value.id, key) if key else None
    if (
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("get", "setdefault", "pop")
        and isinstance(n.func.value, ast.Name)
        and n.args
    ):
        key = _const_str(n.args[0])
        return (n.func.value.id, key) if key else None
    if (
        isinstance(n, ast.Compare)
        and len(n.ops) == 1
        and isinstance(n.ops[0], (ast.In, ast.NotIn))
        and isinstance(n.comparators[0], ast.Name)
    ):
        key = _const_str(n.left)
        return (n.comparators[0].id, key) if key else None
    return None


def _view_keys_read() -> "dict[str, set[str]]":
    """Every key jm's source takes off a view row, with where it does."""
    out: dict = {}
    for path in sorted(SRC.rglob("*.py")):
        if "templates" in path.relative_to(SRC).parts:
            continue  # `<<token>>` placeholders: not Python until rendered
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scopes = [tree] + [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for scope in scopes:
            names = _view_names(scope)
            if not names:
                continue
            for n in ast.walk(scope):
                read = _key_read(n)
                if read and read[0] in names:
                    where = f"{path.relative_to(SRC)}:{n.lineno}"
                    out.setdefault(read[1], set()).add(where)
    return out


def test_the_vocabulary_is_exactly_what_the_readers_read():
    """Both directions, from the readers themselves rather than a copy.

    A key a reader takes that the vocabulary lacks would be reported as
    unread while it changed the build; a vocabulary key no reader takes
    would silence the report on a key that does nothing.
    """
    read = _view_keys_read()
    undeclared = {k: sorted(v) for k, v in read.items() if k not in VIEW_KEYS}
    assert not undeclared, (
        f"read off a view row, not in VIEW_KEYS: {undeclared}"
    )
    unread = sorted(VIEW_KEYS - set(read))
    assert not unread, f"in VIEW_KEYS, read by nothing: {unread}"


def test_the_scan_sees_view_py_and_the_config_helpers():
    """The scan is armed on the readers the issue names: `_view.py`, which
    renders the view's constructor, and `_config`'s `view_*` helpers."""
    read = _view_keys_read()
    files = {w.split(":")[0] for sites in read.values() for w in sites}
    assert {"_view.py", "_config.py", "_apply.py"} <= files, files


# ---------------------------------------------------------------------------
# Through the command


def test_apply_reports_the_issue_trigger_and_still_exits_0(tmp_path):
    """Advisory, as on an object row: reported once, and the apply goes
    ahead. On main it printed nothing."""
    assert run_cli("new", "p", cwd=tmp_path).returncode == 0
    root = tmp_path / "p"
    for step in (
        ("module", "m"),
        ("object", "o", "--module", "m", "--state", "g:double:1.0"),
        ("property", "o", "level", "--module", "m", "--type", "double"),
        ("view", "o", "Peek", "--module", "m", "--create-fn", "o_mk_peek"),
    ):
        r = run_cli(*step, cwd=root)
        assert r.returncode == 0, f"{step}: {r.stdout}{r.stderr}"
    (manifest,) = [
        p
        for p in root.rglob("*.toml")
        if "[[o.views]]" in p.read_text(encoding="utf-8")
    ]
    text = manifest.read_text(encoding="utf-8")
    anchor = 'class_name = "Peek"\n'
    assert text.count(anchor) == 1, text
    manifest.write_text(
        text.replace(anchor, anchor + 'exclude_propertys = ["level"]\n'),
        encoding="utf-8",
    )
    _keys._SEEN.clear()
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    hits = [
        line
        for line in warning_lines(r.stdout + r.stderr)
        if "unknown view key `exclude_propertys`" in line
    ]
    assert len(hits) == 1, r.stdout + r.stderr
    assert "o.views(Peek):" in hits[0], hits[0]
