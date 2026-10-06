"""gh-1901: every keyword a ``steps()`` advertises, its binding accepts.

``jm new p --object g --arg-type void --return-type float`` generated a
``steps()`` that refused ``n=``::

    >>> g.steps(n=4)
    TypeError: G.steps() takes no keyword arguments

while both ``.pyi`` writers declared ``def steps(self, n: int = 1)`` and the
runtime doc read ``steps(n=1) -> ndarray``. gh-240 had made a generator's
``steps()`` keyword-capable only when a field was ``controllable``, to keep
the non-controllable scaffold byte-identical, and the same predicate left a
void->void tick and a scalar->void sink positional too: ``steps(x=a)`` on a
sink was the same ``TypeError``, and its runtime doc advertised an ``out``
that a sink does not have. ``docs/arguments.md`` states the rule the binding
now follows: ``steps()`` is keyword-capable, because the parse amortises over
the block -- gh-412 settled the same mismatch for methods the same way.

GATE: one project holding every ``steps()`` shape that is not blockwise,
standalone AND as a module object, built and its own suites run through
``jm test``. Then each class's ``steps()`` is called with every parameter its
stub declares keyword-capable, and every parameter its runtime doc names, by
keyword. The parameters are read from the generated ``.pyi`` files and
``__doc__``, not listed here, so a keyword either face starts advertising is
checked with no edit.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

from _jmrun import run_cli

#: (object, arg_type, return_type) -- the generator, the tick and the sink
#: this issue is about, and the scalar->scalar shape whose ``steps()`` was
#: already keyword-capable, as the control. Each is built standalone and as
#: an object of module ``m``.
SHAPES = [
    ("gen", "void", "float"),
    ("tick", "void", "void"),
    ("sink", "float", "void"),
    ("amp", "float", "float"),
]

#: Runs inside the built project: calls each class's ``steps()`` by keyword
#: with the union of what its stub and its runtime doc advertise.
_CALL_BY_KEYWORD = r"""
import importlib, json, re, sys
import numpy as np

def value(ann):
    if ann == "int":
        return 4
    m = re.search(r"np\.(\w+)\]", ann or "")
    return np.zeros(4, dtype=getattr(np, m.group(1) if m else "float32"))

failed = []
for row in json.loads(sys.argv[1]):
    cls = getattr(importlib.import_module(row["module"]), row["cls"])
    params = dict(row["params"])
    sig = cls.steps.__doc__.splitlines()[0]
    inner = sig[sig.index("(") + 1 : sig.rindex(")")]
    for part in inner.replace("[", "").replace("]", "").split(","):
        name = part.split("=")[0].strip()
        if name and name not in ("...", "*", "/"):
            params.setdefault(name, None)
    kwargs = {k: value(a) for k, a in params.items()}
    try:
        cls().steps(**kwargs)
        print("ok", row["cls"], sorted(kwargs))
    except TypeError as e:
        failed.append(f"{row['cls']}.steps(**{sorted(kwargs)}): {e}")
print("\n".join(failed))
sys.exit(1 if failed else 0)
"""


def _no_toolchain() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


def _advertised(src: Path) -> "list[dict]":
    """Each stubbed class's keyword-capable ``steps()`` parameters.

    A parameter before ``/`` is positional-only and promises no keyword, so
    only ``args.args`` (after ``self``) and ``args.kwonlyargs`` are read.
    """
    rows = []
    for pyi in sorted(src.rglob("*.pyi")):
        module = ".".join(pyi.relative_to(src).with_suffix("").parts)
        for node in ast.parse(pyi.read_text("utf-8")).body:
            if not isinstance(node, ast.ClassDef):
                continue
            for fn in node.body:
                if isinstance(fn, ast.FunctionDef) and fn.name == "steps":
                    args = fn.args.args[1:] + fn.args.kwonlyargs
                    rows.append(
                        {
                            "module": module,
                            "cls": node.name,
                            "params": [
                                (a.arg, ast.unparse(a.annotation))
                                for a in args
                                if a.annotation is not None
                            ],
                        }
                    )
    return rows


@pytest.mark.skipif(bool(_no_toolchain()), reason=str(_no_toolchain()))
def test_every_advertised_steps_keyword_is_accepted(tmp_path):
    first, *rest = SHAPES
    name, arg, ret = first
    shape = ("--arg-type", arg, "--return-type", ret)
    r = run_cli("new", "kw", "--object", name, *shape, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    proj = tmp_path / "kw"
    steps = [
        ("object", n, "--arg-type", a, "--return-type", t) for n, a, t in rest
    ]
    steps.append(("module", "m"))
    steps += [
        ("object", f"m{n}", "--module", "m", "--arg-type", a)
        + ("--return-type", t)
        for n, a, t in SHAPES
    ]
    for argv in steps:
        r = run_cli(*argv, cwd=proj)
        assert r.returncode == 0, (argv, r.stdout + r.stderr)

    # Builds, and the generated suites still pass with the new bindings.
    r = run_cli("test", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

    rows = _advertised(proj / "src")
    # Armed: every shape, both faces, and the keywords this issue is about.
    assert len(rows) == 2 * len(SHAPES), rows
    advertised = {p for row in rows for p, _ in row["params"]}
    assert {"n", "x"} <= advertised, rows

    out = subprocess.run(
        [sys.executable, "-c", _CALL_BY_KEYWORD, json.dumps(rows)],
        capture_output=True,
        text=True,
        cwd=proj,
        env={**os.environ, "PYTHONPATH": str(proj / "src")},
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.count("ok ") == len(rows), out.stdout
