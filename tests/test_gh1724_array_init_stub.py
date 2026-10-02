"""gh-1724: every array parameter's stub is spelled by ONE helper.

An array argument reaches a generated stub on many faces -- a constructor
argument however it is declared, a method or module-function parameter, a
named method's array ``x`` and its ``out=``, the ``steps()`` input and its
``out=``, an array property's setter, a handle or capsule method, a composer
stream field -- and through two stub generators, the standalone ``_context``
one and the module-aggregated ``_stubs``. Each face spelled its own, and they
disagreed: a standalone constructor said ``npt.ArrayLike`` where the module
peer said ``NDArray[...]`` for the same object, the module peer dropped an
``--array-arg`` from the signature altogether, and ``steps()`` alone left a
byte array without its byte-buffer widening (gh-1819).

The owner's decision (2026-10-02, recorded on gh-1724 / gh-1821): the stub
states EXACTLY what the jm user declared -- ``npt.NDArray`` of the declared
dtype, both dtypes for a dtype-dispatch array, ``| None`` for an optional one,
and for a 1-D one-byte integer INPUT also the byte buffers jm itself reads
(gh-1700). A writable (``out`` / ``mutable``) parameter stays exactly the
ndarray (gh-1733). That is deliberately narrower than the runtime, which
still converts whatever numpy accepts (a list, a tuple, another safely
castable dtype); gh-1821 asked to widen it and was closed by this decision.

So this file checks three things:

* the generator-level init shapes -- every way an array reaches a
  constructor, through BOTH generators -- equal the helper, and the two
  generators render one signature;
* a scaffolded project covering every face, plus the handle / capsule /
  composer stubs, has NO array parameter spelled other than by the helper:
  every annotation naming ``NDArray`` must be one the helper produces, so a
  site reverted to its own ``NDArray[...]`` fails here without being listed;
* mypy, run on those stubs: the declared ndarray is accepted on every face,
  a ``list`` is refused on an input, and ``bytes`` is accepted on a byte
  INPUT and refused on a writable byte array.
"""

from __future__ import annotations

import ast
import re
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _jmrun import run_cli  # noqa: E402
from just_makeit import _capsule, _composer, _handle  # noqa: E402
from just_makeit import _config as C  # noqa: E402
from just_makeit import _stubs  # noqa: E402
from just_makeit import _types as T  # noqa: E402
from just_makeit._context import _build_no_state_init_ctx  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402
from test_capsule_codegen import _cfg as _capsule_cfg  # noqa: E402
from test_composer_codegen import (  # noqa: E402
    _complex_cfg as _composer_complex_cfg,
)
from test_handle_codegen import _ring_cfg  # noqa: E402

# Every shape an array constructor argument can be declared in, as
# (init_params, array_args, the param's C type, a dispatch dtype).
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


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_the_two_generators_agree(shape):
    """Every argument, not only the array: the peers render one signature."""
    ips, aa, _, _ = _SHAPES[shape]
    cfg = _cfg(ips, aa)
    assert _standalone(cfg) == _module(cfg)


def test_the_helper_states_exactly_the_declaration():
    """The decided spelling, stated once (gh-1724, gh-1821's closure)."""
    f = T.array_param_annotation
    assert f("float[]") == "npt.NDArray[np.float32]"
    assert f("double[][]") == "npt.NDArray[np.float64]"
    assert (
        f("float _Complex[]", also="float[]")
        == "npt.NDArray[np.complex64] | npt.NDArray[np.float32]"
    )
    wide = "npt.NDArray[np.uint8] | bytes | bytearray | memoryview"
    assert f("uint8_t[]") == f("uint8_t") == wide
    assert f("uint8_t[]", writable=True) == "npt.NDArray[np.uint8]"
    for ct in ("float[]", "uint8_t[]", "int16_t[]", "double[][]"):
        assert "ArrayLike" not in f(ct) and "list" not in f(ct)


# -- every face, scaffolded: no array parameter spelled outside the helper ---


def _jm(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}\n{r.stdout}\n{r.stderr}"


_ARRAY_INIT = (
    "--array-arg", "coef:float32",
    "--init-param", "taps:float[]",
    "--init-param", "mat:double[][]",
    "--init-param", "bits:uint8_t[]:[]",
)  # fmt: skip

_METHODS = (
    ("peek", "--param", "b:float[]", "--return-type", "int64_t"),
    ("peek8", "--param", "b:uint8_t[]", "--return-type", "int64_t"),
    ("fill", "--param", "b:uint8_t[]", "--out-param", "o:uint8_t[]",
     "--return-type", "size_t"),
    ("drain", "--arg-type", "float", "--return-type", "float",
     "--variable-output"),
)  # fmt: skip


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """One project with every array face, standalone and in a module."""
    return build_project(tmp_path_factory.mktemp("gh1724"))


def build_project(root: Path) -> Path:
    """Scaffold the every-face project under *root*; shared with the mypy
    tests (``test_gh1724_array_stub_mypy.py``), which need the dev env."""
    _jm("new", "jmp", cwd=root)
    p = root / "jmp"
    void = ("--arg-type", "void", "--return-type", "void")
    for args in (
        ("object", "fld", "--no-state", "--no-step", *void, *_ARRAY_INIT),
        ("object", "opt", "--no-state", "--no-step", *void,
         "--init-param", "bank:float[][]:optional:opt_create_bank",
         "--init-param", "rate:double:0.0"),
        ("object", "acc", "--arg-type", "float", "--return-type", "float",
         "--state", "h:float[4]"),
        ("object", "acc8", "--arg-type", "uint8_t",
         "--return-type", "uint8_t"),
        ("object", "blk", "--arg-type", "float[]", "--return-type", "float[]"),
        ("object", "arr", "--arg-type", "uint8_t[]", "--return-type", "int"),
        ("module", "m"),
        ("object", "mfld", "--module", "m", "--no-state", "--no-step",
         *void, *_ARRAY_INIT),
        ("object", "macc8", "--module", "m", "--arg-type", "uint8_t",
         "--return-type", "uint8_t"),
        ("function", "peekf", "--module", "m", "--param", "b:float[]",
         "--return-type", "int64_t"),
        ("function", "tobin", "--module", "m", "--param", "b:uint8_t[]",
         "--out-param", "out:uint8_t[]", "--return-type", "size_t"),
    ):  # fmt: skip
        _jm(*args, cwd=p)
    for obj in ("fld", "mfld"):
        for name, *rest in _METHODS:
            _jm("method", obj, name, *rest, cwd=p)
    _jm("property", "acc", "buf", "--type", "float", "--buf-field", "buf",
        "--writable", cwd=p)  # fmt: skip
    # A controllable state field re-renders `steps()` on its own path, for
    # the blockwise and the scalar shape alike; the CLI cannot declare one.
    for name, arg, ret in (
        ("cblk", "float[]", "float[]"),
        ("cacc", "float", "float"),
    ):
        object_run(
            p,
            name,
            None,
            arg_type=arg,
            return_type=ret,
            state_vars=[("gain", "float", "1.0")],
            controllable_names=frozenset({"gain"}),
        )
    return p


def _project_stubs(project: Path) -> dict[str, str]:
    return {
        f.relative_to(project / "src").as_posix(): f.read_text("utf-8")
        for f in sorted((project / "src").rglob("*.pyi"))
    }


def _kind_stubs() -> dict[str, str]:
    return {
        "handle": _handle.render_pyi(_ring_cfg(), "ringbuf"),
        "capsule": _capsule.render_pyi(_capsule_cfg(), "ddc_fn"),
        "composer": _composer.render_pyi(
            _composer_complex_cfg(), "wfm_compose"
        ),
    }


def _param_annotations(source: str):
    """``(function, param, annotation)`` for every annotated parameter."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs:
                if arg.annotation is not None:
                    yield node.name, arg.arg, ast.unparse(arg.annotation)


# The helper's output, and nothing else: one or two exact ndarrays, then the
# byte buffers of a byte input, then `| None` for an optional one.
_HELPER_SHAPE = re.compile(
    r"npt\.NDArray\[(?:np\.\w+|Any)\]"
    r"(?: \| npt\.NDArray\[np\.\w+\])?"
    r"(?: \| bytes \| bytearray \| memoryview)?"
    r"(?: \| None)?"
)


def test_no_array_parameter_is_spelled_outside_the_helper(project):
    """Registration-free: every face's array params, read from the stubs."""
    stubs = {**_project_stubs(project), **_kind_stubs()}
    seen = 0
    bad = []
    for where, src in stubs.items():
        for fn, param, ann in _param_annotations(src):
            if "NDArray" not in ann and "ArrayLike" not in ann:
                continue
            seen += 1
            if not _HELPER_SHAPE.fullmatch(ann):
                bad.append(f"{where}: {fn}({param}: {ann})")
    assert not bad, "array params not spelled by the helper:\n" + "\n".join(
        bad
    )
    # Armed: the walk read array params on every face, not an empty tree.
    assert seen >= 40, seen


# (stub, function, param) -> the C type the manifest declared, and whether
# the parameter is the caller's writable buffer.
_FACES = {
    ("jmp/fld.pyi", "__init__", "coef"): ("float[]", False),
    ("jmp/fld.pyi", "__init__", "taps"): ("float[]", False),
    ("jmp/fld.pyi", "__init__", "mat"): ("double[][]", False),
    ("jmp/fld.pyi", "__init__", "bits"): ("uint8_t[]", False),
    ("jmp/fld.pyi", "peek", "b"): ("float[]", False),
    ("jmp/fld.pyi", "peek8", "b"): ("uint8_t[]", False),
    ("jmp/fld.pyi", "fill", "b"): ("uint8_t[]", False),
    ("jmp/fld.pyi", "fill", "o"): ("uint8_t[]", True),
    ("jmp/fld.pyi", "drain", "x"): ("float", False),
    ("jmp/acc.pyi", "steps", "x"): ("float", False),
    ("jmp/acc.pyi", "set_h", "value"): ("float", False),
    ("jmp/acc.pyi", "buf", "value"): ("float", False),
    ("jmp/cblk.pyi", "steps", "x"): ("float[]", False),
    ("jmp/cblk.pyi", "steps", "out"): ("float[]", True),
    ("jmp/cacc.pyi", "steps", "x"): ("float", False),
    ("jmp/cacc.pyi", "steps", "out"): ("float", True),
    ("jmp/acc8.pyi", "steps", "x"): ("uint8_t", False),
    ("jmp/acc8.pyi", "steps", "out"): ("uint8_t", True),
    ("jmp/blk.pyi", "steps", "x"): ("float[]", False),
    ("jmp/blk.pyi", "steps", "out"): ("float[]", True),
    ("jmp/arr.pyi", "step", "x"): ("uint8_t[]", False),
    ("jmp/m/m.pyi", "__init__", "coef"): ("float[]", False),
    ("jmp/m/m.pyi", "__init__", "taps"): ("float[]", False),
    ("jmp/m/m.pyi", "__init__", "mat"): ("double[][]", False),
    ("jmp/m/m.pyi", "__init__", "bits"): ("uint8_t[]", False),
    ("jmp/m/m.pyi", "peek", "b"): ("float[]", False),
    ("jmp/m/m.pyi", "fill", "o"): ("uint8_t[]", True),
    ("jmp/m/m.pyi", "drain", "x"): ("float", False),
    ("jmp/m/m.pyi", "steps", "x"): ("uint8_t", False),
    ("jmp/m/m.pyi", "steps", "out"): ("uint8_t", True),
    ("jmp/m/m.pyi", "peekf", "b"): ("float[]", False),
    ("jmp/m/m.pyi", "tobin", "out"): ("uint8_t[]", True),
    ("handle", "push", "x"): ("float[]", False),
    ("handle", "scale", "x"): ("float[]", False),
    ("handle", "scale", "out"): ("float[]", True),
    ("capsule", "ddcr_execute", "x"): ("float[]", False),
    ("capsule", "ddcr_execute", "out"): ("float _Complex[]", True),
}


def test_each_face_states_its_declaration(project):
    """The faces by name: the helper of what the manifest declared."""
    anns: dict = {}
    for where, src in {**_project_stubs(project), **_kind_stubs()}.items():
        for fn, param, ann in _param_annotations(src):
            anns.setdefault((where, fn, param), set()).add(ann)
    wrong = {}
    for key, (ct, writable) in _FACES.items():
        want = T.array_param_annotation(ct, writable=writable)
        got = {a.removesuffix(" | None") for a in anns.get(key, set())}
        if got != {want}:
            wrong[key] = (want, got)
    assert not wrong, wrong
