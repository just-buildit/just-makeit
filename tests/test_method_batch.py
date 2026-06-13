"""A `--batch` method generates the 1:1-rate block signature (gh-179).

The batch binding calls `<comp>_<name>(state, const in *in, size_t n, out *out)`
(or `(state, size_t n, out *out)` for a void arg_type). The C prototype + stub
must match that, not the scalar `(state, T x)` fall-through — otherwise the
project fails to compile ("too many arguments to function").
"""

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _decl(dest: Path, comp: str, name: str) -> str:
    text = (dest / f"native/inc/{comp}/{comp}_core.h").read_text("utf-8")
    return next(line for line in text.splitlines() if f"{name}(" in line)


def test_batch_method_array_input_signature(tmp_path):
    dest = tmp_path / "p"
    _silent(new_run, "p", dest, fragments=True)
    _silent(module_run, dest, "dsp")
    _silent(
        object_run,
        dest,
        "gain",
        module="dsp",
        state_vars=[("g", "float", "1.0")],
        arg_type="float",
        return_type="float",
    )
    _silent(
        method_run,
        dest,
        "gain",
        "process_batch",
        "dsp",
        "float",
        "float",
        False,  # variable_output
        [],  # multi_output
        batch=True,
    )
    _silent(apply_run, dest)
    decl = _decl(dest, "gain", "process_batch")
    assert (
        "void gain_process_batch(gain_state_t *state, const float *in,"
        " size_t n, float *out);" in decl
    ), decl
    # the stub matches (no scalar `float x`)
    stub = (dest / "native/src/gain/gain_core.c").read_text("utf-8")
    assert "const float *in, size_t n, float *out" in stub
    assert "process_batch(gain_state_t *state, float x)" not in stub


def test_batch_method_void_input_signature(tmp_path):
    dest = tmp_path / "p"
    _silent(new_run, "p", dest, fragments=True)
    _silent(module_run, dest, "dsp")
    _silent(
        object_run,
        dest,
        "src",
        module="dsp",
        state_vars=[("k", "float", "1.0")],
        arg_type="void",
        return_type="float",
        mutable=True,
    )
    _silent(
        method_run,
        dest,
        "src",
        "gen_batch",
        "dsp",
        "void",
        "float",
        False,
        [],
        batch=True,
    )
    _silent(apply_run, dest)
    decl = _decl(dest, "src", "gen_batch")
    assert (
        "void src_gen_batch(src_state_t *state, size_t n, float *out);" in decl
    ), decl


def _ext(dest: Path, comp: str) -> str:
    return (dest / f"native/src/{comp}/{comp}_ext.c").read_text("utf-8")


def _gain_with_batch(tmp_path, arg_type="float", name="process_batch"):
    """Scaffold a standalone gain object with a 1:1 batch method; return its
    ext.c text (standalone so the glue lives in native/src/gain/gain_ext.c)."""
    dest = tmp_path / "p"
    _silent(new_run, "p", dest)
    _silent(
        object_run,
        dest,
        "gain",
        module=None,
        state_vars=[("g", "float", "1.0")],
        arg_type=arg_type,
        return_type="float",
        mutable=True,
    )
    _silent(
        method_run,
        dest,
        "gain",
        name,
        None,
        arg_type,
        "float",
        False,
        [],
        batch=True,
    )
    _silent(apply_run, dest)
    return _ext(dest, "gain")


class TestBatchOutParam:
    """gh-222: named 1:1 batch methods accept an optional `out=` buffer
    (write-in-place, validated, else allocate) — parity with the built-in
    steps(x, out=) and variable_output out= paths. Always available, no knob."""

    def test_array_input_accepts_out_keyword(self, tmp_path):
        ext = self._method_block(self._gain(tmp_path))
        assert "PyObject *args, PyObject *kwds" in ext
        assert '{"x", "out", NULL}' in ext
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "O|O"' in ext

    def test_array_input_validates_and_reuses(self, tmp_path):
        ext = self._method_block(self._gain(tmp_path))
        assert "NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE" in ext
        assert "out length %zd != input length %zd" in ext
        # writes into the caller buffer and returns it
        assert "return (PyObject *)out_arr;" in ext
        # default path still allocates
        assert "PyArray_SimpleNew(1, dims" in ext

    def test_pymethoddef_has_keywords_flag(self, tmp_path):
        full = self._gain(tmp_path)
        assert "METH_VARARGS | METH_KEYWORDS" in full
        assert "process_batch(x, out=None) -> ndarray" in full

    def test_void_input_uses_count_kwarg(self, tmp_path):
        ext = self._gain(tmp_path, arg_type="void")
        assert '{"count", "out", NULL}' in ext
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "|nO"' in ext
        assert "out length %zd != count %zd" in ext

    # helpers
    def _gain(self, tmp_path, arg_type="float"):
        return _gain_with_batch(tmp_path, arg_type=arg_type)

    def _method_block(self, ext: str) -> str:
        i = ext.find("Gain_process_batch(")
        start = ext.rfind("static PyObject *", 0, i)
        return ext[start : ext.find("\n}", i) + 2]
