"""gh-1488: a state default that is a C constant never reaches Python as code.

``--state threshold:int:LVL_INFO`` is the ordinary way to seed a field from a
header constant, and every C face compiles against it. The Python faces
restated it verbatim -- ``Gate(threshold=LVL_INFO)`` in the test, the
benchmark and the doctest, ``threshold: int = LVL_INFO`` in the stub, and for
a floating field ``M_PI.0``, a SyntaxError -- so a fresh project's suite
failed five tests with ``NameError`` on its first run.

Two checks, because they fail differently. The first reads every generated
Python file -- its code AND the doctests inside its docstrings -- and asserts
the constant is never a name there: that catches the stub, whose doctests no
test run executes. The second builds the extension and runs the suite jm
generated, which is what the issue reported and the only thing that can show
the omitted keyword really lets the C default run.

Both object shapes are walked: a module object's stub comes from the other
``.pyi`` writer, and the two are a peer pair.
"""

from __future__ import annotations

import ast
import doctest
import shutil
from pathlib import Path

import pytest

from _jmrun import run_cli
from test_gh1109_seeded_construction_is_attempted import _pytest_counts

#: One integer and one floating constant: the float branch of `_py_default`
#: appended `.0` to whatever it was given, so it failed differently. And a
#: bool (gh-1506): its branch mapped any non-`true` spelling to `False`, so
#: the constant never leaked by name -- the stub stated a WRONG default
#: instead. It is defined as 1, so every face that still says `False`
#: disagrees with what C seeds.
_INT_C, _FLOAT_C, _BOOL_C = "GATE_LEVEL", "GATE_SCALE", "GATE_ON"
_STATE = (
    "--state",
    f"threshold:int:{_INT_C}",
    "--state",
    f"scale:double:{_FLOAT_C}",
    "--state",
    "n:int:3",
    "--state",
    f"on:bool:{_BOOL_C}",
    "--state",
    "off:bool:0",
    "--state",
    "lit:bool:1",
)
_DEFINES = (
    f"#define {_INT_C} 20\n#define {_FLOAT_C} 2.5\n#define {_BOOL_C} 1\n"
)

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)


def _scaffold(tmp_path: Path, shape: str) -> Path:
    """A project with a ``gate`` object whose defaults are header constants.

    The constants are defined in the object's sacred header, which is where
    an author puts them; jm never reads it.
    """
    assert run_cli("new", "levels", cwd=tmp_path).returncode == 0
    root = tmp_path / "levels"
    extra = ("--module", "m") if shape == "module" else ()
    if extra:
        assert run_cli("module", "m", cwd=root).returncode == 0
    out = run_cli("object", "gate", "--no-step", *_STATE, *extra, cwd=root)
    assert out.returncode == 0, out.stdout
    header = root / "native" / "inc" / "gate" / "gate_core.h"
    text = header.read_text(encoding="utf-8")
    anchor = '#include "clib_common.h"\n'
    assert text.count(anchor) == 1
    header.write_text(
        text.replace(anchor, anchor + _DEFINES), encoding="utf-8"
    )
    return root


def _python_names(source: str) -> "set[str]":
    """Every name read by *source* or by a doctest in one of its strings."""
    tree = ast.parse(source)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    parser = doctest.DocTestParser()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for ex in parser.get_examples(node.value):
                names |= _python_names(ex.source)
    return names


@pytest.mark.parametrize("shape", ["standalone", "module"])
def test_no_generated_python_names_the_constant(
    tmp_path: Path, shape: str
) -> None:
    root = _scaffold(tmp_path, shape)
    files = sorted(
        p
        for p in (root / "src").rglob("*")
        if p.suffix in (".py", ".pyi")
        and "gate" in p.read_text("utf-8").lower()
    )
    # A walk that found nothing would pass: name the faces it must reach.
    kinds = {p.suffix for p in files} | {
        p.parent.name for p in files if p.suffix == ".py"
    }
    assert {".pyi", "tests", "benchmarks"} <= kinds, files
    for path in files:
        leaked = _python_names(path.read_text("utf-8")) & {
            _INT_C,
            _FLOAT_C,
            _BOOL_C,
        }
        assert not leaked, f"{path.relative_to(root)} names {leaked}"


@pytest.mark.parametrize("shape", ["standalone", "module"])
def test_the_stub_documents_the_constant_by_name(
    tmp_path: Path, shape: str
) -> None:
    """The signature says ``...``; the prose is where a reader learns what
    that stands for, so it keeps the C name rather than ``default ...``."""
    root = _scaffold(tmp_path, shape)
    stub = next(
        p
        for p in (root / "src").rglob("*.pyi")
        if "class Gate" in p.read_text("utf-8")
    ).read_text("utf-8")
    assert f"threshold : int, default {_INT_C}" in stub
    assert f"scale : float, default {_FLOAT_C}" in stub
    assert "threshold: int = ..." in stub
    assert "n: int = 3" in stub
    # gh-1506: the bool constant is `...` and named in prose, like the rest;
    # a numeric bool literal is still restated, as the value it means.
    assert "on: bool = ..." in stub
    assert f"on : bool, default {_BOOL_C}" in stub
    assert "off: bool = False" in stub
    # `1` is a literal the init-param rule accepts; both peers read any
    # non-`true` spelling as False, so it was restated inverted.
    assert "lit: bool = True" in stub


@pytest.mark.parametrize("shape", ["standalone", "module"])
def test_no_python_face_restates_a_bool_constant_as_a_literal(
    tmp_path: Path, shape: str
) -> None:
    """gh-1506: the bool leak was a WRONG literal, not the constant's name,
    so the name-walk above cannot see it. Every construction call and
    signature must leave `on` to the binding's own default."""
    root = _scaffold(tmp_path, shape)
    files = [
        p
        for p in (root / "src").rglob("*")
        if p.suffix in (".py", ".pyi") and "Gate" in p.read_text("utf-8")
    ]
    assert len(files) >= 3, files
    for path in files:
        text = path.read_text("utf-8")
        assert "on=False" not in text, path.relative_to(root)
        assert "on: bool = False" not in text, path.relative_to(root)


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
@pytest.mark.parametrize("shape", ["standalone", "module"])
def test_the_generated_suite_runs_green(tmp_path: Path, shape: str) -> None:
    """What the issue reported: the scaffolded suite, built and run."""
    root = _scaffold(tmp_path, shape)
    out = run_cli("test", cwd=root)
    assert out.returncode == 0, out.stdout
    counts = _pytest_counts(out.stdout)
    assert counts.get("passed", 0) > 0, out.stdout
    assert counts.get("failed", 0) == 0, out.stdout
    assert counts.get("skipped", 0) == 0, out.stdout
