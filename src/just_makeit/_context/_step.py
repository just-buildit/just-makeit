"""_context/_step.py — make_perf_ctx and make_step_ctx."""

from __future__ import annotations

from .._types import (
    _CTYPE_META,
    _ctype_display,
)
from ._parse import _build_ml_doc


def make_perf_ctx(perf: bool) -> dict[str, str]:
    if perf:
        return {
            "perf_include": '#include "jm_perf.h"',
            "step_qualifier": "JM_FORCEINLINE JM_HOT",
            "omp_simd_hint": "    /* #pragma omp simd */\n",
        }
    return {
        "perf_include": "",
        "step_qualifier": "static inline",
        "omp_simd_hint": "",
    }


def make_step_ctx(
    ctx: dict,
    arg_type: str,
    return_type: str,
    no_step: bool = False,
    mutable: bool = False,
    doc_blocks: dict | None = None,
) -> dict[str, str]:
    """Pre-render step() and steps() C and Python bodies for stateful objects.

    Must be called AFTER make_sample_ctx() and make_perf_ctx() so that
    ctx already contains: component, Component, return_ctype, out_np_enum,
    step_qualifier, omp_simd_hint, step_parse_block, step_return_expr.

    Returns seven keys:
      step_header_decl — non-inline step() declaration for _core.h
      step_impl_def    — inline step() definition for _core.h (after struct)
      steps_c_decl     — steps() declaration for _core.h
      steps_c_impl     — steps() implementation for _core.c
      step_ext_fn      — Component_step() C ext function
      steps_ext_fn     — Component_steps() C ext function
      step_py_flags    — METH_NOARGS or METH_VARARGS for PyMethodDef

    The inline step() definition is placed in _core.h after the struct body
    so every consumer gets the inlined version from a single header.
    """
    component = ctx["component"]
    Component = ctx["Component"]
    ret_disp = ctx["return_ctype"]
    out_np_enum = ctx["out_np_enum"]
    step_qualifier = ctx.get("step_qualifier", "static inline")
    omp_simd_hint = ctx.get("omp_simd_hint", "")
    step_return = ctx.get("step_return_expr", "PyFloat_FromDouble((double)y)")
    is_void_return = return_type == "void"

    if no_step:
        py_create_args = ctx.get("py_create_args", "")
        _lifecycle = (
            f"\n"
            f"    def test_context_manager(self):\n"
            f"        with {Component}({py_create_args}) as obj:\n"
            f"            pass\n"
            f"\n"
            f"    def test_destroy(self):\n"
            f"        obj = {Component}({py_create_args})\n"
            f"        obj.destroy()\n"
        )
        _lifecycle_pure = (
            f"\n"
            f"def test_context_manager():\n"
            f"    with {Component}({py_create_args}) as obj:\n"
            f"        pass\n"
            f"\n"
            f"def test_destroy():\n"
            f"    obj = {Component}({py_create_args})\n"
            f"    obj.destroy()\n"
        )
        return {
            "step_header_decl": "",
            "step_impl_def": "",
            "steps_c_decl": "",
            "steps_c_impl": "",
            "step_ext_fn": "",
            "steps_ext_fn": "",
            "step_py_flags": "METH_VARARGS",
            "bench_step_timing_block": "",
            "bench_steps_timing_block": "",
            "steps_def_entry": "",
            "step_pymethoddef_entry": "",
            "step_c_smoke_test": "    /* no step() generated (--no-step) */",
            "pyi_step_methods": "",
            "step_pytest_methods": "",
            "lifecycle_pytest_methods": _lifecycle,
            "step_pytest_methods_pure": "",
            "lifecycle_pytest_methods_pure": _lifecycle_pure,
            "bm_step_py": "",
            "bm_steps_py": "",
            "bench_step_py": "",
            "bench_steps_py": "",
        }

    # ── Blockwise: array-in / array-out (T[] → U[]) ───────────────────────
    # No inline step(); the user writes steps() directly in _core.c.
    # Python steps() allocates an output array of the same length as input.
    if arg_type.endswith("[]") and return_type.endswith("[]"):
        in_elem = arg_type[:-2]
        out_elem = return_type[:-2]
        in_disp = _ctype_display(in_elem)
        out_disp = _ctype_display(out_elem)
        in_np_enum = ctx.get("in_np_enum", "NPY_COMPLEX64")
        out_np_enum_bw = ctx.get("out_np_enum", "NPY_COMPLEX64")
        in_zero = _CTYPE_META[in_elem]["zero"]
        out_zero = _CTYPE_META[out_elem]["zero"]
        py_create_args = ctx.get("py_create_args", "")
        in_np_dtype = ctx.get("in_np_dtype", "np.complex64")
        out_np_dtype = ctx.get("out_np_dtype", "np.complex64")
        pyi_steps = ctx.get("pyi_steps_stub", "")

        steps_c_decl_bw = (
            f"void\n"
            f"{component}_steps(\n"
            f"    {component}_state_t *state,\n"
            f"    const {in_disp}     *in, size_t n,\n"
            f"    {out_disp}          *out);"
        )
        steps_c_impl_bw = (
            f"void\n"
            f"{component}_steps(\n"
            f"    {component}_state_t *state,\n"
            f"    const {in_disp}     *in, size_t n,\n"
            f"    {out_disp}          *out)\n"
            f"{{\n"
            f"    /* <<IMPLEMENT: blockwise transform"
            f" — replace this pass-through>> */\n"
            f"    (void)state;\n"
            f"    for (size_t i = 0; i < n; i++)\n"
            f"        out[i] = ({out_disp})in[i];\n"
            f"}}"
        )
        steps_ext_fn_bw = (
            f"static PyObject *\n"
            f"{Component}_steps"
            f"({Component}Object *self, PyObject *args)\n"
            f"{{\n"
            f"    if (!self->handle) {{\n"
            f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
            f"        return NULL;\n"
            f"    }}\n"
            f"    PyObject *x_obj = NULL, *out_obj = NULL;\n"
            f'    if (!PyArg_ParseTuple(args, "O|O", &x_obj, &out_obj))\n'
            f"        return NULL;\n"
            f"    PyArrayObject *x_arr = (PyArrayObject *)\n"
            f"    PyArray_FROM_OTF(\n"
            f"        x_obj, {in_np_enum}, NPY_ARRAY_C_CONTIGUOUS);\n"
            f"    if (!x_arr)\n"
            f"        return NULL;\n"
            f"    Py_ssize_t n = PyArray_SIZE(x_arr);\n"
            f"    if (out_obj && out_obj != Py_None) {{\n"
            f"        PyArrayObject *out_arr = (PyArrayObject *)\n"
            f"        PyArray_FROM_OTF(\n"
            f"            out_obj, {out_np_enum_bw},\n"
            f"            NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE);\n"
            f"        if (!out_arr) {{ Py_DECREF(x_arr); return NULL; }}\n"
            f"        if (PyArray_SIZE(out_arr) != n) {{\n"
            f"            PyErr_Format(PyExc_ValueError,\n"
            f'                "out length %zd != input length %zd",\n'
            f"                (Py_ssize_t)PyArray_SIZE(out_arr),\n"
            f"                (Py_ssize_t)n);\n"
            f"            Py_DECREF(x_arr);\n"
            f"            Py_DECREF(out_arr);\n"
            f"            return NULL;\n"
            f"        }}\n"
            f"        {component}_steps(\n"
            f"            self->handle,\n"
            f"            (const {in_disp} *)PyArray_DATA(x_arr),\n"
            f"            (size_t)n,\n"
            f"            ({out_disp} *)PyArray_DATA(out_arr));\n"
            f"        Py_DECREF(x_arr);\n"
            f"        return (PyObject *)out_arr;\n"
            f"    }}\n"
            f"    npy_intp dims[] = {{ n }};\n"
            f"    PyObject *out = PyArray_SimpleNew(1, dims,"
            f" {out_np_enum_bw});\n"
            f"    if (!out) {{ Py_DECREF(x_arr); return NULL; }}\n"
            f"    {component}_steps(\n"
            f"        self->handle,\n"
            f"        (const {in_disp} *)PyArray_DATA(x_arr),\n"
            f"        (size_t)n,\n"
            f"        ({out_disp} *)PyArray_DATA((PyArrayObject *)out));\n"
            f"    Py_DECREF(x_arr);\n"
            f"    return out;\n"
            f"}}"
        )
        _bw_steps_doc = [
            f"steps(x[, out]) -> NDArray[{out_np_dtype}]",
            "",
            "Apply the blockwise transform to the input array.",
            "x   — input NDArray; shape (n,).",
            "out — optional pre-allocated output array of the same length.",
            "Returns a newly allocated output array, or `out` if supplied.",
            "",
            "    >>> import numpy as np",
            *(
                [f"    >>> from {ctx.get('package', '')} import {Component}"]
                if ctx.get("package")
                else []
            ),
            f"    >>> obj = {Component}({py_create_args})",
            f"    >>> x = np.zeros(4, dtype={in_np_dtype})",
            "    >>> y = obj.steps(x)",
            "    >>> y.shape",
            "    (4,)",
            "    >>> y.dtype",
            f"    dtype('{out_np_dtype.replace('np.', '')}')",
        ]
        steps_def_entry_bw = (
            f'    {{"steps",    (PyCFunction){Component}_steps,'
            f"    METH_VARARGS,\n"
            f"     {_build_ml_doc(_bw_steps_doc)}}},\n"
        )
        _bw_bench_timing = (
            f"    double _times_steps[ITERATIONS];\n"
            f"    for (int r = 0; r < ITERATIONS; r++) {{\n"
            f"        clock_gettime(CLOCK_MONOTONIC, &t0);\n"
            f"        {component}_steps(obj, in, BENCH_N, out);\n"
            f"        clock_gettime(CLOCK_MONOTONIC, &t1);\n"
            f"        _times_steps[r] = elapsed_sec(&t0, &t1);\n"
            f"    }}\n"
            f'    jm_bench_add(&_bench, "steps",'
            f" _times_steps, ITERATIONS, BENCH_N);\n"
            f"    {{\n"
            f"        double _s = 0.0;\n"
            f"        for (int r = 0; r < ITERATIONS; r++)"
            f" _s += _times_steps[r];\n"
            f'        printf("  steps()  %8.1f MSa/s\\n",\n'
            f"               (double)BENCH_N / (_s / ITERATIONS) / 1e6);\n"
            f"    }}"
        )
        _bw_smoke = (
            f"    /* steps: verify it runs without crashing */\n"
            f"    {{\n"
            f"        {in_disp} _bw_in[1]  = {{{in_zero}}};\n"
            f"        {out_disp} _bw_out[1] = {{{out_zero}}};\n"
            f"        {component}_steps(obj, _bw_in, 1, _bw_out);\n"
            f"    }}"
        )
        _bw_pytest = (
            f"\n"
            f"    def test_steps_runs(self):\n"
            f"        obj = {Component}({py_create_args})\n"
            f"        x = np.zeros(4, dtype={in_np_dtype})\n"
            f"        out = obj.steps(x)\n"
            f"        assert out.shape == (4,)\n"
            f"        assert out.dtype == {in_np_dtype}\n"
            f"\n"
            f"    def test_steps_out_param(self):\n"
            f"        obj = {Component}({py_create_args})\n"
            f"        x   = np.zeros(4, dtype={in_np_dtype})\n"
            f"        buf = np.zeros(4, dtype={out_np_dtype})\n"
            f"        ret = obj.steps(x, buf)\n"
            f"        assert ret is buf\n"
        )
        _bw_pytest_pure = (
            f"\n"
            f"def test_steps_runs():\n"
            f"    obj = {Component}({py_create_args})\n"
            f"    x = np.zeros(4, dtype={in_np_dtype})\n"
            f"    out = obj.steps(x)\n"
            f"    assert out.shape == (4,)\n"
            f"\n"
            f"def test_steps_out_param():\n"
            f"    obj = {Component}({py_create_args})\n"
            f"    x   = np.zeros(4, dtype={in_np_dtype})\n"
            f"    buf = np.zeros(4, dtype={out_np_dtype})\n"
            f"    ret = obj.steps(x, buf)\n"
            f"    assert ret is buf\n"
        )
        _bw_lifecycle = (
            f"\n"
            f"    def test_context_manager(self):\n"
            f"        with {Component}({py_create_args}) as obj:\n"
            f"            x = np.zeros(4, dtype={in_np_dtype})\n"
            f"            obj.steps(x)\n"
            f"\n"
            f"    def test_destroy(self):\n"
            f"        obj = {Component}({py_create_args})\n"
            f"        obj.destroy()\n"
            f"        import pytest\n"
            f'        with pytest.raises(RuntimeError, match="destroyed"):\n'
            f"            obj.steps(np.zeros(4, dtype={in_np_dtype}))\n"
        )
        _bw_lifecycle_pure = (
            f"\n"
            f"def test_context_manager():\n"
            f"    with {Component}({py_create_args}) as obj:\n"
            f"        x = np.zeros(4, dtype={in_np_dtype})\n"
            f"        obj.steps(x)\n"
            f"\n"
            f"def test_destroy():\n"
            f"    obj = {Component}({py_create_args})\n"
            f"    obj.destroy()\n"
            f"    import pytest\n"
            f'    with pytest.raises(RuntimeError, match="destroyed"):\n'
            f"        obj.steps(np.zeros(4, dtype={in_np_dtype}))\n"
        )
        return {
            "step_header_decl": (
                f"/* No inline step() for blockwise objects.\n"
                f" * Implement {component}_steps() in {component}_core.c. */"
            ),
            "step_impl_def": "",
            "steps_c_decl": steps_c_decl_bw,
            "steps_c_impl": steps_c_impl_bw,
            "step_ext_fn": "",
            "steps_ext_fn": steps_ext_fn_bw,
            "step_py_flags": "METH_VARARGS",
            # Warmup and bench: blockwise only has steps(); bench_step_timing_block
            # is intentionally empty to avoid declaring _times_steps twice.
            "bench_warmup_fn": f"{component}_steps",
            "bench_step_timing_block": "",
            "bench_steps_timing_block": _bw_bench_timing,
            "steps_def_entry": steps_def_entry_bw,
            "step_pymethoddef_entry": "",
            "step_c_smoke_test": _bw_smoke,
            "pyi_step_methods": pyi_steps,
            "step_pytest_methods": _bw_pytest,
            "lifecycle_pytest_methods": _bw_lifecycle,
            "step_pytest_methods_pure": _bw_pytest_pure,
            "lifecycle_pytest_methods_pure": _bw_lifecycle_pure,
        }

    if arg_type == "void":
        step_header_decl = (
            f"/* step() is a static inline defined below (after the struct).\n"
            f" * External C consumers use"
            f" {component}_steps() declared below. */"
        )
        if is_void_return:
            step_impl_def = (
                f"/**\n"
                f" * @brief Advance state by one tick (no I/O).\n"
                f" * @param state  Must be non-NULL; state is mutated.\n"
                f" */\n"
                f"{step_qualifier} void\n"
                f"{component}_step({component}_state_t *state)\n"
                f"{{\n"
                f"    (void)state; /* TODO: implement */\n"
                f"}}"
            )
            steps_c_decl = (
                f"/**\n"
                f" * @brief Process n iterations (no scalar output).\n"
                f" *\n"
                f" * @param state  Component state (mutated).\n"
                f" * @param n     Number of iterations.\n"
                f" */\n"
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    size_t               n);"
            )
            steps_c_impl = (
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    size_t               n)\n"
                f"{{\n"
                f"{omp_simd_hint}    for (size_t i = 0; i < n; i++)\n"
                f"        {component}_step(state);\n"
                f"}}"
            )
            step_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_step({Component}Object *self,"
                f" PyObject *Py_UNUSED(ignored))\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    {component}_step(self->handle);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
            steps_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_steps"
                f"({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    Py_ssize_t n = 1;\n"
                f'    if (!PyArg_ParseTuple(args, "|n", &n))\n'
                f"        return NULL;\n"
                f"    {component}_steps(self->handle, (size_t)n);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
            step_py_flags = "METH_NOARGS"
        else:
            _state_qual = "" if mutable else "const "
            step_impl_def = (
                f"/**\n"
                f" * @brief Generate one output sample from internal state.\n"
                f" * @param state  Must be non-NULL.\n"
                f" * @return Next output sample ({ret_disp}).\n"
                f" */\n"
                f"{step_qualifier} {ret_disp}\n"
                f"{component}_step"
                f"({_state_qual}{component}_state_t *state)\n"
                f"{{\n"
                f"    (void)state; /* TODO: implement */\n"
                f"    return ({ret_disp})0;\n"
                f"}}"
            )
            steps_c_decl = (
                f"/**\n"
                f" * @brief Generate a block of output samples.\n"
                f" *\n"
                f" * @param state   Component state (mutated).\n"
                f" * @param output  Output array (length >= n).\n"
                f" * @param n       Number of samples to generate.\n"
                f" */\n"
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    {ret_disp}          *output,\n"
                f"    size_t               n);"
            )
            steps_c_impl = (
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    {ret_disp}          *output,\n"
                f"    size_t               n)\n"
                f"{{\n"
                f"{omp_simd_hint}    for (size_t i = 0; i < n; i++)\n"
                f"        output[i] = {component}_step(state);\n"
                f"}}"
            )
            step_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_step({Component}Object *self,"
                f" PyObject *Py_UNUSED(ignored))\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    {ret_disp} y = {component}_step(self->handle);\n"
                f"    return {step_return};\n"
                f"}}"
            )
            steps_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_steps"
                f"({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    Py_ssize_t n = 1;\n"
                f'    if (!PyArg_ParseTuple(args, "|n", &n))\n'
                f"        return NULL;\n"
                f"\n"
                f"    npy_intp dims[] = {{n}};\n"
                f"    PyObject *out_arr ="
                f" PyArray_SimpleNew(1, dims, {out_np_enum});\n"
                f"    if (!out_arr)\n"
                f"        return NULL;\n"
                f"\n"
                f"    {component}_steps(\n"
                f"        self->handle,\n"
                f"        ({ret_disp} *)PyArray_DATA"
                f"((PyArrayObject *)out_arr),\n"
                f"        (size_t)n);\n"
                f"\n"
                f"    return out_arr;\n"
                f"}}"
            )
            step_py_flags = "METH_NOARGS"
    elif arg_type.endswith("[]"):
        elem_type = arg_type[:-2]
        elem_disp = _ctype_display(elem_type)
        in_np_enum = ctx.get("in_np_enum", "NPY_COMPLEX64")
        step_return = ctx.get("step_return_expr", "Py_RETURN_NONE")

        step_header_decl = (
            f"/* step() is a static inline defined below (after the struct).\n"
            f" * External C consumers use"
            f" {component}_steps() declared below. */"
        )
        if is_void_return:
            step_impl_def = (
                f"/**\n"
                f" * @brief Process one input buffer (no scalar output).\n"
                f" * @param state  Must be non-NULL.\n"
                f" * @param x      Input array ({elem_disp}).\n"
                f" * @param x_len  Number of elements in @p x.\n"
                f" */\n"
                f"{step_qualifier} void\n"
                f"{component}_step(\n"
                f"    {component}_state_t *state,\n"
                f"    const {elem_disp} *x, size_t x_len)\n"
                f"{{\n"
                f"    (void)state; (void)x; (void)x_len;"
                f" /* TODO: implement */\n"
                f"}}"
            )
            step_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_step"
                f"({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    PyObject *x_obj = NULL;\n"
                f'    if (!PyArg_ParseTuple(args, "O", &x_obj))\n'
                f"        return NULL;\n"
                f"    PyArrayObject *x_arr = (PyArrayObject *)"
                f"PyArray_FROM_OTF(\n"
                f"        x_obj, {in_np_enum},"
                f" NPY_ARRAY_C_CONTIGUOUS);\n"
                f"    if (!x_arr)\n"
                f"        return NULL;\n"
                f"    const {elem_disp} *x = "
                f"(const {elem_disp} *)PyArray_DATA(x_arr);\n"
                f"    size_t x_len = (size_t)PyArray_SIZE(x_arr);\n"
                f"    {component}_step(self->handle, x, x_len);\n"
                f"    Py_DECREF(x_arr);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
        else:
            step_impl_def = (
                f"/**\n"
                f" * @brief Process one input buffer and return a result.\n"
                f" * @param state  Must be non-NULL.\n"
                f" * @param x      Input array ({elem_disp}).\n"
                f" * @param x_len  Number of elements in @p x.\n"
                f" * @return Result ({ret_disp}).\n"
                f" */\n"
                f"{step_qualifier} {ret_disp}\n"
                f"{component}_step(\n"
                f"    {component}_state_t *state,\n"
                f"    const {elem_disp} *x, size_t x_len)\n"
                f"{{\n"
                f"    (void)state; (void)x; (void)x_len;"
                f" /* TODO: implement */\n"
                f"    return ({ret_disp})0;\n"
                f"}}"
            )
            step_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_step"
                f"({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    PyObject *x_obj = NULL;\n"
                f'    if (!PyArg_ParseTuple(args, "O", &x_obj))\n'
                f"        return NULL;\n"
                f"    PyArrayObject *x_arr = (PyArrayObject *)"
                f"PyArray_FROM_OTF(\n"
                f"        x_obj, {in_np_enum},"
                f" NPY_ARRAY_C_CONTIGUOUS);\n"
                f"    if (!x_arr)\n"
                f"        return NULL;\n"
                f"    const {elem_disp} *x = "
                f"(const {elem_disp} *)PyArray_DATA(x_arr);\n"
                f"    size_t x_len = (size_t)PyArray_SIZE(x_arr);\n"
                f"    {ret_disp} y ="
                f" {component}_step(self->handle, x, x_len);\n"
                f"    Py_DECREF(x_arr);\n"
                f"    return {step_return};\n"
                f"}}"
            )

        steps_c_decl = ""
        steps_c_impl = ""
        steps_ext_fn = ""
        step_py_flags = "METH_VARARGS"
    else:
        arg_disp = ctx["arg_ctype"]
        in_np_enum = ctx.get("in_np_enum", "NPY_COMPLEX64")
        step_parse = ctx.get("step_parse_block", "")

        step_header_decl = (
            f"/* step() is a static inline defined below (after the struct).\n"
            f" * External C consumers use"
            f" {component}_steps() declared below. */"
        )
        if is_void_return:
            step_impl_def = (
                f"/**\n"
                f" * @brief Consume one input sample (sink; no output).\n"
                f" * @param state  Must be non-NULL.\n"
                f" * @param x      Input sample ({arg_disp}).\n"
                f" */\n"
                f"{step_qualifier} void\n"
                f"{component}_step"
                f"({component}_state_t *state, {arg_disp} x)\n"
                f"{{\n"
                f"    (void)state; (void)x; /* TODO: implement */\n"
                f"}}"
            )
            steps_c_decl = (
                f"/**\n"
                f" * @brief Process a block of input samples (no output).\n"
                f" *\n"
                f" * @param state  Component state (mutated).\n"
                f" * @param input  Input array (length >= n).\n"
                f" * @param n     Number of samples.\n"
                f" */\n"
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    const {arg_disp}    *input,\n"
                f"    size_t               n);"
            )
            steps_c_impl = (
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    const {arg_disp}    *input,\n"
                f"    size_t               n)\n"
                f"{{\n"
                f"{omp_simd_hint}    for (size_t i = 0; i < n; i++)\n"
                f"        {component}_step(state, input[i]);\n"
                f"}}"
            )
            step_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_step"
                f"({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"{step_parse}\n"
                f"    {component}_step(self->handle, x);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
            steps_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_steps"
                f"({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    PyObject *in_obj = NULL;\n"
                f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                f"        return NULL;\n"
                f"\n"
                f"    PyArrayObject *in_arr = (PyArrayObject *)"
                f"PyArray_FROM_OTF(\n"
                f"        in_obj, {in_np_enum},"
                f" NPY_ARRAY_C_CONTIGUOUS);\n"
                f"    if (!in_arr)\n"
                f"        return NULL;\n"
                f"\n"
                f"    {component}_steps(\n"
                f"        self->handle,\n"
                f"        (const {arg_disp} *)PyArray_DATA(in_arr),\n"
                f"        (size_t)PyArray_SIZE(in_arr));\n"
                f"    Py_DECREF(in_arr);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
            step_py_flags = "METH_VARARGS"
        else:
            _state_qual = "" if mutable else "const "
            step_impl_def = (
                f"/**\n"
                f" * @brief Process one input sample.\n"
                f" * @param state  Must be non-NULL.\n"
                f" * @param x      Input sample ({arg_disp}).\n"
                f" * @return Output sample ({ret_disp}).\n"
                f" */\n"
                f"{step_qualifier} {ret_disp}\n"
                f"{component}_step"
                f"({_state_qual}{component}_state_t *state,"
                f" {arg_disp} x)\n"
                f"{{\n"
                f"    (void)state; /* TODO: implement using state variables */\n"
                f"    return ({ret_disp})x;\n"
                f"}}"
            )
            steps_c_decl = (
                f"/**\n"
                f" * @brief Process a block of samples.\n"
                f" *\n"
                f" * @param state   Component state (mutated).\n"
                f" * @param input   Input array (length >= n).\n"
                f" * @param output  Output array (length >= n; may alias"
                f" input for in-place).\n"
                f" * @param n       Number of samples.\n"
                f" */\n"
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    const {arg_disp}    *input,\n"
                f"    {ret_disp}          *output,\n"
                f"    size_t               n);"
            )
            steps_c_impl = (
                f"void {component}_steps(\n"
                f"    {component}_state_t *state,\n"
                f"    const {arg_disp}    *input,\n"
                f"    {ret_disp}          *output,\n"
                f"    size_t               n)\n"
                f"{{\n"
                f"{omp_simd_hint}    for (size_t i = 0; i < n; i++)\n"
                f"        output[i] = {component}_step(state, input[i]);\n"
                f"}}"
            )
            step_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_step"
                f"({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"{step_parse}\n"
                f"    {ret_disp} y ="
                f" {component}_step(self->handle, x);\n"
                f"    return {step_return};\n"
                f"}}"
            )
            steps_ext_fn = (
                f"static PyObject *\n"
                f"{Component}_steps"
                f"({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    PyObject *in_obj  = NULL;\n"
                f"    PyObject *out_obj = NULL;\n"
                f'    if (!PyArg_ParseTuple(args, "O|O", &in_obj, &out_obj))\n'
                f"        return NULL;\n"
                f"\n"
                f"    PyArrayObject *in_arr = (PyArrayObject *)"
                f"PyArray_FROM_OTF(\n"
                f"        in_obj, {in_np_enum},"
                f" NPY_ARRAY_C_CONTIGUOUS);\n"
                f"    if (!in_arr)\n"
                f"        return NULL;\n"
                f"\n"
                f"    Py_ssize_t n = PyArray_SIZE(in_arr);\n"
                f"\n"
                f"    if (out_obj && out_obj != Py_None) {{\n"
                f"        PyArrayObject *out_arr = (PyArrayObject *)"
                f"PyArray_FROM_OTF(\n"
                f"            out_obj, {out_np_enum},\n"
                f"            NPY_ARRAY_C_CONTIGUOUS"
                f" | NPY_ARRAY_WRITEABLE);\n"
                f"        if (!out_arr)"
                f" {{ Py_DECREF(in_arr); return NULL; }}\n"
                f"        if (PyArray_SIZE(out_arr) != n) {{\n"
                f"            PyErr_Format(PyExc_ValueError,\n"
                f'                "out length %zd != input length %zd",\n'
                f"                (Py_ssize_t)PyArray_SIZE(out_arr),"
                f" (Py_ssize_t)n);\n"
                f"            Py_DECREF(out_arr);\n"
                f"            Py_DECREF(in_arr);\n"
                f"            return NULL;\n"
                f"        }}\n"
                f"        {component}_steps(\n"
                f"            self->handle,\n"
                f"            (const {arg_disp} *)PyArray_DATA(in_arr),\n"
                f"            ({ret_disp} *)PyArray_DATA(out_arr),\n"
                f"            (size_t)n);\n"
                f"        Py_DECREF(in_arr);\n"
                f"        return (PyObject *)out_arr;\n"
                f"    }}\n"
                f"\n"
                f"    npy_intp dims[] = {{n}};\n"
                f"    PyObject *out_arr ="
                f" PyArray_SimpleNew(1, dims, {out_np_enum});\n"
                f"    if (!out_arr) {{\n"
                f"        Py_DECREF(in_arr);\n"
                f"        return NULL;\n"
                f"    }}\n"
                f"\n"
                f"    {component}_steps(\n"
                f"        self->handle,\n"
                f"        (const {arg_disp} *)PyArray_DATA(in_arr),\n"
                f"        ({ret_disp} *)PyArray_DATA"
                f"((PyArrayObject *)out_arr),\n"
                f"        (size_t)n);\n"
                f"\n"
                f"    Py_DECREF(in_arr);\n"
                f"    return out_arr;\n"
                f"}}"
            )
            step_py_flags = "METH_VARARGS"

    # bench_step_timing_block
    _bsink = ctx.get("bench_sink_assign", "")
    _bsep = ctx.get("bench_step_input_sep", "")
    _barg = ctx.get("bench_step_input_arg", "")
    _is_arr = arg_type.endswith("[]")
    if _is_arr:
        _inner = (
            f"        clock_gettime(CLOCK_MONOTONIC, &t0);\n"
            f"        {_bsink}{component}_step(obj{_bsep}{_barg});\n"
            f"        clock_gettime(CLOCK_MONOTONIC, &t1);\n"
        )
    else:
        _inner = (
            f"        clock_gettime(CLOCK_MONOTONIC, &t0);\n"
            f"        for (int i = 0; i < BENCH_N; i++)\n"
            f"            {_bsink}{component}_step(obj{_bsep}{_barg});\n"
            f"        clock_gettime(CLOCK_MONOTONIC, &t1);\n"
        )
    bench_step_timing_block = (
        f"    double _times_step[ITERATIONS];\n"
        f"    for (int r = 0; r < ITERATIONS; r++) {{\n"
        f"{_inner}"
        f"        _times_step[r] = elapsed_sec(&t0, &t1);\n"
        f"    }}\n"
        f'    jm_bench_add(&_bench, "step",'
        f" _times_step, ITERATIONS, BENCH_N);\n"
        f"    {{\n"
        f"        double _s = 0.0;\n"
        f"        for (int r = 0; r < ITERATIONS; r++)"
        f" _s += _times_step[r];\n"
        f'        printf("  step()   %8.1f MSa/s\\n",\n'
        f"               (double)BENCH_N / (_s / ITERATIONS) / 1e6);\n"
        f"    }}"
    )

    si_arg = ctx.get("bench_steps_in_arg", "")
    so_arg = ctx.get("bench_steps_out_arg", " BENCH_N")
    if steps_ext_fn:
        bench_steps_timing_block = (
            f"    double _times_steps[ITERATIONS];\n"
            f"    for (int r = 0; r < ITERATIONS; r++) {{\n"
            f"        clock_gettime(CLOCK_MONOTONIC, &t0);\n"
            f"        {component}_steps(obj,{si_arg}{so_arg});\n"
            f"        clock_gettime(CLOCK_MONOTONIC, &t1);\n"
            f"        _times_steps[r] = elapsed_sec(&t0, &t1);\n"
            f"    }}\n"
            f'    jm_bench_add(&_bench, "steps",'
            f" _times_steps, ITERATIONS, BENCH_N);\n"
            f"    {{\n"
            f"        double _s = 0.0;\n"
            f"        for (int r = 0; r < ITERATIONS; r++)"
            f" _s += _times_steps[r];\n"
            f'        printf("  steps()  %8.1f MSa/s\\n",\n'
            f"               (double)BENCH_N / (_s / ITERATIONS) / 1e6);\n"
            f"    }}"
        )
    else:
        bench_steps_timing_block = ""

    _pkg = ctx.get("package", "")
    _create = ctx.get("py_create_args", "")
    _in_val = ctx.get("in_py_test_val", "1")
    _out_np_str = ctx.get("out_np_dtype", "np.complex64").replace("np.", "")
    _in_np_str = ctx.get("in_np_dtype", "np.complex64")
    _is_void_arg = arg_type == "void"
    _is_arr_arg = arg_type.endswith("[]")
    _from_pkg = [f"    >>> from {_pkg} import {Component}"] if _pkg else []
    _obj_create = f"    >>> obj = {Component}({_create})"

    _ret_hint_step = "None" if is_void_return else ret_disp
    if _is_void_arg and is_void_return:
        _step_sig = "step() -> None"
        _step_desc = "Advance state by one tick (no I/O)."
    elif _is_void_arg:
        _step_sig = f"step() -> {_ret_hint_step}"
        _step_desc = "Generate one output sample from internal state."
    elif _is_arr_arg and is_void_return:
        _step_sig = "step(x) -> None"
        _step_desc = "Process an input buffer (no scalar output)."
    elif _is_arr_arg:
        _step_sig = f"step(x) -> {_ret_hint_step}"
        _step_desc = "Process an input buffer and return a result."
    elif is_void_return:
        _step_sig = "step(x) -> None"
        _step_desc = "Consume one input sample (sink; no output)."
    else:
        _step_sig = f"step(x) -> {_ret_hint_step}"
        _step_desc = "Process one input sample."

    # A hand-written @brief in the header overrides the canned description, so
    # help(Obj.step) matches the .pyi. (Scaffold @briefs are filtered out by
    # _load_doc_blocks, so the default stays canned and idempotent.)
    _db = doc_blocks or {}
    _sblk = _db.get(f"{component}_step")
    if _sblk and _sblk.brief:
        _step_desc = _sblk.brief

    _step_doc_lines: list[str] = [_step_sig, "", _step_desc, ""]
    if _is_arr_arg:
        _step_doc_lines.append("    >>> import numpy as np")
    _step_doc_lines += [*_from_pkg, _obj_create]
    _step_call = "obj.step()" if _is_void_arg else f"obj.step({_in_val})"
    _step_doc_lines.append(f"    >>> {_step_call}")
    if not is_void_return and return_type in _CTYPE_META:
        _step_doc_lines.append(
            f"    {_CTYPE_META[return_type].get('py_zero', '0')}"
        )

    if steps_ext_fn:
        if _is_void_arg:
            _steps_sig = (
                "steps(n=1) -> ndarray" if not is_void_return else "steps(n=1)"
            )
            _steps_desc = (
                "Generate n output samples."
                if not is_void_return
                else "Run n iterations."
            )
            _steps_call = "    >>> y = obj.steps(4)"
        else:
            _steps_sig = "steps(x[, out]) -> ndarray"
            _steps_desc = "Process a block of samples in batch."
            _steps_call = (
                f"    >>> y = obj.steps(np.zeros(4, dtype={_in_np_str}))"
            )
        _ssblk = _db.get(f"{component}_steps")
        if _ssblk and _ssblk.brief:
            _steps_desc = _ssblk.brief
        _steps_doc_lines: list[str] = [_steps_sig, "", _steps_desc, ""]
        _steps_doc_lines.append("    >>> import numpy as np")
        _steps_doc_lines += [*_from_pkg, _obj_create, _steps_call]
        if not is_void_return:
            _steps_doc_lines += [
                "    >>> y.shape",
                "    (4,)",
                "    >>> y.dtype",
                f"    dtype('{_out_np_str}')",
            ]
        steps_def_entry = (
            f'    {{"steps",    (PyCFunction){Component}_steps,'
            f"    METH_VARARGS,\n"
            f"     {_build_ml_doc(_steps_doc_lines)}}},\n"
        )
    else:
        steps_def_entry = ""

    step_pymethoddef_entry = (
        f'    {{"step",     (PyCFunction){Component}_step,'
        f"     {step_py_flags},\n"
        f"     {_build_ml_doc(_step_doc_lines)}}},\n"
    )

    _suffix = ctx.get("step_example_suffix", "")
    step_c_smoke_test = (
        f"    /* step: verify it runs without crashing */\n"
        f"    (void){component}_step(obj{_suffix});"
    )

    in_py_hint = ctx.get("in_py_hint", "float")
    out_py_hint = ctx.get("out_py_hint", "float")
    pyi_steps = ctx.get("pyi_steps_stub", "")

    if _is_void_arg and is_void_return:
        _pyi_step_doc = '        """Advance state by one tick (no I/O)."""\n'
        _pyi_step_self = "self"
    elif _is_void_arg:
        _pyi_step_doc = (
            f'        """Generate one output sample from internal state.\n\n'
            f"        Returns\n"
            f"        -------\n"
            f"        {out_py_hint}\n"
            f"            Output sample.\n"
            f'        """\n'
        )
        _pyi_step_self = "self"
    elif is_void_return:
        _pyi_step_doc = (
            f'        """Consume one input sample (no output).\n\n'
            f"        Parameters\n"
            f"        ----------\n"
            f"        x : {in_py_hint}\n"
            f"            Input sample.\n"
            f'        """\n'
        )
        _pyi_step_self = f"self, x: {in_py_hint}"
    else:
        _pyi_step_doc = (
            f'        """Process one input sample.\n\n'
            f"        Parameters\n"
            f"        ----------\n"
            f"        x : {in_py_hint}\n"
            f"            Input sample.\n\n"
            f"        Returns\n"
            f"        -------\n"
            f"        {out_py_hint}\n"
            f"            Output sample.\n"
            f'        """\n'
        )
        _pyi_step_self = f"self, x: {in_py_hint}"
    pyi_step_methods = (
        f"\n    def step({_pyi_step_self}) -> {out_py_hint}:\n"
        f"{_pyi_step_doc}" + pyi_steps
    )

    py_create_args = ctx.get("py_create_args", "")
    in_py_test_val = ctx.get("in_py_test_val", "1")
    out_py_isinstance = ctx.get("out_py_isinstance", "float")
    in_np_dtype = ctx.get("in_np_dtype", "np.float32")
    out_np_dtype = ctx.get("out_np_dtype", "np.float32")
    _step_call_test = (
        "obj.step()" if _is_void_arg else f"obj.step({in_py_test_val})"
    )
    _assert_y = (
        "assert y is None"
        if is_void_return
        else f"assert isinstance(y, {out_py_isinstance})"
    )
    if steps_ext_fn:
        if _is_void_arg:
            if is_void_return:
                step_pytest_methods = (
                    f"\n"
                    f"    def test_step_runs(self):\n"
                    f"        obj = {Component}({py_create_args})\n"
                    f"        y = obj.step()\n"
                    f"        {_assert_y}\n"
                    f"\n"
                    f"    def test_steps_runs(self):\n"
                    f"        obj = {Component}({py_create_args})\n"
                    f"        assert obj.steps(64) is None\n"
                )
            else:
                step_pytest_methods = (
                    f"\n"
                    f"    def test_step_runs(self):\n"
                    f"        obj = {Component}({py_create_args})\n"
                    f"        y = obj.step()\n"
                    f"        {_assert_y}\n"
                    f"\n"
                    f"    def test_steps_shape_dtype(self):\n"
                    f"        obj = {Component}({py_create_args})\n"
                    f"        y = obj.steps(64)\n"
                    f"        self.assertEqual(y.shape, (64,))\n"
                    f"        self.assertEqual(y.dtype, {out_np_dtype})\n"
                )
        elif is_void_return:
            step_pytest_methods = (
                f"\n"
                f"    def test_step_runs(self):\n"
                f"        obj = {Component}({py_create_args})\n"
                f"        y = obj.step({in_py_test_val})\n"
                f"        {_assert_y}\n"
                f"\n"
                f"    def test_steps_runs(self):\n"
                f"        obj = {Component}({py_create_args})\n"
                f"        x = np.ones(64, dtype={in_np_dtype})\n"
                f"        assert obj.steps(x) is None\n"
            )
        else:
            step_pytest_methods = (
                f"\n"
                f"    def test_step_runs(self):\n"
                f"        obj = {Component}({py_create_args})\n"
                f"        y = obj.step({in_py_test_val})\n"
                f"        {_assert_y}\n"
                f"\n"
                f"    def test_steps_shape_dtype(self):\n"
                f"        obj = {Component}({py_create_args})\n"
                f"        x = np.ones(64, dtype={in_np_dtype})\n"
                f"        y = obj.steps(x)\n"
                f"        self.assertEqual(y.shape, (64,))\n"
                f"        self.assertEqual(y.dtype, {out_np_dtype})\n"
                f"\n"
                f"    def test_steps_out_param(self):\n"
                f"        x   = np.ones(64, dtype={in_np_dtype})\n"
                f"        buf = np.zeros(64, dtype={out_np_dtype})\n"
                f"        obj1 = {Component}({py_create_args})\n"
                f"        ret = obj1.steps(x, buf)\n"
                f"        self.assertIs(ret, buf)\n"
                f"        obj2 = {Component}({py_create_args})\n"
                f"        np.testing.assert_array_equal(ret, obj2.steps(x))\n"
            )
    else:
        step_pytest_methods = (
            f"\n"
            f"    def test_step_runs(self):\n"
            f"        obj = {Component}({py_create_args})\n"
            f"        y = {_step_call_test}\n"
            f"        {_assert_y}\n"
        )
    lifecycle_pytest_methods = (
        f"\n"
        f"    def test_context_manager(self):\n"
        f"        with {Component}({py_create_args}) as obj:\n"
        f"            y = {_step_call_test}\n"
        f"        {_assert_y}\n"
        f"\n"
        f"    def test_destroy(self):\n"
        f"        obj = {Component}({py_create_args})\n"
        f"        obj.destroy()\n"
        f'        with _raises(RuntimeError, match="destroyed"):\n'
        f"            {_step_call_test}\n"
    )

    if steps_ext_fn:
        if _is_void_arg:
            if is_void_return:
                step_pytest_methods_pure = (
                    f"\n"
                    f"def test_step_runs():\n"
                    f"    obj = {Component}({py_create_args})\n"
                    f"    y = obj.step()\n"
                    f"    {_assert_y}\n"
                    f"\n"
                    f"def test_steps_runs():\n"
                    f"    obj = {Component}({py_create_args})\n"
                    f"    assert obj.steps(64) is None\n"
                )
            else:
                step_pytest_methods_pure = (
                    f"\n"
                    f"def test_step_runs():\n"
                    f"    obj = {Component}({py_create_args})\n"
                    f"    y = obj.step()\n"
                    f"    {_assert_y}\n"
                    f"\n"
                    f"def test_steps_shape_dtype():\n"
                    f"    obj = {Component}({py_create_args})\n"
                    f"    y = obj.steps(64)\n"
                    f"    assert y.shape == (64,)\n"
                    f"    assert y.dtype == {out_np_dtype}\n"
                )
        elif is_void_return:
            step_pytest_methods_pure = (
                f"\n"
                f"def test_step_runs():\n"
                f"    obj = {Component}({py_create_args})\n"
                f"    y = obj.step({in_py_test_val})\n"
                f"    {_assert_y}\n"
                f"\n"
                f"def test_steps_runs():\n"
                f"    obj = {Component}({py_create_args})\n"
                f"    x = np.ones(64, dtype={in_np_dtype})\n"
                f"    assert obj.steps(x) is None\n"
            )
        else:
            step_pytest_methods_pure = (
                f"\n"
                f"def test_step_runs():\n"
                f"    obj = {Component}({py_create_args})\n"
                f"    y = obj.step({in_py_test_val})\n"
                f"    {_assert_y}\n"
                f"\n"
                f"def test_steps_shape_dtype():\n"
                f"    obj = {Component}({py_create_args})\n"
                f"    x = np.ones(64, dtype={in_np_dtype})\n"
                f"    y = obj.steps(x)\n"
                f"    assert y.shape == (64,)\n"
                f"    assert y.dtype == {out_np_dtype}\n"
                f"\n"
                f"def test_steps_out_param():\n"
                f"    x   = np.ones(64, dtype={in_np_dtype})\n"
                f"    buf = np.zeros(64, dtype={out_np_dtype})\n"
                f"    obj1 = {Component}({py_create_args})\n"
                f"    ret = obj1.steps(x, buf)\n"
                f"    assert ret is buf\n"
                f"    obj2 = {Component}({py_create_args})\n"
                f"    np.testing.assert_array_equal(ret, obj2.steps(x))\n"
            )
    else:
        step_pytest_methods_pure = (
            f"\n"
            f"def test_step_runs():\n"
            f"    obj = {Component}({py_create_args})\n"
            f"    y = {_step_call_test}\n"
            f"    {_assert_y}\n"
        )
    lifecycle_pytest_methods_pure = (
        f"\n"
        f"def test_context_manager():\n"
        f"    with {Component}({py_create_args}) as obj:\n"
        f"        y = {_step_call_test}\n"
        f"    {_assert_y}\n"
        f"\n"
        f"def test_destroy():\n"
        f"    obj = {Component}({py_create_args})\n"
        f"    obj.destroy()\n"
        f'    with pytest.raises(RuntimeError, match="destroyed"):\n'
        f"        {_step_call_test}\n"
    )

    return {
        "step_header_decl": step_header_decl,
        "step_impl_def": step_impl_def,
        "steps_c_decl": steps_c_decl,
        "steps_c_impl": steps_c_impl,
        "step_ext_fn": step_ext_fn,
        "steps_ext_fn": steps_ext_fn,
        "step_py_flags": step_py_flags,
        "bench_warmup_fn": f"{component}_step",
        "bench_step_timing_block": bench_step_timing_block,
        "bench_steps_timing_block": bench_steps_timing_block,
        "steps_def_entry": steps_def_entry,
        "step_pymethoddef_entry": step_pymethoddef_entry,
        "step_c_smoke_test": step_c_smoke_test,
        "pyi_step_methods": pyi_step_methods,
        "step_pytest_methods": step_pytest_methods,
        "lifecycle_pytest_methods": lifecycle_pytest_methods,
        "step_pytest_methods_pure": step_pytest_methods_pure,
        "lifecycle_pytest_methods_pure": lifecycle_pytest_methods_pure,
    }
