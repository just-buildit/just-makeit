"""gh-1887: a complex default on a method or function param compiles.

A ``parse_type`` scalar parses into a ``<name>_raw`` local, and PyArg leaves
an omitted keyword's local untouched, so the declared default has to SEED
that local. For a complex type the local is a ``Py_complex`` -- a struct,
which takes ``{re, im}`` and never a complex expression. gh-1561 gave that
seed one spelling, ``_types.parse_seed``, and moved the init-param faces onto
it. Three copies of the seed it replaced survived, each
``default or <parse_zero>``: method params (``_context/_parse.py``), module
function params (``_render.py``) and variable_output method params
(``_context/_methods.py``, gh-802). Each emitted a complex default verbatim,
``Py_complex z_raw = 1.0f;``, and the project failed to compile with
"invalid initializer" after an ``apply`` that exited 0.

GATE: no binding seeds a ``parse_type`` local from a declared default by its
      own ``or`` -- the fallback is spelled once, in ``parse_seed`` -- and a
      project declaring a complex default on each face builds, passes its
      own tests, and returns the declared value when the keyword is omitted.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _config as C

PKG = Path(__file__).resolve().parent.parent / "src" / "just_makeit"


def default_seeds(source: str) -> "list[int]":
    """Lines of *source* that pick a seed with an ``or`` whose fallback is a
    type's ``parse_zero``: the rule `_types.parse_seed` owns, re-spelled.

    A copy decides "declared default, else the struct's zero" by itself,
    and the three gh-1561 left behind each decided it without the struct.

    >>> default_seeds('x = p.get("default") or meta["parse_zero"]\\n')
    [1]
    >>> default_seeds('x = d or m.get("parse_zero", "0")\\n')
    [1]
    >>> default_seeds('x = d or _parse_zero\\n')
    [1]
    >>> default_seeds('x = T.parse_seed(ct, p.get("default") or "")\\n')
    []
    """
    out = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        if any(
            (isinstance(n, ast.Constant) and n.value == "parse_zero")
            or (isinstance(n, ast.Name) and "parse_zero" in n.id)
            for v in node.values
            for n in ast.walk(v)
        ):
            out.append(node.lineno)
    return out


def test_the_seed_rule_is_spelled_once():
    bad = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG)
        if {"templates", "examples"} & set(rel.parts):
            continue
        for line in default_seeds(path.read_text(encoding="utf-8")):
            bad.append(f"{rel.as_posix()}:{line}")
    assert bad == [], (
        "a parse_type local seeded by its own `default or parse_zero` "
        "(gh-1887): call `_types.parse_seed`, which spells a Py_complex "
        "default as `{re, im}`:\n  " + "\n  ".join(bad)
    )


#: Each kernel returns the param it was handed, so what crosses back is the
#: value the binding's `_raw` local held -- the seed, when the keyword is
#: omitted. Lifted into the stubs by `--impl`, jm's own mechanism.
IMPL_C = """\
float _Complex
rot(void *state, float _Complex z)
{
    (void)state;
    return z;
}

size_t
grab(void *state, double _Complex z, double _Complex *out)
{
    (void)state;
    out[0] = z;
    return 1;
}

double _Complex
f(double _Complex z)
{
    return z;
}
"""

#: face -> (CLI steps, declared default, Python that calls it with the
#: keyword omitted, what it must print). One project per face, so a
#: regression at one site fails that face alone.
FACES = {
    "method": (
        [
            ("object", "o"),
            (
                "method",
                "o",
                "rot",
                "--arg-type",
                "void",
                "--return-type",
                "float _Complex",
                "--param",
                "z:float _Complex",
                "--impl",
                "{impl}::rot",
            ),
        ],
        "1.5f - 0.25f * I",
        "from p import O; print(O().rot())",
        "(1.5-0.25j)",
    ),
    "variable_output": (
        [
            ("object", "o"),
            (
                "method",
                "o",
                "grab",
                "--arg-type",
                "void",
                "--return-type",
                "double _Complex",
                "--param",
                "z:double _Complex",
                "--variable-output",
                "--max-out",
                "1",
                "--impl",
                "{impl}::grab",
            ),
        ],
        "2.0 + 0.5 * I",
        "from p import O; print(O().grab().tolist())",
        "[(2+0.5j)]",
    ),
    "function": (
        [
            # Only so `jm test` has a generated test to collect: a project
            # of module functions alone gets none, and pytest's "no tests
            # ran" exits non-zero.
            ("object", "o"),
            ("module", "m"),
            (
                "function",
                "f",
                "--module",
                "m",
                "--return-type",
                "double _Complex",
                "--param",
                "z:double _Complex",
                "--impl",
                "{impl}::f",
            ),
        ],
        "-3.0 + 1.25 * I",
        "from p.m import f; print(f())",
        "(-3+1.25j)",
    ),
}


def _params(node):
    """Every row of every ``params`` list in a manifest, walked."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "params" and isinstance(v, list):
                yield from (p for p in v if isinstance(p, dict))
            else:
                yield from _params(v)
    elif isinstance(node, list):
        for v in node:
            yield from _params(v)


def _no_toolchain():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


@pytest.mark.skipif(bool(_no_toolchain()), reason=str(_no_toolchain()))
@pytest.mark.parametrize("face", sorted(FACES))
def test_an_omitted_complex_param_reads_its_default(tmp_path, face):
    steps, default, call, want = FACES[face]
    impl = tmp_path / "impl.c"
    impl.write_text(IMPL_C, encoding="utf-8")
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    proj = tmp_path / "p"
    for argv in steps:
        argv = tuple(a.format(impl=impl) for a in argv)
        r = run_cli(*argv, cwd=proj)
        assert r.returncode == 0, (argv, r.stdout + r.stderr)

    # Declared in the manifest: the CLI refuses a complex `--param`
    # default (gh-1909), and the manifest is what `apply` renders from.
    cfg = C.load(proj)
    (z,) = [p for p in _params(cfg) if p["name"] == "z"]
    z["default"] = default
    C.save(proj, cfg)
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

    r = run_cli("test", cwd=proj)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    env = {**os.environ, "PYTHONPATH": str(proj / "src")}
    out = subprocess.run(
        [sys.executable, "-c", call],
        capture_output=True,
        text=True,
        env=env,
        cwd=proj,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == want
