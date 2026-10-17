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
and reads the two artefacts and nothing else: each type's ``PyMethodDef``
rows in the C it was compiled from, and the ``.pyi`` class of that name. A
row without ``METH_KEYWORDS`` must have a ``/`` in its stub, and a row with
it must not, so a stub can neither accept a keyword the binding refuses nor
refuse one it takes. Nothing here lists a member or a shape.
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

from just_makeit._stubs import method_table_flags  # noqa: E402

#: The object projects: standalone shapes, serializable, module + view. The
#: kind modules (`kinds`) have their own gate, gh-2212's
#: `test_gh2212_positional_stub_slash.py`.
_ROWS = ("std", "shapes", "mod")


def _tables(root: Path) -> "dict[str, dict[str, str]]":
    """``{class: {member: flags}}`` for each extension type in *root*'s C,
    keyed by the class name its ``tp_name`` ends in."""
    out: "dict[str, dict[str, str]]" = {}
    for c in root.rglob("native/src/**/*.c"):
        text = c.read_text(encoding="utf-8")
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
    """``{class: {member: def}}`` for every class in *root*'s stubs; a
    property accessor is not a member a row registers."""
    out: "dict[str, dict[str, ast.FunctionDef]]" = {}
    for p in root.rglob("src/**/*.pyi"):
        for node in ast.parse(p.read_text(encoding="utf-8")).body:
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


@pytest.fixture(scope="module")
def pairs(tmp_path_factory):
    """``(project, class, member, flags, stub def)`` for every row."""
    roots = F.build(
        tmp_path_factory.mktemp("gh2212obj"),
        projects={k: F.PROJECTS[k] for k in _ROWS},
    )
    out = []
    for row, root in roots.items():
        stubs = _stubs(root)
        for cls, rows in _tables(root).items():
            for name, flags in rows.items():
                out.append((row, cls, name, flags, stubs[cls].get(name)))
    return out


def _named(fn: ast.FunctionDef) -> "list[str]":
    """The parameters a caller names, ``self`` aside."""
    return [a.arg for a in fn.args.posonlyargs + fn.args.args][1:]


def test_the_fixture_reaches_every_object_kind(pairs):
    """Armed: a standalone object, a serializable one, a module object and
    a view were compared, each with rows of both conventions that name a
    parameter."""
    classes = {cls for _r, cls, *_ in pairs}
    assert {"Fir", "Ser", "O", "Peek"} <= classes, classes
    seen = {
        ("METH_KEYWORDS" in flags)
        for _r, _c, _n, flags, fn in pairs
        if fn is not None and _named(fn)
    }
    assert seen == {True, False}, seen
    assert any(n == "set_state" for _r, _c, n, *_ in pairs)


def test_every_row_has_a_stub(pairs):
    missing = [f"{r} {c}.{n}" for r, c, n, _f, fn in pairs if fn is None]
    assert not missing, "\n".join(missing)


def test_a_stub_is_positional_only_exactly_where_its_row_is(pairs):
    wrong = [
        f"{row} {cls}.{name}: registers {flags}, but its stub"
        f" {'has' if fn.args.posonlyargs else 'has no'} `/`"
        for row, cls, name, flags, fn in pairs
        if fn is not None
        and _named(fn)
        and bool(fn.args.posonlyargs) == ("METH_KEYWORDS" in flags)
    ]
    assert not wrong, "\n".join(wrong)
