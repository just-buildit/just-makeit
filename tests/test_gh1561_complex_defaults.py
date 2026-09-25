"""gh-1561: a complex default means one value on every face.

The issue: ``jm object zz --init-param "z:double _Complex"`` exited 1. The
CLI supplied the type's zero (``0.0 + 0.0 * I``) for a param with no
default, and the default validator refused jm's own spelling of it.

Measuring the class found three more faces, each with its own answer to
"what is this complex default":

- both ``_py_default`` peers returned ``0j`` for EVERY complex default, so a
  declared ``1.5`` compiled as 1.5 and read as ``0j`` in the stub, the
  docstring and the generated test;
- the generated app spelled ``default=0.0 + 0.0 * I`` -- ``I`` is a
  NameError in Python;
- the ``_raw`` seed for ``Py_complex`` (a struct) was written twice: the
  init-param copy emitted the expression verbatim, which does not compile,
  and the state-driven copy dropped every declared default for zero. So
  ``Cc()`` read ``0j`` while ``reset()`` set the declared value, and the
  generated ``test_reset`` of a fresh scaffold FAILED.

One parser (``_types.complex_default_py``) and one seed
(``_types.parse_seed``) now answer for all of them.

GATE: a complex default is accepted in each spelling jm writes, reads as the
      same value in C and on every Python face, seeds the binding's
      ``Py_complex`` local as ``{re, im}``, and a fresh scaffold declaring one
      builds, passes its own tests, and constructs with the declared value.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from _jmrun import run_cli
from just_makeit import _app, _stubs
from just_makeit import _types as T
from just_makeit._context._types import _py_default

COMPLEX = [ct for ct, m in T._CTYPE_META.items() if m["kind"] == "complex"]


@pytest.mark.parametrize("ct", COMPLEX)
def test_jms_own_zero_is_a_valid_default(ct):
    assert T.default_type_error(ct, T._CTYPE_META[ct]["zero"]) == ""


@pytest.mark.parametrize(
    "text, py",
    [
        ("0.0 + 0.0 * I", "0j"),
        ("1.5", "(1.5+0j)"),
        ("2.0f * I", "2j"),
        ("1.0 - 0.5 * I", "(1-0.5j)"),
        ("-3", "(-3+0j)"),
    ],
)
def test_every_python_face_reads_the_declared_value(text, py):
    for ct in COMPLEX:
        assert T.default_type_error(ct, text) == ""
        assert _py_default(ct, text) == py
        assert _stubs._py_default_stub(ct, text) == py
        assert _app._py_default(text, ct) == py


@pytest.mark.parametrize("ct", COMPLEX)
def test_a_c_constant_is_not_restated_in_python(ct):
    assert T.default_type_error(ct, "CPLX_ONE") != ""
    assert T.is_c_only_default(ct, "CPLX_ONE")
    assert _py_default(ct, "CPLX_ONE") == "..."
    assert _app._py_default("CPLX_ONE", ct) is None


def test_the_struct_seed_is_re_im():
    assert T.parse_seed("double _Complex", "1.0 - 0.5 * I") == "{1.0, -0.5}"
    assert T.parse_seed("double _Complex", "") == "{0.0, 0.0}"
    assert (
        T.parse_seed("double _Complex", "", "CPLX_ONE")
        == "{creal(CPLX_ONE), cimag(CPLX_ONE)}"
    )


@pytest.mark.parametrize("ct", ["float _Complex", "double _Complex"])
def test_a_complex_init_param_with_no_default_scaffolds(tmp_path, ct):
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run_cli("object", "zz", "--init-param", f"z:{ct}", cwd=tmp_path / "p")
    assert r.returncode == 0, r.stdout + r.stderr


def _no_toolchain():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


@pytest.mark.skipif(bool(_no_toolchain()), reason=str(_no_toolchain()))
def test_a_declared_complex_default_is_what_the_object_holds(tmp_path):
    r = run_cli("new", "p", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    proj = tmp_path / "p"
    steps = [
        ("object", "zz", "--init-param", "z:double _Complex"),
        ("object", "yy", "--init-param", "w:double _Complex:1.0 - 0.5 * I"),
        ("object", "cc", "--state", "g:double _Complex:1.0 - 0.5 * I"),
        ("property", "cc", "g", "--type", "double _Complex"),
    ]
    for argv in steps:
        r = run_cli(*argv, cwd=proj)
        assert r.returncode == 0, (argv, r.stdout + r.stderr)
    ext = (proj / "native/src/cc/cc_ext.c").read_text(encoding="utf-8")
    assert "Py_complex g_raw = {1.0, -0.5};" in ext

    # Builds (the init-param seeds compile) and passes the scaffold's own
    # tests, whose test_reset restates the declared value.
    r = run_cli("test", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr

    env = {**os.environ, "PYTHONPATH": str(proj / "src")}
    out = subprocess.run(
        [sys.executable, "-c", "from p import Cc; print(Cc().g)"],
        capture_output=True,
        text=True,
        env=env,
        cwd=proj,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "(1-0.5j)"
