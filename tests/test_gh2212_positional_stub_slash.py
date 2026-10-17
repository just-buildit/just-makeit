"""gh-2212: a stub marks a parameter list ``/`` exactly when its binding
takes no keywords.

A handle method registered ``METH_VARARGS`` (positional only) had a stub
``def write(self, iq: ...)``, so ``write(iq=...)`` type-checked and then
raised ``TypeError``. The same held for a handle factory and ``set_state``
(``METH_O``), for every function of a capsule module, and for a composer's
``execute``, ``from_json``, ``from_file`` and a generating source's
``steps``.

The gate reads the two artefacts and never the shape: each ``PyMethodDef``
row the extension registers, and the ``.pyi`` function of that name. A row
without ``METH_KEYWORDS`` must have a ``/`` in its stub, and a row with it
must not -- both directions, so a stub cannot refuse a keyword the binding
takes either. The handle cases are gh-2207's walk over every method the
dispatch keys form; the module around each also carries a factory and the
serializable triplet, and a capsule module carries every function kind.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit import _capsule, _handle  # noqa: E402
from test_gh2207_handle_stub_param_names import (  # noqa: E402
    _BASE,
    _methods,
)

#: A `{"name", <fn, any cast>, <flags>,` row of a `PyMethodDef` table.
_ROW = re.compile(
    r'\{"(\w+)",\s*[^,"]+,\s*(METH_[A-Z_]+(?:\s*\|\s*METH_[A-Z_]+)*)\s*,'
)


def _rows(ext: str) -> "dict[str, set[str]]":
    """Every registered name and the flags each row of that name carries:
    a composer registers members on several types, so one name may have
    more than one row."""
    out: "dict[str, set[str]]" = {}
    for m in _ROW.finditer(ext):
        out.setdefault(m.group(1), set()).add(m.group(2))
    return out


def _stub_defs(pyi: str) -> "list[tuple[ast.FunctionDef, list[str]]]":
    """Every function the stub declares, with the parameters a caller could
    name: a method's without its ``self``/``cls``."""
    out = []
    for node in ast.walk(ast.parse(pyi)):
        if isinstance(node, (ast.ClassDef, ast.Module)):
            for f in node.body:
                if isinstance(f, ast.FunctionDef):
                    params = [a.arg for a in f.args.posonlyargs + f.args.args]
                    if isinstance(node, ast.ClassDef):
                        params = params[1:]
                    out.append((f, params))
    return out


def _disagreements(ident: str, ext: str, pyi: str) -> "list[str]":
    """Each stub whose ``/`` says something its binding's flags do not."""
    wrong = []
    rows = _rows(ext)
    for fn, params in _stub_defs(pyi):
        if fn.name not in rows or not params:
            continue  # not a registered member, or nothing to name
        keywords = {"METH_KEYWORDS" in f for f in rows[fn.name]}
        if len(keywords) != 1:
            wrong.append(f"{ident}: {fn.name}'s rows disagree: {rows}")
            continue
        slash = bool(fn.args.posonlyargs)
        if slash == keywords.pop():
            wrong.append(
                f"{ident}: {fn.name} registers {rows[fn.name]}, but its"
                f" stub {'has' if slash else 'has no'} `/`"
            )
    return wrong


_EXTRAS = {
    "serializable": True,
    "factories": [
        {
            "name": "from_blob",
            "create_fn": "h_restore",
            "init_params": [{"name": "blob", "type": "bytes"}],
        }
    ],
}


@pytest.fixture(scope="module")
def handle_cases():
    """``(id, ext, pyi)`` for each handle method gh-2207's walk forms and
    jm accepts."""
    out = []
    for ident, m in _methods():
        cfg = {
            "project": {"name": "p"},
            "module": {"h": {**_BASE, **_EXTRAS, "methods": [m]}},
        }
        try:
            ext = _handle.render_ext(cfg, "h")
            pyi = _handle.render_pyi(cfg, "h")
        except (ValueError, NotImplementedError):
            continue
        out.append((ident, ext, pyi))
    return out


def test_the_walk_reaches_both_conventions(handle_cases):
    """Armed: methods registered with and without keywords, each with a
    parameter, and the factory and ``set_state`` rows."""
    seen = set()
    for _ident, ext, pyi in handle_cases:
        rows = _rows(ext)
        assert {"from_blob", "set_state", "op"} <= set(rows), rows
        if any(f.name == "op" and p for f, p in _stub_defs(pyi)):
            seen |= {"METH_KEYWORDS" in f for f in rows["op"]}
    assert seen == {True, False}, seen


def test_a_handle_stub_is_positional_only_where_its_binding_is(
    handle_cases,
):
    wrong = [
        w
        for ident, ext, pyi in handle_cases
        for w in _disagreements(ident, ext, pyi)
    ]
    assert not wrong, "\n".join(wrong)


def test_a_capsule_stub_is_positional_only_where_its_binding_is():
    cfg = {
        "project": {"name": "p"},
        "module": {
            "c": {
                "kind": "capsule",
                "backing": "cb",
                "header": "c/c.h",
                "init_params": [{"name": "rate", "type": "double"}],
                "methods": [
                    {
                        "name": "execute",
                        "arg_type": "float[]",
                        "return_type": "float[]",
                        "caller_out": True,
                    },
                    {"name": "reset"},
                ],
                "properties": [
                    {"name": "gain", "type": "double", "writable": True}
                ],
            }
        },
    }
    ext = _capsule.render_ext(cfg, "c")
    pyi = _capsule.render_pyi(cfg, "c")
    rows = _rows(ext)
    # Armed: every function kind the module registers was compared.
    assert {
        "cb_create",
        "cb_execute",
        "cb_reset",
        "cb_destroy",
        "cb_get_gain",
        "cb_set_gain",
    } <= set(rows), rows
    wrong = _disagreements("capsule", ext, pyi)
    assert not wrong, "\n".join(wrong)


def test_a_composer_stub_is_positional_only_where_its_binding_is():
    """Every type a composer registers members on, through
    `test_composer_codegen`'s fixtures: one with a generating source
    (``steps``) and one with JSON (``from_json`` / ``from_file``)."""
    from test_composer_codegen import _cfg, _gen_cfg

    from just_makeit import _composer

    compared = set()
    wrong = []
    for ident, cfg in (("composer", _cfg()), ("generating", _gen_cfg())):
        for mod, body in cfg["module"].items():
            if body.get("kind") != "composer":
                continue
            ext = _composer.render_ext(cfg, mod)
            pyi = _composer.render_pyi(cfg, mod)
            rows = _rows(ext)
            compared |= {f.name for f, p in _stub_defs(pyi) if p} & set(rows)
            wrong += _disagreements(ident, ext, pyi)
    # Armed: the positional members and a keyword one were compared.
    assert {"execute", "from_json", "from_file", "steps", "compose"} <= (
        compared
    ), compared
    assert not wrong, "\n".join(wrong)
