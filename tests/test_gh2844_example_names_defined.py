"""gh-2844: every name a generated example uses, the example defines.

An object with an array ``init_param`` constructs itself in its examples as
``W(h=np.zeros(1, dtype=np.float32))``, and ``step.__doc__`` showed that line
with no ``import numpy as np`` above it: run as a doctest, ``NameError``. The
``steps`` doc beside it did import numpy, so two builders of one example
disagreed. The fix is one helper (`_docstring.ensure_np_import`) every demo
passes through; this file is the gate over the whole generated surface.

The gate reads the OUTPUT, not the builders. It scaffolds objects whose
constructor takes an array, in each shape that renders examples -- a scalar
step, an array step, a void step, a module object, and a method of each
kind -- then takes every docstring jm generated, from BOTH faces: the runtime
``__doc__`` literals in ``<comp>_ext.c`` and the ``.pyi`` docstrings. Each
docstring's examples are read by `doctest.DocTestParser` and checked
statically: a name an example loads must be a builtin or bound by an earlier
statement of the SAME docstring (a doctest's globals do not cross
docstrings). Static, so it needs no compiler and runs in the plain suite; the
built-extension execution of the examples is gh-1212's test.
"""

from __future__ import annotations

import ast
import builtins
import doctest
import re
from pathlib import Path

import pytest
from _jmrun import run_cli

#: (kind, [argv...]) -- every step shape with an array constructor argument.
OBJECTS = [
    ("scal", ["--arg-type", "float", "--return-type", "float"]),
    ("arr", ["--arg-type", "float[]", "--return-type", "float"]),
    ("void", ["--arg-type", "void", "--return-type", "float"]),
    ("cplx", ["--arg-type", "float _Complex", "--return-type", "float"]),
]

#: Method shapes added to the module object and the standalone one.
METHODS = [
    ("blk", ["--arg-type", "float[]", "--return-type", "float"]),
    ("fixed", ["--param", "n:int", "--return-type", "float"]),
    ("noarg", ["--return-type", "float"]),
    ("varout", ["--arg-type", "float[]", "--variable-output"]),
    ("rlist", ["--return-type", "p_rec_t", "--result-field", "a:float"]),
    (
        "rone",
        ["--single", "--return-type", "p_rec_t", "--result-field", "a:float"],
    ),
    (
        "rdt",
        [
            "--variable-output",
            "--record-dtype",
            "p_rec_t",
            "--result-field",
            "a:float",
        ],
    ),
    ("multi", ["--arg-type", "float[]", "--multi-output", "float"]),
]


def _ok(*argv: str, cwd: Path) -> None:
    r = run_cli(*argv, cwd=cwd)
    assert r.returncode == 0, (argv, r.stdout, r.stderr)


@pytest.fixture(scope="module")
def proj(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("gh2844") / "p"
    root.parent.mkdir(exist_ok=True)
    _ok("new", "p", cwd=root.parent)
    _ok("module", "mod", cwd=root)
    for name, args in OBJECTS:
        for where in ([], ["--module", "mod"]):
            obj = name if not where else f"m_{name}"
            _ok(
                "object",
                obj,
                *args,
                "--init-param",
                "h:float[]",
                *where,
                cwd=root,
            )
            for mname, margs in METHODS:
                _ok("method", obj, mname, *margs, *where, cwd=root)
    _ok(
        "view",
        "m_scal",
        "Vw",
        "--module",
        "mod",
        "--create-fn",
        "m_scal_create_vw",
        cwd=root,
    )
    return root


def _c_docs(text: str) -> list[str]:
    """Every run of adjacent C string literals carrying a ``>>>`` prompt."""
    lit = r'"(?:[^"\\\n]|\\.)*"'
    docs = []
    for run in re.finditer(rf"{lit}(?:\s*{lit})*", text):
        parts = re.findall(lit, run.group(0))
        body = "".join(ast.literal_eval(p) for p in parts)
        if ">>>" in body:
            docs.append(body)
    return docs


def _pyi_docs(text: str) -> list[str]:
    return [
        d
        for n in ast.walk(ast.parse(text))
        if isinstance(
            n,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        )
        and (d := ast.get_docstring(n, clean=False))
        and ">>>" in d
    ]


def undefined_names(doc: str) -> list[str]:
    """Names the doctests of *doc* load before anything binds them.

    Examples
    --------
    >>> undefined_names(">>> obj = W(h=np.zeros(1))")
    ['W', 'np']
    >>> undefined_names(">>> import numpy as np\\n>>> x = np.ones(1)")
    []
    """
    bound = set(dir(builtins))
    missing: list[str] = []
    for ex in doctest.DocTestParser().get_examples(doc):
        tree = ast.parse(ex.source)
        # Loads first: `x = x + 1` reads the old x; an import binds after.
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id not in bound
                and node.id not in missing
            ):
                missing.append(node.id)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                bound.add(node.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    bound.add((a.asname or a.name).split(".")[0])
    return missing


def _all_docs(root: Path) -> list[tuple[str, str]]:
    out = []
    for f in sorted(root.glob("native/src/**/*_ext*.c")):
        out += [(f"{f.name}", d) for d in _c_docs(f.read_text("utf-8"))]
    for f in sorted(root.glob("src/**/*.pyi")):
        out += [(f"{f.name}", d) for d in _pyi_docs(f.read_text("utf-8"))]
    return out


def test_the_scaffold_has_array_constructor_examples(proj: Path) -> None:
    """The arrangement must actually render the shape under test."""
    docs = _all_docs(proj)
    assert sum("np.zeros(1" in d for _, d in docs) >= 8, len(docs)


def test_every_generated_example_defines_the_names_it_uses(proj: Path) -> None:
    bad = [
        (src, undefined_names(d), d.splitlines()[0])
        for src, d in _all_docs(proj)
        if undefined_names(d)
    ]
    assert not bad, "\n".join(map(str, bad))
