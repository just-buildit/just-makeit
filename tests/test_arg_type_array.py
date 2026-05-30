"""Integration tests for --arg-type T[] (array-input step function).

--arg-type float[]  means step(state, const float *x, size_t x_len)
This is distinct from --array-arg which adds constructor parameters.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._init import run as init_run
from just_makeit._object import run as object_run

_STRAY_PLACEHOLDER = re.compile(r"<<(?!IMPLEMENT:)")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def standalone_void(tmp_path):
    """Standalone object with float[] arg and void return (default)."""
    root = tmp_path / "dsp"
    new_run("dsp", root)
    init_run(root, "proc", arg_type="float[]")
    return root


@pytest.fixture()
def standalone_scalar_return(tmp_path):
    """Standalone object with float[] arg and explicit scalar return."""
    root = tmp_path / "dsp"
    new_run("dsp", root)
    init_run(root, "peak", arg_type="float[]", return_type="float")
    return root


@pytest.fixture()
def in_module_void(tmp_path):
    """In-module object with float _Complex[] arg and void return."""
    root = tmp_path / "dsp"
    new_run("dsp", root, modules=["filter"])
    object_run(root, "sink", "filter", arg_type="float _Complex[]")
    return root


# ── Core header ───────────────────────────────────────────────────────────────


class TestCoreHeader:
    def test_step_takes_pointer_and_len(self, standalone_void):
        h = (standalone_void / "native/inc/proc/proc_core.h").read_text()
        assert "const float *x, size_t x_len" in h

    def test_step_returns_void(self, standalone_void):
        h = (standalone_void / "native/inc/proc/proc_core.h").read_text()
        # step signature is "void\nproc_step(...)"
        assert re.search(r"\bvoid\b.*proc_step", h, re.DOTALL)

    def test_step_returns_scalar_when_explicit(self, standalone_scalar_return):
        h = (standalone_scalar_return / "native/inc/peak/peak_core.h").read_text()
        assert "const float *x, size_t x_len" in h
        assert re.search(r"\bfloat\b.*peak_step", h, re.DOTALL)

    def test_complex_elem_type(self, in_module_void):
        h = (in_module_void / "native/inc/sink/sink_core.h").read_text()
        # _ctype_display renders "float _Complex" -> "float complex" (C99)
        assert "float complex" in h
        assert "x_len" in h


# ── Ext.c (Python bindings) ───────────────────────────────────────────────────


class TestExtC:
    def _ext(self, root, comp):
        return (root / f"native/src/{comp}/{comp}_ext.c").read_text()

    def test_array_from_otf_present(self, standalone_void):
        assert "PyArray_FROM_OTF" in self._ext(standalone_void, "proc")

    def test_correct_npy_enum(self, standalone_void):
        assert "NPY_FLOAT" in self._ext(standalone_void, "proc")

    def test_data_pointer_cast(self, standalone_void):
        ext = self._ext(standalone_void, "proc")
        assert "(const float *)PyArray_DATA(x_arr)" in ext

    def test_length_extracted(self, standalone_void):
        ext = self._ext(standalone_void, "proc")
        assert "x_len = (size_t)PyArray_SIZE(x_arr)" in ext

    def test_void_return_returns_none(self, standalone_void):
        ext = self._ext(standalone_void, "proc")
        assert "Py_RETURN_NONE" in ext

    def test_scalar_return_converts(self, standalone_scalar_return):
        ext = self._ext(standalone_scalar_return, "peak")
        assert "PyArray_FROM_OTF" in ext
        # step() returns a scalar — must call a Py*_From* conversion, not NONE
        assert "PyFloat_FromDouble" in ext or "PyLong_From" in ext

    def test_complex_elem_npy_enum(self, in_module_void):
        # NPY enum lives in the per-object fragment, not the aggregator.
        ext = (in_module_void / "native/src/filter/filter_ext_sink.c").read_text()
        assert "NPY_CFLOAT" in ext or "NPY_COMPLEX64" in ext


# ── Python test file ──────────────────────────────────────────────────────────


class TestPytestFile:
    def test_uses_np_array_as_test_val(self, standalone_void):
        t = (standalone_void / "src/dsp/tests/test_proc.py").read_text()
        assert "np.zeros" in t or "np.array" in t or "dtype=np.float32" in t

    def test_uses_np_array_scalar_return(self, standalone_scalar_return):
        t = (standalone_scalar_return / "src/dsp/tests/test_peak.py").read_text()
        assert "np.zeros" in t or "dtype=np.float32" in t


# ── Stubs (.pyi) ─────────────────────────────────────────────────────────────


class TestPyi:
    def test_step_arg_is_ndarray(self, standalone_void):
        pyi = (standalone_void / "src/dsp/proc.pyi").read_text()
        assert "NDArray" in pyi

    def test_step_returns_none(self, standalone_void):
        pyi = (standalone_void / "src/dsp/proc.pyi").read_text()
        assert "-> None" in pyi

    def test_step_returns_float_when_explicit(self, standalone_scalar_return):
        pyi = (standalone_scalar_return / "src/dsp/peak.pyi").read_text()
        assert "NDArray" in pyi


# ── No stray placeholders ────────────────────────────────────────────────────


class TestNoPlaceholders:
    def _check(self, root):
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in (
                ".py",
                ".c",
                ".h",
                ".toml",
                ".txt",
            ):
                m = _STRAY_PLACEHOLDER.search(path.read_text(encoding="utf-8"))
                assert m is None, f"Stray placeholder in {path}"

    def test_standalone_void(self, standalone_void):
        self._check(standalone_void)

    def test_standalone_scalar_return(self, standalone_scalar_return):
        self._check(standalone_scalar_return)

    def test_in_module(self, in_module_void):
        self._check(in_module_void)


# ── Config round-trip ────────────────────────────────────────────────────────


class TestConfig:
    def test_arg_type_recorded(self, standalone_void):
        from just_makeit._config import load, arg_type as cfg_arg_type

        cfg = load(standalone_void)
        assert cfg_arg_type(cfg, "proc") == "float[]"

    def test_return_type_recorded(self, standalone_scalar_return):
        from just_makeit._config import load, return_type as cfg_return_type

        cfg = load(standalone_scalar_return)
        assert cfg_return_type(cfg, "peak") == "float"


# ── Array return type is rejected cleanly ────────────────────────────────────


class TestArrayReturnRejected:
    """v0.14 foot-gun #2: a hand-authored array return type (the T[] -> T[]
    'blockwise' shape) used to die with a raw `KeyError: 'float _Complex[]'`
    deep in make_sample_ctx. It now raises a clean, actionable ValueError
    until the blockwise preset lands."""

    def test_make_sample_ctx_raises_valueerror(self):
        from just_makeit._context._sample import make_sample_ctx

        with pytest.raises(ValueError, match="array return type"):
            make_sample_ctx("float _Complex[]", "float _Complex[]")

    def test_scalar_array_arg_still_ok(self):
        """Array input with scalar/void return is unaffected."""
        from just_makeit._context._sample import make_sample_ctx

        ctx = make_sample_ctx("float[]", "float")
        assert ctx["return_ctype"] == "float"

    def test_apply_reports_clean_error(self, tmp_path, capsys):
        """End-to-end: array return type in hand-authored TOML surfaces a
        clean `error:` line through jm apply, not a traceback."""
        root = tmp_path / "blk"
        new_run("blk", root)
        manifest = root / "just-makeit.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + '\n[filt]\narg_type = "float _Complex[]"\n'
            'return_type = "float _Complex[]"\n',
            encoding="utf-8",
        )
        from just_makeit import _apply

        with pytest.raises(SystemExit):
            _apply.run(root)
        err = capsys.readouterr().err
        assert "array return type" in err
