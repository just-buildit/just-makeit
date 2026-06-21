"""_context/_state.py — state init helpers and make_state_ctx().

Builds the struct-fields, getter/setter, and init-parse portions of the
rendering dict.
"""

from __future__ import annotations

from .._types import (
    _CTYPE_META,
    _ARRAY_DTYPE,
    _CTYPE_TO_NPY,
    _CTYPE_TO_DTYPE,
    _ctype_display,
    is_array_param_type,
    array_param_ndim,
    array_elem_ctype,
    is_string_enum_type,
    string_enum_choices,
    parse_array_type,
    is_valid_type,
    SUPPORTED_TYPES,
)
from ._types import (
    _NP_DTYPE_ENUM,
    _c_set_val,
    _py_default,
    _py_sample_val,
)


# ---------------------------------------------------------------------------
# _build_no_state_init_ctx
# ---------------------------------------------------------------------------


def _build_no_state_init_ctx(
    component: str,
    Component: str,
    params: list[tuple],
    array_args: list[tuple[str, str]] = (),
    init_post_parse_impl: str = "",
) -> dict[str, str]:
    """Build the init-parse context keys for a --no-state object.

    Handles three kinds of init-params in addition to --array-arg:

    * Array-typed  (``type = "float _Complex[]"`` or ``"float _Complex[][]"``)
      — required positional numpy arrays.  1-D params expand to
      ``(const T *name, size_t name_len)``; 2-D params expand to
      ``(const T *name, size_t name_dim0, size_t name_dim1)`` and add a
      ``PyArray_NDIM`` shape check in the generated binding.

    * String-enum  (``type = "string_enum:a,b,c,d"``)
      — optional keyword string parsed and mapped to an ``int`` index before
      being forwarded to the C constructor.  Covers any C enum the caller
      exposes as human-readable names.

    * Scalar       (any type in ``_CTYPE_META``)
      — optional keyword with a default value; existing behaviour.

    Ordering in kwlist / C signature:
      --array-arg first, then array init-params, then string-enum,
      then scalars.
    """
    # ── Classify params ───────────────────────────────────────────────────

    arr_ip: list[tuple[str, str, int, str]] = []
    str_enum_ip: list[tuple[str, list[str], str]] = []
    scalar_ip: list[tuple] = []
    dispatch_meta: dict[str, tuple[str, str, str]] = {}
    opt_arr_ip: list[tuple[str, str, int, str, str]] = []

    for param in params:
        name, ct, dflt = param[:3]
        dflt_raw = param[3] if len(param) > 3 else ""
        real_type = param[4] if len(param) > 4 else ""
        real_create_fn_p = param[5] if len(param) > 5 else ""
        optional_flag = param[6] if len(param) > 6 else False
        alt_create_fn = param[7] if len(param) > 7 else ""
        required_flag = param[8] if len(param) > 8 else False
        if is_array_param_type(ct):
            elem_ct = array_elem_ctype(ct)
            ndim = array_param_ndim(ct)
            if optional_flag:
                opt_arr_ip.append(
                    (
                        name,
                        elem_ct,
                        ndim,
                        _CTYPE_TO_NPY[elem_ct],
                        alt_create_fn,
                    )
                )
            else:
                arr_ip.append((name, elem_ct, ndim, _CTYPE_TO_NPY[elem_ct]))
                if real_type and real_create_fn_p:
                    real_elem_ct = array_elem_ctype(real_type)
                    dispatch_meta[name] = (
                        real_elem_ct,
                        _CTYPE_TO_NPY[real_elem_ct],
                        real_create_fn_p,
                    )
        elif is_string_enum_type(ct):
            str_enum_ip.append((name, string_enum_choices(ct), dflt))
        else:
            scalar_ip.append((name, ct, dflt, dflt_raw, required_flag))

    # --array-arg entries (dtype-string form)
    _aa = list(array_args)
    _aa_ctypes = [(_ARRAY_DTYPE[dt][0], _ARRAY_DTYPE[dt][1]) for _, dt in _aa]

    # ── C create() signature, Doxygen docs, and call args — TOML order ───

    _arr_meta: dict[str, tuple] = {
        n: (act, andim) for n, act, andim, _ in arr_ip
    }
    _str_enum_meta: dict[str, tuple] = {
        sn: (choices, sdflt) for sn, choices, sdflt in str_enum_ip
    }
    _scalar_meta: dict[str, tuple] = {
        n: (ct, dflt) for n, ct, dflt, *_ in scalar_ip
    }
    _opt_arr_names: frozenset[str] = frozenset(n for n, *_ in opt_arr_ip)

    # gh-266: split scalar init-params into required (no default — parsed as a
    # mandatory positional, *before* the PyArg `|`) and optional (the historic
    # behaviour, defaulted after `|`). Relative TOML order is preserved within
    # each group. PyArg_ParseTupleAndKeywords (and Python signatures) require
    # every required converter to precede the optionals, so required scalars sit
    # right after the required arrays and ahead of the optional kwargs.
    req_scalar_ip = [(n, ct, d, dr) for n, ct, d, dr, rq in scalar_ip if rq]
    opt_scalar_ip = [
        (n, ct, d, dr) for n, ct, d, dr, rq in scalar_ip if not rq
    ]

    sig_parts: list[str] = []
    doc_parts: list[str] = []
    call_parts: list[str] = []
    c_create_parts_ordered: list[str] = []

    for (name, dt), (ct, __) in zip(_aa, _aa_ctypes):
        disp = _ctype_display(ct)
        sig_parts.append(f"const {disp} *{name}, size_t {name}_len")
        doc_parts.append(
            f" * @param {name}  Input {dt} array (length passed as {name}_len)."
        )
        call_parts.append(
            f"(const {disp} *)PyArray_DATA({name}_arr), {name}_len"
        )
        c_create_parts_ordered.append("NULL, 0")

    for param in params:
        pname = param[0]
        param[1]
        param[2] if len(param) > 2 else ""
        if pname in _opt_arr_names:
            continue
        if pname in _arr_meta:
            act, andim = _arr_meta[pname]
            adisp = _ctype_display(act)
            if andim == 2:
                sig_parts.append(
                    f"const {adisp} *{pname}, size_t {pname}_dim0, size_t {pname}_dim1"
                )
                doc_parts.append(
                    f" * @param {pname}  Input {adisp} 2-D array"
                    f" (shape: {pname}_dim0 x {pname}_dim1)."
                )
                call_parts.append(
                    f"(const {adisp} *)PyArray_DATA({pname}_arr),"
                    f" {pname}_dim0, {pname}_dim1"
                )
                c_create_parts_ordered.append("NULL, 0, 0")
            else:
                sig_parts.append(f"const {adisp} *{pname}, size_t {pname}_len")
                doc_parts.append(
                    f" * @param {pname}  Input {adisp} array"
                    f" (length passed as {pname}_len)."
                )
                call_parts.append(
                    f"(const {adisp} *)PyArray_DATA({pname}_arr), {pname}_len"
                )
                c_create_parts_ordered.append("NULL, 0")
        elif pname in _str_enum_meta:
            choices, _ = _str_enum_meta[pname]
            sig_parts.append(f"int {pname}")
            doc_parts.append(
                f" * @param {pname}  Enum index; 0={choices[0]}"
                + (
                    f"…{len(choices) - 1}={choices[-1]}."
                    if len(choices) > 1
                    else "."
                )
            )
            call_parts.append(pname)
            c_create_parts_ordered.append("0")
        else:
            ct_s, dflt_s = _scalar_meta[pname]
            sig_parts.append(f"{ct_s} {pname}")
            # A required scalar has no default; the @param note says so, and the
            # generated smoke test seeds it with the type's zero (the stub ctor
            # does not validate, so it still builds green) — gh-266.
            req_s = not dflt_s
            doc_parts.append(
                f" * @param {pname}  {pname}"
                + (" (required)." if req_s else f" (default: {dflt_s}).")
            )
            call_parts.append(pname)
            c_create_parts_ordered.append(dflt_s or _CTYPE_META[ct_s]["zero"])

    create_params = ", ".join(sig_parts) or "void"
    create_param_docs = (
        "\n".join(doc_parts)
        or " * @param (none)  Caller is responsible for all state management."
    )

    # ── kwlist / locals / parse format ────────────────────────────────────

    kwlist_items = (
        [f'"{name}"' for name, _ in _aa]
        + [f'"{name}"' for name, _, __, ___ in arr_ip]
        + [f'"{name}"' for name, *_ in req_scalar_ip]
        + [f'"{name}"' for name, _, __ in str_enum_ip]
        + [f'"{name}"' for name, *_ in opt_arr_ip]
        + [f'"{name}"' for name, *_ in opt_scalar_ip]
        + ["NULL"]
    )
    init_kwlist = ", ".join(kwlist_items)

    local_lines: list[str] = (
        [f"    PyObject *{name}_obj = NULL;" for name, _ in _aa]
        + [f"    PyObject *{name}_obj = NULL;" for name, _, __, ___ in arr_ip]
        + [f"    PyObject *{name}_obj = NULL;" for name, *_ in opt_arr_ip]
    )
    parse_args: list[str] = [f"&{name}_obj" for name, _ in _aa] + [
        f"&{name}_obj" for name, _, __, ___ in arr_ip
    ]
    post_lines: list[str] = []

    def _emit_scalar(name: str, ct: str, dflt: str, dflt_raw: str) -> str:
        """Declare a scalar init-param's C local(s) and return its PyArg ref.

        A ``parse_type`` scalar (e.g. ``size_t`` via the ``K`` intermediate)
        parses into a ``_raw`` local that a post-parse line narrows to the real
        type; everything else parses straight into the typed local. Required
        params carry an empty ``dflt`` — the seed value is irrelevant since
        PyArg always overwrites it — so fall back to the type's zero to keep the
        declaration valid C.
        """
        meta = _CTYPE_META[ct]
        if meta.get("parse_type"):
            raw_init = dflt_raw or dflt or meta["parse_zero"]
            local_lines.append(
                f"    {meta['parse_type']} {name}_raw = {raw_init};"
            )
            post_lines.append(f"    {ct} {name} = {meta['to_c'](name)};")
            return f"&{name}_raw"
        local_lines.append(f"    {ct} {name} = {dflt or meta['zero']};")
        return f"&{name}"

    # Required scalars are parsed before the optional kwargs (str-enum, optional
    # array, optional scalar), matching their position ahead of the PyArg `|`.
    for name, ct, dflt, dflt_raw in req_scalar_ip:
        parse_args.append(_emit_scalar(name, ct, dflt, dflt_raw))

    for sname, choices, sdflt in str_enum_ip:
        local_lines.append(f'    const char *{sname}_str = "{sdflt}";')
        parse_args.append(f"&{sname}_str")
        enum_lines = [f"    int {sname} = 0;"]
        for i, choice in enumerate(choices):
            kw = "if" if i == 0 else "else if"
            enum_lines.append(
                f'    {kw} (strcmp({sname}_str, "{choice}") == 0) {sname} = {i};'
            )
        choices_str = ", ".join(f'\\"{c}\\"' for c in choices)
        enum_lines += [
            "    else {",
            f'        PyErr_Format(PyExc_ValueError, "{sname} must be one of'
            f" {choices_str}, got '%s'\", {sname}_str);",
            "        return -1;",
            "    }",
        ]
        post_lines.extend(enum_lines)

    for oname, *_ in opt_arr_ip:
        parse_args.append(f"&{oname}_obj")

    # Optional scalars keep the historic behaviour: defaulted, parsed after the
    # PyArg `|`. (gh-244: a parse_type param such as size_t seeds its `_raw`
    # local from dflt_raw, then dflt, then the type's parse_zero — handled in
    # _emit_scalar.)
    for name, ct, dflt, dflt_raw in opt_scalar_ip:
        parse_args.append(_emit_scalar(name, ct, dflt, dflt_raw))

    if init_post_parse_impl:
        post_lines.append(init_post_parse_impl.rstrip())

    init_locals = "\n".join(local_lines)
    init_post_parse = ("\n".join(post_lines) + "\n") if post_lines else ""

    n_required = len(_aa) + len(arr_ip)
    # Required converters (before the PyArg `|`): the positional arrays, then
    # any required scalars (gh-266).
    required_fmt = "O" * n_required + "".join(
        _CTYPE_META[ct]["fmt"] for _, ct, *_ in req_scalar_ip
    )
    optional_fmt = (
        "s" * len(str_enum_ip)
        + "O" * len(opt_arr_ip)
        + "".join(_CTYPE_META[ct]["fmt"] for _, ct, *_ in opt_scalar_ip)
    )
    if optional_fmt:
        init_parse_fmt = required_fmt + "|" + optional_fmt
    else:
        # All-required (or empty): no `|` at all — e.g. "kk" for two required
        # size_t params, or "|" when the ctor takes nothing.
        init_parse_fmt = required_fmt or "|"

    init_parse_args = ", ".join(parse_args)
    create_call_args = ", ".join(call_parts)

    if _aa or arr_ip or str_enum_ip or opt_arr_ip or scalar_ip:
        init_parse_block = (
            f"    static char *kwlist[] = {{{init_kwlist}}};\n"
            f"{init_locals}\n"
            f"\n"
            f"    if (!PyArg_ParseTupleAndKeywords(args, kwds,"
            f' "{init_parse_fmt}", kwlist,\n'
            f"                                     {init_parse_args}))\n"
            f"        return -1;\n"
            f"{init_post_parse}"
        )
    else:
        init_parse_block = "    (void)args;\n    (void)kwds;\n"

    # ── array_args_parse_block (FROM_OTF) ─────────────────────────────────

    aapb_lines: list[str] = []
    allocated: list[str] = []

    for (name, _), (ct, npy_enum) in zip(_aa, _aa_ctypes):
        cleanup = "".join(f" Py_DECREF({n}_arr);" for n in allocated)
        aapb_lines.append(
            f"    PyArrayObject *{name}_arr ="
            f" (PyArrayObject *)PyArray_FROM_OTF(\n"
            f"        {name}_obj, {npy_enum}, NPY_ARRAY_C_CONTIGUOUS);\n"
            f"    if (!{name}_arr) {{{cleanup} return -1; }}\n"
            f"    size_t {name}_len = (size_t)PyArray_SIZE({name}_arr);\n"
        )
        allocated.append(name)

    for aname, act, andim, anpy in arr_ip:
        cleanup = "".join(f" Py_DECREF({n}_arr);" for n in allocated)
        if aname in dispatch_meta:
            real_ect, real_npy, d_create_fn = dispatch_meta[aname]
            real_adisp = _ctype_display(real_ect)
            complex_adisp = _ctype_display(act)
            complex_cast = (
                f"(const {complex_adisp} *)PyArray_DATA({aname}_arr)"
            )
            real_cast = f"(const {real_adisp} *)PyArray_DATA({aname}_arr)"
            real_call_args = create_call_args.replace(
                complex_cast, real_cast, 1
            )
            aapb_lines.append(
                f"    /* dtype dispatch: {real_adisp} → {d_create_fn},"
                f" {complex_adisp} → {component}_create */\n"
                f"    {{\n"
                f"        PyArrayObject *_{aname}_probe ="
                f" (PyArrayObject *)PyArray_CheckFromAny(\n"
                f"            {aname}_obj, NULL, 1, 1,"
                f" NPY_ARRAY_C_CONTIGUOUS, NULL);\n"
                f"        int _{aname}_real = _{aname}_probe &&"
                f" (PyArray_TYPE(_{aname}_probe) == {real_npy});\n"
                f"        Py_XDECREF(_{aname}_probe);\n"
                f"        if (_{aname}_real) {{\n"
                f"            PyArrayObject *{aname}_arr ="
                f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"                {aname}_obj, {real_npy},"
                f" NPY_ARRAY_C_CONTIGUOUS);\n"
                f"            if (!{aname}_arr) {{{cleanup} return -1; }}\n"
                f"            size_t {aname}_len ="
                f" (size_t)PyArray_SIZE({aname}_arr);\n"
                f"            self->handle ="
                f" {d_create_fn}({real_call_args});\n"
                f"            Py_DECREF({aname}_arr);\n"
                f"        }} else {{\n"
                f"            PyArrayObject *{aname}_arr ="
                f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"                {aname}_obj, {anpy},"
                f" NPY_ARRAY_C_CONTIGUOUS);\n"
                f"            if (!{aname}_arr) {{{cleanup} return -1; }}\n"
                f"            size_t {aname}_len ="
                f" (size_t)PyArray_SIZE({aname}_arr);\n"
                f"            self->handle ="
                f" {component}_create({create_call_args});\n"
                f"            Py_DECREF({aname}_arr);\n"
                f"        }}\n"
                f"    }}\n"
            )
        elif andim == 2:
            aapb_lines.append(
                f"    PyArrayObject *{aname}_arr ="
                f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"        {aname}_obj, {anpy}, NPY_ARRAY_C_CONTIGUOUS);\n"
                f"    if (!{aname}_arr) {{{cleanup} return -1; }}\n"
                f"    if (PyArray_NDIM({aname}_arr) != 2) {{\n"
                f"        PyErr_SetString(PyExc_ValueError,\n"
                f'                        "{aname} must be a 2-D array");\n'
                f"        {cleanup} Py_DECREF({aname}_arr);"
                f" return -1;\n"
                f"    }}\n"
                f"    size_t {aname}_dim0 ="
                f" (size_t)PyArray_DIM({aname}_arr, 0);\n"
                f"    size_t {aname}_dim1 ="
                f" (size_t)PyArray_DIM({aname}_arr, 1);\n"
            )
            allocated.append(aname)
        else:
            aapb_lines.append(
                f"    PyArrayObject *{aname}_arr ="
                f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"        {aname}_obj, {anpy}, NPY_ARRAY_C_CONTIGUOUS);\n"
                f"    if (!{aname}_arr) {{{cleanup} return -1; }}\n"
                f"    size_t {aname}_len ="
                f" (size_t)PyArray_SIZE({aname}_arr);\n"
            )
            allocated.append(aname)

    scalar_call_str = create_call_args
    for oname, oact, ondim, onpy, oalt_fn in opt_arr_ip:
        odisp = _ctype_display(oact)
        if ondim == 2:
            aapb_lines.append(
                f"    if ({oname}_obj && {oname}_obj != Py_None) {{\n"
                f"        PyArrayObject *{oname}_arr ="
                f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"            {oname}_obj, {onpy},"
                f" NPY_ARRAY_C_CONTIGUOUS);\n"
                f"        if (!{oname}_arr) {{ return -1; }}\n"
                f"        if (PyArray_NDIM({oname}_arr) != 2) {{\n"
                f"            PyErr_SetString(PyExc_ValueError,\n"
                f'                            "{oname} must be a 2-D array");\n'
                f"            Py_DECREF({oname}_arr); return -1;\n"
                f"        }}\n"
                f"        size_t {oname}_dim0 ="
                f" (size_t)PyArray_DIM({oname}_arr, 0);\n"
                f"        size_t {oname}_dim1 ="
                f" (size_t)PyArray_DIM({oname}_arr, 1);\n"
                f"        self->handle = {oalt_fn}(\n"
                f"            {oname}_dim0, {oname}_dim1,\n"
                f"            (const {odisp} *)PyArray_DATA({oname}_arr)"
                + (
                    f",\n            {scalar_call_str}"
                    if scalar_call_str
                    else ""
                )
                + f");\n"
                f"        Py_DECREF({oname}_arr);\n"
                f"    }} else {{\n"
                f"        self->handle ="
                f" {component}_create({scalar_call_str});\n"
                f"    }}\n"
            )
        else:
            aapb_lines.append(
                f"    if ({oname}_obj && {oname}_obj != Py_None) {{\n"
                f"        PyArrayObject *{oname}_arr ="
                f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                f"            {oname}_obj, {onpy},"
                f" NPY_ARRAY_C_CONTIGUOUS);\n"
                f"        if (!{oname}_arr) {{ return -1; }}\n"
                f"        size_t {oname}_len ="
                f" (size_t)PyArray_SIZE({oname}_arr);\n"
                f"        self->handle = {oalt_fn}(\n"
                f"            {oname}_len,"
                f" (const {odisp} *)PyArray_DATA({oname}_arr)"
                + (
                    f",\n            {scalar_call_str}"
                    if scalar_call_str
                    else ""
                )
                + f");\n"
                f"        Py_DECREF({oname}_arr);\n"
                f"    }} else {{\n"
                f"        self->handle ="
                f" {component}_create({scalar_call_str});\n"
                f"    }}\n"
            )

    array_args_parse_block = "".join(aapb_lines)
    array_args_decref = "".join(
        f"    Py_DECREF({name}_arr);\n" for name in allocated
    )

    if dispatch_meta or opt_arr_ip:
        create_line = ""
    else:
        create_line = (
            f"    self->handle = {component}_create({create_call_args});\n"
        )

    # ── pyi / test helpers ────────────────────────────────────────────────

    _NP_PY_TYPE: dict[str, str] = {
        "float32": "np.float32",
        "float64": "np.float64",
        "complex64": "np.complex64",
        "complex128": "np.complex128",
        "int8": "np.int8",
        "int16": "np.int16",
        "int32": "np.int32",
        "int64": "np.int64",
        "uint8": "np.uint8",
        "uint16": "np.uint16",
        "uint32": "np.uint32",
        "uint64": "np.uint64",
        "uintp": "np.uintp",
        "intp": "np.intp",
    }

    pyi_parts: list[str] = (
        [f"{name}: npt.ArrayLike" for name, _ in _aa]
        + [f"{aname}: npt.ArrayLike" for aname, _, __, ___ in arr_ip]
        # Required scalars have no default, so they precede every defaulted
        # parameter — Python signatures forbid a default-less arg after a
        # defaulted one (gh-266).
        + [
            f"{name}: {_CTYPE_META[ct]['py_type']}"
            for name, ct, *_ in req_scalar_ip
        ]
        + [f'{sname}: str = "{sdflt}"' for sname, _, sdflt in str_enum_ip]
        + [f"{oname}: npt.ArrayLike | None = None" for oname, *_ in opt_arr_ip]
        + [
            f"{name}: {_CTYPE_META[ct]['py_type']} = {_py_default(ct, dflt)}"
            for name, ct, dflt, *_ in opt_scalar_ip
        ]
    )
    init_params_pyi = ", ".join(pyi_parts)

    pyi_doc_sections: list[str] = []
    if _aa:
        pyi_doc_sections.append(
            "\n".join(
                f"    {name} : array-like\n        {dt} coefficients."
                for name, dt in _aa
            )
        )
    if arr_ip:
        pyi_doc_sections.append(
            "\n".join(
                f"    {aname} : array-like"
                f"{', shape (rows, cols)' if andim == 2 else ''}\n"
                f"        {_ctype_display(act)}"
                f" {'matrix' if andim == 2 else 'array'}."
                for aname, act, andim, _ in arr_ip
            )
        )
    if str_enum_ip:
        pyi_doc_sections.append(
            "\n".join(
                f'    {sname} : str, default "{sdflt}"\n'
                f"        One of: {', '.join(choices)}."
                for sname, choices, sdflt in str_enum_ip
            )
        )
    if opt_arr_ip:
        pyi_doc_sections.append(
            "\n".join(
                f"    {oname} : array-like or None, optional"
                f"{', shape (rows, cols)' if ondim == 2 else ''}\n"
                f"        {_ctype_display(oact)} array; when supplied"
                f" {oalt_fn} is called instead of the default constructor."
                for oname, oact, ondim, _, oalt_fn in opt_arr_ip
            )
        )
    if req_scalar_ip:
        pyi_doc_sections.append(
            "\n".join(
                f"    {name} : {_CTYPE_META[ct]['py_type']}\n"
                f"        {name} constructor parameter (required)."
                for name, ct, *_ in req_scalar_ip
            )
        )
    if opt_scalar_ip:
        pyi_doc_sections.append(
            "\n".join(
                f"    {name} : {_CTYPE_META[ct]['py_type']},"
                f" default {_py_default(ct, dflt)}\n"
                f"        {name} constructor parameter."
                for name, ct, dflt, *_ in opt_scalar_ip
            )
        )
    pyi_param_docs = "\n".join(pyi_doc_sections) or "    (none)"

    py_create_parts: list[str] = []
    for _, dt in _aa:
        py_create_parts.append(
            f"np.zeros(1, dtype={_NP_PY_TYPE.get(dt, 'np.float32')})"
        )
    for aname, act, andim, _ in arr_ip:
        dt = _CTYPE_TO_DTYPE.get(act, "float32")
        npt = _NP_PY_TYPE.get(dt, "np.float32")
        py_create_parts.append(
            f"np.zeros((1, 1), dtype={npt})"
            if andim == 2
            else f"np.zeros(1, dtype={npt})"
        )
    # Required scalars are positional-before the optional kwargs in the
    # generated ctor, so the smoke test passes them first; their seed value is
    # the type's zero (the stub ctor never validates, so the test still
    # builds green) — gh-266.
    py_create_parts += [
        _py_default(ct, dflt or _CTYPE_META[ct]["zero"])
        for _, ct, dflt, *_ in req_scalar_ip
    ]
    py_create_parts += [f'"{sdflt}"' for _, _, sdflt in str_enum_ip]
    py_create_parts += [
        _py_default(ct, dflt) for _, ct, dflt, *_ in opt_scalar_ip
    ]
    py_create_args = ", ".join(py_create_parts)

    c_create_args = ", ".join(c_create_parts_ordered)
    test_obj = f"        obj = {Component}({py_create_args})"

    return {
        "create_params": create_params,
        "create_param_docs": create_param_docs,
        "init_kwlist": init_kwlist,
        "init_locals": init_locals,
        "init_post_parse": init_post_parse,
        "init_parse_fmt": init_parse_fmt,
        "init_parse_args": init_parse_args,
        "init_parse_block": init_parse_block,
        "array_args_parse_block": array_args_parse_block,
        "array_args_decref": array_args_decref,
        "create_line": create_line,
        "create_call_args": create_call_args,
        "init_params_pyi": init_params_pyi,
        "pyi_param_docs": pyi_param_docs,
        "py_create_args": py_create_args,
        "c_create_args": c_create_args,
        # gh-181: on this path an empty c_create_args means the object has no
        # init params, i.e. a `create(void)` — so `<comp>_create()` is callable
        # and the bench must declare `obj` (else the unconditional destroy(obj)
        # below references an undeclared variable and the bench fails to build).
        "bench_create_stmt": (
            f"    {component}_state_t *obj = {component}_create({c_create_args});"
        ),
        "bench_destroy_stmt": f"    {component}_destroy(obj);",
        "getter_setter_test_py": (
            test_obj
            + "\n        pass  # no auto-state; add assertions for your fields"
        ),
        "reset_test_py": (
            test_obj
            + "\n        pass  # no auto-state; add assertions for your reset"
        ),
    }


# ---------------------------------------------------------------------------
# _doctest_safe_output
# ---------------------------------------------------------------------------


def _doctest_safe_output(ctype: str, default: str) -> str | None:
    """Return the expected Python repr for a getter's default, or None.

    Only returns a value when the default round-trips exactly through the C
    type so the doctest output is predictable without knowing float rounding
    details.
    """
    kind = _CTYPE_META[ctype]["kind"]
    if kind == "int":
        val = _py_default(ctype, default)
        try:
            int(val)
            return val
        except ValueError:
            return None
    if kind == "float":
        s = default.rstrip("fF")
        try:
            v = float(s)
            if v == int(v):
                return repr(v)
        except ValueError:
            pass
        return None
    if kind == "complex":
        return "0j"
    return None


# ---------------------------------------------------------------------------
# _pyi_examples_block
# ---------------------------------------------------------------------------


def _pyi_examples_block(
    scalar_vars: list[tuple[str, str, str]],
    has_array_args: bool,
    import_line: str,
    py_create_args: str,
    Component: str,
) -> str:
    """Build an indented ``Examples`` section for a .pyi class docstring.

    Returns an empty string when no doctest-safe getter examples exist.
    The returned string ends with a trailing newline and is ready to embed
    directly before the closing ``\"\"\"`` in the class docstring.
    """
    getter_pairs: list[tuple[str, str]] = []
    for name, ct, dflt in scalar_vars:
        out = _doctest_safe_output(ct, dflt)
        if out is not None:
            getter_pairs.append((name, out))

    lines: list[str] = [
        "    Examples",
        "    --------",
        "    Create with defaults:",
        "",
    ]
    if has_array_args:
        lines.append("    >>> import numpy as np")
    lines.append(f"    >>> {import_line}")
    lines.append(f"    >>> obj = {Component}({py_create_args})")

    for name, out in getter_pairs[:3]:
        lines.append(f"    >>> obj.get_{name}()")
        lines.append(f"    {out}")

    if getter_pairs:
        first_name, first_out = getter_pairs[0]
        first_ct = next(ct for n, ct, _ in scalar_vars if n == first_name)
        kind = _CTYPE_META[first_ct]["kind"]
        set_val = (
            "0"
            if (kind == "int" and first_out != "0")
            else (
                "42"
                if kind == "int"
                else "0.0"
                if first_out != "0.0"
                else "1.0"
            )
        )
        lines += [
            "",
            "    Reset restores defaults:",
            "",
            f"    >>> obj.set_{first_name}({set_val})",
            "    >>> obj.reset()",
            f"    >>> obj.get_{first_name}()",
            f"    {first_out}",
        ]

    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# _make_gs_decls_impls
# ---------------------------------------------------------------------------


def _make_gs_decls_impls(
    component: str,
    scalar_vars: list[tuple[str, str, str]],
    array_info: list[tuple[str, str, int]],
    type_suffix: str,
    ptr_name: str,
) -> tuple[str, str]:
    """Generate getter/setter C declarations and implementations.

    type_suffix: 'state' or 'params'  (produces <comp>_<type_suffix>_t)
    ptr_name:    'state' or 'params'  (the C variable name)
    """
    full_type = f"{component}_{type_suffix}_t"
    decl_parts = []
    for name, ct, _ in scalar_vars:
        decl_parts.append(
            f"/**\n"
            f" * @brief Get current {name}.\n"
            f" */\n"
            f"{ct} {component}_get_{name}(const {full_type} *{ptr_name});\n"
            f"\n"
            f"/**\n"
            f" * @brief Set {name}.\n"
            f" */\n"
            f"void {component}_set_{name}"
            f"({full_type} *{ptr_name}, {ct} {name});"
        )
    for name, elem_ct, size in array_info:
        decl_parts.append(
            f"/**\n"
            f" * @brief Copy {name} into dest.\n"
            f" */\n"
            f"void {component}_get_{name}"
            f"(const {full_type} *{ptr_name}, {elem_ct} *dest);\n"
            f"\n"
            f"/**\n"
            f" * @brief Return a read-only pointer to {name}.\n"
            f" */\n"
            f"const {elem_ct} *{component}_get_{name}_view"
            f"(const {full_type} *{ptr_name});\n"
            f"\n"
            f"/**\n"
            f" * @brief Set {name} from src.\n"
            f" */\n"
            f"void {component}_set_{name}"
            f"({full_type} *{ptr_name}, const {elem_ct} *src);"
        )
    decls = "\n\n".join(decl_parts)

    impl_parts = []
    for name, ct, _ in scalar_vars:
        impl_parts.append(
            f"{ct}\n"
            f"{component}_get_{name}(const {full_type} *{ptr_name})\n"
            f"{{\n"
            f"    return {ptr_name}->{name};\n"
            f"}}\n"
            f"\n"
            f"void\n"
            f"{component}_set_{name}"
            f"({full_type} *{ptr_name}, {ct} {name})\n"
            f"{{\n"
            f"    {ptr_name}->{name} = {name};\n"
            f"}}"
        )
    for name, elem_ct, size in array_info:
        impl_parts.append(
            f"void\n"
            f"{component}_get_{name}"
            f"(const {full_type} *{ptr_name}, {elem_ct} *dest)\n"
            f"{{\n"
            f"    memcpy(dest, {ptr_name}->{name},"
            f" {size} * sizeof({elem_ct}));\n"
            f"}}\n"
            f"\n"
            f"const {elem_ct} *\n"
            f"{component}_get_{name}_view"
            f"(const {full_type} *{ptr_name})\n"
            f"{{\n"
            f"    return {ptr_name}->{name};\n"
            f"}}\n"
            f"\n"
            f"void\n"
            f"{component}_set_{name}"
            f"({full_type} *{ptr_name}, const {elem_ct} *src)\n"
            f"{{\n"
            f"    memcpy({ptr_name}->{name}, src,"
            f" {size} * sizeof({elem_ct}));\n"
            f"}}"
        )
    impls = "\n\n".join(impl_parts)
    return decls, impls


# ---------------------------------------------------------------------------
# make_state_ctx
# ---------------------------------------------------------------------------


def _unseedable_required(init_params: list) -> list:
    """Names of ``required`` scalar init-params that carry no default (gh-273).

    Such a param has no value jm can put in a generated smoke test or doctest;
    a validating constructor would reject the type's zero. Arrays are excluded
    (they seed as ``np.zeros`` and are always positional). Returns the names in
    declaration order (empty when the constructor is fully seedable)."""
    return [
        p[0]
        for p in init_params
        if len(p) > 8
        and p[8]  # required
        and not (len(p) > 2 and p[2])  # no default
        and not str(p[1]).endswith("[]")  # scalar
    ]


def _ctor_seed_slots(component: str, init_params: list) -> dict:
    """Smoke-test slots that depend on whether the constructor can be seeded.

    A ``required`` scalar init-param with no default (gh-266) has no value jm
    can put in a generated smoke test. For a constructor that *validates* its
    inputs (rejecting the type's zero — sample rate, span, size …), the
    auto-seeded C smoke (``create(0, …)`` → ``CHECK(obj != NULL)``) and the
    pytest construction then fail with the constructor's own error (gh-273). The
    feature is most useful for exactly such params, so jm must not assert a
    construction it cannot validly seed.

    When such a param exists the generated tests *defer* instead of asserting:

    - ``obj_null_check`` — the C smoke treats a NULL return as a skip (prints a
      note and returns 0) rather than ``CHECK(obj != NULL)``; if the constructor
      happens to accept the zero seed the rest of the smoke still runs;
    - ``pytest_class_skip`` / ``pytest_module_skip`` — the pytest case is skipped
      (a ``setUp`` ``skipTest`` for the unittest-style file, a module
      ``pytestmark`` for the pure-pytest file) with a note to pass valid args.

    Without an unseedable param every slot is its historic value, so existing
    output is byte-identical.
    """
    unseed = _unseedable_required(init_params)
    if not unseed:
        return {
            "obj_null_check": (
                "    CHECK(obj != NULL);\n    if (!obj) return 1;"
            ),
            "pytest_class_skip": "",
            "pytest_module_skip": "",
        }
    names = ", ".join(unseed)
    msg = (
        f"required constructor parameter(s) {names} have no default; "
        "seed valid arguments to enable this smoke test"
    )
    return {
        "obj_null_check": (
            "    if (!obj) {\n"
            f"        /* {names}: required with no default — a validating\n"
            f"           {component}_create() may reject the zero-seeded call\n"
            "           above. Pass valid arguments to smoke-test further. */\n"
            f'        printf("test_{component}_core SKIPPED'
            f' ({names} need seeding)\\n");\n'
            "        return 0;\n"
            "    }"
        ),
        "pytest_class_skip": (
            f'    def setUp(self):\n        self.skipTest("{msg}")\n\n'
        ),
        "pytest_module_skip": (
            f'pytestmark = pytest.mark.skip(reason="{msg}")\n'
        ),
    }


def make_state_ctx(
    component: str,
    Component: str,
    state_vars: list[tuple[str, str, str]],
    array_args: list[tuple[str, str]] = (),
    roles: dict[str, str] | None = None,
    no_state: bool = False,
    init_params: list[tuple] = (),
    init_post_parse_impl: str = "",
    opaque_fields: list[tuple[str, str]] = (),
    no_ctor_names: "frozenset[str]" = frozenset(),
) -> dict[str, str]:
    """Return template context keys derived from the state variable list.

    Each entry in state_vars is (name, ctype, default), where default is a
    C literal used for both reset and as the Python __init__ default value.
    Array types like 'float[64]' are always zero-initialised and do not
    appear as constructor parameters.

    array_args is a list of (name, dtype) pairs from --array-arg, e.g.
    [("h", "float32")].  Each becomes a required positional constructor
    argument: const <ctype> *name, size_t name_len.  Array args appear
    before scalar args in both the kwlist and the create() signature.

    roles is a dict mapping state-var name to "state" (default) or
    "config".  Config fields are preserved on reset() — they represent
    construction-time parameters (e.g. filter coefficients, sample rate)
    that should survive a soft-reset of runtime state (e.g. phase
    accumulator, filter history).
    """
    if no_state:
        _ns_reset_fn = f"{Component}Obj_reset"
        base = {
            "ComponentW": f"{Component}Obj",
            "state_struct_fields": "    /* <<IMPLEMENT: add fields >> */",
            "create_params": "void",
            "create_param_docs": (
                " * @param (none)  Caller is responsible for all state management."
            ),
            "getter_setter_decls": "",
            "create_assignments": "    /* <<IMPLEMENT: initialise state >> */",
            "reset_assignments": "    /* <<IMPLEMENT: restore defaults >> */",
            "destroy_impl": "    /* <<IMPLEMENT: free resources >> */\n",
            "getter_setter_impls": "",
            "init_kwlist": "NULL",
            "init_locals": "",
            "init_post_parse": "",
            "init_parse_fmt": "|",
            "init_parse_args": "",
            "init_parse_block": "    (void)args;\n    (void)kwds;\n",
            "create_call_args": "",
            "getter_setter_methods_c": "",
            "getter_setter_pymethoddef": "",
            "init_params_pyi": "",
            "pyi_param_docs": "    (none)",
            "pyi_examples": "",
            "getter_setter_stubs_pyi": "",
            "py_create_args": "",
            "getter_setter_test_py": (
                "        pass  # no auto-state; add assertions for your fields"
            ),
            "reset_test_py": (
                "        pass  # no auto-state; add assertions for your reset"
            ),
            "getter_setter_test_py_pure": (
                "    pass  # no auto-state; add assertions for your fields"
            ),
            "reset_test_py_pure": (
                "    pass  # no auto-state; add assertions for your reset"
            ),
            "c_create_args": "",
            "bench_create_stmt": (
                f"    /* TODO: {component}_state_t *obj = {component}_create(...); */"
            ),
            "bench_destroy_stmt": "",
            "getter_setter_test_c": "",
            "reset_test_c": (f"    /* reset */\n    {component}_reset(obj);"),
            "array_args_parse_block": "",
            "array_args_decref": "",
            "create_line": (f"    self->handle = {component}_create();\n"),
            "method_decls": "",
            "extra_buf_fields": "",
            "extra_buf_free": "",
            "extra_buf_alloc": "",
            "extra_methods_c": "",
            "extra_methods_pymethoddef": "",
            "getset_def": "",
            "tp_getset_decl": "",
            "property_decls": "",
            "property_struct_fields": "",
            "builtin_reset_c": (
                f"static PyObject *\n"
                f"{_ns_reset_fn}({Component}Object *self,"
                f" PyObject *Py_UNUSED(ignored))\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    {component}_reset(self->handle);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            ),
            "builtin_reset_pmd": (
                f'    {{"reset",    (PyCFunction){_ns_reset_fn},'
                f"    METH_NOARGS,\n"
                f'     "Reset state to post-create defaults."}},\n'
            ),
            "builtin_reset_decl": (
                f"/**\n"
                f" * @brief Reset {Component} to its post-create state.\n"
                f" * @param state  Must be non-NULL.\n"
                f" */\n"
                f"void {component}_reset({component}_state_t *state);"
            ),
            "builtin_reset_pyi": (
                "\n"
                "    def reset(self) -> None:\n"
                '        """Reset state to post-create defaults."""\n'
            ),
        }
        if init_params or array_args:
            base.update(
                _build_no_state_init_ctx(
                    component,
                    Component,
                    list(init_params),
                    list(array_args),
                    init_post_parse_impl=init_post_parse_impl,
                )
            )
        if opaque_fields:
            base["state_struct_fields"] = "\n".join(
                f"    {ct} {name};" for name, ct in opaque_fields
            )
        base.update(_ctor_seed_slots(component, list(init_params)))
        return base

    if roles is None:
        roles = {}
    for name, ct, _ in state_vars:
        if not is_valid_type(ct):
            supported = ", ".join(sorted(SUPPORTED_TYPES))
            raise ValueError(
                f"unsupported type '{ct}' for '{name}'. Supported: {supported}"
            )

    scalar_vars = [
        (n, ct, dflt) for n, ct, dflt in state_vars if ct in _CTYPE_META
    ]
    array_info: list[tuple[str, str, int]] = []
    for n, ct, _ in state_vars:
        parsed = parse_array_type(ct)
        if parsed:
            array_info.append((n, parsed[0], parsed[1]))

    # When the user supplies ``init_params``, those drive the constructor
    # signature instead of state-field names — state stays internal and the
    # user manages it via ``create_impl`` / setters (gh-69). Hiding every
    # scalar from the ctor avoids generating ``obj->fd = fd;`` lines that
    # reference parameters that no longer exist.
    if init_params:
        ctor_scalars: list = []
        hidden_scalars = list(scalar_vars)
    else:
        ctor_scalars = [v for v in scalar_vars if v[0] not in no_ctor_names]
        hidden_scalars = [v for v in scalar_vars if v[0] in no_ctor_names]

    # ── CORE_H: state_struct_fields ─────────────────────────────────────

    struct_field_lines = []
    for name, ct, _ in state_vars:
        parsed = parse_array_type(ct)
        if parsed:
            struct_field_lines.append(f"    {parsed[0]} {name}[{parsed[1]}];")
        else:
            struct_field_lines.append(f"    {ct} {name};")
    for name, ct in opaque_fields:
        struct_field_lines.append(f"    {ct} {name};")
    state_struct_fields = "\n".join(struct_field_lines)

    _aa = list(array_args)
    _aa_ctypes = [(_ARRAY_DTYPE[dt][0], _ARRAY_DTYPE[dt][1]) for _, dt in _aa]

    arr_param_parts = [
        f"const {ct} *{name}, size_t {name}_len"
        for (name, _), (ct, __) in zip(_aa, _aa_ctypes)
    ]
    scalar_param_parts = [f"{ct} {name}" for name, ct, _ in ctor_scalars]
    all_param_parts = arr_param_parts + scalar_param_parts
    create_params = ", ".join(all_param_parts) or "void"

    arr_doc_parts = [
        f" * @param {name}  Input {dt} array (length passed as {name}_len)."
        for (name, dt) in _aa
    ]
    scalar_doc_parts = [
        f" * @param {name}  Initial {name} (default: {dflt})."
        for name, _, dflt in ctor_scalars
    ]
    all_docs = arr_doc_parts + scalar_doc_parts
    create_param_docs = (
        "\n".join(all_docs)
        or " * @param (none)  All array fields initialise to zero."
    )

    # ── CORE_H: getter_setter_decls ─────────────────────────────────────

    decl_parts = []
    for name, ct, _ in scalar_vars:
        decl_parts.append(
            f"/**\n"
            f" * @brief Get current {name}.\n"
            f" * @param state  Must be non-NULL.\n"
            f" */\n"
            f"{ct} {component}_get_{name}"
            f"(const {component}_state_t *state);\n"
            f"\n"
            f"/**\n"
            f" * @brief Set {name}.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param val    New value.\n"
            f" */\n"
            f"void {component}_set_{name}"
            f"({component}_state_t *state, {ct} val);"
        )
    for name, elem_ct, size in array_info:
        decl_parts.append(
            f"/**\n"
            f" * @brief Copy {name} into dest.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param dest   Output buffer of length {size}.\n"
            f" */\n"
            f"void {component}_get_{name}"
            f"(const {component}_state_t *state, {elem_ct} *dest);\n"
            f"\n"
            f"/**\n"
            f" * @brief Get a read-only pointer to {name}.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @return Pointer valid until {component}_destroy() is called.\n"
            f" */\n"
            f"const {elem_ct} *{component}_get_{name}_view"
            f"(const {component}_state_t *state);\n"
            f"\n"
            f"/**\n"
            f" * @brief Set {name} from src.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param src    Source buffer of length {size}.\n"
            f" */\n"
            f"void {component}_set_{name}"
            f"({component}_state_t *state, const {elem_ct} *src);"
        )
    getter_setter_decls = "\n\n".join(decl_parts)

    # ── CORE_C: assignments ─────────────────────────────────────────────

    create_assign_lines = [f"    obj->{n} = {n};" for n, _, _ in ctor_scalars]
    create_assign_lines += [
        f"    obj->{n} = {dflt};" for n, _, dflt in hidden_scalars
    ]
    for name, _, size in array_info:
        create_assign_lines.append(
            f"    memset(obj->{name}, 0, sizeof(obj->{name}));"
        )
    create_assignments = "\n".join(create_assign_lines)

    reset_assign_lines = []
    for n, _, dflt in scalar_vars:
        if roles.get(n, "state") == "config":
            reset_assign_lines.append(
                f"    /* {n}: config field — preserved on reset */"
            )
        else:
            reset_assign_lines.append(f"    state->{n} = {dflt};")
    for name, _, size in array_info:
        if roles.get(name, "state") == "config":
            reset_assign_lines.append(
                f"    /* {name}[]: config field — preserved on reset */"
            )
        else:
            reset_assign_lines.append(
                f"    memset(state->{name}, 0, sizeof(state->{name}));"
            )
    reset_assignments = "\n".join(reset_assign_lines)

    # ── CORE_C: getter_setter_impls ─────────────────────────────────────

    impl_parts = []
    for name, ct, _ in scalar_vars:
        impl_parts.append(
            f"{ct}\n"
            f"{component}_get_{name}"
            f"(const {component}_state_t *state)\n"
            f"{{\n"
            f"    return state->{name};\n"
            f"}}\n"
            f"\n"
            f"void\n"
            f"{component}_set_{name}"
            f"({component}_state_t *state, {ct} val)\n"
            f"{{\n"
            f"    state->{name} = val;\n"
            f"}}"
        )
    for name, elem_ct, size in array_info:
        impl_parts.append(
            f"void\n"
            f"{component}_get_{name}"
            f"(const {component}_state_t *state, {elem_ct} *dest)\n"
            f"{{\n"
            f"    memcpy(dest, state->{name},"
            f" {size} * sizeof({elem_ct}));\n"
            f"}}\n"
            f"\n"
            f"const {elem_ct} *\n"
            f"{component}_get_{name}_view"
            f"(const {component}_state_t *state)\n"
            f"{{\n"
            f"    return state->{name};\n"
            f"}}\n"
            f"\n"
            f"void\n"
            f"{component}_set_{name}"
            f"({component}_state_t *state, const {elem_ct} *src)\n"
            f"{{\n"
            f"    memcpy(state->{name}, src,"
            f" {size} * sizeof({elem_ct}));\n"
            f"}}"
        )
    getter_setter_impls = "\n\n".join(impl_parts)

    # ── EXT_C: init parse block ─────────────────────────────────────────

    kwlist_items = (
        [f'"{name}"' for name, _ in _aa]
        + [f'"{name}"' for name, _, __ in ctor_scalars]
        + ["NULL"]
    )
    init_kwlist = ", ".join(kwlist_items)

    local_lines = [f"    PyObject *{name}_obj = NULL;" for name, _ in _aa]
    post_lines = []
    parse_args = [f"&{name}_obj" for name, _ in _aa]
    for name, ct, dflt in ctor_scalars:
        meta = _CTYPE_META[ct]
        if meta.get("parse_type"):
            # gh-377: seed the _raw local from dflt when valid as an
            # initializer for parse_type; struct parse_types (Py_complex,
            # parse_zero starts with "{") cannot accept a C99 expression like
            # "0.0 + 0.0 * I" — fall back to parse_zero for those.
            pz = meta["parse_zero"]
            raw_init = (
                dflt if (dflt and not pz.startswith("{")) else pz
            )
            local_lines.append(
                f"    {meta['parse_type']} {name}_raw = {raw_init};"
            )
            post_lines.append(f"    {ct} {name} = {meta['to_c'](name)};")
            parse_args.append(f"&{name}_raw")
        else:
            local_lines.append(f"    {ct} {name} = {dflt};")
            parse_args.append(f"&{name}")
    init_locals = "\n".join(local_lines)
    init_post_parse = ("\n".join(post_lines) + "\n") if post_lines else ""

    array_fmt = "O" * len(_aa)
    scalar_fmt_str = "".join(
        _CTYPE_META[ct]["fmt"] for _, ct, __ in ctor_scalars
    )
    if ctor_scalars:
        init_parse_fmt = array_fmt + "|" + scalar_fmt_str
    else:
        init_parse_fmt = array_fmt or "|"

    init_parse_args = ", ".join(parse_args)

    arr_call_parts = [
        f"(const {ct} *)PyArray_DATA({name}_arr), {name}_len"
        for (name, _), (ct, __) in zip(_aa, _aa_ctypes)
    ]
    scalar_call_parts = [name for name, _, __ in ctor_scalars]
    create_call_args = ", ".join(arr_call_parts + scalar_call_parts)

    if _aa or ctor_scalars:
        post_str = ("\n".join(post_lines) + "\n") if post_lines else ""
        init_parse_block = (
            f"    static char *kwlist[] = {{{init_kwlist}}};\n"
            f"{init_locals}\n"
            f"\n"
            f"    if (!PyArg_ParseTupleAndKeywords(args, kwds,"
            f' "{init_parse_fmt}", kwlist,\n'
            f"                                     {init_parse_args}))\n"
            f"        return -1;\n"
            f"{post_str}"
        )
    else:
        init_parse_block = "    (void)args;\n    (void)kwds;\n"

    # ── EXT_C: array-arg post-parse ─────────────────────────────────────

    aapb_lines: list[str] = []
    already_allocated: list[str] = []
    for (name, _), (ct, npy_enum) in zip(_aa, _aa_ctypes):
        cleanup = "".join(f" Py_DECREF({n}_arr);" for n in already_allocated)
        aapb_lines.append(
            f"    PyArrayObject *{name}_arr ="
            f" (PyArrayObject *)PyArray_FROM_OTF(\n"
            f"        {name}_obj, {npy_enum}, NPY_ARRAY_C_CONTIGUOUS);\n"
            f"    if (!{name}_arr) {{{cleanup} return -1; }}\n"
            f"    size_t {name}_len = (size_t)PyArray_SIZE({name}_arr);\n"
        )
        already_allocated.append(name)
    array_args_parse_block = "".join(aapb_lines)
    array_args_decref = "".join(
        f"    Py_DECREF({name}_arr);\n" for name, _ in _aa
    )

    c_arr_call_parts = ["NULL, 0" for _ in _aa]
    c_create_args = ", ".join(
        c_arr_call_parts + [dflt for _, _, dflt in ctor_scalars]
    )

    _NP_PY_TYPE: dict[str, str] = {
        "float32": "np.float32",
        "float64": "np.float64",
        "complex64": "np.complex64",
        "complex128": "np.complex128",
        "int8": "np.int8",
        "int16": "np.int16",
        "int32": "np.int32",
        "int64": "np.int64",
        "uint8": "np.uint8",
        "uint16": "np.uint16",
        "uint32": "np.uint32",
        "uint64": "np.uint64",
    }
    py_arr_args = [f"np.zeros(1, dtype={_NP_PY_TYPE[dt]})" for _, dt in _aa]

    # ── EXT_C: getter/setter methods ────────────────────────────────────

    guard = (
        "    if (!self->handle) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
    )
    method_parts = []
    for name, ct, _ in scalar_vars:
        meta = _CTYPE_META[ct]
        to_py = meta["to_py"](f"{component}_get_{name}(self->handle)")
        getter = (
            f"static PyObject *\n"
            f"{Component}_get_{name}(\n"
            f"    {Component}Object *self, PyObject *Py_UNUSED(ignored))\n"
            f"{{\n"
            f"{guard}"
            f"    return {to_py};\n"
            f"}}"
        )
        if meta.get("parse_type"):
            setter = (
                f"static PyObject *\n"
                f"{Component}_set_{name}(\n"
                f"    {Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"{guard}"
                f"    {meta['parse_type']} v_raw = {meta['parse_zero']};\n"
                f'    if (!PyArg_ParseTuple(args, "{meta["fmt"]}", &v_raw))\n'
                f"        return NULL;\n"
                f"    {ct} v = {meta['to_c']('v')};\n"
                f"    {component}_set_{name}(self->handle, v);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
        else:
            setter = (
                f"static PyObject *\n"
                f"{Component}_set_{name}(\n"
                f"    {Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"{guard}"
                f"    {ct} v = {meta['zero']};\n"
                f'    if (!PyArg_ParseTuple(args, "{meta["fmt"]}", &v))\n'
                f"        return NULL;\n"
                f"    {component}_set_{name}(self->handle, v);\n"
                f"    Py_RETURN_NONE;\n"
                f"}}"
            )
        method_parts.append(getter + "\n\n" + setter)

    for name, elem_ct, size in array_info:
        npy_enum = _NP_DTYPE_ENUM[elem_ct]
        ptr_cast = f"({elem_ct} *)"
        const_ptr_cast = f"(const {elem_ct} *)"

        copy_getter = (
            f"static PyObject *\n"
            f"{Component}_get_{name}(\n"
            f"    {Component}Object *self, PyObject *Py_UNUSED(ignored))\n"
            f"{{\n"
            f"{guard}"
            f"    npy_intp dims[] = {{{size}}};\n"
            f"    PyObject *arr = PyArray_SimpleNew(1, dims, {npy_enum});\n"
            f"    if (!arr) return NULL;\n"
            f"    {component}_get_{name}(self->handle,\n"
            f"        {ptr_cast}PyArray_DATA((PyArrayObject *)arr));\n"
            f"    return arr;\n"
            f"}}"
        )
        view_getter = (
            f"static PyObject *\n"
            f"{Component}_get_{name}_view(\n"
            f"    {Component}Object *self, PyObject *Py_UNUSED(ignored))\n"
            f"{{\n"
            f"{guard}"
            f"    npy_intp dims[] = {{{size}}};\n"
            f"    PyObject *arr = PyArray_SimpleNewFromData(\n"
            f"        1, dims, {npy_enum},\n"
            f"        (void *){component}_get_{name}_view(self->handle));\n"
            f"    if (!arr) return NULL;\n"
            f"    PyArray_CLEARFLAGS("
            f"(PyArrayObject *)arr, NPY_ARRAY_WRITEABLE);\n"
            f"    return arr;\n"
            f"}}"
        )
        array_setter = (
            f"static PyObject *\n"
            f"{Component}_set_{name}(\n"
            f"    {Component}Object *self, PyObject *args)\n"
            f"{{\n"
            f"{guard}"
            f"    PyObject *in_obj = NULL;\n"
            f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
            f"        return NULL;\n"
            f"    PyArrayObject *arr = (PyArrayObject *)PyArray_FROM_OTF(\n"
            f"        in_obj, {npy_enum}, NPY_ARRAY_C_CONTIGUOUS);\n"
            f"    if (!arr) return NULL;\n"
            f"    if (PyArray_SIZE(arr) != {size}) {{\n"
            f"        PyErr_Format(PyExc_ValueError,\n"
            f'            "{name} requires exactly {size} elements,'
            f' got %zd",\n'
            f"            (Py_ssize_t)PyArray_SIZE(arr));\n"
            f"        Py_DECREF(arr);\n"
            f"        return NULL;\n"
            f"    }}\n"
            f"    {component}_set_{name}(self->handle,\n"
            f"        {const_ptr_cast}PyArray_DATA(arr));\n"
            f"    Py_DECREF(arr);\n"
            f"    Py_RETURN_NONE;\n"
            f"}}"
        )
        method_parts.append(
            copy_getter + "\n\n" + view_getter + "\n\n" + array_setter
        )

    getter_setter_methods_c = "\n\n".join(method_parts)

    # ── EXT_C: PyMethodDef ───────────────────────────────────────────────

    pmd_lines = []
    for name, _, __ in scalar_vars:
        pmd_lines += [
            f'    {{"get_{name}",',
            f"     (PyCFunction){Component}_get_{name}, METH_NOARGS,",
            f'     "Get {name}."}},',
            f'    {{"set_{name}",',
            f"     (PyCFunction){Component}_set_{name}, METH_VARARGS,",
            f'     "Set {name}."}},',
        ]
    for name, elem_ct, size in array_info:
        py_type = _CTYPE_META[elem_ct]["py_type"]
        pmd_lines += [
            f'    {{"get_{name}",',
            f"     (PyCFunction){Component}_get_{name}, METH_NOARGS,",
            f'     "Return a copy of {name} as {py_type} ndarray (length {size})."}},',
            f'    {{"get_{name}_view",',
            f"     (PyCFunction){Component}_get_{name}_view, METH_NOARGS,",
            f'     "Return read-only view of {name}. Valid until destroy()."}},',
            f'    {{"set_{name}",',
            f"     (PyCFunction){Component}_set_{name}, METH_VARARGS,",
            f'     "Set {name} from {py_type} array of length {size}."}},',
        ]
    getter_setter_pymethoddef = "\n".join(pmd_lines)
    if pmd_lines:
        getter_setter_pymethoddef += "\n"

    # ── PYI ─────────────────────────────────────────────────────────────

    init_params_pyi = ", ".join(
        f"{name}: {_CTYPE_META[ct]['py_type']} = {_py_default(ct, dflt)}"
        for name, ct, dflt in ctor_scalars
    )

    pyi_param_docs = "\n".join(
        f"    {name} : {_CTYPE_META[ct]['py_type']},"
        f" default {_py_default(ct, dflt)}\n"
        f"        {name} state variable."
        for name, ct, dflt in ctor_scalars
    )

    stub_groups: list[str] = []
    for name, ct, _ in scalar_vars:
        py_type = _CTYPE_META[ct]["py_type"]
        stub_groups.append(
            "\n".join(
                [
                    f"    def get_{name}(self) -> {py_type}:",
                    f'        """Return current {name}."""',
                    "",
                    f"    def set_{name}(self, value: {py_type}) -> None:",
                    f'        """Set {name}."""',
                ]
            )
        )
    for name, elem_ct, size in array_info:
        py_type = _CTYPE_META[elem_ct]["py_type"]
        stub_groups.append(
            "\n".join(
                [
                    f"    def get_{name}(self) -> NDArray[{py_type}]:",
                    f'        """Return a copy of {name}'
                    f' (length {size}, dtype {py_type})."""'
                    "",
                    f"    def get_{name}_view(self) -> NDArray[{py_type}]:",
                    f'        """Return a read-only view of {name}.',
                    "",
                    "        Backed by the component's internal state buffer.",
                    "        **Do not use after destroy().**",
                    '        """',
                    "",
                    f"    def set_{name}(self, value: NDArray[{py_type}]) -> None:",
                    f'        """Set {name} from a {py_type}'
                    f' array of length {size}."""',
                ]
            )
        )
    getter_setter_stubs_pyi = (
        "\n" + "\n\n".join(stub_groups) + "\n" if stub_groups else ""
    )

    # ── Shared: create args ──────────────────────────────────────────────

    py_create_args = ", ".join(
        py_arr_args + [_py_default(ct, dflt) for _, ct, dflt in ctor_scalars]
    )

    # ── PYI Examples ────────────────────────────────────────────────────
    # gh-273: a required init-param with no default has no valid construction
    # seed, so suppress the doctest rather than emit one a validating ctor
    # rejects under `pytest --doctest-glob='*.pyi'`.
    pyi_examples = (
        _pyi_examples_block(
            ctor_scalars,
            bool(py_arr_args),
            "from <<package>> import <<Component>>",
            py_create_args,
            Component,
        )
        if ctor_scalars and not _unseedable_required(init_params)
        else ""
    )

    # ── PYTEST: getter_setter_test_py ────────────────────────────────────

    gs_lines = [f"        obj = {Component}({py_create_args})"]
    for name, ct, dflt in scalar_vars:
        meta = _CTYPE_META[ct]
        iv = _py_default(ct, dflt)
        sv = _py_sample_val(meta)
        if meta["kind"] == "float":
            gs_lines += [
                f"        assert obj.get_{name}() == _approx({iv})",
                f"        obj.set_{name}({sv})",
                f"        assert obj.get_{name}() == _approx({sv})",
            ]
        else:
            gs_lines += [
                f"        assert obj.get_{name}() == {iv}",
                f"        obj.set_{name}({sv})",
                f"        assert obj.get_{name}() == {sv}",
            ]
    for name, elem_ct, size in array_info:
        np_dtype = _CTYPE_META[elem_ct]["py_type"].replace("np.", "")
        gs_lines += [
            f"        _arr = np.zeros({size}, dtype=np.{np_dtype})",
            "        _arr[0] = 1",
            f"        obj.set_{name}(_arr)",
            f"        _got = obj.get_{name}()",
            "        assert _got[0] == _approx(1)",
            f"        _view = obj.get_{name}_view()",
            "        assert not _view.flags['WRITEABLE']",
            "        assert _view[0] == _approx(1)",
        ]
    getter_setter_test_py = "\n".join(gs_lines)

    # ── PYTEST: reset_test_py ────────────────────────────────────────────

    rs_lines = [f"        obj = {Component}({py_create_args})"]
    for name, ct, _ in scalar_vars:
        rs_lines.append(
            f"        obj.set_{name}({_py_sample_val(_CTYPE_META[ct])})"
        )
    for name, elem_ct, size in array_info:
        np_dtype = _CTYPE_META[elem_ct]["py_type"].replace("np.", "")
        rs_lines.append(
            f"        obj.set_{name}(np.ones({size}, dtype=np.{np_dtype}))"
        )
    rs_lines.append("        obj.reset()")
    for name, ct, dflt in scalar_vars:
        meta = _CTYPE_META[ct]
        iv = _py_default(ct, dflt)
        if meta["kind"] == "float":
            rs_lines.append(
                f"        assert obj.get_{name}() == _approx({iv})"
            )
        else:
            rs_lines.append(f"        assert obj.get_{name}() == {iv}")
    for name, elem_ct, _ in array_info:
        rs_lines.append(f"        assert obj.get_{name}()[0] == _approx(0)")
    reset_test_py = "\n".join(rs_lines)

    # ── CTEST: getter_setter_test_c ──────────────────────────────────────

    cgs_lines: list[str] = []
    for name, ct, dflt in scalar_vars:
        sv = _c_set_val(ct)
        cgs_lines += [
            f"    /* {name}: getter / setter */",
            f"    CHECK({component}_get_{name}(obj) == {dflt});",
            f"    {component}_set_{name}(obj, {sv});",
            f"    CHECK({component}_get_{name}(obj) == {sv});",
            "",
        ]
    for name, elem_ct, size in array_info:
        sv = _c_set_val(elem_ct)
        cgs_lines += [
            f"    /* {name}: getter / setter */",
            "    {",
            f"        {elem_ct} src[{size}], dst[{size}];",
            f"        src[0] = {sv};",
            f"        {component}_set_{name}(obj, src);",
            f"        {component}_get_{name}(obj, dst);",
            f"        CHECK(dst[0] == {sv});",
            "    }",
            "",
        ]
    getter_setter_test_c = "\n".join(cgs_lines).rstrip()

    # ── CTEST: reset_test_c ──────────────────────────────────────────────

    rst_lines = ["    /* reset restores defaults */"]
    for name, ct, _ in scalar_vars:
        rst_lines.append(f"    {component}_set_{name}(obj, {_c_set_val(ct)});")
    for name, elem_ct, size in array_info:
        sv = _c_set_val(elem_ct)
        rst_lines += [
            "    {",
            f"        {elem_ct} ones[{size}];",
            f"        size_t i_; for (i_ = 0; i_ < {size}; i_++) ones[i_] = {sv};",
            f"        {component}_set_{name}(obj, ones);",
            "    }",
        ]
    rst_lines.append(f"    {component}_reset(obj);")
    for name, _, dflt in scalar_vars:
        rst_lines.append(f"    CHECK({component}_get_{name}(obj) == {dflt});")
    for name, elem_ct, size in array_info:
        zero = _CTYPE_META[elem_ct]["zero"]
        rst_lines += [
            "    {",
            f"        {elem_ct} buf[{size}];",
            f"        {component}_get_{name}(obj, buf);",
            f"        CHECK(buf[0] == {zero});",
            "    }",
        ]
    reset_test_c = "\n".join(rst_lines)

    result: dict[str, str] = {
        "state_struct_fields": state_struct_fields,
        "create_params": create_params,
        "create_param_docs": create_param_docs,
        "getter_setter_decls": getter_setter_decls,
        "create_assignments": create_assignments,
        "reset_assignments": reset_assignments,
        "destroy_impl": "",
        "getter_setter_impls": getter_setter_impls,
        "init_kwlist": init_kwlist,
        "init_locals": init_locals,
        "init_post_parse": init_post_parse,
        "init_parse_fmt": init_parse_fmt,
        "init_parse_args": init_parse_args,
        "init_parse_block": init_parse_block,
        "create_call_args": create_call_args,
        "create_line": (
            f"    self->handle = {component}_create({create_call_args});\n"
        ),
        "getter_setter_methods_c": getter_setter_methods_c,
        "getter_setter_pymethoddef": getter_setter_pymethoddef,
        "init_params_pyi": init_params_pyi,
        "pyi_param_docs": pyi_param_docs,
        "pyi_examples": pyi_examples,
        "getter_setter_stubs_pyi": getter_setter_stubs_pyi,
        "py_create_args": py_create_args,
        "getter_setter_test_py": getter_setter_test_py,
        "reset_test_py": reset_test_py,
        "getter_setter_test_py_pure": (
            getter_setter_test_py.replace("        ", "    ").replace(
                "_approx(", "pytest.approx("
            )
        ),
        "reset_test_py_pure": (
            reset_test_py.replace("        ", "    ").replace(
                "_approx(", "pytest.approx("
            )
        ),
        "c_create_args": c_create_args,
        # gh-181: on this path an empty c_create_args means the object has no
        # init params, i.e. a `create(void)` — so `<comp>_create()` is callable
        # and the bench must declare `obj` (else the unconditional destroy(obj)
        # below references an undeclared variable and the bench fails to build).
        "bench_create_stmt": (
            f"    {component}_state_t *obj = {component}_create({c_create_args});"
        ),
        "bench_destroy_stmt": f"    {component}_destroy(obj);",
        "getter_setter_test_c": getter_setter_test_c,
        "reset_test_c": reset_test_c,
        # ComponentW is the wrapper-function prefix.
        "ComponentW": Component,
        "array_args_parse_block": array_args_parse_block,
        "array_args_decref": array_args_decref,
        "method_decls": "",
        "extra_buf_fields": "",
        "extra_buf_free": "",
        "extra_buf_alloc": "",
        "extra_methods_c": "",
        "extra_methods_pymethoddef": "",
        "getset_def": "",
        "tp_getset_decl": "",
        "property_decls": "",
        "property_struct_fields": "",
        "builtin_reset_c": (
            f"static PyObject *\n"
            f"{Component}_reset({Component}Object *self,"
            f" PyObject *Py_UNUSED(ignored))\n"
            f"{{\n"
            f"    if (!self->handle) {{\n"
            f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
            f"        return NULL;\n"
            f"    }}\n"
            f"    {component}_reset(self->handle);\n"
            f"    Py_RETURN_NONE;\n"
            f"}}"
        ),
        "builtin_reset_pmd": (
            f'    {{"reset",    (PyCFunction){Component}_reset,'
            f"    METH_NOARGS,\n"
            f'     "Reset state to post-create defaults."}},\n'
        ),
        "builtin_reset_decl": (
            f"/**\n"
            f" * @brief Reset {Component} to its post-create state.\n"
            f" * @param state  Must be non-NULL.\n"
            f" */\n"
            f"void {component}_reset({component}_state_t *state);"
        ),
        "builtin_reset_pyi": (
            "\n"
            "    def reset(self) -> None:\n"
            '        """Reset state to post-create defaults."""\n'
        ),
    }
    # gh-69: when init_params are present, they replace state-field-driven
    # ctor signature and Python __init__ parsing. The state struct, getters,
    # setters, and reset tests stay intact — only the user-facing ctor and
    # the bench/create boilerplate are swapped.
    if init_params:
        _init_ctx = _build_no_state_init_ctx(
            component,
            Component,
            list(init_params),
            list(array_args),
            init_post_parse_impl=init_post_parse_impl,
        )
        # gh-122: _build_no_state_init_ctx generates empty create-arg strings
        # when an init_param has no explicit default. Fall back to the matching
        # state-var default (common pattern: init_param and state_var share a
        # name).  Only rebuilds the args when any param is missing a default.
        _sv_dflt = {n: d for n, _, d in state_vars}
        _ip_missing = any(
            not (p[2] if len(p) > 2 else "")
            for p in init_params
            if p[1] in _CTYPE_META
        )
        if _ip_missing:
            _ip_c, _ip_py = [], []
            for p in init_params:
                n, ct = p[0], p[1]
                if ct not in _CTYPE_META:
                    continue
                raw_dflt = p[2] if len(p) > 2 else ""
                dflt = (
                    raw_dflt or _sv_dflt.get(n, "") or _CTYPE_META[ct]["zero"]
                )
                _ip_c.append(dflt)
                _ip_py.append(_py_default(ct, dflt))
            _aa_c = ["NULL, 0" for _ in array_args]
            _aa_py = [
                f"np.zeros(1, dtype={_NP_PY_TYPE.get(_CTYPE_TO_DTYPE.get(dt, 'float32'), 'np.float32')})"
                for _, dt in array_args
            ]
            _c_args = ", ".join(_aa_c + _ip_c)
            _py_args = ", ".join(_aa_py + _ip_py)
            _init_ctx["c_create_args"] = _c_args
            _init_ctx["py_create_args"] = _py_args
            _init_ctx["bench_create_stmt"] = (
                f"    {component}_state_t *obj = {component}_create({_c_args});"
                if _c_args
                else (
                    f"    /* TODO: {component}_state_t *obj ="
                    f" {component}_create(...); */"
                )
            )
        _CTOR_OVERRIDE_KEYS = (
            "create_params",
            "create_param_docs",
            "init_kwlist",
            "init_locals",
            "init_post_parse",
            "init_parse_fmt",
            "init_parse_args",
            "init_parse_block",
            "array_args_parse_block",
            "array_args_decref",
            "create_line",
            "create_call_args",
            "init_params_pyi",
            "pyi_param_docs",
            "py_create_args",
            "c_create_args",
            "bench_create_stmt",
        )
        for _k in _CTOR_OVERRIDE_KEYS:
            if _k in _init_ctx:
                result[_k] = _init_ctx[_k]
    result.update(_ctor_seed_slots(component, list(init_params)))
    return result
