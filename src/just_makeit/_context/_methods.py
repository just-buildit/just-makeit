"""_context/_methods.py — method/property context builders.

Contains _bench_method_block, make_methods_ctx, and make_properties_ctx.
"""

from .._types import (
    _CTYPE_META,
    _NP_ENUM,
    _CTYPE_TO_NPY,
    _KIND_PY_TEST_VAL,
    _PYBUILD_FMT,
    _ctype_display,
    is_array_param_type,
    array_elem_ctype,
)
from ._parse import _build_ml_doc, _build_params_parse, _step_parse_block


# ---------------------------------------------------------------------------
# _bench_method_block
# ---------------------------------------------------------------------------


def _bench_method_block(component: str, m: dict) -> str:
    """Return a self-contained C bench timing block for method *m*.

    Returns an empty string when the method should not be benchmarked
    (``bench == False`` in the method dict, or ``variable_output`` methods
    whose output size is indeterminate at bench time).

    The generated block is wrapped in ``{}`` for scope isolation so that
    per-method locals (buffers, sink variables) do not conflict with each
    other or with the surrounding ``step()``/``steps()`` bench variables.

    Parameters
    ----------
    component : str
        Snake-case component name (e.g. ``"fir"``).
    m : dict
        Method dict with keys: name, arg_type, return_type, variable_output,
        batch, params.  Same shape as the dicts in ``[[comp.methods]]``.

    Returns
    -------
    str
        C source fragment (indented 4 spaces, blank line before/after) ready
        to paste into ``main()`` of the bench executable, or ``""`` to skip.
    """
    if m.get("bench") is False or m.get("variable_output") or m.get("varargs"):
        return ""

    name: str = m["name"]
    arg_type: str = m.get("arg_type", "void")
    return_type: str = m.get("return_type", "float _Complex")
    batch: bool = m.get("batch", False)
    params: list[dict] = m.get("params", [])

    has_arg = arg_type != "void"
    has_ret = return_type != "void"
    is_array_arg = arg_type.endswith("[]")

    if has_arg:
        arg_elem = arg_type[:-2] if is_array_arg else arg_type
        arg_meta = _CTYPE_META[arg_elem]
        arg_disp = _ctype_display(arg_type)
        arg_elem_disp = _ctype_display(arg_elem)
        arg_zero = arg_meta["zero"]

    if has_ret:
        ret_meta = _CTYPE_META.get(return_type)
        ret_disp = _ctype_display(return_type) if ret_meta else return_type

    param_args = ""
    for p in params:
        pt = p["type"]
        if is_array_param_type(pt):
            param_args += ", NULL, 0"
        else:
            pm = _CTYPE_META.get(pt, {})
            param_args += f", {pm.get('zero', '0')}"

    lines: list[str] = [f"    /* bench: {name}() */", "    {"]
    lines.append(f"        double _times_{name}[ITERATIONS];")

    if batch:
        if has_arg:
            lines += [
                f"        {arg_disp} *{name}_in ="
                f" ({arg_disp} *)calloc(BENCH_N,"
                f" sizeof({arg_disp}));",
            ]
        ret_disp_b = _ctype_display(return_type)
        lines += [
            f"        {ret_disp_b} *{name}_out ="
            f" ({ret_disp_b} *)malloc("
            f"BENCH_N * sizeof({ret_disp_b}));",
        ]
        chk_vars = f"{name}_in && {name}_out" if has_arg else f"{name}_out"
        lines += [
            f'        if (!({chk_vars})) {{ fprintf(stderr, "OOM\\n"); return 1; }}',
        ]
        in_arg = f" {name}_in," if has_arg else ""
        call = f"{component}_{name}(obj,{in_arg} BENCH_N, {name}_out)"
        lines += [
            "        for (int i = 0; i < 4; i++)",
            f"            {call};",
            "        for (int r = 0; r < ITERATIONS; r++) {",
            "            clock_gettime(CLOCK_MONOTONIC, &t0);",
            f"            {call};",
            "            clock_gettime(CLOCK_MONOTONIC, &t1);",
            f"            _times_{name}[r] = elapsed_sec(&t0, &t1);",
            "        }",
        ]
        if has_arg:
            lines.append(f"        free({name}_in);")
        lines.append(f"        free({name}_out);")

    elif is_array_arg:
        lines += [
            f"        {arg_elem_disp} *{name}_in ="
            f" ({arg_elem_disp} *)calloc("
            f"BENCH_N, sizeof({arg_elem_disp}));",
            f'        if (!{name}_in) {{ fprintf(stderr, "OOM\\n"); return 1; }}',
        ]
        if has_ret:
            lines.append(f"        volatile {ret_disp} {name}_sink;")
        sink = f"{name}_sink = " if has_ret else ""
        call = f"{component}_{name}(obj, {name}_in, BENCH_N{param_args})"
        lines += [
            "        for (int i = 0; i < 4; i++)",
            f"            {sink}{call};",
            "        for (int r = 0; r < ITERATIONS; r++) {",
            "            clock_gettime(CLOCK_MONOTONIC, &t0);",
            f"            {sink}{call};",
            "            clock_gettime(CLOCK_MONOTONIC, &t1);",
            f"            _times_{name}[r] = elapsed_sec(&t0, &t1);",
            "        }",
            f"        free({name}_in);",
        ]

    else:
        if has_ret:
            lines.append(f"        volatile {ret_disp} {name}_sink;")
        sink = f"{name}_sink = " if has_ret else ""
        in_arg = f", {arg_zero}" if has_arg else ""
        call = f"{component}_{name}(obj{in_arg}{param_args})"
        lines += [
            f"        for (int i = 0; i < 16; i++) {sink}{call};",
            "        for (int r = 0; r < ITERATIONS; r++) {",
            "            clock_gettime(CLOCK_MONOTONIC, &t0);",
            "            for (int i = 0; i < BENCH_N; i++)",
            f"                {sink}{call};",
            "            clock_gettime(CLOCK_MONOTONIC, &t1);",
            f"            _times_{name}[r] = elapsed_sec(&t0, &t1);",
            "        }",
        ]

    add_line = f'        jm_bench_add(&_bench, "{name}", _times_{name}, ITERATIONS, BENCH_N);'
    lines += [
        add_line,
        "        {",
        "            double _s = 0.0;",
        f"            for (int r = 0; r < ITERATIONS; r++) _s += _times_{name}[r];",
        f'            printf("  {name}()  %8.1f MSa/s\\n",',
        "                   (double)BENCH_N / (_s / ITERATIONS) / 1e6);",
        "        }",
    ]

    lines.append("    }")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# make_methods_ctx
# ---------------------------------------------------------------------------


def make_methods_ctx(
    component: str,
    Component: str,
    methods: list[dict],
    pkg: str = "",
    py_create_args: str = "",
    no_state: bool = False,
) -> dict[str, str]:
    """Generate template context keys for extra named methods.

    Each method dict has: name, arg_type ("void" or a _CTYPE_META key),
    return_type (a _CTYPE_META key), variable_output (bool),
    batch (bool), and optionally multi_output (list of additional return
    ctypes).

    batch=True generates a 1:1-rate array method:
      C: void comp_name(state_t *, [const arg_t *in,] size_t n, ret_t *out)
      Python: allocates output array each call with PyArray_SimpleNew.

    pkg and py_create_args are used in the generated PyMethodDef docstrings
    to produce working doctests; omitting them produces functional but
    package-anonymous examples.
    """
    _KIND_TO_PY: dict[str, str] = {
        "float": "float",
        "int": "int",
        "complex": "complex",
        "str": "str",
    }

    def _pyi_scalar(ctype: str) -> str:
        if ctype == "void":
            return "None"
        if ctype == "bool":
            return "bool"
        meta = _CTYPE_META.get(ctype)
        return _KIND_TO_PY.get(meta["kind"], "Any") if meta else "Any"

    def _pyi_ndarray(ctype: str) -> str:
        elem = ctype[:-2] if ctype.endswith("[]") else ctype
        meta = _CTYPE_META.get(elem)
        return f"NDArray[{meta['py_type']}]" if meta else "NDArray[Any]"

    _EMPTY: dict = {
        "method_decls": "",
        "extra_buf_fields": "",
        "extra_buf_free": "",
        "extra_buf_alloc": "",
        "extra_methods_c": "",
        "extra_methods_pymethoddef": "",
        "pyi_extra_methods": "",
        "bench_methods_timing_block": "",
        "varargs_binding_files": [],
    }
    if not methods:
        return _EMPTY

    wrapper_prefix = f"{Component}Obj" if no_state else Component

    guard = (
        "    if (!self->handle) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
    )

    decl_lines: list[str] = []
    buf_fields: list[str] = []
    buf_free: list[str] = []
    buf_alloc: list[str] = []
    method_c_parts: list[str] = []
    pmd_lines: list[str] = []
    pyi_lines: list[str] = []
    varargs_binding_files: list[str] = []
    user_has_reset: bool = any(m["name"] == "reset" for m in methods)

    for m in methods:
        name: str = m["name"]

        # ── varargs method (*args, **kwargs) ─────────────────────────────
        if m.get("varargs"):
            binding_file = f"{component}_{name}_core.c"
            varargs_binding_files.append(binding_file)
            extern_decl = (
                f"/* varargs binding — body in {binding_file} */\n"
                f"extern PyObject *\n"
                f"{component}_{name}"
                f"(PyObject *, PyObject *, PyObject *);\n"
            )
            method_c_parts.append(extern_decl)
            pmd_lines.append(
                f'    {{"{name}",'
                f" (PyCFunction)(void *){component}_{name},"
                f" METH_VARARGS | METH_KEYWORDS,\n"
                f'     "{name}(*args, **kwargs)."}},\n'
            )
            pyi_lines.append(
                f"    def {name}(self, *args: Any, **kwargs: Any)"
                f" -> Any:\n"
                f'        """{name.replace("_", " ").capitalize()}."""\n'
            )
            continue

        arg_type: str = m.get("arg_type", "void")
        return_type: str = m.get("return_type", "float _Complex")
        variable_output: bool = m.get("variable_output", False)
        batch: bool = m.get("batch", False)
        multi_output: list[str] = m.get("multi_output", [])
        params: list[dict] = m.get("params", [])
        result_fields: list[dict] = m.get("result_fields", [])
        max_results: int = int(m.get("max_results", 64))
        none_on_empty: bool = m.get("none_on_empty", False)
        # gh-138: opt into the 5-arg `(..., out, size_t max_out)` form for a
        # variable_output method whose C API forwards an explicit output
        # capacity (the buffer cap jm already tracks for grow-on-demand).
        pass_capacity: bool = m.get("pass_capacity", False)
        _cap_param = ", size_t max_out" if pass_capacity else ""
        _cap_arg = f", self->_{name}_buf_cap" if pass_capacity else ""

        ret_disp = _ctype_display(return_type)
        _ret_elem = (
            return_type[:-2] if return_type.endswith("[]") else return_type
        )
        ret_meta = _CTYPE_META.get(_ret_elem)
        ret_np = _NP_ENUM.get(ret_meta["py_type"]) if ret_meta else "NPY_FLOAT"

        out_type: str | None = m.get("out_type")
        out_divisor: int = int(m.get("out_divisor", 1))
        _vo_out_elem = (
            out_type if (variable_output and out_type) else return_type
        )
        _vo_out_disp = _ctype_display(_vo_out_elem)
        _vo_out_meta = _CTYPE_META.get(_vo_out_elem)
        _vo_out_np = (
            _NP_ENUM.get(_vo_out_meta["py_type"])
            if _vo_out_meta
            else "NPY_FLOAT"
        )
        has_params = bool(params)
        has_arg = arg_type != "void"
        if has_arg:
            arg_disp = _ctype_display(arg_type)
            _arg_elem = arg_type[:-2] if arg_type.endswith("[]") else arg_type
            arg_meta = _CTYPE_META[_arg_elem]
            arg_np = _NP_ENUM[arg_meta["py_type"]]

        _param_docs = " * @param state  Must be non-NULL.\n"
        if has_arg:
            _param_docs += (
                f" * @param x      Input ({_ctype_display(arg_type)}).\n"
            )
        for _p in params:
            _pdisp = _ctype_display(_p["type"])
            _param_docs += f" * @param {_p['name']}  {_pdisp} parameter.\n"
        _doc_ret_disp = _vo_out_disp if variable_output else ret_disp
        _ret_doc = (
            f" * @return Result ({_doc_ret_disp}).\n"
            if return_type != "void"
            else ""
        )
        _method_doc = f"/**\n * @brief {name}.\n *\n{_param_docs}{_ret_doc} */"
        _ndecl = len(decl_lines)

        if not has_arg:
            _in_example = ""
            _in_dtype_str = ""
        elif arg_type.endswith("[]"):
            _elem = arg_type[:-2]
            _in_dtype_str = (
                _CTYPE_META[_elem]["py_type"]
                if _elem in _CTYPE_META
                else "np.float32"
            )
            _in_example = f"np.zeros(4, dtype={_in_dtype_str})"
        elif arg_type in _CTYPE_META:
            _in_dtype_str = _CTYPE_META[arg_type]["py_type"]
            _kind = _CTYPE_META[arg_type]["kind"]
            _in_example = _KIND_PY_TEST_VAL.get(_kind, "1")
        else:
            _in_dtype_str = "np.float32"
            _in_example = "x"
        _from_line = [f"    >>> from {pkg} import {Component}"] if pkg else []
        _obj_line = f"    >>> obj = {Component}({py_create_args})"

        # ── batch method ─────────────────────────────────────────────────
        if batch:
            if has_arg:
                decl_lines.append(
                    f"void {component}_{name}({component}_state_t *state,"
                    f" const {arg_disp} *in, size_t n, {ret_disp} *out);"
                )
                wrapper = (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}"
                    f"({Component}Object *self, PyObject *args)\n"
                    f"{{\n"
                    f"{guard}"
                    f"    PyObject *in_obj = NULL;\n"
                    f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                    f"        return NULL;\n"
                    f"    PyArrayObject *in_arr ="
                    f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"        in_obj, {arg_np}, NPY_ARRAY_C_CONTIGUOUS);\n"
                    f"    if (!in_arr) return NULL;\n"
                    f"    Py_ssize_t n = PyArray_SIZE(in_arr);\n"
                    f"    npy_intp dims[] = {{n}};\n"
                    f"    PyObject *out ="
                    f" PyArray_SimpleNew(1, dims, {ret_np});\n"
                    f"    if (!out) {{ Py_DECREF(in_arr); return NULL; }}\n"
                    f"    {component}_{name}(self->handle,\n"
                    f"        (const {arg_disp} *)PyArray_DATA(in_arr),\n"
                    f"        (size_t)n,\n"
                    f"        ({ret_disp} *)PyArray_DATA"
                    f"((PyArrayObject *)out));\n"
                    f"    Py_DECREF(in_arr);\n"
                    f"    return out;\n"
                    f"}}"
                )
            else:
                decl_lines.append(
                    f"void {component}_{name}({component}_state_t *state,"
                    f" size_t n, {ret_disp} *out);"
                )
                wrapper = (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}"
                    f"({Component}Object *self, PyObject *args)\n"
                    f"{{\n"
                    f"{guard}"
                    f"    Py_ssize_t n = 1;\n"
                    f'    if (!PyArg_ParseTuple(args, "|n", &n))\n'
                    f"        return NULL;\n"
                    f"    npy_intp dims[] = {{n}};\n"
                    f"    PyObject *out ="
                    f" PyArray_SimpleNew(1, dims, {ret_np});\n"
                    f"    if (!out) return NULL;\n"
                    f"    {component}_{name}(self->handle,\n"
                    f"        (size_t)n,\n"
                    f"        ({ret_disp} *)PyArray_DATA"
                    f"((PyArrayObject *)out));\n"
                    f"    return out;\n"
                    f"}}"
                )
            method_c_parts.append(wrapper)
            _ret_np_str = _CTYPE_META[return_type]["py_type"].replace(
                "np.", ""
            )
            _batch_sig = f"{name}({'x' if has_arg else 'n'}) -> ndarray"
            _batch_doc_lines = [
                _batch_sig,
                "",
                f"1:1-rate batch transform. Returns an ndarray of dtype {_ret_np_str}.",
                "",
                "    >>> import numpy as np",
                *_from_line,
                _obj_line,
            ]
            if has_arg:
                _batch_doc_lines += [
                    f"    >>> x = np.zeros(4, dtype={_in_dtype_str})",
                    f"    >>> y = obj.{name}(x)",
                ]
            else:
                _batch_doc_lines.append(f"    >>> y = obj.{name}(4)")
            _batch_doc_lines += [
                "    >>> y.shape",
                "    (4,)",
                "    >>> y.dtype",
                f"    dtype('{_ret_np_str}')",
            ]
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction){wrapper_prefix}_{name},'
                f" METH_VARARGS,\n"
                f"     {_build_ml_doc(_batch_doc_lines)}}},\n"
            )
            for _j in range(_ndecl, len(decl_lines)):
                decl_lines[_j] = _method_doc + "\n" + decl_lines[_j]
            continue

        # ── declarations for _core.h ─────────────────────────────────────
        if result_fields:
            if has_arg:
                decl_lines.append(
                    f"size_t {component}_{name}"
                    f"({component}_state_t *state,"
                    f" const {arg_disp} *in, size_t n_in,"
                    f" {ret_disp} *result, size_t max_results);"
                )
            else:
                decl_lines.append(
                    f"size_t {component}_{name}"
                    f"({component}_state_t *state,"
                    f" {ret_disp} *result, size_t max_results);"
                )
        elif variable_output:
            extra_params = "".join(
                f", {_ctype_display(rt)} *out{i + 1}"
                for i, rt in enumerate(multi_output)
            )
            if has_arg:
                decl_lines.append(
                    f"size_t {component}_{name}_max_out"
                    f"({component}_state_t *state);\n"
                    f"size_t {component}_{name}"
                    f"({component}_state_t *state,"
                    f" const {arg_disp} *in, size_t n_in,"
                    f" {_vo_out_disp} *out{extra_params}{_cap_param});"
                )
            elif has_params:
                _vp_parts: list[str] = []
                for _p in params:
                    if is_array_param_type(_p["type"]):
                        _e = _ctype_display(array_elem_ctype(_p["type"]))
                        _vp_parts.append(f"const {_e} *{_p['name']}")
                        _vp_parts.append(f"size_t {_p['name']}_len")
                    else:
                        _vp_parts.append(
                            f"{_ctype_display(_p['type'])} {_p['name']}"
                        )
                decl_lines.append(
                    f"size_t {component}_{name}_max_out"
                    f"({component}_state_t *state);\n"
                    f"size_t {component}_{name}"
                    f"({component}_state_t *state,"
                    f" {', '.join(_vp_parts)},"
                    f" {_vo_out_disp} *out{extra_params}{_cap_param});"
                )
            else:
                decl_lines.append(
                    f"size_t {component}_{name}_max_out"
                    f"({component}_state_t *state);\n"
                    f"size_t {component}_{name}"
                    f"({component}_state_t *state, size_t n,"
                    f" {_vo_out_disp} *out{extra_params}{_cap_param});"
                )
        else:
            extra_params = "".join(
                f", {_ctype_display(rt)} *out{i + 1}"
                for i, rt in enumerate(multi_output)
            )
            out_type_param = (
                f", {_ctype_display(out_type)} *out" if out_type else ""
            )
            if has_params:
                p_parts: list[str] = []
                if has_arg:
                    if is_array_param_type(arg_type):
                        _e_disp = _ctype_display(array_elem_ctype(arg_type))
                        p_parts.append(f"const {_e_disp} *x")
                        p_parts.append("size_t x_len")
                    else:
                        p_parts.append(f"{arg_disp} x")
                for p in params:
                    if is_array_param_type(p["type"]):
                        e_disp = _ctype_display(array_elem_ctype(p["type"]))
                        p_parts.append(f"const {e_disp} *{p['name']}")
                        p_parts.append(f"size_t {p['name']}_len")
                    else:
                        p_parts.append(
                            f"{_ctype_display(p['type'])} {p['name']}"
                        )
                c_param_str = ", ".join(p_parts)
                decl_lines.append(
                    f"{ret_disp} {component}_{name}"
                    f"({component}_state_t *state,"
                    f" {c_param_str}{extra_params}{out_type_param});"
                )
            elif has_arg:
                if is_array_param_type(arg_type):
                    _e_disp = _ctype_display(array_elem_ctype(arg_type))
                    decl_lines.append(
                        f"{ret_disp} {component}_{name}"
                        f"({component}_state_t *state,"
                        f" const {_e_disp} *x, size_t x_len"
                        f"{extra_params}{out_type_param});"
                    )
                else:
                    decl_lines.append(
                        f"{ret_disp} {component}_{name}"
                        f"({component}_state_t *state,"
                        f" {arg_disp} x{extra_params}{out_type_param});"
                    )
            else:
                decl_lines.append(
                    f"{ret_disp} {component}_{name}"
                    f"({component}_state_t *state"
                    f"{extra_params}{out_type_param});"
                )

        for _j in range(_ndecl, len(decl_lines)):
            decl_lines[_j] = _method_doc + "\n" + decl_lines[_j]

        # ── pre-allocated buffer fields + alloc + free ───────────────────
        if variable_output:
            all_return_types = [_vo_out_elem] + list(multi_output)
            _malloc_lines: list[str] = []
            for i, rt in enumerate(all_return_types):
                suffix = f"_{i}" if i > 0 else ""
                rt_disp = _ctype_display(rt)
                field_name = f"_{name}_buf{suffix}"
                buf_fields.append(
                    f"    {rt_disp} *{field_name};"
                    f"  /* pre-allocated output for {name} */\n"
                )
                buf_free.append(f"    free(self->{field_name});\n")
                _malloc_lines.append(
                    f"        self->{field_name} = malloc("
                    f"_max * sizeof({rt_disp}));\n"
                    f"        if (!self->{field_name}) {{"
                    f" PyErr_NoMemory(); return -1; }}\n"
                )
            buf_fields.append(
                f"    size_t _{name}_buf_cap;  /* allocated capacity for {name} */\n"
            )
            buf_alloc.append(
                f"    {{\n"
                f"        size_t _max ="
                f" {component}_{name}_max_out(self->handle);\n"
                f"        if (_max) {{\n"
                + "".join(_malloc_lines)
                + f"            self->_{name}_buf_cap = _max;\n"
                + "        }\n"
                "    }\n"
            )

        # ── Python wrapper in ext.c ──────────────────────────────────────
        if variable_output:
            if has_arg:
                parse_block = (
                    f"    PyObject *in_obj = NULL;\n"
                    f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                    f"        return NULL;\n"
                    f"    PyArrayObject *in_arr ="
                    f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"        in_obj, {arg_np}, NPY_ARRAY_C_CONTIGUOUS);\n"
                    f"    if (!in_arr) return NULL;\n"
                    f"    Py_ssize_t n = PyArray_SIZE(in_arr);\n"
                )
                call_data = (
                    f"self->handle,"
                    f" (const {arg_disp} *)PyArray_DATA(in_arr),"
                    f" (size_t)n, self->_{name}_buf"
                )
                decref_in = "    Py_DECREF(in_arr);\n"
                _lazy_fallback = "(size_t)n"
            elif has_params:
                _pb_lines: list[str] = []
                _cd_parts: list[str] = ["self->handle"]
                _dr_lines: list[str] = []
                _fmt = ""
                _fmt_args: list[str] = []
                _first_arr: str | None = None
                _first_scalar: str | None = None
                for _p in params:
                    _pn = _p["name"]
                    _pt = _p["type"]
                    if is_array_param_type(_pt):
                        _pe = array_elem_ctype(_pt)
                        _pe_np = _NP_ENUM[_CTYPE_META[_pe]["py_type"]]
                        _pe_disp = _ctype_display(_pe)
                        _pb_lines += [
                            f"    PyObject *{_pn}_obj = NULL;",
                        ]
                        _fmt += "O"
                        _fmt_args.append(f"&{_pn}_obj")
                        _pb_lines += [
                            f"    PyArrayObject *{_pn}_arr = NULL;",
                        ]
                        _cd_parts.append(
                            f"(const {_pe_disp} *)PyArray_DATA({_pn}_arr)"
                        )
                        _cd_parts.append(f"(size_t)PyArray_SIZE({_pn}_arr)")
                        _dr_lines.append(f"    Py_DECREF({_pn}_arr);")
                        if _first_arr is None:
                            _first_arr = _pn
                    else:
                        _pt_meta = _CTYPE_META.get(_pt, {})
                        _fmt_char = _pt_meta.get("fmt", "d")
                        _has_parse = "parse_type" in _pt_meta
                        _parse_t = _pt_meta.get(
                            "parse_type", _ctype_display(_pt)
                        )
                        _parse_zero = _pt_meta.get("parse_zero", "0")
                        if _has_parse:
                            _raw = f"{_pn}_raw"
                            _pb_lines.append(
                                f"    {_parse_t} {_raw} = {_parse_zero};"
                            )
                            _fmt += _fmt_char
                            _fmt_args.append(f"&{_raw}")
                        else:
                            _pb_lines.append(
                                f"    {_parse_t} {_pn} = {_parse_zero};"
                            )
                            _fmt += _fmt_char
                            _fmt_args.append(f"&{_pn}")
                        _cd_parts.append(_pn)
                        if _first_scalar is None:
                            _first_scalar = _pn
                _cd_parts.append(f"self->_{name}_buf")
                parse_block = (
                    "\n".join(_pb_lines) + "\n"
                    f'    if (!PyArg_ParseTuple(args, "{_fmt}", '
                    + ", ".join(_fmt_args)
                    + "))\n"
                    "        return NULL;\n"
                )
                _conv_lines: list[str] = []
                for _p in params:
                    _pn = _p["name"]
                    _pt = _p["type"]
                    if is_array_param_type(_pt):
                        _pe = array_elem_ctype(_pt)
                        _pe_np = _NP_ENUM[_CTYPE_META[_pe]["py_type"]]
                        _conv_lines += [
                            f"    {_pn}_arr = (PyArrayObject *)PyArray_FROM_OTF(",
                            f"        {_pn}_obj, {_pe_np}, NPY_ARRAY_C_CONTIGUOUS);",
                            f"    if (!{_pn}_arr) return NULL;",
                        ]
                    elif "parse_type" in _CTYPE_META.get(_pt, {}):
                        _pm = _CTYPE_META[_pt]
                        _pt_disp = _ctype_display(_pt)
                        _conv_lines.append(
                            f"    {_pt_disp} {_pn} = {_pm['to_c'](_pn)};"
                        )
                parse_block += (
                    "\n".join(_conv_lines) + "\n" if _conv_lines else ""
                )
                call_data = ", ".join(_cd_parts)
                decref_in = "\n".join(_dr_lines) + "\n" if _dr_lines else ""
                _lazy_fallback = (
                    f"(size_t)PyArray_SIZE({_first_arr}_arr)"
                    if _first_arr is not None
                    else f"(size_t){_first_scalar}"
                    if _first_scalar is not None
                    else "1"
                )
            else:
                parse_block = (
                    "    Py_ssize_t n = 1;\n"
                    '    if (!PyArg_ParseTuple(args, "|n", &n))\n'
                    "        return NULL;\n"
                )
                call_data = f"self->handle, (size_t)n, self->_{name}_buf"
                decref_in = ""
                _lazy_fallback = "(size_t)n"

            if multi_output:
                all_rts = [return_type] + list(multi_output)
                call_extra = "".join(
                    f", self->_{name}_buf_{i}" for i in range(1, len(all_rts))
                )
                np_enums = [
                    _NP_ENUM[
                        _CTYPE_META[rt[:-2] if rt.endswith("[]") else rt][
                            "py_type"
                        ]
                    ]
                    for rt in all_rts
                ]
                arr_decls = "\n".join(
                    f"    PyObject *arr{i} ="
                    f" PyArray_SimpleNewFromData(\n"
                    f"        1, &dim, {np_enums[i]},"
                    f" self->_{name}_buf"
                    f"{'_' + str(i) if i > 0 else ''});"
                    for i in range(len(all_rts))
                )
                incref_lines = "\n".join(
                    f"    PyArray_SetBaseObject("
                    f"(PyArrayObject *)arr{i},"
                    f" (PyObject *)self); Py_INCREF(self);"
                    for i in range(len(all_rts))
                )
                null_checks = " || ".join(
                    f"!arr{i}" for i in range(len(all_rts))
                )
                decref_cleanup = " ".join(
                    f"Py_XDECREF(arr{i});" for i in range(len(all_rts))
                )
                pack_args = ", ".join(f"arr{i}" for i in range(len(all_rts)))
                decref_after = "\n".join(
                    f"    Py_DECREF(arr{i});" for i in range(len(all_rts))
                )
                wrapper = (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}"
                    f"({Component}Object *self, PyObject *args)\n"
                    f"{{\n"
                    f"{guard}"
                    f"{parse_block}"
                    f"    size_t n_out ="
                    f" {component}_{name}({call_data}{call_extra}{_cap_arg});\n"
                    f"    npy_intp dim = (npy_intp)n_out;\n"
                    f"{arr_decls}\n"
                    f"    if ({null_checks}) {{\n"
                    f"        {decref_cleanup} return NULL;\n"
                    f"    }}\n"
                    f"{incref_lines}\n"
                    f"    PyObject *result = PyTuple_Pack("
                    f"{len(all_rts)}, {pack_args});\n"
                    f"{decref_after}\n"
                    f"{decref_in}"
                    f"    return result;\n"
                    f"}}"
                )
            else:
                _none_on_empty_line = (
                    "    if (!n_out) Py_RETURN_NONE;\n"
                    if none_on_empty
                    else ""
                )
                _decref_early_vo = (
                    " ".join(
                        line.strip()
                        for line in decref_in.splitlines()
                        if line.strip()
                    )
                    + " "
                    if decref_in.strip()
                    else ""
                )
                _lazy_alloc_vo = (
                    f"    size_t _need = {_lazy_fallback};\n"
                    f"    if (!self->_{name}_buf"
                    f" || self->_{name}_buf_cap < _need) {{\n"
                    f"        size_t _max ="
                    f" {component}_{name}_max_out(self->handle);\n"
                    f"        if (!_max || _max < _need) _max = _need;\n"
                    f"        {_vo_out_disp} *_tmp = realloc("
                    f"self->_{name}_buf,"
                    f" _max * sizeof({_vo_out_disp}));\n"
                    f"        if (!_tmp) {{"
                    f" {_decref_early_vo}PyErr_NoMemory();"
                    f" return NULL; }}\n"
                    f"        self->_{name}_buf = _tmp;\n"
                    f"        self->_{name}_buf_cap = _max;\n"
                    f"    }}\n"
                )
                wrapper = (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}"
                    f"({Component}Object *self, PyObject *args)\n"
                    f"{{\n"
                    f"{guard}"
                    f"{parse_block}"
                    f"{_lazy_alloc_vo}"
                    f"    size_t n_out ="
                    f" {component}_{name}({call_data}{_cap_arg});\n"
                    f"{_none_on_empty_line}"
                    f"    npy_intp dim = (npy_intp)n_out;\n"
                    f"    PyObject *arr = PyArray_SimpleNewFromData(\n"
                    f"        1, &dim, {_vo_out_np},"
                    f" self->_{name}_buf);\n"
                    f"    if (!arr) return NULL;\n"
                    f"    PyArray_SetBaseObject("
                    f"(PyArrayObject *)arr, (PyObject *)self);\n"
                    f"    Py_INCREF(self);\n"
                    f"{decref_in}"
                    f"    return arr;\n"
                    f"}}"
                )
            _all_rts_vo = [_vo_out_elem] + list(multi_output)
            _dtype_strs_vo = [
                _CTYPE_META[rt[:-2] if rt.endswith("[]") else rt][
                    "py_type"
                ].replace("np.", "")
                for rt in _all_rts_vo
            ]
            _ret_hint_vo = (
                f"tuple[{', '.join('ndarray' for _ in _all_rts_vo)}]"
                if len(_all_rts_vo) > 1
                else "ndarray"
            )
            if has_arg:
                _vo_sig_arg = "x"
                _vo_call_example = f"obj.{name}({_in_example})"
            elif has_params:
                _first_ap = next(
                    (p for p in params if is_array_param_type(p["type"])),
                    None,
                )
                _vo_sig_arg = _first_ap["name"] if _first_ap else "n=1"
                _vo_call_example = f"obj.{name}(np.zeros(4))"
            else:
                _vo_sig_arg = "n=1"
                _vo_call_example = f"obj.{name}(4)"
            _vo_doc_lines = [
                f"{name}({_vo_sig_arg}) -> {_ret_hint_vo}",
                "",
                "Zero-copy view into pre-allocated output buffer.",
                "",
                "    >>> import numpy as np",
                *_from_line,
                _obj_line,
            ]
            _vo_doc_lines.append(f"    >>> y = {_vo_call_example}")
            _vo_doc_lines += [
                f"    >>> y{'[0]' if len(_all_rts_vo) > 1 else ''}.dtype",
                f"    dtype('{_dtype_strs_vo[0]}')",
            ]
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction){wrapper_prefix}_{name},'
                f" METH_VARARGS,\n"
                f"     {_build_ml_doc(_vo_doc_lines)}}},\n"
            )
        elif result_fields:
            _rf_fmt_parts: list[str] = []
            _rf_arg_parts: list[str] = []
            for _rf in result_fields:
                _rft = _rf["type"]
                _rfn = _rf["name"]
                _fmt_c, _cast = _PYBUILD_FMT.get(_rft, ("i", ""))
                _rf_fmt_parts.append(_fmt_c)
                _rft_val = f"results[i].{_rfn}"
                if _cast:
                    _rft_val = f"({_cast}){_rft_val}"
                _rf_arg_parts.append(_rft_val)
            _bvfmt = '"(' + "".join(_rf_fmt_parts) + ')"'
            _bvargs = ", ".join(_rf_arg_parts)
            if has_arg:
                _rf_parse = (
                    f"    PyObject *in_obj = NULL;\n"
                    f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                    f"        return NULL;\n"
                    f"    PyArrayObject *in_arr"
                    f" = (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"        in_obj, {arg_np},"
                    f" NPY_ARRAY_C_CONTIGUOUS);\n"
                    f"    if (!in_arr) return NULL;\n"
                    f"    size_t n_in ="
                    f" (size_t)PyArray_SIZE(in_arr);\n"
                )
                _rf_call = (
                    f"    {ret_disp} results[{max_results}];\n"
                    f"    size_t n_out ="
                    f" {component}_{name}(self->handle,\n"
                    f"        (const {arg_disp} *)"
                    f"PyArray_DATA(in_arr),"
                    f" n_in,\n"
                    f"        results, {max_results});\n"
                    f"    Py_DECREF(in_arr);\n"
                )
            else:
                _rf_parse = ""
                _rf_call = (
                    f"    {ret_disp} results[{max_results}];\n"
                    f"    size_t n_out ="
                    f" {component}_{name}(self->handle,\n"
                    f"        results, {max_results});\n"
                )
            wrapper = (
                f"static PyObject *\n"
                f"{wrapper_prefix}_{name}"
                f"({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"{guard}"
                f"{_rf_parse}"
                f"{_rf_call}"
                f"    PyObject *lst ="
                f" PyList_New((Py_ssize_t)n_out);\n"
                f"    if (!lst) return NULL;\n"
                f"    for (size_t i = 0; i < n_out; i++) {{\n"
                f"        PyObject *tup ="
                f" Py_BuildValue({_bvfmt}, {_bvargs});\n"
                f"        if (!tup)"
                f" {{ Py_DECREF(lst); return NULL; }}\n"
                f"        PyList_SET_ITEM(lst, (Py_ssize_t)i, tup);\n"
                f"    }}\n"
                f"    return lst;\n"
                f"}}"
            )
            _rf_field_names = ", ".join(f["name"] for f in result_fields)
            _rf_call_arg = (
                f"np.zeros(4, dtype={_in_dtype_str})" if has_arg else ""
            )
            _rf_doc_lines = [
                f"{name}({'x' if has_arg else ''}) -> list[tuple]",
                "",
                f"Returns list of ({_rf_field_names},) tuples.",
                "",
                "    >>> import numpy as np",
                *_from_line,
                _obj_line,
                f"    >>> results = obj.{name}({_rf_call_arg})",
                "    >>> isinstance(results, list)",
                "    True",
            ]
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction){wrapper_prefix}_{name},'
                f" METH_VARARGS,\n"
                f"     {_build_ml_doc(_rf_doc_lines)}}},\n"
            )
        else:
            # Fixed-output wrapper
            _p_cleanup = ""
            if has_params and has_arg:
                _x_param = {"name": "x", "type": arg_type}
                _combined = [_x_param] + list(params)
                parse_block, _p_call, _p_cleanup = _build_params_parse(
                    _combined
                )
                call_args_c = f"self->handle, {_p_call}"
                fn_sig = f"{Component}Object *self, PyObject *args"
                meth_flags = "METH_VARARGS"
            elif has_params:
                parse_block, _p_call, _p_cleanup = _build_params_parse(params)
                call_args_c = f"self->handle, {_p_call}"
                fn_sig = f"{Component}Object *self, PyObject *args"
                meth_flags = "METH_VARARGS"
            elif has_arg and arg_type.endswith("[]"):
                _x_param = {"name": "x", "type": arg_type}
                parse_block, _p_call, _p_cleanup = _build_params_parse(
                    [_x_param]
                )
                call_args_c = f"self->handle, {_p_call}"
                fn_sig = f"{Component}Object *self, PyObject *args"
                meth_flags = "METH_VARARGS"
            elif has_arg:
                parse_block = _step_parse_block(arg_type, arg_meta) + "\n"
                call_args_c = "self->handle, x"
                fn_sig = f"{Component}Object *self, PyObject *args"
                meth_flags = "METH_VARARGS"
            else:
                parse_block = ""
                call_args_c = "self->handle"
                fn_sig = (
                    f"{Component}Object *self, PyObject *Py_UNUSED(ignored)"
                )
                meth_flags = "METH_NOARGS"

            if multi_output:
                extra_decls = "".join(
                    f"    {_ctype_display(rt)} out{i + 1}"
                    f" = {_CTYPE_META[rt]['zero']};\n"
                    for i, rt in enumerate(multi_output)
                )
                extra_call = "".join(
                    f", &out{i + 1}" for i in range(len(multi_output))
                )
                if ret_meta:
                    call_line = (
                        f"    {ret_disp} y ="
                        f" {component}_{name}"
                        f"({call_args_c}{extra_call});\n"
                    )
                    py_primary = ret_meta["to_py"]("y")
                else:
                    call_line = (
                        f"    {component}_{name}({call_args_c}{extra_call});\n"
                    )
                    py_primary = "Py_None"
                pack_parts = [py_primary] + [
                    _CTYPE_META[rt]["to_py"](f"out{i + 1}")
                    if rt in _CTYPE_META
                    else f"PyLong_FromLong(out{i + 1})"
                    for i, rt in enumerate(multi_output)
                ]
                n = len(multi_output) + 1
                ret_body = (
                    f"{extra_decls}"
                    f"{call_line}"
                    f"{_p_cleanup}"
                    f"    return PyTuple_Pack({n},"
                    f" {', '.join(pack_parts)});\n"
                )
            elif out_type:
                out_disp = _ctype_display(out_type)
                out_npy = _CTYPE_TO_NPY[out_type]
                first_arr = next(
                    (
                        p["name"]
                        for p in params
                        if is_array_param_type(p["type"])
                    ),
                    None,
                )
                # Buffer size: prefer the length of the first array param.
                # If there is no array param, fall back to the first scalar
                # integer param so methods like ``foo(n: int) -> ndarray`` (n
                # samples requested) allocate an n-sized output rather than
                # an empty one (gh-65).
                if first_arr:
                    raw_len = f"{first_arr}_len"
                else:
                    first_int = next(
                        (
                            p["name"]
                            for p in params
                            if not is_array_param_type(p["type"])
                            and _CTYPE_META.get(p["type"], {}).get("kind")
                            == "int"
                        ),
                        None,
                    )
                    raw_len = first_int if first_int else "0"
                if out_divisor > 1:
                    len_expr = f"({raw_len} / {out_divisor})"
                else:
                    len_expr = raw_len
                cleanup_inline = _p_cleanup.replace("\n    ", " ").strip()
                ret_body = (
                    f"    npy_intp _dims[] ="
                    f" {{(npy_intp){len_expr}}};\n"
                    f"    PyObject *_out ="
                    f" PyArray_EMPTY(1, _dims, {out_npy}, 0);\n"
                    f"    if (!_out)"
                    f" {{{cleanup_inline} return NULL; }}\n"
                    f"    {component}_{name}({call_args_c},"
                    f" ({out_disp} *)PyArray_DATA"
                    f"((PyArrayObject *)_out));\n"
                    f"{_p_cleanup}"
                    f"    return _out;\n"
                )
            elif ret_meta:
                ret_expr = ret_meta["to_py"]("y")
                ret_body = (
                    f"    {ret_disp} y ="
                    f" {component}_{name}({call_args_c});\n"
                    f"{_p_cleanup}"
                    f"    return {ret_expr};\n"
                )
            else:
                ret_body = (
                    f"    {component}_{name}({call_args_c});\n"
                    f"{_p_cleanup}"
                    f"    Py_RETURN_NONE;\n"
                )
            wrapper = (
                f"static PyObject *\n"
                f"{wrapper_prefix}_{name}({fn_sig})\n"
                f"{{\n"
                f"{guard}"
                f"{parse_block}"
                f"{ret_body}"
                f"}}"
            )
            _fix_sig_in = (
                f"{'x' if has_arg else ''}"
                + (", " if has_arg and has_params else "")
                + ", ".join(p["name"] for p in params)
            )
            _fix_ret_hint = (
                "ndarray"
                if out_type or multi_output
                else _pyi_scalar(return_type)
            )
            _fix_doc_lines = [
                f"{name}({_fix_sig_in}) -> {_fix_ret_hint}".rstrip(),
                "",
                f"{name}.",
            ]
            if has_arg or has_params:
                _fix_doc_lines += ["", "    >>> import numpy as np"]
            else:
                _fix_doc_lines.append("")
            _fix_doc_lines += [*_from_line, _obj_line]
            _call_parts: list[str] = []
            if has_arg:
                _call_parts.append(_in_example if _in_example else "x")
            for _p in params:
                _pt = _p["type"]
                if _pt.endswith("[]"):
                    _pe = _pt[:-2]
                    _pe_str = (
                        _CTYPE_META[_pe]["py_type"]
                        if _pe in _CTYPE_META
                        else "np.float32"
                    )
                    _call_parts.append(f"np.zeros(4, dtype={_pe_str})")
                elif _pt in _CTYPE_META:
                    _call_parts.append(_CTYPE_META[_pt].get("py_zero", "0"))
                else:
                    _call_parts.append("0")
            _call_str = ", ".join(_call_parts)
            if out_type or multi_output:
                _fix_doc_lines.append(f"    >>> y = obj.{name}({_call_str})")
                _fix_doc_lines.append("    >>> y.ndim")
                _fix_doc_lines.append("    1")
            elif return_type != "void" and return_type in _CTYPE_META:
                _py_z = _CTYPE_META[return_type].get("py_zero", "0")
                _fix_doc_lines.append(f"    >>> obj.{name}({_call_str})")
                _fix_doc_lines.append(f"    {_py_z}")
            else:
                _fix_doc_lines.append(f"    >>> obj.{name}({_call_str})")
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction){wrapper_prefix}_{name},'
                f" {meth_flags},\n"
                f"     {_build_ml_doc(_fix_doc_lines)}}},\n"
            )

        method_c_parts.append(wrapper)

        # pyi stub for this method
        m_var = variable_output
        m_multi = multi_output
        param_parts: list[str] = []
        if arg_type != "void":
            if arg_type.endswith("[]"):
                elem = arg_type[:-2]
                param_parts.append(f"x: {_pyi_ndarray(elem)}")
            else:
                param_parts.append(f"x: {_pyi_scalar(arg_type)}")
        for p in params:
            pt = p["type"]
            if pt.endswith("[]"):
                param_parts.append(f"{p['name']}: {_pyi_ndarray(pt[:-2])}")
            else:
                param_parts.append(f"{p['name']}: {_pyi_scalar(pt)}")
        if result_fields:
            ret_ann = "list[tuple]"
        elif m_var:
            all_rts = [return_type] + list(m_multi)
            ndarrays = [_pyi_ndarray(rt) for rt in all_rts]
            ret_ann = (
                f"tuple[{', '.join(ndarrays)}]"
                if len(ndarrays) > 1
                else ndarrays[0]
            )
        else:
            ret_ann = _pyi_scalar(return_type)
        sig = ", ".join(param_parts)
        _pyi_ret_desc = (
            f"Returns\n        -------\n        {ret_ann}\n"
            f"            Output.\n        "
            if ret_ann != "None"
            else ""
        )
        _pyi_param_desc = ""
        for _pp in (["x"] if has_arg else []) + [p["name"] for p in params]:
            _pyi_param_desc += f"        {_pp}\n            Input.\n"
        _pyi_params_section = (
            f"        Parameters\n        ----------\n{_pyi_param_desc}        "
            if _pyi_param_desc
            else "        "
        )
        _pyi_doc = (
            f'        """{name}.\n\n'
            f"{_pyi_params_section}\n"
            f"        {_pyi_ret_desc}\n"
            f'        """\n'
            if (sig or ret_ann != "None")
            else f'        """{name}."""\n'
        )
        stub = (
            f"    def {name}(self, {sig}) -> {ret_ann}:\n{_pyi_doc}"
            if sig
            else f"    def {name}(self) -> {ret_ann}:\n{_pyi_doc}"
        )
        pyi_lines.append(stub)

    method_decls = "\n\n".join(decl_lines) + "\n" if decl_lines else ""

    _method_bench_blocks = [_bench_method_block(component, m) for m in methods]
    _filled = [b for b in _method_bench_blocks if b]
    bench_methods_timing_block = "\n" + "\n\n".join(_filled) if _filled else ""
    return {
        "method_decls": method_decls,
        "extra_buf_fields": "".join(buf_fields),
        "extra_buf_free": "".join(buf_free),
        "extra_buf_alloc": "".join(buf_alloc),
        "extra_methods_c": "\n\n".join(method_c_parts),
        "extra_methods_pymethoddef": "".join(pmd_lines),
        "pyi_extra_methods": (
            "\n" + "\n\n".join(pyi_lines) + "\n" if pyi_lines else ""
        ),
        "bench_methods_timing_block": bench_methods_timing_block,
        "varargs_binding_files": varargs_binding_files,
        **(
            {
                "builtin_reset_c": "",
                "builtin_reset_pmd": "",
                "builtin_reset_decl": "",
                # gh-131: suppress the template's hardcoded reset() stub
                # when the user declared a [[methods]] entry named "reset";
                # pyi_extra_methods will contain the user-defined variant.
                "builtin_reset_pyi": "",
            }
            if user_has_reset
            else {}
        ),
    }


# ---------------------------------------------------------------------------
# make_properties_ctx
# ---------------------------------------------------------------------------


def make_properties_ctx(
    component: str,
    Component: str,
    properties: list[dict],
    state_var_names: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Generate getset_def and tp_getset_decl context keys for Python properties.

    state_var_names: names already declared by make_state_ctx(); those are
    excluded from property_decls to avoid duplicate C declarations.

    Each property dict has: name, type (a _CTYPE_META key), writable (bool).
    """
    _EMPTY: dict[str, str] = {
        "getset_def": "",
        "tp_getset_decl": "",
        "property_decls": "",
        "property_struct_fields": "",
    }
    if not properties:
        return _EMPTY

    guard = (
        "    if (!self->handle) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
    )

    getter_parts: list[str] = []
    getset_entries: list[str] = []
    decl_lines: list[str] = []
    struct_field_lines: list[str] = []

    for p in properties:
        pname: str = p["name"]
        ctype: str = p.get("type") or p.get("ctype", "size_t")
        writable: bool = p.get("writable", False)
        field: bool = p.get("field", False)
        buf_field: str = p.get("buf_field", "")
        len_field: str = p.get("len_field", "n")
        valid_field: str = p.get("valid_field", "")

        meta = _CTYPE_META.get(ctype, _CTYPE_META["size_t"])
        disp = _ctype_display(ctype)

        if buf_field:
            _elem_ct = ctype[:-2] if ctype.endswith("[]") else ctype
            _elem_meta = _CTYPE_META.get(
                _elem_ct, _CTYPE_META["float _Complex"]
            )
            _np_enum = _NP_ENUM.get(_elem_meta["py_type"], "NPY_CFLOAT")
            _valid_check = (
                f"    if (!self->handle->{valid_field}) Py_RETURN_NONE;\n"
                if valid_field
                else ""
            )
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"{_valid_check}"
                f"    npy_intp dim ="
                f" (npy_intp)self->handle->{len_field};\n"
                f"    PyObject *arr = PyArray_SimpleNewFromData(\n"
                f"        1, &dim, {_np_enum},"
                f" self->handle->{buf_field});\n"
                f"    if (!arr) return NULL;\n"
                f"    PyArray_SetBaseObject("
                f"(PyArrayObject *)arr, (PyObject *)self);\n"
                f"    Py_INCREF(self);\n"
                f"    return arr;\n"
                f"}}"
            )
        elif p.get("expr"):
            _expr = p["expr"]
            to_py = meta["to_py"](_expr)
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"    return {to_py};\n"
                f"}}"
            )
        elif field:
            # When a property aliases an existing state field (same name), do
            # not re-emit the struct member — make_state_ctx already declared
            # it.  Otherwise the struct ends up with duplicate fields and the
            # compiler errors out (gh-70).
            if pname not in state_var_names:
                struct_field_lines.append(f"    {disp} {pname};")
            to_py = meta["to_py"](f"self->handle->{pname}")
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"    return {to_py};\n"
                f"}}"
            )
        else:
            to_py = meta["to_py"](f"{component}_get_{pname}(self->handle)")
            implement_cmt = (
                "    /* <<IMPLEMENT: return the computed or stored value>> */\n"
                if pname not in state_var_names
                else ""
            )
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"{implement_cmt}"
                f"    return {to_py};\n"
                f"}}"
            )
            if pname not in state_var_names:
                decl_lines.append(
                    f"/**\n"
                    f" * @brief Get {pname}.\n"
                    f" * @param state  Must be non-NULL.\n"
                    f" * @return Current {pname} value ({disp}).\n"
                    f" */\n"
                    f"{disp} {component}_get_{pname}"
                    f"(const {component}_state_t *state);"
                )

        getter_parts.append(getter)

        setter_name = "NULL"
        if writable:
            setter_name = f"(setter){Component}_setprop_{pname}"
            if "parse_type" in meta:
                parse_block = (
                    f"    {meta['parse_type']} v_raw ="
                    f" {meta['parse_zero']};\n"
                    f'    if (!PyArg_Parse(value, "{meta["fmt"]}", &v_raw))'
                    f" return -1;\n"
                    f"    {disp} v = {meta['to_c']('v')};\n"
                )
            else:
                parse_block = (
                    f"    {disp} v = {meta['zero']};\n"
                    f'    if (!PyArg_Parse(value, "{meta["fmt"]}", &v))'
                    f" return -1;\n"
                )
            if field:
                assign_line = f"    self->handle->{pname} = v;\n"
            else:
                assign_line = (
                    f"    {component}_set_{pname}(self->handle, v);\n"
                )
                if pname not in state_var_names:
                    decl_lines.append(
                        f"/**\n"
                        f" * @brief Set {pname}.\n"
                        f" * @param state  Must be non-NULL.\n"
                        f" * @param val    New value ({disp}).\n"
                        f" */\n"
                        f"void {component}_set_{pname}"
                        f"({component}_state_t *state, {disp} val);"
                    )
            setter = (
                f"static int\n"
                f"{Component}_setprop_{pname}"
                f"({Component}Object *self,"
                f" PyObject *value, void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return -1;\n"
                f"    }}\n"
                f"{parse_block}"
                f"{assign_line}"
                f"    return 0;\n"
                f"}}"
            )
            getter_parts.append(setter)

        getset_entries.append(
            f'    {{ "{pname}", (getter){Component}_getprop_{pname},'
            f" {setter_name}, NULL, NULL }},"
        )

    getset_body = "\n".join(getter_parts)
    entries_str = "\n".join(getset_entries)
    getset_def = (
        f"{getset_body}\n\n"
        f"static PyGetSetDef {Component}_getset[] = {{\n"
        f"{entries_str}\n"
        f"    {{ NULL }}\n"
        f"}};\n"
    )
    tp_getset_decl = f"\n    .tp_getset    = {Component}_getset,"
    property_decls = "\n".join(decl_lines) + "\n" if decl_lines else ""
    property_struct_fields = (
        "\n" + "\n".join(struct_field_lines) if struct_field_lines else ""
    )

    return {
        "getset_def": getset_def,
        "tp_getset_decl": tp_getset_decl,
        "property_decls": property_decls,
        "property_struct_fields": property_struct_fields,
    }
