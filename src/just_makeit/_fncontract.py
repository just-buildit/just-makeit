"""_fncontract.py — the contract smoke test a functions-only module gets.

gh-2127. A module that declares module-level functions builds an extension and
generated no Python test that calls it, so a broken wrapper passed `jm build`
and `jm test` both (gh-1950 made the empty suite a pass, not a test). An object
gets a scaffolded test; a function did not. A mixed module's functions are
covered too (#2156).

The maintainer's decision (gh-2127, option c) fixes what this test asserts:
the CONTRACT the generated binding makes, not any value. For each wrapper it
calls the function with arguments derived from its declared parameters and
checks the call succeeds and the result has the declared return type. It does
not assert values: at scaffold time the body is the author's stub, so jm has no
expected value, and the header's ``@code`` examples already carry the cases the
author documented. A binding that breaks the contract (a wrong return type, a
parameter the parser does not read) fails here. A wrong kernel does not, and is
not meant to.

The argument and return mappings are the ones the binding already uses, read
from :data:`_types._CTYPE_META` and the array helpers, not a second table. A
function whose shape the contract cannot exercise yet (an out parameter, a
record result, a string return) is written as a SKIPPED test naming the reason,
never silently dropped, so ``jm test`` reports it.

Examples
--------
>>> fn = {
...     "name": "fft_scale",
...     "params": [{"name": "n", "type": "int"}, {"name": "x", "type": "float"}],
...     "return_type": "float",
... }
>>> text, uses_np = case(fn)
>>> print(text, end="")
    def test_fft_scale_contract(self):
        result = module.fft_scale(n=1, x=1.0)
        self.assertIsInstance(result, float)
>>> uses_np
False
"""

from __future__ import annotations

import re
from pathlib import Path

from . import _config as C
from . import _render as R
from . import _textio
from . import _types as T
from ._docstring import class_import_path

#: Function flags that change the Python call's shape or its result, so a
#: contract written for the plain form would not describe this wrapper.
_SHAPE_FLAGS = (
    "out_type",
    "result_fields",
    "variable_output",
    "why",
    "max_results_param",
    "out_size",
)


def _arg(p: dict) -> "str | None":
    """The Python expression that passes parameter *p*, or None if its shape
    has no contract argument yet (the caller then skips the test).

    An array is a four-element zero buffer of its element's numpy dtype: the
    binding checks dtype and contiguity, not values, and four is enough for
    any stub kernel. An OUT array takes the same buffer: the binding requires
    the caller to supply a writable array of the element dtype and fills it,
    so a zero buffer is exactly what it expects. A scalar is the type's own
    "one", which the binding parses for any value. An out scalar has no
    Python form the binding accepts, so it has no argument here.
    """
    t = p["type"]
    if T.is_array_param_type(t):
        meta = T._CTYPE_META.get(T.array_elem_ctype(t))
        if meta is None or meta["kind"] not in ("int", "float", "complex"):
            return None
        return f"np.zeros(4, dtype={meta['py_type']})"
    if p.get("out"):
        return None
    meta = T._CTYPE_META.get(t)
    if meta is None or meta["kind"] not in ("int", "float", "complex"):
        return None
    return meta["py_one"]


def _result(rt: str) -> "str | None":
    """The assertion that the wrapper's result has the declared return type,
    or None for a return the contract cannot check yet.

    ``int`` excludes ``bool`` because the binding returns a Python bool for a
    ``bool`` return and an int for every other integer, and an int check that
    accepted a bool would pass a wrong-typed binding.
    """
    if rt == "void":
        return "self.assertIsNone(result)"
    if rt == "bool":
        return "self.assertIsInstance(result, bool)"
    meta = T._CTYPE_META.get(rt)
    if meta is None:
        return None
    kind = meta["kind"]
    if kind == "int":
        return (
            "self.assertIsInstance(result, int)\n"
            "        self.assertNotIsInstance(result, bool)"
        )
    if kind in ("float", "complex"):
        pytype = "float" if kind == "float" else "complex"
        return f"self.assertIsInstance(result, {pytype})"
    return None


def _skip_reason(fn: dict) -> "str | None":
    """Why *fn* gets a skipped test rather than a contract call, or None."""
    for flag in _SHAPE_FLAGS:
        if fn.get(flag):
            return f"{flag} changes the call's shape: no contract call yet"
    for p in fn.get("params", []):
        if _arg(p) is None:
            return (
                f"parameter {p['name']!r} of type {p['type']!r} has no "
                "contract argument yet"
            )
    if _result(fn.get("return_type", "void")) is None:
        return (
            f"return type {fn.get('return_type')!r} has no contract check yet"
        )
    return None


def case(fn: dict) -> "tuple[str, bool]":
    """The test method for one wrapper, and whether it uses numpy.

    A wrapper whose shape the contract does not cover yet becomes a method
    decorated ``unittest.skip`` with the reason, so the skip is visible in the
    run and cannot pass for a check that ran.
    """
    name = fn["name"]
    reason = _skip_reason(fn)
    if reason is not None:
        return (
            f"    @unittest.skip({reason!r})\n"
            f"    def test_{name}_contract(self):\n"
            "        pass\n"
        ), False
    args = [f"{p['name']}={_arg(p)}" for p in fn.get("params", [])]
    uses_np = any(
        T.is_array_param_type(p["type"]) for p in fn.get("params", [])
    )
    call = f"module.{name}({', '.join(args)})"
    check = _result(fn.get("return_type", "void"))
    return (
        f"    def test_{name}_contract(self):\n"
        f"        result = {call}\n"
        f"        {check}\n"
    ), uses_np


def _class_name(leaf: str) -> str:
    """``fft`` -> ``Fft``; ``filter_bank`` -> ``FilterBank``."""
    return "".join(p.capitalize() for p in re.split(r"[^0-9A-Za-z]+", leaf))


def render(import_path: str, leaf: str, functions: list[dict]) -> str:
    """The whole test module for a functions-only module.

    *import_path* is the dotted path the module's package imports from
    (``dsp.fft``); *leaf* is the module's own name, which names the class.
    The test imports the module, not its functions, so a skipped wrapper
    leaves no unused import behind.
    """
    cases = []
    uses_np = False
    for fn in functions:
        text, np_used = case(fn)
        cases.append(text)
        uses_np = uses_np or np_used
    head = "import unittest\n\n"
    if uses_np:
        head += "import numpy as np\n\n"
    head += f"import {import_path} as module\n\n\n"
    body = (
        f"class Test{_class_name(leaf)}Functions(unittest.TestCase):\n"
        f'    """Contract smoke test for the module-level functions (gh-2127).\n'
        "\n"
        "    Each wrapper is called with arguments derived from its declared\n"
        "    parameters. The test asserts the call succeeds and the result has\n"
        "    the declared return type. It asserts no values: the bodies are the\n"
        "    author's, and their documented cases are the header's ``@code``\n"
        '    examples.\n    """\n\n'
    )
    return head + body + "\n".join(cases)


def _qualifies(cfg: dict, module: str) -> bool:
    """A module gets the contract test when it declares at least one function.

    Objects do not matter: the file is ``test_<leaf>_functions.py``, which no
    object's test is named, so a mixed module's functions are covered beside
    its objects' tests (#2156).
    """
    return bool(C.module_functions(cfg, module))


def sync(root: Path, cfg: dict, module: str) -> None:
    """Make the module's contract test match the manifest, both ways.

    Called wherever the module's functions change: a function added or
    removed. A module with functions gets the file, rewritten from the
    manifest's current function list. A module whose last function is removed
    loses it.

    Both directions hold only while the file carries jm's ownership token
    (gh-1489). The token is what makes the file jm's: an author who deletes it
    has taken the file over, and neither a write nor a delete may touch it
    again. A file that exists without the token is therefore left exactly as
    it is, and a file jm never wrote is created only where none exists.

    The file is born owned, so ``apply`` re-renders it from the replay. A
    replay that no longer produces it does not delete it, which is why the
    deletion happens here, in the same command as the change.
    """
    pkg = C.project_name(cfg)
    module_dir = C.module_package_resolved(cfg, module)
    leaf = module_dir.rsplit("/", 1)[-1]
    tests = root / "src" / pkg / module_dir / "tests"
    fname = f"test_{leaf}_functions.py"
    path = tests / fname
    owned = path.is_file() and R.is_owned_render(
        path.read_text(encoding="utf-8"), fname
    )
    if _qualifies(cfg, module):
        if path.is_file() and not owned:
            return
        tests.mkdir(parents=True, exist_ok=True)
        init = tests / "__init__.py"
        if not init.exists():
            _textio.write_text(init, R.TESTS_INIT_PY)
        text = render(
            class_import_path(pkg, module_dir),
            leaf,
            C.module_functions(cfg, module),
        )
        _textio.write_text(path, R.owned_scaffold(text, fname))
        print(f"  write   {path}")
    elif owned:
        path.unlink()
        print(f"  delete  {path}")
