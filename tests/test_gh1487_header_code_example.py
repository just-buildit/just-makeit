"""gh-1487: the ``_core.h`` example uses only what the object declares.

The file comment jm writes at the top of every object's sacred header carries
a lifecycle summary and an ``@code`` example. Both were template text that
assumed a scalar ``step()``:

    jm object gate --no-step
    * gate_state_t *obj = gate_create(0);
    * float _Complex y = gate_step(obj, 0.0f + 0.0f * I);   <- no such function

A blockwise object (``T[] -> U[]``) has only ``steps()``, and its example
called ``step()`` too. The header is create-only, so the wrong example was
frozen into the file a C reader copies first.

The property is checked on what was emitted, not predicted: for every shape
in the matrix the example is lifted out of the header, wrapped in ``main()``
and compiled against that same header with implicit declarations as errors,
and the lifecycle line's verbs must be exactly the ``step`` / ``steps`` /
``reset`` functions the header's code (not its comments) declares.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _jmrun import run_cli  # noqa: E402

_CC = shutil.which("cc") or shutil.which("gcc")

#: One object per shape jm can scaffold, spanning every branch of
#: ``make_step_ctx`` (no-step, blockwise, array-in scalar step, generator,
#: sink, scalar) and the flags that change the create() call or the verbs.
_SHAPES: dict[str, list[str]] = {
    "dflt": [],
    "nostep": ["--no-step"],
    "nostate": ["--no-state"],
    "nostepnostate": ["--no-step", "--no-state"],
    "typed": ["--arg-type", "float", "--return-type", "int16_t"],
    "initp": ["--init-param", "n:size_t:16", "--state", "g:double:1.0"],
    "nostepinit": ["--no-step", "--init-param", "level:int:3"],
    "gen": ["--arg-type", "void", "--return-type", "float"],
    "sink": ["--return-type", "void"],
    "voidvoid": ["--arg-type", "void", "--return-type", "void"],
    "hdronly": [
        "--header-only",
        "--state",
        "g:float:1.0",
        "--arg-type",
        "float",
        "--return-type",
        "float",
    ],
    "hdronlynostep": ["--header-only", "--no-step", "--no-state"],
    "blockwise": ["--preset", "blockwise"],
    "reader": ["--preset", "reader"],
    "arrin": ["--arg-type", "float[]", "--return-type", "float"],
    "varout": [
        "--arg-type",
        "float[]",
        "--return-type",
        "float[]",
        "--variable-output",
    ],
    "opaque": ["--opaque-state", "--no-step"],
    "noreset": ["--no-reset", "--arg-type", "float", "--return-type", "float"],
    "createfn": ["--create-fn", "createfn_open", "--init-param", "n:int:3"],
    "delegate": [
        "--arg-type",
        "float",
        "--return-type",
        "float",
        "--step-delegates-to-steps",
    ],
}


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """One project holding an object per shape (scaffolding is the cost)."""
    base = tmp_path_factory.mktemp("gh1487")
    r = run_cli("new", "p", cwd=base)
    assert r.returncode == 0, r.stderr
    root = base / "p"
    for name, flags in _SHAPES.items():
        r = run_cli("object", name, *flags, cwd=root)
        assert r.returncode == 0, f"{name}: {r.stderr}"
    return root


def _header(root: Path, name: str) -> str:
    return (root / "native" / "inc" / name / f"{name}_core.h").read_text(
        encoding="utf-8"
    )


def _file_comment(h: str) -> str:
    """The leading ``/** ... */`` block -- where the lifecycle line lives."""
    m = re.match(r"\s*/\*\*(.*?)\*/", h, re.S)
    assert m, "header does not open with a file comment"
    return m.group(1)


def _example(h: str) -> list[str]:
    """The ``@code`` body of the file comment, comment prefix stripped."""
    m = re.search(r"@code\n(.*?)\n\s*\*\s*@endcode", _file_comment(h), re.S)
    assert m, "file comment has no @code block"
    return [re.sub(r"^\s*\* ?", "", ln) for ln in m.group(1).splitlines()]


def _declared_verbs(h: str, name: str) -> set[str]:
    """``step`` / ``steps`` / ``reset`` as the header's CODE names them.

    Comment lines are dropped first: the example itself names functions, and
    reading it back would make the check agree with whatever it says.
    """
    code = "\n".join(
        ln
        for ln in re.sub(r"/\*.*?\*/", "", h, flags=re.S).splitlines()
        if not ln.lstrip().startswith("//")
    )
    return set(re.findall(rf"\b{name}_(step|steps|reset)\s*\(", code))


@pytest.mark.parametrize("name", sorted(_SHAPES))
def test_lifecycle_names_only_declared_verbs(project, name):
    h = _header(project, name)
    m = re.search(r"Lifecycle: (.*)", _file_comment(h))
    assert m, "no Lifecycle line"
    said = set(re.findall(r"\b(step|steps|reset)\b", m.group(1)))
    assert said == _declared_verbs(h, name), m.group(0)


@pytest.mark.parametrize("name", sorted(_SHAPES))
def test_example_calls_only_declared_functions(project, name):
    h = _header(project, name)
    body = "\n".join(_example(h))
    called = set(re.findall(rf"\b{name}_(step|steps)\s*\(", body))
    assert called <= _declared_verbs(h, name), body


@pytest.mark.skipif(_CC is None, reason="no C compiler")
@pytest.mark.parametrize("name", sorted(_SHAPES))
def test_example_compiles_against_its_header(project, name, tmp_path):
    """The example, as a function body, compiles against the header."""
    h = _header(project, name)
    tu = tmp_path / "example.c"
    tu.write_text(
        f'#include "{name}/{name}_core.h"\n'
        "int main(void)\n{\n"
        + "".join(f"    {ln}\n" for ln in _example(h))
        + "    return 0;\n}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            _CC,
            "-std=c99",
            "-fsyntax-only",
            "-Werror=implicit-function-declaration",
            "-Werror=int-conversion",
            "-Werror=incompatible-pointer-types",
            f"-I{project / 'native' / 'inc'}",
            str(tu),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, tu.read_text() + proc.stderr
