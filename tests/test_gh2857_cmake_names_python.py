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


#: A cmake invocation carrying one of these is not a configure.
_NOT_CONFIGURE = frozenset({"--build", "--install", "-E", "-P", "--preset"})


def _is_cmake(node: ast.expr) -> bool:
    """The program of a command: `"cmake"`, `shutil.which("cmake")`,
    `str(cmake)`, or a variable named for it (`cmake = shutil.which(...)`).
    """
    if isinstance(node, ast.Constant):
        return node.value == "cmake"
    if isinstance(node, ast.Name):
        return "cmake" in node.id.lower()
    if isinstance(node, ast.Call):
        if any(_is_cmake(a) for a in node.args):  # which("cmake"), str(cmake)
            return True
    return False


def _strings(elts: "list[ast.expr]") -> "set[str]":
    return {
        e.value
        for e in elts
        if isinstance(e, ast.Constant) and isinstance(e.value, str)
    }


def _callee(call: ast.Call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")


def configures(tree: ast.Module) -> "list[tuple[ast.AST, list[ast.expr]]]":
    """Every cmake CONFIGURE in *tree*, with the arguments it passes.

    Any spelling the suite has used: a list or a tuple, `-S` or `-B` first,
    the program `"cmake"`, `shutil.which("cmake")` or a variable holding
    it -- and the arguments a test hands a helper named for cmake
    (`_cmake(proj, "-B", ...)`). A configure is a cmake command with a
    source or build directory (`-S`, `-B`, attached or not) and none of
    `_NOT_CONFIGURE`.
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            elts = list(node.elts)
            if not _is_cmake(elts[0]):
                continue
        elif isinstance(node, ast.Call) and "cmake" in _callee(node).lower():
            if _callee(node) == "which":
                continue
            elts = list(node.args)
        else:
            continue
        flags = _strings(elts)
        if flags & _NOT_CONFIGURE:
            continue
        if any(f.startswith(("-S", "-B")) for f in flags):
            out.append((node, elts))
    return out


def _names_the_interpreter(elts: "list[ast.expr]") -> bool:
    """`PYTHON3_ARG`; or another interpreter named on purpose (an
    f-string `-DPython3_EXECUTABLE={venv_python}` -- the one rule there is
    that the choice is made, `test_no_other_file_spells...` keeps THIS
    interpreter's spelling in one place); or no extension built."""
    return (
        "-DBUILD_PYTHON=OFF" in _strings(elts)
        or any(isinstance(e, ast.Name) and e.id == "PYTHON3_ARG" for e in elts)
        or any(
            isinstance(e, ast.JoinedStr)
            and isinstance(e.values[0], ast.Constant)
            and str(e.values[0].value).startswith("-DPython3_EXECUTABLE=")
            for e in elts
        )
    )


def test_the_one_spelling_names_this_interpreter():
    assert _cmake.PYTHON3_ARG == f"-DPython3_EXECUTABLE={sys.executable}"


def test_every_configure_names_the_interpreter_or_builds_no_extension():
    unnamed = []
    seen = 0
    for f, src, tree in _sources():
        for node, elts in configures(tree):
            seen += 1
            if not _names_the_interpreter(elts):
                unnamed.append(f"{f.name}:{node.lineno}")
    # Not vacuous: the suite configures dozens of projects this way.
    assert seen > 40, seen
    assert not unnamed, (
        "a cmake configure that builds the extension names no interpreter,"
        " so CMake takes the first python3 on PATH (gh-2857): pass"
        " `_cmake.PYTHON3_ARG`, or -DBUILD_PYTHON=OFF if it builds none: "
        + ", ".join(unnamed)
    )


def test_the_scan_sees_every_spelling():
    """The scan is the only guard -- `make test`'s isolated runner sets
    VIRTUAL_ENV, which hides the bug -- so it must see each way a test has
    spelled a configure."""
    spellings = {
        "list, -S first": '["cmake", "-S", ".", "-B", "b"]',
        "list, -B first": '["cmake", "-B", "build", "-S", "."]',
        "attached flags": '["cmake", "-S.", "-Bbuild"]',
        "tuple": '("cmake", "-S", ".", "-B", "b")',
        "which()": '[shutil.which("cmake"), "-S", ".", "-B", "b"]',
        "a variable": '[cmake, "-S", str(root), "-B", str(build)]',
        "str() of it": '[str(cmake), "-B", "b", "-S", "."]',
        "a helper's arguments": '_cmake(proj, "-B", "build", "-S", ".")',
    }
    for what, code in spellings.items():
        tree = ast.parse(code)
        assert len(configures(tree)) == 1, what
        assert not _names_the_interpreter(configures(tree)[0][1]), what
        named = code[:-1] + ", PYTHON3_ARG" + code[-1]
        assert _names_the_interpreter(configures(ast.parse(named))[0][1]), what
    for code in (
        '["cmake", "--build", "b"]',
        '["cmake", "--install", "b", "--prefix", "p"]',
        '["cmake", "-E", "copy", "a", "b"]',
        '["cmake", "-P", "s.cmake"]',
        '["python", "-B", "x.py"]',
        'shutil.which("cmake")',
    ):
        assert configures(ast.parse(code)) == [], code


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
