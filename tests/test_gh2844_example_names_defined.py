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
import importlib.util
import os
import re
import runpy
import shutil
from pathlib import Path

import pytest
from _jmrun import run_cli

from just_makeit import _config as C
from just_makeit import _incpath as INC

EXAMPLES = Path(__file__).parent.parent / "src/just_makeit/examples"

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
def objects_proj(tmp_path_factory: pytest.TempPathFactory) -> Path:
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


def _load_example(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_gh2844_{name}", EXAMPLES / name / "test.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def capsule_proj(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A `kind = "capsule"` module: scalar ctor arg, array method param."""
    root = tmp_path_factory.mktemp("gh2844cap") / "p"
    _ok("new", "p", cwd=root.parent)
    cfg = C.load(root)
    cfg.setdefault("module", {})["cap"] = {
        "kind": "capsule",
        "backing": "bk",
        "header": "p/bk/bk.h",
        "init_params": [{"name": "gain", "type": "double"}],
        "methods": [
            {
                "name": "execute",
                "arg_type": "float[]",
                "return_type": "float[]",
                "caller_out": True,
            },
            {"name": "reset"},
        ],
    }
    C.save(root, cfg)
    _ok("apply", cwd=root)
    return root


@pytest.fixture(scope="module")
def handle_proj(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The composites example's `kind = "handle"` module, applied only."""
    ex = _load_example("composites")
    root = tmp_path_factory.mktemp("gh2844hnd") / "composites"
    _ok("new", "composites", cwd=root.parent)
    inc = INC.header_root(root) / "ringbuf"
    inc.mkdir(parents=True, exist_ok=True)
    (inc / "ringbuf.h").write_text(ex._RINGBUF_H, encoding="utf-8")
    rb = root / "native/src/ringbuf"
    rb.mkdir(parents=True, exist_ok=True)
    (rb / "ringbuf.c").write_text(ex._RINGBUF_C, encoding="utf-8")
    (rb / "CMakeLists.txt").write_text(ex._RINGBUF_CMAKE, encoding="utf-8")
    cfg = C.load(root)
    cfg["project"]["c_deps"] = ["ringbuf"]
    cfg.setdefault("module", {})["ring"] = ex._ring_module()
    C.save(root, cfg)
    _ok("apply", cwd=root)
    return root


@pytest.fixture(scope="module")
def composer_proj(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The composer_seams example's `kind = "composer"` module, applied."""
    steps = EXAMPLES / "composer_seams/.steps"
    root = tmp_path_factory.mktemp("gh2844cmp") / "studio"
    _ok("new", "studio", cwd=root.parent)
    _ok(
        "object",
        "clip",
        "--arg-type",
        "void",
        "--return-type",
        "float _Complex",
        cwd=root,
    )
    (INC.header_root(root) / "playlist").mkdir(parents=True, exist_ok=True)
    backing = root / "native/src/backing"
    backing.mkdir(parents=True, exist_ok=True)
    shutil.copy(
        steps / "02_playlist_core.h",
        INC.header_root(root) / "playlist/playlist_core.h",
    )
    shutil.copy(steps / "02_playlist_core.c", backing / "playlist_core.c")
    shutil.copy(steps / "02_CMakeLists.txt", backing / "CMakeLists.txt")
    here = os.getcwd()
    os.chdir(root)
    try:
        runpy.run_path(str(steps / "03_manifest.py"), run_name="__main__")
    finally:
        os.chdir(here)
    _ok("apply", cwd=root)
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


def _all_docs_files(root: Path) -> list[tuple[str, str]]:
    """Every generated file the walker reads, with or without examples."""
    files = list(root.glob("native/src/**/*_ext*.c"))
    files += root.glob("src/**/*.pyi")
    return [(f.name, "") for f in sorted(files)]


def _all_docs(root: Path) -> list[tuple[str, str]]:
    out = []
    for f in sorted(root.glob("native/src/**/*_ext*.c")):
        out += [(f"{f.name}", d) for d in _c_docs(f.read_text("utf-8"))]
    for f in sorted(root.glob("src/**/*.pyi")):
        out += [(f"{f.name}", d) for d in _pyi_docs(f.read_text("utf-8"))]
    return out


#: kind -> (fixture, the fewest doctest examples its scaffold must carry,
#: the fewest generated docstring FILES the walker must have read). A count
#: that falls is a scaffold that stopped rendering the shape under test. A
#: capsule renders no synthesized example today (an example there would be
#: the author's), so its floor is 0 examples, but the two files the walker
#: reads must exist: a future builder that adds an example is walked.
KINDS = {
    "object": ("objects_proj", 100, 12),
    "capsule": ("capsule_proj", 0, 2),
    "handle": ("handle_proj", 1, 2),
    "composer": ("composer_proj", 1, 4),
}


def _examples(doc: str) -> int:
    return len(doctest.DocTestParser().get_examples(doc))


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_the_scaffold_renders_examples_of_every_kind(
    kind: str, request: pytest.FixtureRequest
) -> None:
    """The vacuity guard, counted per kind: a kind whose scaffold stopped
    rendering examples must fail here, not pass `undefined_names` empty."""
    fixture, min_examples, min_files = KINDS[kind]
    root = request.getfixturevalue(fixture)
    n = sum(_examples(d) for _, d in _all_docs(root))
    assert n >= min_examples, (kind, n)
    files = {src for src, _ in _all_docs_files(root)}
    assert len(files) >= min_files, (kind, sorted(files))


def test_objects_render_array_constructor_examples(
    objects_proj: Path,
) -> None:
    """The shape gh-2844 is about is actually rendered."""
    docs = _all_docs(objects_proj)
    assert sum("np.zeros(1" in d for _, d in docs) >= 8, len(docs)


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_generated_example_defines_the_names_it_uses(
    kind: str, request: pytest.FixtureRequest
) -> None:
    root = request.getfixturevalue(KINDS[kind][0])
    bad = [
        (src, undefined_names(d), d.splitlines()[0])
        for src, d in _all_docs(root)
        if undefined_names(d)
    ]
    assert not bad, "\n".join(map(str, bad))


def test_a_capsule_constructor_takes_no_array(tmp_path: Path) -> None:
    """Why no `np.` constructor line exists for that kind: an array
    `init_param` is refused on a capsule (only an object's takes one)."""
    root = tmp_path / "p"
    _ok("new", "p", cwd=tmp_path)
    cfg = C.load(root)
    cfg.setdefault("module", {})["cap"] = {
        "kind": "capsule",
        "backing": "bk",
        "header": "p/bk/bk.h",
        "init_params": [{"name": "taps", "type": "float[]"}],
    }
    C.save(root, cfg)
    r = run_cli("apply", cwd=root)
    assert r.returncode != 0 and "array" in r.stderr, (r.stdout, r.stderr)
