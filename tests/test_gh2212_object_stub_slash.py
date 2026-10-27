"""gh-2212, the object face: an object's positional-only members are stubbed
with ``/``.

An object's ``step``, ``set_<field>``, ``__exit__``, ``set_state``
(``METH_O``) and the declared methods that parse positionally register
without ``METH_KEYWORDS``, so ``obj.step(x=1.0)`` raises ``TypeError``.
Their stubs named the parameters plainly, so a type checker accepted that
call. Both stub writers -- the standalone ``render_component_pyi`` and the
module ``make_module_pyi`` -- now ask one function,
``_stubs.mark_positional_only``, of the rows the class's own binding
registers.

The gate builds `_csym_fixtures`' projects by running jm -- standalone
objects of every shape, a serializable object, module objects and a view --
plus a module of ``jm function``s taking every argument kind and result
shape the verb has, and reads the two artefacts and nothing else: each
type's ``PyMethodDef`` rows in the C it was compiled from and the ``.pyi``
class of that name, and the module's own function table and its top-level
stubs. A row without ``METH_KEYWORDS`` must have a ``/`` in its stub, and a
row with it must not, so a stub can neither accept a keyword the binding
refuses nor refuse one it takes. Nothing here lists a member or a shape.

Every ``jm function`` with a parameter binds ``METH_VARARGS |
METH_KEYWORDS`` today, so on that face the gate holds the keyword direction
live; the module stub asks the same function of the module's table, so a
positional function binding would be stubbed with ``/`` without an edit.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import _csym_fixtures as F  # noqa: E402

from _jmrun import run_cli  # noqa: E402

from just_makeit import _config as C  # noqa: E402
from just_makeit._stubs import method_table_flags  # noqa: E402

#: The object projects: standalone shapes, serializable, module + view. The
#: kind modules (`kinds`) have their own gate, gh-2212's
#: `test_gh2212_positional_stub_slash.py`.
_ROWS = ("std", "shapes", "mod")

#: A module of `jm function`s: no parameter, a scalar with a default, an
#: array, a writable out-param, a complex scalar, a path, an enum choice,
#: and the result shapes -- `check_return`, `why`, `variable_output`,
#: `out_type` and `result_field` rows.
_FX = "--module", "fx"
_FUNCTIONS = [
    ("noarg", "--return-type", "int"),
    ("scal", "--param", "a:double", "--param", "b:int=3"),
    ("arr", "--param", "x:float[]", "--return-type", "float"),
    ("outp", "--param", "x:float[]", "--out-param", "y:float[]"),
    ("cplx", "--param", "z:float _Complex", "--return-type", "double"),
    ("pth", "--param", "p:path", "--return-type", "int"),
    ("en", "--param", "m:enum:mode=lo", "--return-type", "int"),
    ("chk", "--param", "a:int", "--return-type", "int", "--check-return"),
    (
        "wy",
        "--param",
        "a:int",
        "--return-type",
        "int",
        "--check-return",
        "--why",
    ),
    (
        "vo",
        "--param",
        "x:float[]",
        "--out-type",
        "float",
        "--return-type",
        "size_t",
        "--variable-output",
    ),
    ("ot", "--param", "x:float[]", "--out-type", "float"),
    (
        "rf",
        "--param",
        "x:float[]",
        "--return-type",
        "pk_t",
        "--result-field",
        "i:int",
        "--result-field",
        "v:float",
    ),
]


def _functions_project(where: Path) -> Path:
    """The function module, built by running jm. The `[[enum]]` an enum
    param names is written through `_config`: no verb declares one."""
    assert run_cli("new", "fp", "--no-c-prefix", cwd=where).returncode == 0
    root = where / "fp"
    cfg = C.load(root)
    cfg.setdefault("enum", []).append({"name": "mode", "values": ["lo", "hi"]})
    C.save(root, cfg)
    for step in [("module", "fx")] + [
        ("function", name, *_FX, *rest) for name, *rest in _FUNCTIONS
    ]:
        r = run_cli(*step, cwd=root)
        assert r.returncode == 0, f"{step}: {r.stdout}{r.stderr}"
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


#: A string literal or a comment, in one pass: what `_entry_openers`
#: blanks before counting. Written here, not borrowed from `_docsync`, so
#: the count is not the reader's own walk.
_LITERAL_OR_COMMENT = re.compile(
    r'"(?:[^"\\\n]|\\.)*"|/\*.*?\*/|//[^\n]*', re.S
)


def _entry_openers(body: str) -> int:
    """How many named rows a ``PyMethodDef`` table body holds, counted
    without `method_table_flags`: each ``{`` followed by a string literal,
    once strings are emptied and comments blanked, so a ``{"`` inside a
    docstring is not one. The ``{NULL, ...}`` sentinel names nothing."""
    masked = _LITERAL_OR_COMMENT.sub(
        lambda m: '""' if m.group(0).startswith('"') else " ", body
    )
    return len(re.findall(r'\{\s*"', masked))


def _table_bodies(text: str) -> "list[tuple[str, str]]":
    """``(table name, body)`` for each ``static PyMethodDef`` table."""
    return re.findall(
        r"static\s+PyMethodDef\s+(\w+)\s*\[\s*\]\s*=\s*\{(.*?)\n\s*\};",
        text,
        re.S,
    )


def unread_rows(text: str) -> "list[str]":
    """Each table in *text* whose named rows `method_table_flags` did not
    all return -- the miss a stub cannot show, because a member the reader
    drops is simply left unmarked."""
    return [
        f"{name}: {_entry_openers(body)} rows, read"
        f" {sorted(method_table_flags(body))}"
        for name, body in _table_bodies(text)
        if _entry_openers(body) != len(method_table_flags(body))
    ]


def _tables(root: Path) -> "dict[str, dict[str, str]]":
    """``{class: {member: flags}}`` for each extension type in *root*'s C,
    keyed by the class name its ``tp_name`` ends in, and the modules'
    function tables under ``""``."""
    out: "dict[str, dict[str, str]]" = {}
    for c in root.rglob("native/src/**/*.c"):
        text = c.read_text(encoding="utf-8")
        for fns in re.findall(
            r"static PyMethodDef \w+_module_methods\[\] = \{(.*?)\n\};",
            text,
            re.S,
        ):
            out.setdefault("", {}).update(method_table_flags(fns))
        tp = re.search(r'\.tp_name\s*=\s*"([\w.]+)"', text)
        tm = re.search(r"\.tp_methods\s*=\s*(\w+)", text)
        if not (tp and tm):
            continue
        body = re.search(
            rf"static PyMethodDef {tm.group(1)}\[\] = \{{(.*?)\n\}};",
            text,
            re.S,
        )
        assert body, f"{c}: no {tm.group(1)} table"
        out[tp.group(1).rsplit(".", 1)[1]] = method_table_flags(body.group(1))
    return out


def _stubs(root: Path) -> "dict[str, dict[str, ast.FunctionDef]]":
    """``{class: {member: def}}`` for every class in *root*'s stubs, and
    the top-level functions under ``""``; a property accessor is not a
    member a row registers."""
    out: "dict[str, dict[str, ast.FunctionDef]]" = {}
    for p in root.rglob("src/**/*.pyi"):
        for node in ast.parse(p.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef):
                out.setdefault("", {})[node.name] = node
            if isinstance(node, ast.ClassDef):
                out.setdefault(node.name, {}).update(
                    {
                        f.name: f
                        for f in node.body
                        if isinstance(f, ast.FunctionDef)
                        and not f.decorator_list
                    }
                )
    return out


#: Every generated C file the fixture wrote, for the row-count check.
_CS: "list[str]" = []


@pytest.fixture(scope="module")
def pairs(tmp_path_factory):
    """``(project, class, member, flags, stub def)`` for every row."""
    where = tmp_path_factory.mktemp("gh2212obj")
    roots = F.build(where, projects={k: F.PROJECTS[k] for k in _ROWS})
    roots["fn"] = _functions_project(where)
    out = []
    _CS.clear()
    _CS.extend(
        c.read_text(encoding="utf-8")
        for root in roots.values()
        for c in root.rglob("native/src/**/*.c")
    )
    for row, root in roots.items():
        stubs = _stubs(root)
        for cls, rows in _tables(root).items():
            for name, flags in rows.items():
                out.append((row, cls, name, flags, stubs[cls].get(name)))
    return out


def _named(fn: ast.FunctionDef, cls: str) -> "list[str]":
    """The parameters a caller names: a method's without its ``self``."""
    names = [a.arg for a in fn.args.posonlyargs + fn.args.args]
    return names[1:] if cls else names


def test_the_fixture_reaches_every_object_kind(pairs):
    """Armed: a standalone object, a serializable one, a module object and
    a view were compared, each with rows of both conventions that name a
    parameter."""
    classes = {cls for _r, cls, *_ in pairs}
    assert {"Fir", "Ser", "O", "Peek"} <= classes, classes
    seen = {
        ("METH_KEYWORDS" in flags)
        for _r, c, _n, flags, fn in pairs
        if fn is not None and _named(fn, c)
    }
    assert seen == {True, False}, seen
    assert any(n == "set_state" for _r, _c, n, *_ in pairs)


def test_the_fixture_reaches_every_function(pairs):
    """Armed: every function the fixture declares was compared on the
    module face, the parameterless one included."""
    compared = {n for _r, c, n, _f, fn in pairs if c == "" and fn}
    assert {name for name, *_ in _FUNCTIONS} <= compared, compared


def test_every_row_has_a_stub(pairs):
    missing = [f"{r} {c}.{n}" for r, c, n, _f, fn in pairs if fn is None]
    assert not missing, "\n".join(missing)


def test_a_stub_is_positional_only_exactly_where_its_row_is(pairs):
    wrong = [
        f"{row} {cls}.{name}: registers {flags}, but its stub"
        f" {'has' if fn.args.posonlyargs else 'has no'} `/`"
        for row, cls, name, flags, fn in pairs
        if fn is not None
        and _named(fn, cls)
        and bool(fn.args.posonlyargs) == ("METH_KEYWORDS" in flags)
    ]
    assert not wrong, "\n".join(wrong)


def test_every_row_of_every_table_is_read(pairs):
    """The reader accounts for every named row: a row its pattern misses
    would leave that member's stub unmarked, which the comparison above
    cannot see, because it reads rows through the same function."""
    tables = sum(len(_table_bodies(text)) for text in _CS)
    owners = {cls for _r, cls, *_ in pairs}
    assert tables >= len(owners), (tables, sorted(owners))
    unread = [u for text in _CS for u in unread_rows(text)]
    assert not unread, "\n".join(unread)
