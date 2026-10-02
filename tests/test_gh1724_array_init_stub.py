"""gh-1724: an array constructor argument's stub never admits ``str``.

After gh-1700 every generated binding refuses a ``str`` for an array
argument (``jm_array_arg``), and a ``uint8_t[]`` / ``int8_t[]`` also takes a
byte buffer. The standalone stub generator (``_context/_state``) still wrote
``npt.ArrayLike`` for every array init-param -- required, defaulted,
optional dispatch and ``--array-arg`` alike -- and ``npt.ArrayLike`` admits
``str``, so ``Fld("0101")`` type-checked and failed only when run. The module
generator (``_stubs._obj_stub``) wrote ``NDArray[...]`` for the same object,
except that its optional-array branch skipped the byte widening (a ``bytes``
the binding takes was a type error) and it did not read ``--array-arg`` at
all (``(self, /, *args, **kwargs)``).

Both now call ``T.array_param_annotation``. This walks every way an array
reaches a constructor, renders it through BOTH generators, and requires the
two to agree with that helper and with each other. The mypy half type-checks
a ``str`` call against each generated stub.
"""

from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _stubs  # noqa: E402
from just_makeit import _types as T  # noqa: E402
from just_makeit._context import _build_no_state_init_ctx  # noqa: E402

# Every shape an array constructor argument can be declared in, as
# (init_params, array_args, the param's name, its C type, a dispatch dtype).
_SHAPES = {
    "required byte": (
        [{"name": "a", "type": "uint8_t[]"}], [], "uint8_t[]", ""
    ),
    "required wide": ([{"name": "a", "type": "float[]"}], [], "float[]", ""),
    "required 2-D": (
        [{"name": "a", "type": "double[][]"}], [], "double[][]", ""
    ),
    "defaulted byte": (
        [{"name": "a", "type": "uint8_t[]", "default": "[]"}],
        [], "uint8_t[]", "",
    ),
    "defaulted signed byte": (
        [{"name": "a", "type": "int8_t[]", "default": "[]"}],
        [], "int8_t[]", "",
    ),
    "defaulted wide": (
        [{"name": "a", "type": "float[]", "default": "[]"}],
        [], "float[]", "",
    ),
    "optional dispatch byte": (
        [{"name": "a", "type": "uint8_t[]", "optional": True,
          "create_fn": "obj_create_a"},
         {"name": "rate", "type": "double", "default": "0.0"}],
        [], "uint8_t[]", "",
    ),
    "dtype dispatch": (
        [{"name": "a", "type": "float _Complex[]", "real_type": "float[]",
          "real_create_fn": "obj_create_real"}],
        [], "float _Complex[]", "float[]",
    ),
    "array-arg": ([], [{"name": "a", "type": "uint8"}], "uint8_t[]", ""),
}  # fmt: skip


def _cfg(init_params, array_args) -> dict:
    return {
        "project": {"name": "p"},
        "obj": {
            "arg_type": "void",
            "return_type": "void",
            "no_state": "true",
            "no_step": "true",
            "init_params": init_params,
            "array_args": array_args,
        },
    }


def _init_args(stub_source: str) -> dict[str, str]:
    """``{param: annotation}`` of the one ``__init__`` in *stub_source*."""
    tree = ast.parse(textwrap.dedent(stub_source))
    inits = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    ]
    assert len(inits) == 1, stub_source
    return {
        a.arg: ast.unparse(a.annotation)
        for a in inits[0].args.args + inits[0].args.kwonlyargs
        if a.annotation is not None
    }


def _standalone(cfg: dict) -> dict[str, str]:
    """The standalone generator's ``__init__`` for ``obj``."""
    ctx = _build_no_state_init_ctx(
        "obj",
        "Obj",
        C.init_params(cfg, "obj"),
        array_args=C.array_args(cfg, "obj"),
        csym="obj",
    )
    return _init_args(
        f"def __init__(self, {ctx['init_params_pyi']}) -> None: ..."
    )


def _module(cfg: dict) -> dict[str, str]:
    """The module-aggregated generator's ``__init__`` for ``obj``."""
    return _init_args(_stubs._obj_stub(cfg, "obj", pkg="p", module="m"))


_GENERATORS = {"standalone": _standalone, "module": _module}


def _expected(shape: str) -> str:
    ips, _, ct, real = _SHAPES[shape]
    ann = T.array_param_annotation(ct, also=real)
    if any(p.get("optional") for p in ips):
        return f"{ann} | None"
    return ann


@pytest.mark.parametrize("gen", sorted(_GENERATORS))
@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_each_generator_annotates_the_array_by_the_one_helper(gen, shape):
    ips, aa, _, _ = _SHAPES[shape]
    ann = _GENERATORS[gen](_cfg(ips, aa)).get("a")
    assert ann == _expected(shape), (gen, shape, ann)
    assert "str" not in ann and "ArrayLike" not in ann, ann


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_the_two_generators_agree(shape):
    """Every argument, not only the array: the peers render one signature."""
    ips, aa, _, _ = _SHAPES[shape]
    cfg = _cfg(ips, aa)
    assert _standalone(cfg) == _module(cfg)


def test_required_and_defaulted_spell_the_array_alike():
    """The issue's ask, stated directly: a default changes no annotation."""
    for gen in _GENERATORS.values():
        req = gen(_cfg(_SHAPES["required byte"][0], []))["a"]
        dflt = gen(_cfg(_SHAPES["defaulted byte"][0], []))["a"]
        assert req == dflt == T.array_param_annotation("uint8_t[]")


# -- the type checker: a str call is an error against both stubs -------------


def _mypy_errors(stub: str, use: str, tmp_path: Path) -> list[str]:
    from mypy import api

    pkg = tmp_path / "stubpkg"
    pkg.mkdir()
    (pkg / "__init__.pyi").write_text(stub, encoding="utf-8")
    (tmp_path / "use.py").write_text(use, encoding="utf-8")
    out, _, _ = api.run(
        [
            "--no-incremental",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(tmp_path / "use.py"),
        ]
    )
    return [ln for ln in out.splitlines() if ": error:" in ln]


@pytest.mark.parametrize("gen", sorted(_GENERATORS))
def test_mypy_refuses_a_str_and_accepts_bytes(gen, tmp_path, monkeypatch):
    pytest.importorskip("mypy.api")
    cfg = _cfg(
        [
            {"name": "req", "type": "uint8_t[]"},
            {"name": "bits", "type": "uint8_t[]", "default": "[]"},
            {"name": "taps", "type": "float[]", "default": "[]"},
        ],
        [],
    )
    if gen == "module":
        stub = _stubs._obj_stub(cfg, "obj", pkg="p", module="m")
    else:
        sig = _build_no_state_init_ctx(
            "obj", "Obj", C.init_params(cfg, "obj"), csym="obj"
        )["init_params_pyi"]
        stub = f"class Obj:\n    def __init__(self, {sig}) -> None: ...\n"
    stub = "from typing import Any, final\n" + textwrap.dedent(stub)
    stub = "\n".join(_stubs.numpy_imports(stub)) + "\n" + stub
    use = (
        "from stubpkg import Obj\n"
        'Obj("0101")\n'
        'Obj(b"\\x01", bits="0101")\n'
        'Obj(b"\\x01", taps="1.5")\n'
        'Obj(b"\\x01", bits=bytearray(b"\\x01"), taps=None)\n'
    )
    monkeypatch.setenv("MYPYPATH", str(tmp_path))
    errs = _mypy_errors(stub, use, tmp_path)
    lines = sorted(int(e.split(":")[1]) for e in errs)
    # One error per str call (lines 2-4), and the last call's only error is
    # `taps=None` -- proof the checker read the stub rather than failing it.
    assert lines == [2, 3, 4, 5], errs
    assert '"taps"' in errs[-1] and '"None"' in errs[-1], errs
