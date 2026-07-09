"""_context/_parse.py — shared parse-block builders.

Functions that construct C parse/argument-handling fragments used by
multiple make_*_ctx() builders.
"""

from __future__ import annotations

from .._types import (
    _CTYPE_META,
    _CTYPE_TO_NPY,
    _join_fmt_with_optional,
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
        elif p.get("capsule"):
            # gh-432: a foreign C pointer crossing as a named PyCapsule.
            # Parsed as a raw object; unwrapped after ParseTuple (before any
            # array acquisition, so an unwrap error needs no cleanup):
            #   None                        -> NULL (the C-side detach idiom)
            #   PyCapsule("<name>")         -> its pointer (name-checked)
            #   anything with a `_capsule`  -> that attribute, then as above
            # The `_capsule` duck-typing lets callers pass the friendly
            # wrapper object rather than digging the capsule out themselves.
            cname = p["capsule"]
            disp = _ctype_display(ptype)
            if not disp.endswith("*"):
                disp += " "
            obj_var = f"{pname}_obj"
            decl_lines.append(f"    PyObject *{obj_var} = Py_None;")
            fmt_chars.append("O")
            addr_exprs.append(f"&{obj_var}")
            conv_lines.append(
                f"    {disp}{pname} = NULL;\n"
                f"    if ({obj_var} != Py_None) {{\n"
                f"        PyObject *{pname}_cap = {obj_var};\n"
                f"        Py_INCREF({pname}_cap);\n"
                f"        if (!PyCapsule_CheckExact({pname}_cap)) {{\n"
                f"            Py_DECREF({pname}_cap);\n"
                f"            {pname}_cap = PyObject_GetAttrString(\n"
                f'                {obj_var}, "_capsule");\n'
                f"            if (!{pname}_cap)\n"
                f"                return NULL;\n"
                f"        }}\n"
                f"        {pname} = ({disp})PyCapsule_GetPointer(\n"
                f'            {pname}_cap, "{cname}");\n'
                f"        Py_DECREF({pname}_cap);\n"
                f"        if (!{pname})\n"
                f"            return NULL;\n"
                f"    }}"
            )
            call_args.append(pname)
        else:
            meta = _CTYPE_META[ptype]
            disp = _ctype_display(ptype)
            fmt_chars.append(meta["fmt"])

            if "parse_type" in meta:
                raw = f"{pname}_raw"
                # gh-432 drive-by: seed the raw local with the gh-240
                # default (not parse_zero) so an omitted defaulted arg
                # yields the default — previously only the non-parse_type
                # branch honoured `default`, so e.g. `decim: uint32_t = 1`
                # silently parsed as 0 when omitted.
                _raw_init = p.get("default") or meta["parse_zero"]
                decl_lines.append(
                    f"    {meta['parse_type']} {raw} = {_raw_init};"
                )
                addr_exprs.append(f"&{raw}")
                conv_lines.append(
                    f"    {disp} {pname} = {meta['to_c'](pname)};"
                )
            else:
                # gh-240: a scalar with a `default` is optional — seed its C
                # local with the default literal so an omitted arg yields it.
                init = p.get("default") or meta["zero"]
                decl_lines.append(f"    {disp} {pname} = {init};")
                addr_exprs.append(f"&{pname}")

            call_args.append(pname)

    # gh-238/gh-240: named methods are positional-OR-keyword (matching functions
    # and constructors). Each param name is a kwarg; a param with a `default`
    # goes after the `|` (optional). The wrapper must take a `PyObject *kwds`.
    kwnames = "".join(f'"{p["name"]}", ' for p in params)
    fmt_str = _join_fmt_with_optional(fmt_chars, params)
    addr_str = ", ".join(addr_exprs)
    lines = (
        [f"    static char *_kwlist[] = {{{kwnames}NULL}};"]
        + decl_lines
        + [
            f'    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{fmt_str}",',
            f"            _kwlist, {addr_str}))",
            "        return NULL;",
        ]
        + conv_lines
        + arr_acq
    )
    cleanup = "".join(f"    Py_DECREF({a});\n" for a in arr_names)
    return "\n".join(lines) + "\n", ", ".join(call_args), cleanup


def _step_parse_block(
    sample_type: str,
    samp: dict,
    ctrl: "list[tuple[str, str, str]]" = (),
) -> str:
    """4-space-indented parse block for step(); ends without trailing newline.

    Uses 'x_raw' as the intermediate parse variable so to_c("x") works
    (the to_c lambdas append '_raw' to the base name they receive).

    ``ctrl`` is the controllable per-call override list (gh-240): each entry
    ``(name, c_display_type, pyarg_fmt)`` becomes a **positional-optional**
    argument after ``x`` — ``step(x[, gain])``. step() stays positional-only
    (never METH_KEYWORDS) because a keyword call costs ~3.4x the call on this
    per-sample hot path. Each override defaults to the live ``self->handle->``
    field, so omitting it is free and the override is non-persistent. Empty
    ``ctrl`` reproduces the original byte-for-byte (the ``|`` is absent).
    """
    disp = _ctype_display(sample_type)
    ctrl_locals = "".join(
        f"    {cdisp} {name} = self->handle->{name};\n"
        for name, cdisp, _ in ctrl
    )
    ctrl_fmt = "|" + "".join(f for _, _, f in ctrl) if ctrl else ""
    ctrl_refs = "".join(f", &{name}" for name, _, _ in ctrl)
    if "parse_type" in samp:
        parse_type = samp["parse_type"]
        parse_zero = samp["parse_zero"]
        fmt = samp["fmt"]
        to_c_expr = samp["to_c"]("x")
        return (
            f"    {parse_type} x_raw = {parse_zero};\n"
            f"{ctrl_locals}"
            f'    if (!PyArg_ParseTuple(args, "{fmt}{ctrl_fmt}",'
            f" &x_raw{ctrl_refs}))\n"
            f"        return NULL;\n"
            f"    {disp} x = {to_c_expr};"
        )
    else:
        fmt = samp["fmt"]
        return (
            f"    {disp} x;\n"
            f"{ctrl_locals}"
            f'    if (!PyArg_ParseTuple(args, "{fmt}{ctrl_fmt}",'
            f" &x{ctrl_refs}))\n"
            f"        return NULL;"
        )
