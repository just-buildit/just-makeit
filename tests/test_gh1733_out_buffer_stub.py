"""gh-1733: a byte-array OUTPUT buffer is stubbed as the ndarray it must be.

gh-1700 widened every ``uint8_t[]`` / ``int8_t[]`` parameter's stub to
``NDArray[...] | bytes | bytearray | memoryview``, because the binding's
``jm_array_arg`` reads a byte buffer one element per byte. That is true of an
INPUT only. An ``out`` (or ``mutable``) parameter is the caller's buffer for C
to fill, and its binding refuses anything but a writable ndarray of the exact
dtype (``_coerce.out_buffer_guard``) -- a ``bytes`` could never be one. So the
stub promised a call the runtime raises on, and a type checker approved it.

Found on doppler: cvt ``int_to_bin`` / ``hex_to_bin`` / ``bytes_to_bin`` (module
functions) and coding ``ReedSolomon.generator`` (a module object's method).

Every shape that can carry an out param is generated here by the CLI and its
``.pyi`` read: a standalone object's method (``_context/_methods``), a module
object's method and a module function (both ``_stubs``, the module-aggregated
peer). Each also carries -- or sits beside -- an INPUT byte array, which must
stay widened, so a fix that simply stopped widening fails too.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from _jmrun import run_cli  # noqa: E402
from just_makeit import _types as T  # noqa: E402

_WIDE = "npt.NDArray[np.uint8] | bytes | bytearray | memoryview"


def _jm(*args, cwd):
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}\n{r.stdout}\n{r.stderr}"


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("gh1733")
    _jm("new", "jmp", cwd=root)
    p = root / "jmp"
    for args in (
        ("object", "fld", "--no-step",
         "--arg-type", "void", "--return-type", "void"),
        ("method", "fld", "fill", "--param", "b:uint8_t[]",
         "--out-param", "o:uint8_t[]", "--return-type", "size_t"),
        ("module", "m"),
        ("object", "rs", "--module", "m", "--no-step",
         "--arg-type", "void", "--return-type", "void"),
        ("method", "rs", "gen", "--param", "b:uint8_t[]",
         "--out-param", "o:uint8_t[]", "--return-type", "size_t"),
        ("function", "tobin", "--module", "m", "--param", "b:uint8_t[]",
         "--out-param", "out:uint8_t[]", "--return-type", "size_t"),
    ):  # fmt: skip
        _jm(*args, cwd=p)
    return p


def _ann(pyi: str, name: str) -> list[str]:
    """Every annotation *name* gets in *pyi*: signature and ``Parameters``."""
    sig = re.findall(rf"\b{name}: ([^,)=\n]+)", pyi)
    doc = re.findall(rf"^\s*{name} : (.+)$", pyi, re.M)
    return [a.strip() for a in sig + doc]


_SHAPES = {
    "object-method": ("fld.pyi", "o"),
    "module-object-method": ("m/m.pyi", "o"),
    "module-function": ("m/m.pyi", "out"),
}


@pytest.mark.parametrize("shape", list(_SHAPES))
def test_an_out_buffer_is_stubbed_as_an_ndarray_only(project, shape):
    rel, name = _SHAPES[shape]
    pyi = (project / "src" / "jmp" / rel).read_text("utf-8")
    anns = _ann(pyi, name)
    # Armed: the parameter is in the stub at all.
    assert anns, f"{shape}: no annotation for {name!r} in {rel}"
    assert all(a == "npt.NDArray[np.uint8]" for a in anns), (shape, anns)


@pytest.mark.parametrize("rel", ["fld.pyi", "m/m.pyi"])
def test_an_input_byte_array_stays_widened(project, rel):
    pyi = (project / "src" / "jmp" / rel).read_text("utf-8")
    anns = _ann(pyi, "b")
    assert anns and all(a == _WIDE for a in anns), (rel, anns)


def test_the_runtime_doc_says_the_same(project):
    """The generated ``__doc__`` is rendered from the same parameter list."""
    ext = (project / "native" / "src" / "fld" / "fld_ext.c").read_text()
    assert '"o : npt.NDArray[np.uint8]\\n"' in ext
    assert f'"b : {_WIDE}\\n"' in ext


@pytest.mark.parametrize("key", ["out", "mutable"])
def test_the_predicate_is_param_writable(key):
    p = {"name": "o", "type": "uint8_t[]", key: True}
    ann = T.array_param_annotation(p["type"], writable=T.param_writable(p))
    assert ann == "npt.NDArray[np.uint8]"
