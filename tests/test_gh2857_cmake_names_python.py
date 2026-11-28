"""gh-2857: a test's own cmake configure names the interpreter it imports with.

The test-suite side of gh-814. A test that configures a scaffolded project
with ``cmake -S ... -B ...`` and named no ``Python3_EXECUTABLE`` built the
extension for whatever ``python3`` came first on ``PATH``. On a box whose
``/usr/bin/python3`` is 3.14 while the suite runs 3.12, the extension came
out ``*.cpython-314-*.so`` and the import failed: ``ModuleNotFoundError: No
module named 'p.dsp.filters.filters'`` in ``test_nested_modules``, on
``main`` too. CI was green only because its image has one interpreter.

``tests/_cmake.py`` declares the one spelling, :data:`_cmake.PYTHON3_ARG`.
These scans hold the suite to it:

- every ``["cmake", "-S", ...]`` list passes it, or ``-DBUILD_PYTHON=OFF``
  and builds no extension at all;
- no other file spells ``-DPython3_EXECUTABLE={sys.executable}`` itself,
  so the choice has one place to change. A configure pointed at ANOTHER
  interpreter on purpose (a project's own venv), or an assertion about
  what jm prints, names that one and is not this.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import _cmake

TESTS = Path(__file__).parent


def _sources() -> "list[tuple[Path, str, ast.Module]]":
    out = []
    for f in sorted(TESTS.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        out.append((f, src, ast.parse(src)))
    return out


def _configure_lists(tree: ast.Module) -> "list[ast.List]":
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.List)
        and len(n.elts) >= 2
        and isinstance(n.elts[0], ast.Constant)
        and n.elts[0].value == "cmake"
        and isinstance(n.elts[1], ast.Constant)
        and n.elts[1].value == "-S"
    ]


def test_the_one_spelling_names_this_interpreter():
    assert _cmake.PYTHON3_ARG == f"-DPython3_EXECUTABLE={sys.executable}"


def test_every_configure_names_the_interpreter_or_builds_no_extension():
    unnamed = []
    seen = 0
    for f, src, tree in _sources():
        for node in _configure_lists(tree):
            seen += 1
            names = {e.id for e in node.elts if isinstance(e, ast.Name)} | {
                e.value
                for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
            if (
                "PYTHON3_ARG" not in names
                and "-DBUILD_PYTHON=OFF" not in names
            ):
                unnamed.append(f"{f.name}:{node.lineno}")
    # Not vacuous: the suite configures dozens of projects this way.
    assert seen > 40, seen
    assert not unnamed, (
        "a cmake configure that builds the extension names no interpreter,"
        " so CMake takes the first python3 on PATH (gh-2857): pass"
        " `_cmake.PYTHON3_ARG`, or -DBUILD_PYTHON=OFF if it builds none: "
        + ", ".join(unnamed)
    )


def test_no_other_file_spells_the_interpreter_argument():
    spelled = []
    for f, src, tree in _sources():
        if f.name in ("_cmake.py", Path(__file__).name):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                text = ast.get_source_segment(src, node) or ""
                if "-DPython3_EXECUTABLE={sys.executable}" in text:
                    spelled.append(f"{f.name}:{node.lineno}")
    assert not spelled, (
        "spell the interpreter argument as `_cmake.PYTHON3_ARG`, the one"
        " place that chooses it (gh-2857): " + ", ".join(spelled)
    )
