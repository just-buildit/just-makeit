"""gh-1959: a method whose stub takes no argument refuses one at runtime.

A method with ``arg_type = "void"`` and no params that returns one record
(``single``), or a list of them, was bound as::

    static PyObject *
    Meter_fer(MeterObject *self, PyObject *args)
    ...
    {"fer", (PyCFunction)Meter_fer, METH_VARARGS, ...}

and nothing read ``args``. ``-Wall -Wextra`` reported the parameter (the
``-Werror`` sweep in ``test_preset_build.py`` now builds both shapes, on both
faces), and the call took any positional arguments and dropped them --
``meter.fer(1, 2)`` returned the record -- although both ``.pyi`` writers
declare ``def fer(self)``. Every other no-argument wrapper jm emits is
``METH_NOARGS`` with ``Py_UNUSED(ignored)``; `_methods.call_convention` now
decides that for every method shape, from what the wrapper's body reads.

GATE: the property, read off the generated files rather than listed. Every
method a class's stub declares with ``self`` alone must be ``METH_NOARGS`` in
that class's ``PyMethodDef`` table, and its wrapper must spell the slot
``Py_UNUSED``. The project carries the two record shapes standalone and as an
object of a module, beside the methods jm generates for itself (``reset``,
``destroy``, a state getter, ``__enter__``) as the control.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from _jmrun import run_cli

#: One record, and a list of them: the two shapes this issue is about. The
#: record is ``div_t``, which ``clib_common.h``'s ``<stdlib.h>`` declares.
_REC = ["--arg-type", "void", "--return-type", "div_t"] + [
    "--result-field",
    "quot:int",
    "--result-field",
    "rem:int",
]

#: A table row: ``{"name", (PyCFunction)Wrapper, FLAGS,``.
_ROW = re.compile(
    r'\{\s*"(\w+)",\s*\(PyCFunction\)(?:\(void \*\))?\s*(\w+),\s*'
    r"([A-Z_| ]+?)\s*,"
)


def _no_arg_methods(pyi: Path) -> "dict[str, set[str]]":
    """Class name -> the plain methods its stub declares with ``self`` only.

    Properties and other decorated members are not called, so they are not
    methods here; a ``*args`` or a keyword-only parameter is an argument.
    """
    out: "dict[str, set[str]]" = {}
    for node in ast.parse(pyi.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.ClassDef):
            continue
        for fn in node.body:
            if not isinstance(fn, ast.FunctionDef) or fn.decorator_list:
                continue
            a = fn.args
            only_self = (
                len(a.posonlyargs) + len(a.args) == 1
                and not a.kwonlyargs
                and a.vararg is None
                and a.kwarg is None
            )
            if only_self and fn.name != "__init__":
                out.setdefault(node.name, set()).add(fn.name)
    return out


def _rows(ext: Path) -> "dict[str, tuple[str, str]]":
    """Member name -> (wrapper, flags), from the file's PyMethodDef rows."""
    return {
        m.group(1): (m.group(2), " ".join(m.group(3).split()))
        for m in _ROW.finditer(ext.read_text(encoding="utf-8"))
    }


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("gh1959") / "wp"
    cmds = [("new", "wp", str(root))]
    for obj, module in (("meter", ()), ("frame", ("--module", "m"))):
        if module:
            cmds.append(("module", "m"))
        cmds += [
            ("object", obj, *module, "--state", "k:int:0", "--no-step")
            + ("--arg-type", "void", "--return-type", "void"),
            ("method", obj, "fer", *module, *_REC, "--single"),
            ("method", obj, "rows", *module, *_REC),
        ]
    for cmd in cmds:
        r = run_cli(*cmd, cwd=root if cmd[0] != "new" else None)
        assert r.returncode == 0, f"jm {' '.join(cmd)}:\n{r.stderr}"
    return root


@pytest.mark.parametrize(
    "cls,pyi,ext",
    [
        ("Meter", "src/wp/meter.pyi", "native/src/meter/meter_ext.c"),
        ("Frame", "src/wp/m/m.pyi", "native/src/m/m_ext_frame.c"),
    ],
    ids=["standalone", "module"],
)
def test_a_stub_without_arguments_is_bound_noargs(project, cls, pyi, ext):
    methods = _no_arg_methods(project / pyi).get(cls, set())
    # Armed: the two shapes this issue is about are among those checked.
    assert {"fer", "rows"} <= methods, sorted(methods)
    text = (project / ext).read_text(encoding="utf-8")
    rows = _rows(project / ext)
    wrong = []
    for name in sorted(methods):
        wrapper, flags = rows[name]
        sig = re.search(rf"^{wrapper}\s*\(([^)]*\)?)\)", text, re.M)
        assert sig, f"{cls}.{name}: no definition of {wrapper} in {ext}"
        if flags != "METH_NOARGS" or "Py_UNUSED" not in sig.group(1):
            wrong.append(f"{cls}.{name}: {flags}, ({sig.group(1)})")
    assert not wrong, (
        "a method whose stub takes only `self` must refuse an argument "
        "(METH_NOARGS, its slot Py_UNUSED):\n" + "\n".join(wrong)
    )
