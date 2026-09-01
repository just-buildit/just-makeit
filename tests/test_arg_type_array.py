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
        h = (
            standalone_scalar_return / "native/inc/peak/peak_core.h"
        ).read_text()
        assert "const float *x, size_t x_len" in h
        assert re.search(r"\bfloat\b.*peak_step", h, re.DOTALL)

    def test_complex_elem_type(self, in_module_void):
        h = (in_module_void / "native/inc/sink/sink_core.h").read_text()
        # gh-1246: jm emits the `_Complex` spelling, never the
        # <complex.h> `complex` macro (which does not exist in C++).
        assert "float _Complex" in h
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
        ext = (
            in_module_void / "native/src/filter/filter_ext_sink.c"
        ).read_text()
        assert "NPY_CFLOAT" in ext or "NPY_COMPLEX64" in ext


# ── Python test file ──────────────────────────────────────────────────────────


class TestPytestFile:
    def test_uses_np_array_as_test_val(self, standalone_void):
        t = (standalone_void / "src/dsp/tests/test_proc.py").read_text()
        assert "np.zeros" in t or "np.array" in t or "dtype=np.float32" in t

    def test_uses_np_array_scalar_return(self, standalone_scalar_return):
        t = (
            standalone_scalar_return / "src/dsp/tests/test_peak.py"
        ).read_text()
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


# ── Blockwise: array-in / array-out (T[] → U[]) ──────────────────────────────


class TestBlockwise:
    """Blockwise (T[] → U[]) shape produces a steps()-only interface with an
    output array allocated per call. Array input + scalar/void return is the
    existing reduction path and is unaffected."""

    def test_make_sample_ctx_returns_blockwise_dict(self):
        from just_makeit._context._sample import make_sample_ctx

        ctx = make_sample_ctx("float _Complex[]", "float _Complex[]")
        # steps() stub should be present; step() stub should be absent
        assert "NDArray" in ctx["pyi_steps_stub"]
        assert "steps" in ctx["pyi_steps_stub"]

    def test_scalar_array_arg_still_ok(self):
        """Array input with scalar/void return (reduction shape) is unaffected."""
        from just_makeit._context._sample import make_sample_ctx

        ctx = make_sample_ctx("float[]", "float")
        assert ctx["return_ctype"] == "float"

    def test_scalar_arg_array_return_rejected(self):
        """Scalar input + array return is invalid and raises cleanly."""
        from just_makeit._context._sample import make_sample_ctx

        with pytest.raises(ValueError, match="requires an array arg type"):
            make_sample_ctx("float", "float[]")

    def test_blockwise_apply_materialises_files(self, tmp_path):
        """End-to-end: blockwise TOML via jm apply produces a valid scaffold."""
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

        _apply.run(root)  # must not raise
        core_h = root / "native/inc/filt/filt_core.h"
        core_c = root / "native/src/filt/filt_core.c"
        assert core_h.exists() and core_c.exists()
        # steps() signature in header; no inline step()
        h = core_h.read_text()
        assert "filt_steps" in h
        assert "static inline" not in h  # no inline step() body for blockwise

    def test_blockwise_different_element_types(self, tmp_path):
        """float[] in → double[] out is accepted (heterogeneous blockwise)."""
        from just_makeit._context._sample import make_sample_ctx

        ctx = make_sample_ctx("float[]", "double[]")
        assert ctx["in_np_dtype"] == "np.float32"
        assert ctx["out_np_dtype"] == "np.float64"

    def test_blockwise_pyi_has_ndarray_return(self, tmp_path):
        """Generated .pyi stub has NDArray return for steps()."""
        root = tmp_path / "bw"
        new_run(
            "bw",
            root,
            object_names=["xform"],
            arg_type="float _Complex[]",
            return_type="float _Complex[]",
        )
        pyi = (root / "src/bw/xform.pyi").read_text()
        assert "NDArray" in pyi
        assert "steps" in pyi
        # No scalar step() in the pyi
        assert "def step(" not in pyi

    def test_blockwise_no_step_in_core_h(self, tmp_path):
        """Generated _core.h has steps() declaration but no inline step()."""
        root = tmp_path / "bw"
        new_run(
            "bw",
            root,
            object_names=["xform"],
            arg_type="float _Complex[]",
            return_type="float _Complex[]",
        )
        h = (root / "native/inc/xform/xform_core.h").read_text()
        assert "xform_steps" in h
        # No inline step() function body (the @code comment may reference step
        # but there must be no static inline definition)
        assert "static inline" not in h

    def test_blockwise_steps_in_ext_c(self, tmp_path):
        """Generated _ext.c has a steps() binding that allocates an output."""
        root = tmp_path / "bw"
        new_run(
            "bw",
            root,
            object_names=["xform"],
            arg_type="float _Complex[]",
            return_type="float _Complex[]",
        )
        ext = (root / "native/src/xform/xform_ext.c").read_text()
        assert "PyArray_SimpleNew" in ext
        assert "Xform_steps" in ext


# ── bool scalar type ─────────────────────────────────────────────────────────


class TestBoolScalarType:
    """`bool` is a registered scalar type but `np.bool_` was missing from
    _NP_ENUM, so make_sample_ctx KeyError'd on a bool arg/return. And the
    generated C used `bool` without <stdbool.h>, so even past the KeyError
    the scaffold didn't compile. Both are now fixed."""

    def test_make_sample_ctx_bool_no_keyerror(self):
        from just_makeit._context._sample import make_sample_ctx

        ctx = make_sample_ctx("bool", "bool")
        assert ctx["in_np_enum"] == "NPY_BOOL"
        assert ctx["out_np_enum"] == "NPY_BOOL"

    def test_np_enum_has_bool(self):
        from just_makeit._types import _CTYPE_META, _NP_ENUM

        # Every registered scalar's py_type must have an _NP_ENUM entry.
        for key, meta in _CTYPE_META.items():
            assert meta["py_type"] in _NP_ENUM, (
                f"_CTYPE_META[{key!r}].py_type={meta['py_type']!r} "
                f"is missing from _NP_ENUM"
            )

    def test_common_header_includes_stdbool(self):
        from just_makeit import _render as R

        assert "#include <stdbool.h>" in R.CLIB_COMMON_H

    def test_bool_object_scaffolds(self, tmp_path):
        from just_makeit._object import run as object_run

        root = tmp_path / "bp"
        new_run("bp", root)
        object_run(root, "flg", None, arg_type="bool", return_type="bool")
        core = (root / "native" / "inc" / "flg" / "flg_core.h").read_text()
        assert "bool" in core


class TestVariableOutputArrayArg:
    """gh-139: a variable_output method given an array --arg-type must lower
    the block input to ``const <elem> *in`` — not the invalid
    ``const <elem>[] *in`` / ``(const <elem>[] *)`` cast.
    """

    @pytest.fixture()
    def proj(self, tmp_path):
        from just_makeit._method import run as method_run

        root = tmp_path / "dsp"
        new_run("dsp", root)
        init_run(root, "widget")
        method_run(
            root,
            "widget",
            "execute",
            None,
            "float _Complex[]",
            "float _Complex",
            True,
            [],
        )
        return root

    def test_header_decl_is_valid_c(self, proj):
        h = (proj / "native/inc/widget/widget_core.h").read_text()
        assert "const float _Complex *in, size_t n_in" in h
        assert "[] *" not in h

    def test_ext_cast_is_valid_c(self, proj):
        e = (proj / "native/src/widget/widget_ext.c").read_text()
        assert "(const float _Complex *)PyArray_DATA" in e
        assert "[] *" not in e

    def test_bench_buffer_is_valid_c(self, proj):
        b = (proj / "native/benchmarks/bench_widget_core.c").read_text()
        assert "[] *" not in b
