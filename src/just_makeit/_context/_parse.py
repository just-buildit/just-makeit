"""_context/_parse.py — shared parse-block builders.

Functions that construct C parse/argument-handling fragments used by
multiple make_*_ctx() builders.
"""

from .._types import (
    _CTYPE_META,
    _CTYPE_TO_NPY,
    is_array_param_type,
    array_elem_ctype,
    _ctype_display,
)


def _build_ml_doc(lines: list[str]) -> str:
    """Render a list of logical doc lines into adjacent C string literals.

    Each item in *lines* becomes one C string literal ending with ``\\n``.
    Continuation lines are indented with 5 spaces so the result slots
    cleanly into the 4th element of a PyMethodDef entry::

        pmd = (
            f'    {{"step", (PyCFunction)Nco_step, METH_VARARGS,\\n'
            f'     {_build_ml_doc(lines)}}},\\n'
        )

    Special characters (backslashes, double-quotes) inside *lines* are
    escaped for the C string literal automatically.
    """

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    return "\n     ".join(f'"{_esc(ln)}\\n"' for ln in lines)


def _build_params_parse(
    params: list[dict],
) -> tuple[str, str, str]:
    """Build parse block + C call args + cleanup for a named multi-param method.

    params: list of {"name": str, "type": str}
      Scalar types come from _CTYPE_META.
      Array types end with '[]', e.g. "float _Complex[]"; their element type
      must be in _CTYPE_TO_NPY.  Array params expand to two C args:
      (const elem_t *name, size_t name_len).

    Returns (parse_block, call_args_c, cleanup):
      parse_block  — indented C code: declarations, PyArg_ParseTuple, array
                     conversion and error-exit paths with partial cleanup
      call_args_c  — comma-sep C variables/expressions for the downstream call
      cleanup      — Py_DECREF lines for all acquired numpy arrays (empty string
                     when no array params); caller must emit before every return
    """
    decl_lines: list[str] = []  # before PyArg_ParseTuple
    addr_exprs: list[str] = []  # &name args for PyArg_ParseTuple
    fmt_chars: list[str] = []  # format characters
    conv_lines: list[str] = []  # after PyArg_ParseTuple (scalars needing to_c)
    arr_acq: list[str] = []  # array acquisition lines (after ParseTuple)
    call_args: list[str] = []  # final C args to pass
    arr_names: list[str] = []  # arr variable names for Py_DECREF cleanup

    for p in params:
        pname = p["name"]
        ptype = p["type"]

        if is_array_param_type(ptype):
            elem_ct = array_elem_ctype(ptype)
            npy_enum = _CTYPE_TO_NPY[elem_ct]
            elem_disp = _ctype_display(elem_ct)
            obj_var = f"{pname}_obj"
            arr_var = f"{pname}_arr"

            decl_lines.append(f"    PyObject *{obj_var} = NULL;")
            fmt_chars.append("O")
            addr_exprs.append(f"&{obj_var}")

            # Build error path: decref all arrays acquired so far.
            prior_decrefs = "".join(f" Py_DECREF({a});" for a in arr_names)
            is_out = bool(p.get("out"))
            npy_flags = (
                "NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE"
                if is_out
                else "NPY_ARRAY_C_CONTIGUOUS"
            )
            const_qual = "" if is_out else "const "
            arr_acq.append(
                f"    PyArrayObject *{arr_var} = (PyArrayObject *)"
                f"PyArray_FROM_OTF(\n"
                f"        {obj_var}, {npy_enum}, {npy_flags});\n"
                f"    if (!{arr_var}) {{{prior_decrefs} return NULL; }}"
            )
            arr_acq.append(
                f"    {const_qual}{elem_disp} *{pname} = "
                f"({const_qual}{elem_disp} *)PyArray_DATA({arr_var});\n"
                f"    size_t {pname}_len = (size_t)PyArray_SIZE({arr_var});"
            )
            arr_names.append(arr_var)
            call_args.extend([pname, f"{pname}_len"])
        else:
            meta = _CTYPE_META[ptype]
            disp = _ctype_display(ptype)
            fmt_chars.append(meta["fmt"])

            if "parse_type" in meta:
                raw = f"{pname}_raw"
                decl_lines.append(
                    f"    {meta['parse_type']} {raw} = {meta['parse_zero']};"
                )
                addr_exprs.append(f"&{raw}")
                conv_lines.append(
                    f"    {disp} {pname} = {meta['to_c'](pname)};"
                )
            else:
                decl_lines.append(f"    {disp} {pname} = {meta['zero']};")
                addr_exprs.append(f"&{pname}")

            call_args.append(pname)

    fmt_str = "".join(fmt_chars)
    addr_str = ", ".join(addr_exprs)
    lines = (
        decl_lines
        + [
            f'    if (!PyArg_ParseTuple(args, "{fmt_str}", {addr_str}))',
            "        return NULL;",
        ]
        + conv_lines
        + arr_acq
    )
    cleanup = "".join(f"    Py_DECREF({a});\n" for a in arr_names)
    return "\n".join(lines) + "\n", ", ".join(call_args), cleanup


def _step_parse_block(sample_type: str, samp: dict) -> str:
    """4-space-indented parse block for step(); ends without trailing newline.

    Uses 'x_raw' as the intermediate parse variable so to_c("x") works
    (the to_c lambdas append '_raw' to the base name they receive).
    """
    disp = _ctype_display(sample_type)
    if "parse_type" in samp:
        parse_type = samp["parse_type"]
        parse_zero = samp["parse_zero"]
        fmt = samp["fmt"]
        to_c_expr = samp["to_c"]("x")
        return (
            f"    {parse_type} x_raw = {parse_zero};\n"
            f'    if (!PyArg_ParseTuple(args, "{fmt}", &x_raw))\n'
            f"        return NULL;\n"
            f"    {disp} x = {to_c_expr};"
        )
    else:
        fmt = samp["fmt"]
        return (
            f"    {disp} x;\n"
            f'    if (!PyArg_ParseTuple(args, "{fmt}", &x))\n'
            f"        return NULL;"
        )
