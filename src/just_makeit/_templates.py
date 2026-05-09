"""
_templates.py — file templates for just-makeit init.

Placeholders use <<name>> syntax so C/CMake braces are unambiguous.

Context keys
------------
component   snake_case name            e.g. my_filter
Component   TitleCase Python class     e.g. MyFilter
COMPONENT   UPPER_CASE C macro guard   e.g. MY_FILTER
package     Python package dir         e.g. my_filter
project     distribution name          e.g. my-filter
version     version string             e.g. 0.1.0

State-variable keys (produced by make_state_ctx)
-------------------------------------------------
All <<...>> placeholders in the templates below are filled by either the base
context or make_state_ctx().  No placeholders survive rendering.
"""

import re as _re


# Shared to_py lambdas reused across fixed-width integer groups.
def _TO_PY_LONG(v):
    return f"PyLong_FromLong((long){v})"


def _TO_PY_LLONG(v):
    return f"PyLong_FromLongLong((long long){v})"


def _TO_PY_ULONG(v):
    return f"PyLong_FromUnsignedLong((unsigned long){v})"


def _TO_PY_ULLONG(v):
    return f"PyLong_FromUnsignedLongLong((unsigned long long){v})"


def _fwint(ctype, fmt, parse_type, parse_zero, np_type, to_py, zero="0"):
    """Build a fixed-width integer _CTYPE_META entry."""
    return {
        "kind": "int",
        "fmt": fmt,
        "zero": zero,
        "py_type": np_type,
        "parse_type": parse_type,
        "parse_zero": parse_zero,
        "to_c": lambda n, t=ctype: f"({t}){n}_raw",
        "to_py": to_py,
    }


_CTYPE_META: dict[str, dict] = {
    # ── Floating point ────────────────────────────────────────────────────────
    "double": {
        "kind": "float",
        "fmt": "d",
        "zero": "0.0",
        "py_type": "np.float64",
        "to_py": lambda v: f"PyFloat_FromDouble({v})",
    },
    "float": {
        "kind": "float",
        "fmt": "f",
        "zero": "0.0f",
        "py_type": "np.float32",
        "to_py": lambda v: f"PyFloat_FromDouble((double){v})",
    },
    # ── Integers ──────────────────────────────────────────────────────────────
    "int": {
        "kind": "int",
        "fmt": "i",
        "zero": "0",
        "py_type": "np.int32",
        "to_py": _TO_PY_LONG,
    },
    # Fixed-width signed
    "int8_t": _fwint("int8_t", "i", "int", "0", "np.int8", _TO_PY_LONG),
    "int16_t": _fwint("int16_t", "i", "int", "0", "np.int16", _TO_PY_LONG),
    "int32_t": _fwint("int32_t", "l", "long", "0L", "np.int32", _TO_PY_LONG),
    "int64_t": _fwint("int64_t", "L", "long long", "0LL", "np.int64", _TO_PY_LLONG),
    # Fixed-width unsigned
    "uint8_t": _fwint(
        "uint8_t", "I", "unsigned int", "0U", "np.uint8", _TO_PY_ULONG, "0U"
    ),
    "uint16_t": _fwint(
        "uint16_t", "I", "unsigned int", "0U", "np.uint16", _TO_PY_ULONG, "0U"
    ),
    "uint32_t": _fwint(
        "uint32_t", "k", "unsigned long", "0UL", "np.uint32", _TO_PY_ULONG, "0U"
    ),
    "uint64_t": _fwint(
        "uint64_t", "K", "unsigned long long", "0ULL", "np.uint64", _TO_PY_ULLONG, "0U"
    ),
    "size_t": _fwint(
        "size_t", "K", "unsigned long long", "0ULL", "np.uintp", _TO_PY_ULLONG, "0"
    ),
    "ptrdiff_t": _fwint(
        "ptrdiff_t", "L", "long long", "0LL", "np.intp", _TO_PY_LLONG, "0"
    ),
    # ── C99 complex — parsed via Py_complex (format "D"), then cast. ──────────
    "float _Complex": {
        "kind": "complex",
        "fmt": "D",
        "zero": "0.0f + 0.0f * I",
        "py_type": "np.complex64",
        "parse_type": "Py_complex",
        "parse_zero": "{0.0, 0.0}",
        "to_c": lambda n: f"(float){n}_raw.real + (float){n}_raw.imag * I",
        "to_py": lambda v: f"PyComplex_FromDoubles((double)crealf({v}), (double)cimagf({v}))",
    },
    "double _Complex": {
        "kind": "complex",
        "fmt": "D",
        "zero": "0.0 + 0.0 * I",
        "py_type": "np.complex128",
        "parse_type": "Py_complex",
        "parse_zero": "{0.0, 0.0}",
        "to_c": lambda n: f"{n}_raw.real + {n}_raw.imag * I",
        "to_py": lambda v: f"PyComplex_FromDoubles(creal({v}), cimag({v}))",
    },
    "long double _Complex": {
        "kind": "complex",
        "fmt": "D",
        "zero": "0.0L + 0.0L * I",
        "py_type": "np.clongdouble",
        "parse_type": "Py_complex",
        "parse_zero": "{0.0, 0.0}",
        "to_c": lambda n: f"(long double){n}_raw.real + (long double){n}_raw.imag * I",
        "to_py": lambda v: f"PyComplex_FromDoubles((double)creall({v}), (double)cimagl({v}))",
    },
}

SUPPORTED_TYPES: frozenset[str] = frozenset(_CTYPE_META)

# Maps py_type → NumPy C-API enum constant (for ext.c array ops).
_NP_ENUM: dict[str, str] = {
    "np.float32": "NPY_FLOAT",
    "np.float64": "NPY_DOUBLE",
    "np.complex64": "NPY_COMPLEX64",
    "np.complex128": "NPY_COMPLEX128",
    "np.clongdouble": "NPY_CLONGDOUBLE",
    "np.int8": "NPY_INT8",
    "np.int16": "NPY_INT16",
    "np.int32": "NPY_INT",
    "np.int64": "NPY_INT64",
    "np.uint8": "NPY_UINT8",
    "np.uint16": "NPY_UINT16",
    "np.uint32": "NPY_UINT32",
    "np.uint64": "NPY_UINT64",
    "np.uintp": "NPY_UINTP",
    "np.intp": "NPY_INTP",
}

# Maps kind → Python isinstance target.
_KIND_PY_ISINSTANCE: dict[str, str] = {
    "float": "float",
    "int": "int",
    "complex": "complex",
}

# Maps kind → Python test input literal.
_KIND_PY_TEST_VAL: dict[str, str] = {
    "float": "1.0",
    "int": "1",
    "complex": "1.0 + 0.0j",
}


def _ctype_display(ct: str) -> str:
    """Internal key → C display form: 'float _Complex' → 'float complex'."""
    return ct.replace("_Complex", "complex")


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
        to_c_expr = samp["to_c"]("x")  # to_c("x") → "(type)x_raw..." using x_raw var
        return (
            f'    {parse_type} x_raw = {parse_zero};\n'
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


def _bench_in_init(sample_type: str, samp: dict) -> str:
    if samp["kind"] == "complex":
        base = sample_type.replace(" _Complex", "")
        suffix = samp["zero"][samp["zero"].index("+"):]
        return f"({base})(i){suffix}"
    return f"({_ctype_display(sample_type)})(i)"


def _bench_warmup(samp: dict) -> str:
    z = samp["zero"]
    if samp["kind"] == "complex":
        return z.replace("0.0f +", "1.0f +").replace("0.0 +", "1.0 +").replace("0.0L +", "1.0L +")
    if samp["kind"] == "float":
        return z.replace("0.0f", "1.0f").replace("0.0", "1.0")
    return "1"


def _test_arr_4_init(sample_type: str, samp: dict) -> str:
    if samp["kind"] == "complex":
        base = sample_type.replace(" _Complex", "")
        if "long double" in base:
            return "{1.0L, 2.0L, 3.0L, 4.0L}"
        elif base == "double":
            return "{1.0, 2.0, 3.0, 4.0}"
        return "{1.0f, 2.0f, 3.0f, 4.0f}"
    if samp["kind"] == "float":
        return "{1.0, 2.0, 3.0, 4.0}" if sample_type == "double" else "{1.0f, 2.0f, 3.0f, 4.0f}"
    return "{1, 2, 3, 4}"


def make_sample_ctx(
    arg_type: str = "float _Complex",
    return_type: str | None = None,
) -> dict[str, str]:
    """Return template context keys derived from step() arg/return types.

    arg_type    — C type for the step() input parameter x (default: float _Complex)
    return_type — C type for the step() return value (default: same as arg_type)
    """
    if return_type is None:
        return_type = arg_type

    for t, label in [(arg_type, "--arg-type"), (return_type, "--return-type")]:
        if t not in _CTYPE_META:
            supported = ", ".join(sorted(_CTYPE_META))
            raise ValueError(
                f"unsupported {label} value '{t}'. Supported scalar types: {supported}"
            )

    samp = _CTYPE_META[arg_type]
    ret = _CTYPE_META[return_type]

    in_np_dtype = samp["py_type"]
    out_np_dtype = ret["py_type"]

    # pure_x_* keys: used inside pure-scalar fn() to parse the x argument.
    # Use x_raw as intermediate so to_c("x") works (lambdas append _raw).
    samp_disp = _ctype_display(arg_type)
    if "parse_type" in samp:
        pure_x_local = f"    {samp['parse_type']} x_raw = {samp['parse_zero']};"
        pure_x_parse_arg = "&x_raw"
        pure_x_to_c = f"    {samp_disp} x = {samp['to_c']('x')};\n"
    else:
        pure_x_local = f"    {samp_disp} x;"
        pure_x_parse_arg = "&x"
        pure_x_to_c = ""

    return {
        "arg_ctype":        _ctype_display(arg_type),
        "return_ctype":     _ctype_display(return_type),
        "arg_zero":         samp["zero"],
        "in_np_dtype":      in_np_dtype,
        "out_np_dtype":     out_np_dtype,
        "in_np_enum":       _NP_ENUM[in_np_dtype],
        "out_np_enum":      _NP_ENUM[out_np_dtype],
        "in_py_hint":       _KIND_PY_ISINSTANCE[samp["kind"]],
        "out_py_hint":      _KIND_PY_ISINSTANCE[ret["kind"]],
        "out_py_isinstance":_KIND_PY_ISINSTANCE[ret["kind"]],
        "in_py_test_val":   _KIND_PY_TEST_VAL[samp["kind"]],
        "step_parse_block": _step_parse_block(arg_type, samp),
        "step_return_expr": ret["to_py"]("y"),
        "bench_in_init":    _bench_in_init(arg_type, samp),
        "bench_warmup":     _bench_warmup(samp),
        "test_arr_4_init":  _test_arr_4_init(arg_type, samp),
        "pure_x_local":     pure_x_local,
        "pure_x_fmt_char":  samp["fmt"],
        "pure_x_parse_arg": pure_x_parse_arg,
        "pure_x_to_c":      pure_x_to_c,
    }


_C_SET_VAL: dict[str, str] = {
    "float": "2.0f",
    "double": "2.0",
    "float _Complex": "2.0f + 0.0f * I",
    "double _Complex": "2.0 + 0.0 * I",
    "long double _Complex": "2.0L + 0.0L * I",
}


def _c_set_val(ctype: str) -> str:
    return _C_SET_VAL.get(ctype, "2")  # all integer types


def _py_default(ctype: str, default: str) -> str:
    """Convert a C default literal to a valid Python literal."""
    kind = _CTYPE_META[ctype]["kind"]
    if kind == "float":
        s = default.rstrip("fF")
        if "." not in s and "e" not in s.lower():
            s += ".0"
        return s
    if kind == "complex":
        return "0j"
    return default


def _py_sample_val(meta: dict) -> str:
    """Return a Python test set-value for the given type metadata."""
    if meta["kind"] == "complex":
        return "1.0+0.0j"
    if meta["kind"] == "float":
        return "2.0"
    return "2"


# Maps scalar element type → NumPy C-API enum constant (for array state).
_NP_DTYPE_ENUM: dict[str, str] = {
    "float": "NPY_FLOAT",
    "double": "NPY_DOUBLE",
    "int": "NPY_INT",
    "int8_t": "NPY_INT8",
    "int16_t": "NPY_INT16",
    "int32_t": "NPY_INT32",
    "int64_t": "NPY_INT64",
    "uint8_t": "NPY_UINT8",
    "uint16_t": "NPY_UINT16",
    "uint32_t": "NPY_UINT32",
    "uint64_t": "NPY_UINT64",
    "float _Complex": "NPY_COMPLEX64",
    "double _Complex": "NPY_COMPLEX128",
    "long double _Complex": "NPY_CLONGDOUBLE",
}

_ARRAY_RE = _re.compile(r"^(.+)\[(\d+)\]$")


def parse_array_type(ctype: str) -> tuple[str, int] | None:
    """Return (elem_ctype, size) if ctype is a valid array type, else None.

    Valid: 'float[64]', 'double _Complex[32]', 'int32_t[128]'
    The element type must be a key in _CTYPE_META.
    """
    m = _ARRAY_RE.match(ctype.strip())
    if not m:
        return None
    elem = m.group(1).rstrip()
    if elem not in _CTYPE_META:
        return None
    return (elem, int(m.group(2)))


def is_valid_type(ctype: str) -> bool:
    """Return True for scalar types in _CTYPE_META or array types like float[64]."""
    return ctype in _CTYPE_META or parse_array_type(ctype) is not None


def make_state_ctx(
    component: str,
    Component: str,
    state_vars: list[tuple[str, str, str]],
) -> dict[str, str]:
    """Return template context keys derived from the state variable list.

    Each entry in state_vars is (name, ctype, default), where default is a
    C literal used for both reset and as the Python __init__ default value.
    Array types like 'float[64]' are always zero-initialised and do not appear
    as constructor parameters.
    """
    for name, ct, _ in state_vars:
        if not is_valid_type(ct):
            supported = ", ".join(sorted(SUPPORTED_TYPES))
            raise ValueError(
                f"unsupported type '{ct}' for '{name}'. Supported: {supported}"
            )

    # Split into scalar vars (in _CTYPE_META) and array vars.
    scalar_vars = [(n, ct, dflt) for n, ct, dflt in state_vars if ct in _CTYPE_META]
    array_info: list[tuple[str, str, int]] = []  # (name, elem_ctype, size)
    for n, ct, _ in state_vars:
        parsed = parse_array_type(ct)
        if parsed:
            array_info.append((n, parsed[0], parsed[1]))

    # ── CORE_H: state_struct_fields ──────────────────────────────────────────

    struct_field_lines = []
    for name, ct, _ in state_vars:
        parsed = parse_array_type(ct)
        if parsed:
            struct_field_lines.append(f"    {parsed[0]} {name}[{parsed[1]}];")
        else:
            struct_field_lines.append(f"    {ct} {name};")
    state_struct_fields = "\n".join(struct_field_lines)

    create_params = ", ".join(f"{ct} {name}" for name, ct, _ in scalar_vars) or "void"

    create_param_docs = (
        "\n".join(
            f" * @param {name}  Initial {name} (default: {dflt})."
            for name, _, dflt in scalar_vars
        )
        or " * @param (none)  All array fields initialise to zero."
    )

    # ── CORE_H: getter_setter_decls ──────────────────────────────────────────

    decl_parts = []
    for name, ct, _ in scalar_vars:
        decl_parts.append(
            f"/**\n"
            f" * @brief Get current {name}.\n"
            f" * @param state  Must be non-NULL.\n"
            f" */\n"
            f"{ct} {component}_get_{name}(const {component}_state_t *state);\n"
            f"\n"
            f"/**\n"
            f" * @brief Set {name}.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param {name}  New value.\n"
            f" */\n"
            f"void {component}_set_{name}({component}_state_t *state, {ct} {name});"
        )
    for name, elem_ct, size in array_info:
        decl_parts.append(
            f"/**\n"
            f" * @brief Copy {name} into dest.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param dest   Output buffer of length {size}.\n"
            f" */\n"
            f"void {component}_get_{name}(const {component}_state_t *state, {elem_ct} *dest);\n"
            f"\n"
            f"/**\n"
            f" * @brief Get a read-only pointer to {name}.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @return Pointer valid until {component}_destroy() is called.\n"
            f" */\n"
            f"const {elem_ct} *{component}_get_{name}_view(const {component}_state_t *state);\n"
            f"\n"
            f"/**\n"
            f" * @brief Set {name} from src.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param src    Source buffer of length {size}.\n"
            f" */\n"
            f"void {component}_set_{name}({component}_state_t *state, const {elem_ct} *src);"
        )
    getter_setter_decls = "\n\n".join(decl_parts)

    # ── CORE_C: assignments ──────────────────────────────────────────────────

    create_assign_lines = [f"    state->{n} = {n};" for n, _, _ in scalar_vars]
    for name, _, size in array_info:
        create_assign_lines.append(
            f"    memset(state->{name}, 0, sizeof(state->{name}));"
        )
    create_assignments = "\n".join(create_assign_lines)

    reset_assign_lines = [f"    state->{n} = {dflt};" for n, _, dflt in scalar_vars]
    for name, _, size in array_info:
        reset_assign_lines.append(
            f"    memset(state->{name}, 0, sizeof(state->{name}));"
        )
    reset_assignments = "\n".join(reset_assign_lines)

    # ── CORE_C: getter_setter_impls ──────────────────────────────────────────

    impl_parts = []
    for name, ct, _ in scalar_vars:
        impl_parts.append(
            f"{ct}\n"
            f"{component}_get_{name}(const {component}_state_t *state)\n"
            f"{{\n"
            f"    return state->{name};\n"
            f"}}\n"
            f"\n"
            f"void\n"
            f"{component}_set_{name}({component}_state_t *state, {ct} {name})\n"
            f"{{\n"
            f"    state->{name} = {name};\n"
            f"}}"
        )
    for name, elem_ct, size in array_info:
        impl_parts.append(
            f"void\n"
            f"{component}_get_{name}(const {component}_state_t *state, {elem_ct} *dest)\n"
            f"{{\n"
            f"    memcpy(dest, state->{name}, {size} * sizeof({elem_ct}));\n"
            f"}}\n"
            f"\n"
            f"const {elem_ct} *\n"
            f"{component}_get_{name}_view(const {component}_state_t *state)\n"
            f"{{\n"
            f"    return state->{name};\n"
            f"}}\n"
            f"\n"
            f"void\n"
            f"{component}_set_{name}({component}_state_t *state, const {elem_ct} *src)\n"
            f"{{\n"
            f"    memcpy(state->{name}, src, {size} * sizeof({elem_ct}));\n"
            f"}}"
        )
    getter_setter_impls = "\n\n".join(impl_parts)

    # ── EXT_C: init parse block (scalars only) ───────────────────────────────

    # Individual keys kept for backward-compat with tests; init_parse_block is
    # what the template actually uses.
    kwlist_items = [f'"{name}"' for name, _, __ in scalar_vars] + ["NULL"]
    init_kwlist = ", ".join(kwlist_items)

    local_lines = []
    post_lines = []
    parse_args = []
    for name, ct, dflt in scalar_vars:
        meta = _CTYPE_META[ct]
        if meta.get("parse_type"):
            local_lines.append(
                f"    {meta['parse_type']} {name}_raw = {meta['parse_zero']};"
            )
            post_lines.append(f"    {ct} {name} = {meta['to_c'](name)};")
            parse_args.append(f"&{name}_raw")
        else:
            local_lines.append(f"    {ct} {name} = {dflt};")
            parse_args.append(f"&{name}")
    init_locals = "\n".join(local_lines)
    init_post_parse = ("\n".join(post_lines) + "\n") if post_lines else ""
    init_parse_fmt = "|" + "".join(_CTYPE_META[ct]["fmt"] for _, ct, __ in scalar_vars)
    init_parse_args = ", ".join(parse_args)
    create_call_args = ", ".join(name for name, _, __ in scalar_vars)

    if scalar_vars:
        post_str = ("\n".join(post_lines) + "\n") if post_lines else ""
        init_parse_block = (
            f"    static char *kwlist[] = {{{init_kwlist}}};\n"
            f"{init_locals}\n"
            f"\n"
            f'    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{init_parse_fmt}", kwlist,\n'
            f"                                     {init_parse_args}))\n"
            f"        return -1;\n"
            f"{post_str}"
        )
    else:
        init_parse_block = "    (void)args;\n    (void)kwds;\n"

    # ── EXT_C: getter/setter methods (scalars + arrays) ──────────────────────

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
            f"    PyArray_CLEARFLAGS((PyArrayObject *)arr, NPY_ARRAY_WRITEABLE);\n"
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
            f'            "{name} requires exactly {size} elements, got %zd",\n'
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
        method_parts.append(copy_getter + "\n\n" + view_getter + "\n\n" + array_setter)

    getter_setter_methods_c = "\n\n".join(method_parts)

    # ── EXT_C: PyMethodDef ───────────────────────────────────────────────────

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

    # ── PYI ──────────────────────────────────────────────────────────────────

    init_params_pyi = ", ".join(
        f"{name}: {_CTYPE_META[ct]['py_type']} = {_py_default(ct, dflt)}"
        for name, ct, dflt in scalar_vars
    )

    pyi_param_docs = "\n".join(
        f"    {name} : {_CTYPE_META[ct]['py_type']}, default {_py_default(ct, dflt)}\n"
        f"        {name} state variable."
        for name, ct, dflt in scalar_vars
    )

    stub_lines: list[str] = []
    for name, ct, _ in scalar_vars:
        py_type = _CTYPE_META[ct]["py_type"]
        stub_lines += [
            f"    def get_{name}(self) -> {py_type}:",
            f'        """Return current {name}."""',
            f"    def set_{name}(self, value: {py_type}) -> None:",
            f'        """Set {name}."""',
        ]
    for name, elem_ct, size in array_info:
        py_type = _CTYPE_META[elem_ct]["py_type"]
        stub_lines += [
            f"    def get_{name}(self) -> NDArray[{py_type}]:",
            f'        """Return a copy of {name} (length {size}, dtype {py_type})."""',
            f"    def get_{name}_view(self) -> NDArray[{py_type}]:",
            f'        """Return a read-only view of {name}.',
            "",
            "        Backed by the component's internal state buffer.",
            "        **Do not use after destroy().**",
            '        """',
            f"    def set_{name}(self, value: NDArray[{py_type}]) -> None:",
            f'        """Set {name} from a {py_type} array of length {size}."""',
        ]
    getter_setter_stubs_pyi = "\n".join(stub_lines)

    # ── Shared: create args ───────────────────────────────────────────────────

    py_create_args = ", ".join(_py_default(ct, dflt) for _, ct, dflt in scalar_vars)
    c_create_args = ", ".join(dflt for _, _, dflt in scalar_vars)

    # ── PYTEST: getter_setter_test_py ─────────────────────────────────────────

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

    # ── PYTEST: reset_test_py ─────────────────────────────────────────────────

    rs_lines = [f"        obj = {Component}({py_create_args})"]
    for name, ct, _ in scalar_vars:
        rs_lines.append(f"        obj.set_{name}({_py_sample_val(_CTYPE_META[ct])})")
    for name, elem_ct, size in array_info:
        np_dtype = _CTYPE_META[elem_ct]["py_type"].replace("np.", "")
        rs_lines.append(f"        obj.set_{name}(np.ones({size}, dtype=np.{np_dtype}))")
    rs_lines.append("        obj.reset()")
    for name, ct, dflt in scalar_vars:
        meta = _CTYPE_META[ct]
        iv = _py_default(ct, dflt)
        if meta["kind"] == "float":
            rs_lines.append(f"        assert obj.get_{name}() == _approx({iv})")
        else:
            rs_lines.append(f"        assert obj.get_{name}() == {iv}")
    for name, elem_ct, _ in array_info:
        rs_lines.append(f"        assert obj.get_{name}()[0] == _approx(0)")
    reset_test_py = "\n".join(rs_lines)

    # ── CTEST: getter_setter_test_c ───────────────────────────────────────────

    cgs_lines: list[str] = []
    for name, ct, dflt in scalar_vars:
        sv = _c_set_val(ct)
        cgs_lines += [
            f"    /* {name}: getter / setter */",
            f"    assert({component}_get_{name}(obj) == {dflt});",
            f"    {component}_set_{name}(obj, {sv});",
            f"    assert({component}_get_{name}(obj) == {sv});",
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
            f"        assert(dst[0] == {sv});",
            "    }",
            "",
        ]
    getter_setter_test_c = "\n".join(cgs_lines).rstrip()

    # ── CTEST: reset_test_c ───────────────────────────────────────────────────

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
        rst_lines.append(f"    assert({component}_get_{name}(obj) == {dflt});")
    for name, elem_ct, size in array_info:
        zero = _CTYPE_META[elem_ct]["zero"]
        rst_lines += [
            "    {",
            f"        {elem_ct} buf[{size}];",
            f"        {component}_get_{name}(obj, buf);",
            f"        assert(buf[0] == {zero});",
            "    }",
        ]
    reset_test_c = "\n".join(rst_lines)

    return {
        "state_struct_fields": state_struct_fields,
        "create_params": create_params,
        "create_param_docs": create_param_docs,
        "getter_setter_decls": getter_setter_decls,
        "create_assignments": create_assignments,
        "reset_assignments": reset_assignments,
        "getter_setter_impls": getter_setter_impls,
        "init_kwlist": init_kwlist,
        "init_locals": init_locals,
        "init_post_parse": init_post_parse,
        "init_parse_fmt": init_parse_fmt,
        "init_parse_args": init_parse_args,
        "init_parse_block": init_parse_block,
        "create_call_args": create_call_args,
        "getter_setter_methods_c": getter_setter_methods_c,
        "getter_setter_pymethoddef": getter_setter_pymethoddef,
        "init_params_pyi": init_params_pyi,
        "pyi_param_docs": pyi_param_docs,
        "getter_setter_stubs_pyi": getter_setter_stubs_pyi,
        "py_create_args": py_create_args,
        "getter_setter_test_py": getter_setter_test_py,
        "reset_test_py": reset_test_py,
        "c_create_args": c_create_args,
        "getter_setter_test_c": getter_setter_test_c,
        "reset_test_c": reset_test_c,
    }


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
            f"void {component}_set_{name}({full_type} *{ptr_name}, {ct} {name});"
        )
    for name, elem_ct, size in array_info:
        decl_parts.append(
            f"/**\n"
            f" * @brief Copy {name} into dest.\n"
            f" */\n"
            f"void {component}_get_{name}(const {full_type} *{ptr_name}, {elem_ct} *dest);\n"
            f"\n"
            f"/**\n"
            f" * @brief Return a read-only pointer to {name}.\n"
            f" */\n"
            f"const {elem_ct} *{component}_get_{name}_view(const {full_type} *{ptr_name});\n"
            f"\n"
            f"/**\n"
            f" * @brief Set {name} from src.\n"
            f" */\n"
            f"void {component}_set_{name}({full_type} *{ptr_name}, const {elem_ct} *src);"
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
            f"{component}_set_{name}({full_type} *{ptr_name}, {ct} {name})\n"
            f"{{\n"
            f"    {ptr_name}->{name} = {name};\n"
            f"}}"
        )
    for name, elem_ct, size in array_info:
        impl_parts.append(
            f"void\n"
            f"{component}_get_{name}(const {full_type} *{ptr_name}, {elem_ct} *dest)\n"
            f"{{\n"
            f"    memcpy(dest, {ptr_name}->{name}, {size} * sizeof({elem_ct}));\n"
            f"}}\n"
            f"\n"
            f"const {elem_ct} *\n"
            f"{component}_get_{name}_view(const {full_type} *{ptr_name})\n"
            f"{{\n"
            f"    return {ptr_name}->{name};\n"
            f"}}\n"
            f"\n"
            f"void\n"
            f"{component}_set_{name}({full_type} *{ptr_name}, const {elem_ct} *src)\n"
            f"{{\n"
            f"    memcpy({ptr_name}->{name}, src, {size} * sizeof({elem_ct}));\n"
            f"}}"
        )
    impls = "\n\n".join(impl_parts)
    return decls, impls


def make_pure_ctx(
    component: str,
    Component: str,
    state_vars: list[tuple[str, str, str]],
    arg_type: str = "float _Complex",
) -> dict[str, str]:
    """Return context keys for a pure (stateless / caller-managed) component.

    Auto-selects style:
      'scalar' — scalar state only → params passed per call, no struct
      'struct' — any array state   → caller-managed params_t struct
    """
    for name, ct, _ in state_vars:
        if not is_valid_type(ct):
            supported = ", ".join(sorted(SUPPORTED_TYPES))
            raise ValueError(
                f"unsupported type '{ct}' for '{name}'. Supported: {supported}"
            )

    scalar_vars = [(n, ct, d) for n, ct, d in state_vars if ct in _CTYPE_META]
    array_info: list[tuple[str, str, int]] = []
    for n, ct, _ in state_vars:
        parsed = parse_array_type(ct)
        if parsed:
            array_info.append((n, parsed[0], parsed[1]))

    if not array_info:
        return _make_scalar_pure_ctx(component, Component, scalar_vars, arg_type)
    return _make_struct_pure_ctx(component, Component, scalar_vars, array_info)


def _make_scalar_pure_ctx(
    component: str,
    Component: str,
    scalar_vars: list[tuple[str, str, str]],
    arg_type: str = "float _Complex",
) -> dict[str, str]:
    """Pure scalar style: params passed directly per call, no struct."""
    # C function signature suffix: , double scale, int n
    c_fn_params = "".join(f", {ct} {name}" for name, ct, _ in scalar_vars)
    c_fn_call_args = "".join(f", {name}" for name, _, _ in scalar_vars)
    c_fn_call_defaults = "".join(f", {dflt}" for _, _, dflt in scalar_vars)
    c_fn_param_docs = "\n".join(
        f" * @param {name}  Parameter (default: {dflt})."
        for name, _, dflt in scalar_vars
    )

    # C kwlist portion (just param names, no first positional arg)
    py_kwlist = "".join(f'"{name}", ' for name, _, _ in scalar_vars)

    # Locals, parse format, args
    local_lines: list[str] = []
    post_lines: list[str] = []
    param_parse_args: list[str] = []
    param_fmt = ""
    for name, ct, dflt in scalar_vars:
        meta = _CTYPE_META[ct]
        if meta.get("parse_type"):
            local_lines.append(f"    {meta['parse_type']} {name}_raw = {meta['parse_zero']};")
            post_lines.append(f"    {ct} {name} = {meta['to_c'](name)};")
            param_parse_args.append(f"&{name}_raw")
        else:
            local_lines.append(f"    {ct} {name} = {dflt};")
            param_parse_args.append(f"&{name}")
        param_fmt += meta["fmt"]

    x_fmt = _CTYPE_META[arg_type]["fmt"]
    pure_locals = "\n".join(local_lines)
    pure_fn_fmt = x_fmt + (f"|{param_fmt}" if scalar_vars else "")
    pure_steps_fmt = "O" + (f"|{param_fmt}" if scalar_vars else "")
    pure_params_args = (
        (", " + ", ".join(param_parse_args)) if param_parse_args else ""
    )
    pure_post_parse = ("\n".join(post_lines) + "\n") if post_lines else ""

    # Python type params and kwargs for .pyi and tests
    if scalar_vars:
        py_fn_type_params = ", " + ", ".join(
            f"{name}: {_CTYPE_META[ct]['py_type']} = {_py_default(ct, dflt)}"
            for name, ct, dflt in scalar_vars
        )
        py_fn_kwargs = ", " + ", ".join(
            f"{name}={_py_default(ct, dflt)}" for name, ct, dflt in scalar_vars
        )
    else:
        py_fn_type_params = ""
        py_fn_kwargs = ""

    c_create_args = ", ".join(dflt for _, _, dflt in scalar_vars)
    py_create_args = ", ".join(_py_default(ct, dflt) for _, ct, dflt in scalar_vars)

    # pure_x_* keys for the x argument in py_<<component>>() (fn, not steps).
    samp_meta = _CTYPE_META[arg_type]
    samp_disp = _ctype_display(arg_type)
    if "parse_type" in samp_meta:
        pure_x_local = f"    {samp_meta['parse_type']} x_raw = {samp_meta['parse_zero']};"
        pure_x_parse_arg = "&x_raw"
        pure_x_to_c = f"    {samp_disp} x = {samp_meta['to_c']('x')};\n"
    else:
        pure_x_local = f"    {samp_disp} x;"
        pure_x_parse_arg = "&x"
        pure_x_to_c = ""

    return {
        "pure_style": "scalar",
        "c_fn_params": c_fn_params,
        "c_fn_call_args": c_fn_call_args,
        "c_fn_call_defaults": c_fn_call_defaults,
        "c_fn_param_docs": c_fn_param_docs,
        "py_kwlist": py_kwlist,
        "pure_locals": pure_locals,
        "pure_fn_fmt": pure_fn_fmt,
        "pure_steps_fmt": pure_steps_fmt,
        "pure_params_args": pure_params_args,
        "pure_post_parse": pure_post_parse,
        "py_fn_type_params": py_fn_type_params,
        "py_fn_kwargs": py_fn_kwargs,
        "c_create_args": c_create_args,
        "py_create_args": py_create_args,
        "pure_x_local": pure_x_local,
        "pure_x_parse_arg": pure_x_parse_arg,
        "pure_x_to_c": pure_x_to_c,
    }


def _make_struct_pure_ctx(
    component: str,
    Component: str,
    scalar_vars: list[tuple[str, str, str]],
    array_info: list[tuple[str, str, int]],
) -> dict[str, str]:
    """Pure struct style: caller-managed params_t; aligned alloc helper provided."""
    # Reuse state context for Python-side code (getter/setter methods, init parsing,
    # type stubs).  The Python ext code calls get_X(self->handle) which works for
    # both state_t* and params_t* — the method bodies are identical.
    all_vars = scalar_vars + [(n, f"{et}[{sz}]", "") for n, et, sz in array_info]
    state_ctx = make_state_ctx(component, Component, all_vars)

    # C core — params_t specific declarations and implementations
    params_gs_decls, params_gs_impls = _make_gs_decls_impls(
        component, scalar_vars, array_info, "params", "params"
    )

    # params_t struct fields (same layout as state_t)
    struct_field_lines = []
    for name, ct, _ in scalar_vars:
        struct_field_lines.append(f"    {ct} {name};")
    for name, elem_ct, size in array_info:
        struct_field_lines.append(f"    {elem_ct} {name}[{size}];")
    params_struct_fields = "\n".join(struct_field_lines)

    # create: set scalar defaults, zero arrays
    create_lines = [f"    p->{n} = {dflt};" for n, _, dflt in scalar_vars]
    for name, _, size in array_info:
        create_lines.append(f"    memset(p->{name}, 0, sizeof(p->{name}));")
    params_create_assigns = "\n".join(create_lines)
    params_reset_assigns = params_create_assigns  # same

    # After params_create() in __init__, set scalar fields from parsed kwargs
    override_lines = [f"    self->handle->{n} = {n};" for n, _, _ in scalar_vars]
    params_init_overrides = ("\n".join(override_lines) + "\n") if override_lines else ""

    c_create_args = state_ctx["c_create_args"]
    py_create_args = state_ctx["py_create_args"]

    return {
        "pure_style": "struct",
        # Python-side: reused from make_state_ctx
        "getter_setter_methods_c": state_ctx["getter_setter_methods_c"],
        "getter_setter_pymethoddef": state_ctx["getter_setter_pymethoddef"],
        "init_parse_block": state_ctx["init_parse_block"],
        "init_params_pyi": state_ctx["init_params_pyi"],
        "getter_setter_stubs_pyi": state_ctx["getter_setter_stubs_pyi"],
        "py_create_args": py_create_args,
        "c_create_args": c_create_args,
        # C core: params_t specific
        "params_struct_fields": params_struct_fields,
        "params_create_assigns": params_create_assigns,
        "params_reset_assigns": params_reset_assigns,
        "params_getter_setter_decls": params_gs_decls,
        "params_getter_setter_impls": params_gs_impls,
        "params_init_overrides": params_init_overrides,
    }


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


def render(template: str, ctx: dict[str, str]) -> str:
    result = template
    for k, v in ctx.items():
        result = result.replace(f"<<{k}>>", v)
    return result


# ── C headers ────────────────────────────────────────────────────────────────

CLIB_COMMON_H = """\
/**
 * clib_common.h — common C99 types for <<package>>.
 */
#ifndef CLIB_COMMON_H
#define CLIB_COMMON_H

#include <complex.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#endif /* CLIB_COMMON_H */
"""

PYEX_COMMON_H = """\
/**
 * pyex_common.h — common Python extension includes for <<package>>.
 *
 * To add NumPy support, append after this include:
 *   #define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
 *   #include <numpy/arrayobject.h>
 */
#ifndef PYEX_COMMON_H
#define PYEX_COMMON_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#endif /* PYEX_COMMON_H */
"""

JM_SIMD_H = """\
/**
 * jm_simd.h — width-portable SIMD operation macros.
 *
 * Selects the widest available instruction set at compile time:
 *   AVX-512F  ->  16 float / 8 double lanes  (JM_SIMD_WIDTH_F32 = 16)
 *   AVX2+FMA  ->   8 float / 4 double lanes  (JM_SIMD_WIDTH_F32 =  8)
 *   Scalar    ->   1 lane  (auto-vectorisation still applies)
 *
 * Typical usage (FIR inner loop):
 *
 *   JM_VEC_F32 acc = JM_ZERO_F32();
 *   for (int k = 0; k < N_TAPS; k++)
 *       JM_MAC_F32(acc, window + k, coeffs[k]);
 *   *out = JM_HSUM_F32(acc);
 *
 * JM_SIMD_WIDTH_F32 tells you how many floats the loop above advances
 * per iteration — stride your outer loop accordingly.
 *
 * Can be included standalone; reuses JM_RESTRICT from jm_perf.h if
 * already defined, otherwise provides its own fallback.
 */
#ifndef JM_SIMD_H
#define JM_SIMD_H

/* Reuse JM_RESTRICT from jm_perf.h if available; otherwise define locally. */
#ifndef JM_RESTRICT
#  if defined(__GNUC__) || defined(__clang__)
#    define JM_RESTRICT __restrict__
#  elif defined(_MSC_VER)
#    define JM_RESTRICT __restrict
#  else
#    define JM_RESTRICT restrict
#  endif
#endif

/* Pull in x86 intrinsic headers if not already included. */
#if (defined(__x86_64__) || defined(_M_X64) || \\
     defined(__i386__)   || defined(_M_IX86))
#  ifndef _IMMINTRIN_H_INCLUDED
#    include <immintrin.h>
#  endif
#endif

/* ════════════════════════════════════════════════════════════════════════
 * Tier 1 — AVX-512F  (16 float / 8 double lanes)
 * ════════════════════════════════════════════════════════════════════════ */
#if defined(__AVX512F__)

#define JM_SIMD_WIDTH_F32   16
#define JM_SIMD_WIDTH_F64    8
#define JM_SIMD_WIDTH       JM_SIMD_WIDTH_F32

typedef __m512  JM_VEC_F32;
typedef __m512d JM_VEC_F64;

#define JM_ZERO_F32()            _mm512_setzero_ps()
#define JM_ZERO_F64()            _mm512_setzero_pd()
#define JM_SPLAT_F32(x)          _mm512_set1_ps(x)
#define JM_SPLAT_F64(x)          _mm512_set1_pd(x)
#define JM_LOAD_F32(p)           _mm512_loadu_ps(p)
#define JM_LOAD_F64(p)           _mm512_loadu_pd(p)
#define JM_STORE_F32(p, v)       _mm512_storeu_ps(p, v)
#define JM_STORE_F64(p, v)       _mm512_storeu_pd(p, v)
#define JM_ADD_F32(a, b)         _mm512_add_ps(a, b)
#define JM_ADD_F64(a, b)         _mm512_add_pd(a, b)
#define JM_MUL_F32(a, b)         _mm512_mul_ps(a, b)
#define JM_MUL_F64(a, b)         _mm512_mul_pd(a, b)
/* acc += a * b */
#define JM_FMA_F32(acc, a, b)    ((acc) = _mm512_fmadd_ps(a, b, acc))
#define JM_FMA_F64(acc, a, b)    ((acc) = _mm512_fmadd_pd(a, b, acc))
/* Load JM_SIMD_WIDTH_F32 floats from ptr, multiply by scalar s, accumulate */
#define JM_MAC_F32(acc, ptr, s)  JM_FMA_F32(acc, JM_LOAD_F32(ptr), JM_SPLAT_F32(s))
#define JM_MAC_F64(acc, ptr, s)  JM_FMA_F64(acc, JM_LOAD_F64(ptr), JM_SPLAT_F64(s))
/* Horizontal sum: reduce all lanes to one scalar */
#define JM_HSUM_F32(v)           _mm512_reduce_add_ps(v)
#define JM_HSUM_F64(v)           _mm512_reduce_add_pd(v)

/* ════════════════════════════════════════════════════════════════════════
 * Tier 2 — AVX2 + FMA  (8 float / 4 double lanes)
 * ════════════════════════════════════════════════════════════════════════ */
#elif defined(__AVX2__) && defined(__FMA__)

#define JM_SIMD_WIDTH_F32    8
#define JM_SIMD_WIDTH_F64    4
#define JM_SIMD_WIDTH        JM_SIMD_WIDTH_F32

typedef __m256  JM_VEC_F32;
typedef __m256d JM_VEC_F64;

#define JM_ZERO_F32()            _mm256_setzero_ps()
#define JM_ZERO_F64()            _mm256_setzero_pd()
#define JM_SPLAT_F32(x)          _mm256_set1_ps(x)
#define JM_SPLAT_F64(x)          _mm256_set1_pd(x)
#define JM_LOAD_F32(p)           _mm256_loadu_ps(p)
#define JM_LOAD_F64(p)           _mm256_loadu_pd(p)
#define JM_STORE_F32(p, v)       _mm256_storeu_ps(p, v)
#define JM_STORE_F64(p, v)       _mm256_storeu_pd(p, v)
#define JM_ADD_F32(a, b)         _mm256_add_ps(a, b)
#define JM_ADD_F64(a, b)         _mm256_add_pd(a, b)
#define JM_MUL_F32(a, b)         _mm256_mul_ps(a, b)
#define JM_MUL_F64(a, b)         _mm256_mul_pd(a, b)
#define JM_FMA_F32(acc, a, b)    ((acc) = _mm256_fmadd_ps(a, b, acc))
#define JM_FMA_F64(acc, a, b)    ((acc) = _mm256_fmadd_pd(a, b, acc))
#define JM_MAC_F32(acc, ptr, s)  JM_FMA_F32(acc, JM_LOAD_F32(ptr), JM_SPLAT_F32(s))
#define JM_MAC_F64(acc, ptr, s)  JM_FMA_F64(acc, JM_LOAD_F64(ptr), JM_SPLAT_F64(s))

/* Horizontal-sum helpers (SSE3 hadd guaranteed when AVX2 is available) */
static inline float _jm_hsum256_f32(__m256 v) {
    __m128 lo = _mm256_castps256_ps128(v);
    __m128 hi = _mm256_extractf128_ps(v, 1);
    __m128 s  = _mm_add_ps(lo, hi);
    s = _mm_hadd_ps(s, s);
    s = _mm_hadd_ps(s, s);
    return _mm_cvtss_f32(s);
}
static inline double _jm_hsum256_f64(__m256d v) {
    __m128d lo = _mm256_castpd256_pd128(v);
    __m128d hi = _mm256_extractf128_pd(v, 1);
    __m128d s  = _mm_add_pd(lo, hi);
    s = _mm_hadd_pd(s, s);
    return _mm_cvtsd_f64(s);
}
#define JM_HSUM_F32(v)           _jm_hsum256_f32(v)
#define JM_HSUM_F64(v)           _jm_hsum256_f64(v)

/* ════════════════════════════════════════════════════════════════════════
 * Tier 3 — Scalar  (1 lane; compiler auto-vectorisation still applies)
 * ════════════════════════════════════════════════════════════════════════ */
#else

#define JM_SIMD_WIDTH_F32    1
#define JM_SIMD_WIDTH_F64    1
#define JM_SIMD_WIDTH        1

typedef float  JM_VEC_F32;
typedef double JM_VEC_F64;

#define JM_ZERO_F32()            0.0f
#define JM_ZERO_F64()            0.0
#define JM_SPLAT_F32(x)          (x)
#define JM_SPLAT_F64(x)          (x)
#define JM_LOAD_F32(p)           (*(p))
#define JM_LOAD_F64(p)           (*(p))
#define JM_STORE_F32(p, v)       (*(p) = (v))
#define JM_STORE_F64(p, v)       (*(p) = (v))
#define JM_ADD_F32(a, b)         ((a) + (b))
#define JM_ADD_F64(a, b)         ((a) + (b))
#define JM_MUL_F32(a, b)         ((a) * (b))
#define JM_MUL_F64(a, b)         ((a) * (b))
#define JM_FMA_F32(acc, a, b)    ((acc) += (a) * (b))
#define JM_FMA_F64(acc, a, b)    ((acc) += (a) * (b))
#define JM_MAC_F32(acc, ptr, s)  ((acc) += (*(ptr)) * (s))
#define JM_MAC_F64(acc, ptr, s)  ((acc) += (*(ptr)) * (s))
#define JM_HSUM_F32(v)           (v)
#define JM_HSUM_F64(v)           (v)

#endif /* SIMD tier */

/* ── Dot product: SIMD-vectorised + scalar tail ───────────────────────── */

static inline float jm_dot_f32(
        const float  * JM_RESTRICT a,
        const float  * JM_RESTRICT b, int n)
{
    JM_VEC_F32 acc = JM_ZERO_F32();
    int i = 0;
#if JM_SIMD_WIDTH_F32 > 1
    for (; i <= n - JM_SIMD_WIDTH_F32; i += JM_SIMD_WIDTH_F32)
        JM_FMA_F32(acc, JM_LOAD_F32(a + i), JM_LOAD_F32(b + i));
#endif
    float s = JM_HSUM_F32(acc);
    for (; i < n; i++) s += a[i] * b[i];
    return s;
}

static inline double jm_dot_f64(
        const double * JM_RESTRICT a,
        const double * JM_RESTRICT b, int n)
{
    JM_VEC_F64 acc = JM_ZERO_F64();
    int i = 0;
#if JM_SIMD_WIDTH_F64 > 1
    for (; i <= n - JM_SIMD_WIDTH_F64; i += JM_SIMD_WIDTH_F64)
        JM_FMA_F64(acc, JM_LOAD_F64(a + i), JM_LOAD_F64(b + i));
#endif
    double s = JM_HSUM_F64(acc);
    for (; i < n; i++) s += a[i] * b[i];
    return s;
}

#endif /* JM_SIMD_H */
"""

JM_PERF_H = """\
/**
 * jm_perf.h — compiler-hint macros for <<package>>.
 *
 * All macros expand to safe no-ops on unknown compilers.
 * Include freely; zero runtime cost.
 */
#ifndef JM_PERF_H
#define JM_PERF_H

/* Hint that x is almost always true; guides branch-predictor, reducing misprediction stalls. */
#define JM_LIKELY(x)     _JM_LIKELY_(x)
/* Hint that x is almost never true; keeps cold-path code out of the L1 instruction cache. */
#define JM_UNLIKELY(x)   _JM_UNLIKELY_(x)
/* Assert that a pointer does not alias any other; lets the compiler reorder/vectorise freely. */
#define JM_RESTRICT      _JM_RESTRICT_
/* Override inlining heuristics and force inlining; eliminates call overhead on hot functions. */
#define JM_FORCEINLINE   _JM_FORCEINLINE_
/* Align a variable or struct member to n bytes; required for safe SIMD load/store operations. */
#define JM_ALIGNED(n)    _JM_ALIGNED_(n)
/* Mark a function as performance-critical; compiler may place it in a hot section and optimise more aggressively. */
#define JM_HOT           _JM_HOT_

/* GCC / Clang */
#if defined(__GNUC__) || defined(__clang__)
#  define _JM_LIKELY_(x)     __builtin_expect(!!(x), 1)
#  define _JM_UNLIKELY_(x)   __builtin_expect(!!(x), 0)
#  define _JM_RESTRICT_      restrict
#  define _JM_FORCEINLINE_   __attribute__((always_inline)) inline
#  define _JM_ALIGNED_(n)    __attribute__((aligned(n)))
#  define _JM_HOT_           __attribute__((hot))

/* MSVC */
#elif defined(_MSC_VER)
#  define _JM_LIKELY_(x)     (x)
#  define _JM_UNLIKELY_(x)   (x)
#  define _JM_RESTRICT_      __restrict
#  define _JM_FORCEINLINE_   __forceinline
#  define _JM_ALIGNED_(n)    __declspec(align(n))
#  define _JM_HOT_

/* Unknown / strict C99 — safe no-ops */
#else
#  define _JM_LIKELY_(x)     (x)
#  define _JM_UNLIKELY_(x)   (x)
#  define _JM_RESTRICT_      restrict
#  define _JM_FORCEINLINE_   inline
#  define _JM_ALIGNED_(n)
#  define _JM_HOT_
#endif

/* Loop-unroll directive: JM_UNROLL(8) before a for loop instructs GCC/Clang
 * to unroll it exactly n times regardless of the compiler's own cost model.
 * Unlike advisory hints (JM_HOT, JM_LIKELY), this is obeyed unconditionally —
 * a large n on a non-trivial body will bloat code size and hurt icache.
 * Use only on tight, well-measured inner loops with a known iteration count. */
#define JM_UNROLL(n)     _JM_UNROLL_(n)

/* Inform the compiler that ptr is aligned to n bytes; enables SIMD
 * loads/stores without alignment penalties on older ISAs. */
#define JM_ASSUME_ALIGNED(ptr, n)  _JM_ASSUME_ALIGNED_(ptr, n)

/* Software prefetch: rw=0 for read, rw=1 for write; locality 0-3
 * (0=NTA, 3=L1).  No-op on unknown compilers. */
#define JM_PREFETCH(ptr, rw, loc)  _JM_PREFETCH_(ptr, rw, loc)

#if defined(__GNUC__) || defined(__clang__)
#  define _JM_STRINGIFY_(x)           #x
#  define _JM_UNROLL_(n)              _Pragma(_JM_STRINGIFY_(GCC unroll n))
#  define _JM_ASSUME_ALIGNED_(p, n)   __builtin_assume_aligned(p, n)
#  define _JM_PREFETCH_(p, rw, loc)   __builtin_prefetch(p, rw, loc)
#else
#  define _JM_UNROLL_(n)
#  define _JM_ASSUME_ALIGNED_(p, n)   (p)
#  define _JM_PREFETCH_(p, rw, loc)
#endif

/* x86 SIMD intrinsics (SSE through AVX-512) */
#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__) || defined(_M_IX86)
#  include <immintrin.h>
#endif

/* Width-portable SIMD operation macros (JM_VEC_F32, JM_MAC_F32, etc.) */
#include "jm_simd.h"

/* ── JM_DEFINE_STEPS ────────────────────────────────────────────────────────
 *
 * Stamps out <fn>_steps() — the outer dispatch loop — from three separate
 * concerns:
 *
 *   fn        - name prefix; calls fn##_step() and fn##_step_batch()
 *   state_t   - state struct type
 *   sample_t  - per-sample type  (e.g. float complex)
 *   LENGTH    - history depth: samples held in state->delay[]  [algorithm]
 *   BATCH     - SIMD width in samples                          [parallelism]
 *   CHUNK     - samples per scratch-buffer fill                [tuning]
 *
 * Convention: state->delay[0..LENGTH-1] is the delay line, delay[0] = newest.
 * LENGTH, BATCH, and CHUNK must be integer constant expressions (no VLA).
 *
 * Usage (16-tap FIR: TAPS=16, LENGTH=TAPS-1=15):
 *   JM_DEFINE_STEPS(fir_filter, fir_filter_state_t, float complex,
 *                   FIR_LENGTH, FIR_BATCH, FIR_CHUNK)
 */
#if JM_SIMD_WIDTH_F32 > 1
#  define _JM_STEPS_SIMD_(fn, st, samp, LENGTH, BATCH, CHUNK)                \\
    {                                                                          \\
        samp _scratch[(LENGTH) + (CHUNK)];                                    \\
        while (_i + (BATCH) <= n) {                                           \\
            size_t _blk  = (n - _i < (CHUNK)) ? (n - _i) : (CHUNK);          \\
            size_t _main = _blk & ~(size_t)((BATCH) - 1);                    \\
            for (int _j = 0; _j < (LENGTH); _j++)                             \\
                _scratch[_j] = state->delay[(LENGTH) - 1 - _j];              \\
            memcpy(_scratch + (LENGTH), input + _i, _blk * sizeof(samp));    \\
            for (size_t _p = 0; _p < _main; _p += (BATCH))                   \\
                fn##_step_batch(state, _scratch + _p, output + _i + _p);     \\
            for (int _j = 0; _j < (LENGTH); _j++)                             \\
                state->delay[_j] = _scratch[_main + (LENGTH) - 1 - _j];     \\
            _i += _main;                                                      \\
        }                                                                      \\
    }
#else
#  define _JM_STEPS_SIMD_(fn, st, samp, LENGTH, BATCH, CHUNK)  /* scalar: no batching */
#endif

#define JM_DEFINE_STEPS(fn, state_t, sample_t, LENGTH, BATCH, CHUNK)         \\
void fn##_steps(                                                               \\
        state_t            *state,                                             \\
        const sample_t     *input,                                             \\
        sample_t           *output,                                            \\
        size_t              n)                                                 \\
{                                                                              \\
    size_t _i = 0;                                                             \\
    _JM_STEPS_SIMD_(fn, state_t, sample_t, LENGTH, BATCH, CHUNK)              \\
    for (; _i < n; _i++)                                                       \\
        output[_i] = fn##_step(state, input[_i]);                             \\
}

#endif /* JM_PERF_H */
"""

COMPONENT_CORE_H = """\
/**
 * @file <<component>>_core.h
 * @brief <<Component>> component API.
 *
 * Lifecycle: create → [step / steps / reset]* → destroy
 *
 * Example:
 * @code
 * <<component>>_state_t *obj = <<component>>_create(<<c_create_args>>);
 * <<return_ctype>> y = <<component>>_step(obj, <<arg_zero>>);
 * <<component>>_destroy(obj);
 * @endcode
 */
#ifndef <<COMPONENT>>_CORE_H
#define <<COMPONENT>>_CORE_H

#include "clib_common.h"
<<perf_include>>
#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief <<Component>> state.
 *
 * Opaque to callers — allocate with <<component>>_create().
 */
typedef struct {
<<state_struct_fields>>
} <<component>>_state_t;

/**
 * @brief Create a <<component>> instance.
 *
<<create_param_docs>>
 * @return Heap-allocated state, or NULL on allocation failure.
 * @note Caller must call <<component>>_destroy() when done.
 */
<<component>>_state_t *<<component>>_create(<<create_params>>);

/**
 * @brief Destroy a <<component>> instance and release all memory.
 * @param state  May be NULL.
 */
void <<component>>_destroy(<<component>>_state_t *state);

/**
 * @brief Reset <<component>> to its post-create state.
 * @param state  Must be non-NULL.
 */
void <<component>>_reset(<<component>>_state_t *state);

/**
 * @brief Process a single complex sample.
 *
 * @param state  Component state.
 * @param x      Input sample.
 * @return       Output sample.
 * @note Inlined for maximum performance.
 */
<<step_qualifier>> <<return_ctype>>
<<component>>_step(const <<component>>_state_t *state, <<arg_ctype>> x)
{
    (void)state; /* TODO: implement using state variables */
    return (<<return_ctype>>)x;
}

/**
 * @brief Process a block of samples.
 *
 * @param state   Component state.
 * @param input   Input array (length >= n).
 * @param output  Output array (length >= n; may alias input for in-place).
 * @param n       Number of samples.
 * @note Output buffer must be pre-allocated by caller.
 */
void <<component>>_steps(
    <<component>>_state_t *state,
    const <<arg_ctype>>   *input,
    <<return_ctype>>      *output,
    size_t                 n);

<<getter_setter_decls>>

#ifdef __cplusplus
}
#endif

#endif /* <<COMPONENT>>_CORE_H */
"""

# ── C source ─────────────────────────────────────────────────────────────────

COMPONENT_CORE_C = """\
#include "<<component>>/<<component>>_core.h"

<<component>>_state_t *
<<component>>_create(<<create_params>>)
{
    <<component>>_state_t *state = malloc(sizeof(*state));
    if (!state)
        return NULL;
<<create_assignments>>
    return state;
}

void
<<component>>_destroy(<<component>>_state_t *state)
{
    free(state);
}

void
<<component>>_reset(<<component>>_state_t *state)
{
<<reset_assignments>>
}

void
<<component>>_steps(
    <<component>>_state_t *state,
    const <<arg_ctype>>   *input,
    <<return_ctype>>      *output,
    size_t                 n)
{
<<omp_simd_hint>>    for (size_t i = 0; i < n; i++)
        output[i] = <<component>>_step(state, input[i]);
}

<<getter_setter_impls>>
"""

COMPONENT_EXT_C = """\
/*
 * <<component>>_ext.c — Python C extension for <<component>>_core.h
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>

#include "<<component>>/<<component>>_core.h"

/* ======================================================== */
/* <<Component>>Object — wraps <<component>>_state_t *       */
/* ======================================================== */

typedef struct {
    PyObject_HEAD
    <<component>>_state_t *handle;
} <<Component>>Object;

static void
<<Component>>_dealloc(<<Component>>Object *self)
{
    if (self->handle)
        <<component>>_destroy(self->handle);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
<<Component>>_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    <<Component>>Object *self = (<<Component>>Object *)type->tp_alloc(type, 0);
    if (self)
        self->handle = NULL;
    return (PyObject *)self;
}

static int
<<Component>>_init(<<Component>>Object *self, PyObject *args, PyObject *kwds)
{
<<init_parse_block>>    self->handle = <<component>>_create(<<create_call_args>>);
    if (!self->handle) {
        PyErr_SetString(PyExc_MemoryError,
                        "<<component>>_create returned NULL");
        return -1;
    }
    return 0;
}

static PyObject *
<<Component>>_reset(<<Component>>Object *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    <<component>>_reset(self->handle);
    Py_RETURN_NONE;
}

static PyObject *
<<Component>>_step(<<Component>>Object *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
<<step_parse_block>>
    <<return_ctype>> y = <<component>>_step(self->handle, x);
    return <<step_return_expr>>;
}

static PyObject *
<<Component>>_steps(<<Component>>Object *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    PyObject *in_obj = NULL;
    if (!PyArg_ParseTuple(args, "O", &in_obj))
        return NULL;

    PyArrayObject *in_arr = (PyArrayObject *)PyArray_FROM_OTF(
        in_obj, <<in_np_enum>>, NPY_ARRAY_C_CONTIGUOUS);
    if (!in_arr)
        return NULL;

    Py_ssize_t n = PyArray_SIZE(in_arr);
    npy_intp dims[] = {n};
    PyObject *out_arr = PyArray_SimpleNew(1, dims, <<out_np_enum>>);
    if (!out_arr) {
        Py_DECREF(in_arr);
        return NULL;
    }

    <<component>>_steps(
        self->handle,
        (const <<arg_ctype>> *)PyArray_DATA(in_arr),
        (<<return_ctype>> *)PyArray_DATA((PyArrayObject *)out_arr),
        (size_t)n);

    Py_DECREF(in_arr);
    return out_arr;
}

<<getter_setter_methods_c>>

static PyObject *
<<Component>>_destroy(<<Component>>Object *self, PyObject *Py_UNUSED(ignored))
{
    if (self->handle) {
        <<component>>_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *
<<Component>>_enter(<<Component>>Object *self, PyObject *Py_UNUSED(ignored))
{
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *
<<Component>>_exit(<<Component>>Object *self, PyObject *args)
{
    (void)args;
    if (self->handle) {
        <<component>>_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef <<Component>>_methods[] = {
    {"reset",    (PyCFunction)<<Component>>_reset,    METH_NOARGS,
     "Reset state to post-create defaults."},
    {"step",     (PyCFunction)<<Component>>_step,     METH_VARARGS,
     "Process one sample. Returns a scalar."},
    {"steps",    (PyCFunction)<<Component>>_steps,    METH_VARARGS,
     "Process a samples array. Returns an ndarray."},
<<getter_setter_pymethoddef>>
    {"destroy",  (PyCFunction)<<Component>>_destroy,  METH_NOARGS,
     "Release resources."},
    {"__enter__", (PyCFunction)<<Component>>_enter,   METH_NOARGS,  NULL},
    {"__exit__",  (PyCFunction)<<Component>>_exit,    METH_VARARGS, NULL},
    {NULL}
};

static PyTypeObject <<Component>>Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "<<component>>.<<Component>>",
    .tp_basicsize = sizeof(<<Component>>Object),
    .tp_dealloc   = (destructor)<<Component>>_dealloc,
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_doc       = "<<Component>> component. Wraps <<component>>_state_t.",
    .tp_methods   = <<Component>>_methods,
    .tp_new       = <<Component>>_new,
    .tp_init      = (initproc)<<Component>>_init,
};

/* ======================================================== */
/* Module definition                                         */
/* ======================================================== */

static PyModuleDef <<component>>_module = {
    PyModuleDef_HEAD_INIT,
    .m_name    = "<<component>>",
    .m_doc     = "Python binding for <<component>>_core.h.",
    .m_size    = -1,
    .m_methods = NULL,
};

PyMODINIT_FUNC
PyInit_<<component>>(void)
{
    import_array();
    if (PyType_Ready(&<<Component>>Type) < 0)
        return NULL;

    PyObject *m = PyModule_Create(&<<component>>_module);
    if (!m)
        return NULL;

    Py_INCREF(&<<Component>>Type);
    if (PyModule_AddObject(m, "<<Component>>",
                           (PyObject *)&<<Component>>Type) < 0) {
        Py_DECREF(&<<Component>>Type);
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
"""

# ── C test ───────────────────────────────────────────────────────────────────

COMPONENT_TEST_C = """\
#include "<<component>>/<<component>>_core.h"
#include <assert.h>
#include <complex.h>
#include <stdio.h>

int main(void)
{
    <<component>>_state_t *obj = <<component>>_create(<<c_create_args>>);
    assert(obj != NULL);

    /* step: verify it runs */
    (void)<<component>>_step(obj, <<arg_zero>>);

<<getter_setter_test_c>>

<<reset_test_c>>

    <<component>>_destroy(obj);
    printf("test_<<component>>_core PASSED\\n");
    return 0;
}
"""

# ── C benchmark ──────────────────────────────────────────────────────────────

COMPONENT_BENCH_C = """\
#include "<<component>>/<<component>>_core.h"
#include <complex.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define BENCH_N    65536
#define ITERATIONS 200

static double
elapsed_sec(struct timespec *t0, struct timespec *t1)
{
    return (double)(t1->tv_sec - t0->tv_sec)
           + (double)(t1->tv_nsec - t0->tv_nsec) * 1e-9;
}

int
main(void)
{
    <<arg_ctype>> *in  = malloc(BENCH_N * sizeof(<<arg_ctype>>));
    <<return_ctype>> *out = malloc(BENCH_N * sizeof(<<return_ctype>>));
    if (!in || !out) { fprintf(stderr, "OOM\\n"); return 1; }
    for (int i = 0; i < BENCH_N; i++) in[i] = <<bench_in_init>>;

    <<component>>_state_t *obj = <<component>>_create(<<c_create_args>>);

    /* warmup */
    for (int i = 0; i < 16; i++) (void)<<component>>_step(obj, <<bench_warmup>>);

    struct timespec t0, t1;
    double sec;

    printf("=== <<component>> benchmark ===\\n");
    printf("block = %d samples,  %d iterations\\n\\n", BENCH_N, ITERATIONS);

    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int r = 0; r < ITERATIONS; r++)
        for (int i = 0; i < BENCH_N; i++)
            (void)<<component>>_step(obj, in[i]);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    sec = elapsed_sec(&t0, &t1);
    printf("  step()   %8.1f MSa/s\\n",
           (double)ITERATIONS * BENCH_N / sec / 1e6);

    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int r = 0; r < ITERATIONS; r++)
        <<component>>_steps(obj, in, out, BENCH_N);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    sec = elapsed_sec(&t0, &t1);
    printf("  steps()  %8.1f MSa/s\\n",
           (double)ITERATIONS * BENCH_N / sec / 1e6);

    <<component>>_destroy(obj);
    free(in); free(out);
    return 0;
}
"""

# ── Python benchmark ──────────────────────────────────────────────────────────

COMPONENT_BENCH_PY = """\
import numpy as np
import pytest

from <<package>> import <<Component>>


@pytest.fixture
def obj():
    return <<Component>>(<<py_create_args>>)


@pytest.mark.benchmark(group="<<component>>")
def test_bench_step(benchmark, obj):
    benchmark(obj.step, <<in_py_test_val>>)


@pytest.mark.benchmark(group="<<component>>")
def test_bench_steps_1k(benchmark, obj):
    x = np.ones(1024, dtype=<<in_np_dtype>>)
    benchmark(obj.steps, x)


@pytest.mark.benchmark(group="<<component>>")
def test_bench_steps_64k(benchmark, obj):
    x = np.ones(65536, dtype=<<in_np_dtype>>)
    benchmark(obj.steps, x)
"""

# ── Pure scalar templates ─────────────────────────────────────────────────────

PURE_SCALAR_CORE_H = """\
/**
 * @file <<component>>_core.h
 * @brief <<Component>> — pure (stateless) transform.
 *
 * Usage:
 * @code
 * <<return_ctype>> y = <<component>>_fn(x<<c_fn_call_defaults>>);
 * @endcode
 */
#ifndef <<COMPONENT>>_CORE_H
#define <<COMPONENT>>_CORE_H

#include "clib_common.h"
<<perf_include>>
#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Process a single sample.
 * @param x   Input sample.
<<c_fn_param_docs>>
 * @return    Output sample.
 */
<<step_qualifier>><<return_ctype>>
<<component>>_fn(<<arg_ctype>> x<<c_fn_params>>)
{
    (void)x;  /* TODO: implement */
    return (<<return_ctype>>)x;
}

/**
 * @brief Process a block of samples.
 * @param input   Input array (length >= n).
 * @param output  Output array (length >= n; may alias input).
 * @param n       Number of samples.
<<c_fn_param_docs>>
 */
void <<component>>_steps(
    const <<arg_ctype>> *input,
    <<return_ctype>>    *output,
    size_t               n<<c_fn_params>>);

#ifdef __cplusplus
}
#endif

#endif /* <<COMPONENT>>_CORE_H */
"""

PURE_SCALAR_CORE_C = """\
#include "<<component>>/<<component>>_core.h"

void
<<component>>_steps(
    const <<arg_ctype>> *input,
    <<return_ctype>>    *output,
    size_t               n<<c_fn_params>>)
{
    for (size_t i = 0; i < n; i++)
        output[i] = <<component>>_fn(input[i]<<c_fn_call_args>>);
}
"""

PURE_SCALAR_EXT_C = """\
/*
 * <<component>>_ext.c — Python C extension for <<component>>_core.h (pure/scalar)
 *
 * Exports two module-level functions:
 *   <<component>>(x<<c_fn_params>>) -> scalar
 *   <<component>>_steps(arr<<c_fn_params>>) -> NDArray
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>

#include "<<component>>/<<component>>_core.h"

/* ── <<component>>(x, **params) ─────────────────────────────────────────────── */

static PyObject *
py_<<component>>(PyObject *module, PyObject *args, PyObject *kwds)
{
    (void)module;
    static char *kwlist[] = {"x", <<py_kwlist>>NULL};
<<pure_x_local>>
<<pure_locals>>
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "<<pure_fn_fmt>>", kwlist,
                                     <<pure_x_parse_arg>><<pure_params_args>>))
        return NULL;
<<pure_post_parse>><<pure_x_to_c>>    <<return_ctype>> y = <<component>>_fn(x<<c_fn_call_args>>);
    return <<step_return_expr>>;
}

/* ── <<component>>_steps(arr, **params) ──────────────────────────────────────── */

static PyObject *
py_<<component>>_steps(PyObject *module, PyObject *args, PyObject *kwds)
{
    (void)module;
    static char *kwlist[] = {"arr", <<py_kwlist>>NULL};
    PyObject *in_obj = NULL;
<<pure_locals>>
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "<<pure_steps_fmt>>", kwlist,
                                     &in_obj<<pure_params_args>>))
        return NULL;
<<pure_post_parse>>
    PyArrayObject *in_arr = (PyArrayObject *)PyArray_FROM_OTF(
        in_obj, <<in_np_enum>>, NPY_ARRAY_C_CONTIGUOUS);
    if (!in_arr)
        return NULL;

    Py_ssize_t n = PyArray_SIZE(in_arr);
    npy_intp dims[] = {n};
    PyObject *out_arr = PyArray_SimpleNew(1, dims, <<out_np_enum>>);
    if (!out_arr) { Py_DECREF(in_arr); return NULL; }

    <<component>>_steps(
        (const <<arg_ctype>> *)PyArray_DATA(in_arr),
        (<<return_ctype>> *)PyArray_DATA((PyArrayObject *)out_arr),
        (size_t)n<<c_fn_call_args>>);

    Py_DECREF(in_arr);
    return out_arr;
}

/* ── Module ──────────────────────────────────────────────────────────────────── */

static PyMethodDef <<component>>_methods[] = {
    {"<<component>>",
     (PyCFunction)py_<<component>>, METH_VARARGS | METH_KEYWORDS,
     "<<component>>(x<<c_fn_params>>) -> scalar"},
    {"<<component>>_steps",
     (PyCFunction)py_<<component>>_steps, METH_VARARGS | METH_KEYWORDS,
     "<<component>>_steps(arr<<c_fn_params>>) -> NDArray"},
    {NULL}
};

static PyModuleDef <<component>>_module = {
    PyModuleDef_HEAD_INIT,
    .m_name    = "<<component>>",
    .m_doc     = "Pure (stateless) binding for <<component>>_core.h.",
    .m_size    = -1,
    .m_methods = <<component>>_methods,
};

PyMODINIT_FUNC
PyInit_<<component>>(void)
{
    import_array();
    return PyModule_Create(&<<component>>_module);
}
"""

PURE_SCALAR_TEST_C = """\
#include "<<component>>/<<component>>_core.h"
#include <assert.h>
#include <complex.h>
#include <stdio.h>

int main(void)
{
    /* fn: verify it runs */
    <<return_ctype>> y = <<component>>_fn(<<arg_zero>><<c_fn_call_defaults>>);
    (void)y;

    /* steps: verify it runs */
    <<arg_ctype>> in[4]  = <<test_arr_4_init>>;
    <<return_ctype>> out[4] = {0};
    <<component>>_steps(in, out, 4<<c_fn_call_defaults>>);

    printf("test_<<component>>_core PASSED\\n");
    return 0;
}
"""

PURE_SCALAR_PYI = """\
import numpy as np
from numpy.typing import NDArray

def <<component>>(x: <<in_py_hint>><<py_fn_type_params>>) -> <<out_py_hint>>:
    \"\"\"<<Component>> — pure (stateless) transform. Returns a single output sample.\"\"\"
    ...

def <<component>>_steps(arr: NDArray[<<in_np_dtype>>]<<py_fn_type_params>>) -> NDArray[<<out_np_dtype>>]:
    \"\"\"Process a block of samples. Returns an ndarray.\"\"\"
    ...
"""

PYTEST_PURE_SCALAR_TEST = """\
import numpy as np
import pytest

from <<package>> import <<component>>, <<component>>_steps


class Test<<Component>>:
    def test_fn_runs(self):
        y = <<component>>(<<in_py_test_val>><<py_fn_kwargs>>)
        assert isinstance(y, <<out_py_isinstance>>)

    def test_steps_runs(self):
        x = np.ones(16, dtype=<<in_np_dtype>>)
        y = <<component>>_steps(x<<py_fn_kwargs>>)
        assert y.shape == (16,)
        assert y.dtype == <<out_np_dtype>>

    def test_steps_sizes(self):
        for n in (1, 64, 1024):
            x = np.ones(n, dtype=<<in_np_dtype>>)
            y = <<component>>_steps(x<<py_fn_kwargs>>)
            assert y.shape == (n,)

    def test_steps_attr(self):
        from <<package>> import <<component>>
        y = <<component>>.steps(np.ones(4, dtype=<<in_np_dtype>>)<<py_fn_kwargs>>)
        assert y.shape == (4,)
"""

PURE_SCALAR_BENCH_C = """\
#include "<<component>>/<<component>>_core.h"
#include <complex.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define BENCH_N    65536
#define ITERATIONS 200

static double
elapsed_sec(struct timespec *t0, struct timespec *t1)
{
    return (double)(t1->tv_sec - t0->tv_sec)
           + (double)(t1->tv_nsec - t0->tv_nsec) * 1e-9;
}

int
main(void)
{
    <<arg_ctype>> *in  = malloc(BENCH_N * sizeof(<<arg_ctype>>));
    <<return_ctype>> *out = malloc(BENCH_N * sizeof(<<return_ctype>>));
    if (!in || !out) { fprintf(stderr, "OOM\\n"); return 1; }
    for (int i = 0; i < BENCH_N; i++) in[i] = <<bench_in_init>>;

    for (int i = 0; i < 16; i++)
        (void)<<component>>_fn(<<bench_warmup>><<c_fn_call_defaults>>);

    struct timespec t0, t1;
    double sec;

    printf("=== <<component>> benchmark (pure/scalar) ===\\n");
    printf("block = %d samples,  %d iterations\\n\\n", BENCH_N, ITERATIONS);

    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int r = 0; r < ITERATIONS; r++)
        for (int i = 0; i < BENCH_N; i++)
            (void)<<component>>_fn(in[i]<<c_fn_call_defaults>>);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    sec = elapsed_sec(&t0, &t1);
    printf("  fn()     %8.1f MSa/s\\n",
           (double)ITERATIONS * BENCH_N / sec / 1e6);

    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int r = 0; r < ITERATIONS; r++)
        <<component>>_steps(in, out, BENCH_N<<c_fn_call_defaults>>);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    sec = elapsed_sec(&t0, &t1);
    printf("  steps()  %8.1f MSa/s\\n",
           (double)ITERATIONS * BENCH_N / sec / 1e6);

    free(in); free(out);
    return 0;
}
"""

PURE_SCALAR_BENCH_PY = """\
import numpy as np
import pytest

from <<package>> import <<component>>, <<component>>_steps


@pytest.mark.benchmark(group="<<component>>")
def test_bench_fn(benchmark):
    benchmark(<<component>>, <<in_py_test_val>><<py_fn_kwargs>>)


@pytest.mark.benchmark(group="<<component>>")
def test_bench_steps_1k(benchmark):
    x = np.ones(1024, dtype=<<in_np_dtype>>)
    benchmark(<<component>>_steps, x<<py_fn_kwargs>>)


@pytest.mark.benchmark(group="<<component>>")
def test_bench_steps_64k(benchmark):
    x = np.ones(65536, dtype=<<in_np_dtype>>)
    benchmark(<<component>>_steps, x<<py_fn_kwargs>>)
"""

# ── Pure struct templates ─────────────────────────────────────────────────────

PURE_STRUCT_CORE_H = """\
/**
 * @file <<component>>_core.h
 * @brief <<Component>> — pure (caller-managed) params.
 *
 * The caller owns <<component>>_params_t and passes it to every call.
 * Multiple channels can use the same <<component>>_fn by keeping separate
 * params instances — no hidden global state.
 *
 * Stack usage:
 * @code
 * <<component>>_params_t p;
 * <<component>>_params_init(&p);
 * <<return_ctype>> y = <<component>>_fn(x, &p);
 * @endcode
 *
 * Heap usage (with aligned allocation for SIMD):
 * @code
 * <<component>>_params_t *p = <<component>>_params_create();
 * <<return_ctype>> y = <<component>>_fn(x, p);
 * <<component>>_params_free(p);
 * @endcode
 */
#ifndef <<COMPONENT>>_CORE_H
#define <<COMPONENT>>_CORE_H

#include "clib_common.h"
<<perf_include>>
#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Caller-managed params for <<component>>.
 *
 * Layout is non-opaque — all fields are directly accessible.
 * Allocate with <<component>>_params_create() for heap (aligned),
 * or declare on the stack and call <<component>>_params_init().
 */
typedef struct {
<<params_struct_fields>>
} <<component>>_params_t;

/** @brief Heap-allocate and zero-initialise params. Returns NULL on OOM. */
<<component>>_params_t *<<component>>_params_create(void);

/** @brief Free heap-allocated params. Safe to call with NULL. */
void <<component>>_params_free(<<component>>_params_t *p);

/** @brief In-place zero-initialise (for stack / custom allocation). */
void <<component>>_params_init(<<component>>_params_t *p);

/**
 * @brief Process a single complex sample.
 * @param x       Input sample.
 * @param params  Caller-managed params (modified in place).
 * @return        Output sample.
 */
<<step_qualifier>><<return_ctype>>
<<component>>_fn(<<arg_ctype>> x, <<component>>_params_t *params)
{
    (void)x; (void)params;  /* TODO: implement */
    return (<<return_ctype>>)x;
}

/**
 * @brief Process a block of samples.
 * @param input   Input array  (length >= n).
 * @param output  Output array (length >= n; may alias input).
 * @param n       Number of samples.
 * @param params  Caller-managed params (modified in place).
 */
void <<component>>_steps(
    const <<arg_ctype>>    *input,
    <<return_ctype>>       *output,
    size_t                  n,
    <<component>>_params_t *params);

<<params_getter_setter_decls>>

#ifdef __cplusplus
}
#endif

#endif /* <<COMPONENT>>_CORE_H */
"""

PURE_STRUCT_CORE_C = """\
#include "<<component>>/<<component>>_core.h"

<<component>>_params_t *
<<component>>_params_create(void)
{
    /* calloc: zero-initialises all fields.
     * For SIMD alignment use aligned_alloc(64, sizeof(*p)) and memset. */
    <<component>>_params_t *p = calloc(1, sizeof(*p));
    if (!p) return NULL;
<<params_create_assigns>>
    return p;
}

void
<<component>>_params_free(<<component>>_params_t *p)
{
    free(p);
}

void
<<component>>_params_init(<<component>>_params_t *p)
{
    memset(p, 0, sizeof(*p));
<<params_reset_assigns>>
}

void
<<component>>_steps(
    const <<arg_ctype>>    *input,
    <<return_ctype>>       *output,
    size_t                  n,
    <<component>>_params_t *params)
{
    for (size_t i = 0; i < n; i++)
        output[i] = <<component>>_fn(input[i], params);
}

<<params_getter_setter_impls>>
"""

PURE_STRUCT_EXT_C = """\
/*
 * <<component>>_ext.c — Python C extension for <<component>>_core.h (pure/struct)
 *
 * Exposes <<Component>> — a thin Python wrapper over <<component>>_params_t.
 * The caller owns the params; multiple instances can share the same algorithm.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>

#include "<<component>>/<<component>>_core.h"

/* ======================================================== */
/* <<Component>>Object — wraps <<component>>_params_t *      */
/* ======================================================== */

typedef struct {
    PyObject_HEAD
    <<component>>_params_t *handle;
} <<Component>>Object;

static void
<<Component>>_dealloc(<<Component>>Object *self)
{
    if (self->handle)
        <<component>>_params_free(self->handle);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
<<Component>>_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    <<Component>>Object *self = (<<Component>>Object *)type->tp_alloc(type, 0);
    if (self) self->handle = NULL;
    return (PyObject *)self;
}

static int
<<Component>>_init(<<Component>>Object *self, PyObject *args, PyObject *kwds)
{
<<init_parse_block>>    self->handle = <<component>>_params_create();
    if (!self->handle) {
        PyErr_SetString(PyExc_MemoryError,
                        "<<component>>_params_create returned NULL");
        return -1;
    }
<<params_init_overrides>>    return 0;
}

static PyObject *
<<Component>>_call(<<Component>>Object *self, PyObject *args, PyObject *kwds)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
<<step_parse_block>>
    <<return_ctype>> y = <<component>>_fn(x, self->handle);
    return <<step_return_expr>>;
}

static PyObject *
<<Component>>_steps(<<Component>>Object *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    PyObject *in_obj = NULL;
    if (!PyArg_ParseTuple(args, "O", &in_obj))
        return NULL;

    PyArrayObject *in_arr = (PyArrayObject *)PyArray_FROM_OTF(
        in_obj, <<in_np_enum>>, NPY_ARRAY_C_CONTIGUOUS);
    if (!in_arr) return NULL;

    Py_ssize_t n = PyArray_SIZE(in_arr);
    npy_intp dims[] = {n};
    PyObject *out_arr = PyArray_SimpleNew(1, dims, <<out_np_enum>>);
    if (!out_arr) { Py_DECREF(in_arr); return NULL; }

    <<component>>_steps(
        (const <<arg_ctype>> *)PyArray_DATA(in_arr),
        (<<return_ctype>> *)PyArray_DATA((PyArrayObject *)out_arr),
        (size_t)n,
        self->handle);

    Py_DECREF(in_arr);
    return out_arr;
}

static PyObject *
<<Component>>_reset(<<Component>>Object *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    <<component>>_params_init(self->handle);
    Py_RETURN_NONE;
}

<<getter_setter_methods_c>>

static PyObject *
<<Component>>_destroy(<<Component>>Object *self, PyObject *Py_UNUSED(ignored))
{
    if (self->handle) {
        <<component>>_params_free(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *
<<Component>>_enter(<<Component>>Object *self, PyObject *Py_UNUSED(ignored))
{
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *
<<Component>>_exit(<<Component>>Object *self, PyObject *args)
{
    (void)args;
    if (self->handle) {
        <<component>>_params_free(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef <<Component>>_methods[] = {
    {"steps",     (PyCFunction)<<Component>>_steps,   METH_VARARGS,
     "Process a samples array. Returns an ndarray."},
    {"reset",     (PyCFunction)<<Component>>_reset,   METH_NOARGS,
     "Re-zero all params fields to post-create defaults."},
<<getter_setter_pymethoddef>>
    {"destroy",   (PyCFunction)<<Component>>_destroy,  METH_NOARGS,
     "Release resources."},
    {"__enter__", (PyCFunction)<<Component>>_enter,    METH_NOARGS,  NULL},
    {"__exit__",  (PyCFunction)<<Component>>_exit,     METH_VARARGS, NULL},
    {NULL}
};

static PyTypeObject <<Component>>Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "<<component>>.<<Component>>",
    .tp_basicsize = sizeof(<<Component>>Object),
    .tp_dealloc   = (destructor)<<Component>>_dealloc,
    .tp_call      = (ternaryfunc)<<Component>>_call,
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_doc       = "<<Component>> — caller-managed params wrapper.",
    .tp_methods   = <<Component>>_methods,
    .tp_new       = <<Component>>_new,
    .tp_init      = (initproc)<<Component>>_init,
};

static PyModuleDef <<component>>_module = {
    PyModuleDef_HEAD_INIT,
    .m_name    = "<<component>>",
    .m_doc     = "Pure (caller-managed) binding for <<component>>_core.h.",
    .m_size    = -1,
    .m_methods = NULL,
};

PyMODINIT_FUNC
PyInit_<<component>>(void)
{
    import_array();
    if (PyType_Ready(&<<Component>>Type) < 0) return NULL;

    PyObject *m = PyModule_Create(&<<component>>_module);
    if (!m) return NULL;

    Py_INCREF(&<<Component>>Type);
    if (PyModule_AddObject(m, "<<Component>>",
                           (PyObject *)&<<Component>>Type) < 0) {
        Py_DECREF(&<<Component>>Type);
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
"""

PURE_STRUCT_TEST_C = """\
#include "<<component>>/<<component>>_core.h"
#include <assert.h>
#include <complex.h>
#include <stdio.h>

int main(void)
{
    /* heap-allocated params */
    <<component>>_params_t *p = <<component>>_params_create();
    assert(p != NULL);

    /* fn: verify it runs */
    (void)<<component>>_fn(<<arg_zero>>, p);

    /* steps: verify it runs */
    <<arg_ctype>> in[4]  = <<test_arr_4_init>>;
    <<return_ctype>> out[4] = {0};
    <<component>>_steps(in, out, 4, p);

    /* stack allocation via init */
    <<component>>_params_t stack_p;
    <<component>>_params_init(&stack_p);
    (void)<<component>>_fn(<<arg_zero>>, &stack_p);

    <<component>>_params_free(p);
    printf("test_<<component>>_core PASSED\\n");
    return 0;
}
"""

PURE_STRUCT_PYI = """\
import numpy as np
from numpy.typing import NDArray

class <<Component>>:
    \"\"\"<<Component>> — caller-managed params wrapper.

    Wraps <<component>>_params_t.  The instance IS the params; pass it to
    <<component>>_fn / <<component>>_steps to process samples.

    Parameters
    ----------
<<pyi_param_docs>>
    \"\"\"

    def __init__(self, <<init_params_pyi>>) -> None: ...
    def __call__(self, x: <<in_py_hint>>) -> <<out_py_hint>>:
        \"\"\"Process one sample.\"\"\"
        ...
    def steps(self, arr: NDArray[<<in_np_dtype>>]) -> NDArray[<<out_np_dtype>>]:
        \"\"\"Process a samples array. Returns ndarray.\"\"\"
        ...
    def reset(self) -> None:
        \"\"\"Re-zero all params fields to post-create defaults.\"\"\"
        ...
<<getter_setter_stubs_pyi>>
    def destroy(self) -> None:
        \"\"\"Release resources.\"\"\"
        ...
    def __enter__(self) -> "<<Component>>": ...
    def __exit__(self, *args: object) -> None: ...
"""

PYTEST_PURE_STRUCT_TEST = """\
import numpy as np
import pytest

from <<package>> import <<Component>>


def _raises(exc, **kw):
    return pytest.raises(exc, **kw)


class Test<<Component>>:
    def test_create(self):
        obj = <<Component>>(<<py_create_args>>)
        assert obj is not None

    def test_call_runs(self):
        obj = <<Component>>(<<py_create_args>>)
        y = obj(<<in_py_test_val>>)
        assert isinstance(y, <<out_py_isinstance>>)

    def test_steps_runs(self):
        obj = <<Component>>(<<py_create_args>>)
        x = np.ones(16, dtype=<<in_np_dtype>>)
        y = obj.steps(x)
        assert y.shape == (16,)
        assert y.dtype == <<out_np_dtype>>

    def test_steps_sizes(self):
        obj = <<Component>>(<<py_create_args>>)
        for n in (1, 64, 1024):
            x = np.ones(n, dtype=<<in_np_dtype>>)
            assert obj.steps(x).shape == (n,)

    def test_reset(self):
        obj = <<Component>>(<<py_create_args>>)
        obj.reset()
        y = obj(<<in_py_test_val>>)
        assert isinstance(y, <<out_py_isinstance>>)

    def test_context_manager(self):
        with <<Component>>(<<py_create_args>>) as obj:
            y = obj.steps(np.ones(4, dtype=<<in_np_dtype>>))
        assert y.shape == (4,)

    def test_destroy(self):
        obj = <<Component>>(<<py_create_args>>)
        obj.destroy()
        with _raises(RuntimeError, match="destroyed"):
            obj(<<in_py_test_val>>)
"""

PURE_STRUCT_BENCH_C = """\
#include "<<component>>/<<component>>_core.h"
#include <complex.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define BENCH_N    65536
#define ITERATIONS 200

static double
elapsed_sec(struct timespec *t0, struct timespec *t1)
{
    return (double)(t1->tv_sec - t0->tv_sec)
           + (double)(t1->tv_nsec - t0->tv_nsec) * 1e-9;
}

int
main(void)
{
    <<arg_ctype>> *in  = malloc(BENCH_N * sizeof(<<arg_ctype>>));
    <<return_ctype>> *out = malloc(BENCH_N * sizeof(<<return_ctype>>));
    if (!in || !out) { fprintf(stderr, "OOM\\n"); return 1; }
    for (int i = 0; i < BENCH_N; i++) in[i] = <<bench_in_init>>;

    <<component>>_params_t *p = <<component>>_params_create();

    for (int i = 0; i < 16; i++) (void)<<component>>_fn(<<bench_warmup>>, p);

    struct timespec t0, t1;
    double sec;

    printf("=== <<component>> benchmark (pure/struct) ===\\n");
    printf("block = %d samples,  %d iterations\\n\\n", BENCH_N, ITERATIONS);

    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int r = 0; r < ITERATIONS; r++)
        for (int i = 0; i < BENCH_N; i++)
            (void)<<component>>_fn(in[i], p);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    sec = elapsed_sec(&t0, &t1);
    printf("  fn()     %8.1f MSa/s\\n",
           (double)ITERATIONS * BENCH_N / sec / 1e6);

    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int r = 0; r < ITERATIONS; r++)
        <<component>>_steps(in, out, BENCH_N, p);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    sec = elapsed_sec(&t0, &t1);
    printf("  steps()  %8.1f MSa/s\\n",
           (double)ITERATIONS * BENCH_N / sec / 1e6);

    <<component>>_params_free(p);
    free(in); free(out);
    return 0;
}
"""

PURE_STRUCT_BENCH_PY = """\
import numpy as np
import pytest

from <<package>> import <<Component>>


@pytest.fixture
def obj():
    return <<Component>>(<<py_create_args>>)


@pytest.mark.benchmark(group="<<component>>")
def test_bench_fn(benchmark, obj):
    benchmark(obj, <<in_py_test_val>>)


@pytest.mark.benchmark(group="<<component>>")
def test_bench_steps_1k(benchmark, obj):
    x = np.ones(1024, dtype=<<in_np_dtype>>)
    benchmark(obj.steps, x)


@pytest.mark.benchmark(group="<<component>>")
def test_bench_steps_64k(benchmark, obj):
    x = np.ones(65536, dtype=<<in_np_dtype>>)
    benchmark(obj.steps, x)
"""

# ── CMakeLists.txt ───────────────────────────────────────────────────────────

CMAKE_LISTS_TOP = """\
cmake_minimum_required(VERSION 3.16)
project(<<project_underscore>> VERSION <<version>> LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

option(ENABLE_SIMD "Enable SIMD flags (-march=native -ffast-math / /arch:AVX2 /fp:fast)" OFF)
if(ENABLE_SIMD)
    if(MSVC)
        add_compile_options(/arch:AVX2 /fp:fast)
    else()
        add_compile_options(-march=native -ffast-math)
    endif()
endif()

find_package(Python3 REQUIRED COMPONENTS Development NumPy)

set(PYTHON_PACKAGE_DIR "${CMAKE_SOURCE_DIR}/src/<<package>>")

# Combined C shared library — links all component OBJECT libraries.
# No Python dependency; distributable to C, C++, and Rust consumers.
add_library(<<project_underscore>>_lib SHARED "")
set_target_properties(<<project_underscore>>_lib PROPERTIES OUTPUT_NAME <<project_underscore>>)
target_include_directories(<<project_underscore>>_lib PUBLIC
    $<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/native/inc>
    $<INSTALL_INTERFACE:include>)

enable_testing()

# ── Components ───────────────────────────────────────────────────────────────
# just-makeit init appends: add_subdirectory + target_link_libraries per component

# ── Install ──────────────────────────────────────────────────────────────────

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

install(TARGETS <<project_underscore>>_lib
    EXPORT <<project_underscore>>-targets
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})

install(DIRECTORY ${CMAKE_SOURCE_DIR}/native/inc/
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
    FILES_MATCHING PATTERN "*.h"
    PATTERN "pyex_common.h" EXCLUDE)

install(EXPORT <<project_underscore>>-targets
    FILE <<project_underscore>>-config.cmake
    NAMESPACE <<project_underscore>>::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/<<project_underscore>>)

write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config-version.cmake"
    VERSION ${PROJECT_VERSION}
    COMPATIBILITY SameMajorVersion)
install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/<<project_underscore>>-config-version.cmake"
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/<<project_underscore>>)

configure_file(cmake/<<project>>.pc.in <<project>>.pc @ONLY)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/<<project>>.pc"
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/pkgconfig)
"""

CMAKE_LISTS_COMPONENT = """\
# OBJECT library — pure C core, no Python dependency.
# Linked into both the Python DSO and the combined libmy_dsp.so.
add_library(<<component>>_core OBJECT <<component>>_core.c)
target_include_directories(<<component>>_core PUBLIC
    ${CMAKE_SOURCE_DIR}/native/inc
    ${CMAKE_SOURCE_DIR}/native/inc/<<component>>)

Python3_add_library(<<component>> MODULE WITH_SOABI <<component>>_ext.c)
target_link_libraries(<<component>> PRIVATE
    <<component>>_core
    Python3::NumPy)
target_include_directories(<<component>> PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
set_target_properties(<<component>> PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${PYTHON_PACKAGE_DIR}")

add_executable(test_<<component>>_core
    ${CMAKE_SOURCE_DIR}/native/tests/test_<<component>>_core.c)
target_link_libraries(test_<<component>>_core PRIVATE <<component>>_core)
target_include_directories(test_<<component>>_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
add_test(NAME test_<<component>>_core COMMAND test_<<component>>_core)

add_executable(bench_<<component>>_core
    ${CMAKE_SOURCE_DIR}/native/benchmarks/bench_<<component>>_core.c)
target_link_libraries(bench_<<component>>_core PRIVATE <<component>>_core)
target_include_directories(bench_<<component>>_core
    PRIVATE ${CMAKE_SOURCE_DIR}/native/inc)
"""

CMAKE_PC_IN = """\
prefix=@CMAKE_INSTALL_PREFIX@
exec_prefix=${prefix}
libdir=@CMAKE_INSTALL_FULL_LIBDIR@
includedir=@CMAKE_INSTALL_FULL_INCLUDEDIR@

Name: <<project>>
Description: <<project>> C library
Version: @PROJECT_VERSION@
Libs: -L${libdir} -l<<project_underscore>>
Cflags: -I${includedir}
"""

UMBRELLA_H = """\
/**
 * <<package>>.h — umbrella header for the <<package>> C library.
 *
 * Include this header to access all component APIs, or include
 * individual component headers directly.
 *
 * Generated by just-makeit; updated on each `just-makeit init`.
 */
#ifndef <<PACKAGE>>_H
#define <<PACKAGE>>_H

#ifdef __cplusplus
extern "C" {
#endif

/* ── Components ─────────────────────────────────────────────────────────── */

#ifdef __cplusplus
}
#endif

#endif /* <<PACKAGE>>_H */
"""

# ── Makefile (basic — no CMake) ──────────────────────────────────────────────

MAKEFILE_SIMPLE = """\
# <<project>> Makefile  (--basic build, no CMake)
#
# Targets:
#   make             Build extension(s)
#   make test        C tests + pytest
#   make just-build  PEP 517 hook for just-buildit
#   make clean       Remove build artifacts
#   make help        Show this message

SHELL  := /bin/sh
PYTHON ?= $(or $(JUST_BUILDIT_PYTHON),python3)
CC     ?= cc
CFLAGS ?= -O2 -fPIC -std=c99 -Wall

PY_INC := $(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_path('include'))")
EXT    := $(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
INC     = -I$(PY_INC) -Inative/inc

TARGETS :=
C_TESTS :=

.PHONY: all test just-build clean help

all: $(TARGETS)

# ── Component rules ───────────────────────────────────────────────────────────

# ── Fixed targets ─────────────────────────────────────────────────────────────

test: all $(C_TESTS)
\t@for t in $(C_TESTS); do echo "--- $$t ---" && ./$$t || exit 1; done
\t$(PYTHON) -m pytest src/ -v 2>/dev/null || \\
\t\t$(PYTHON) -m unittest discover -s src/<<package>>/tests -t src -p "test_*.py" -v

just-build: all
\tmkdir -p $(JUST_BUILDIT_OUTPUT_DIR)
\tcp -r src/<<package>> $(JUST_BUILDIT_OUTPUT_DIR)/<<package>>

clean:
\trm -f $(TARGETS) $(C_TESTS)
\tfind src -name "*.so" -o -name "*.pyd" | xargs rm -f 2>/dev/null; true

help:
\t@echo ""
\t@echo "<<project>> build targets"
\t@echo ""
\t@echo "  make          Build extension(s)"
\t@echo "  make test     Run C tests + pytest"
\t@echo "  make clean    Remove build artifacts"
\t@echo ""
"""

MAKEFILE_SIMPLE_COMPONENT = """\
src/<<package>>/<<component>>$(EXT): native/src/<<component>>/<<component>>_core.c native/src/<<component>>/<<component>>_ext.c
\t@$(PYTHON) -c "import numpy" 2>/dev/null || $(PYTHON) -m pip install numpy
\t$(CC) $(CFLAGS) $(INC) -I$$($(PYTHON) -c "import numpy; print(numpy.get_include())") -shared $^ -o $@

test_<<component>>_core: native/tests/test_<<component>>_core.c native/src/<<component>>/<<component>>_core.c
\t$(CC) -O2 -std=c99 -Inative/inc $^ -o $@

"""

# ── Makefile (CMake wrapper) ──────────────────────────────────────────────────

MAKEFILE = """\
# <<project>> project Makefile
#
# Targets:
#   make              Configure + build (Release)
#   make test         CTest + pytest
#   make bench        C + Python benchmarks (output only)
#   make bench-save   Save benchmark baseline (tagged with git describe)
#   make bench-compare  Compare against last saved baseline
#   make just-build   PEP 517 hook for just-buildit
#   make clean        Remove build artifacts
#   make help         Show this message

SHELL      = /bin/sh
BUILD_DIR  ?= build
BUILD_TYPE ?= Release
NPROC      ?= $(shell nproc 2>/dev/null || echo 4)
PYTHON     ?= $(or $(JUST_BUILDIT_PYTHON),$(shell which python3))
BENCH_TAG  ?= $(shell git describe --tags --dirty 2>/dev/null || date +%Y%m%d)

.PHONY: all build test bench bench-save bench-compare just-build clean help

all: build

$(BUILD_DIR)/CMakeCache.txt:
\t@$(PYTHON) -c "import numpy" 2>/dev/null || $(PYTHON) -m pip install numpy
\tcmake -B $(BUILD_DIR) -S . \\
\t\t-DCMAKE_BUILD_TYPE=$(BUILD_TYPE) \\
\t\t-DPython3_EXECUTABLE=$(PYTHON) \\
\t\t-DCMAKE_EXPORT_COMPILE_COMMANDS=ON

compile_commands.json: $(BUILD_DIR)/CMakeCache.txt
\tcp $(BUILD_DIR)/compile_commands.json $@

build: $(BUILD_DIR)/CMakeCache.txt
\tcmake --build $(BUILD_DIR) --parallel $(NPROC)

test: build
\tctest --test-dir $(BUILD_DIR) --output-on-failure
\t$(PYTHON) -m pytest src/ -v 2>/dev/null || \
\t\t$(PYTHON) -m unittest discover -s src/<<package>>/tests -t src -p "test_*.py" -v

bench: build
\t@for b in $(BUILD_DIR)/bench_*_core; do [ -x "$$b" ] && echo "--- $$b ---" && "$$b" && echo; done
\t$(PYTHON) -m pytest src/<<package>>/benchmarks/ -v --benchmark-disable-gc \\
\t\t2>/dev/null || echo "(hint: pip install pytest-benchmark)"

bench-save: build
\t$(PYTHON) -m pytest src/<<package>>/benchmarks/ \\
\t\t--benchmark-save=$(BENCH_TAG) --benchmark-disable-gc

bench-compare: build
\t$(PYTHON) -m pytest src/<<package>>/benchmarks/ \\
\t\t--benchmark-compare --benchmark-disable-gc

just-build: build
\tmkdir -p $(JUST_BUILDIT_OUTPUT_DIR)
\tcp -r src/<<package>> $(JUST_BUILDIT_OUTPUT_DIR)/<<package>>

clean:
\trm -rf $(BUILD_DIR)
\tfind src -name "*.so" -o -name "*.pyd" | xargs rm -f 2>/dev/null; true

help:
\t@echo ""
\t@echo "<<project>> build targets"
\t@echo ""
\t@echo "  make               Configure + build"
\t@echo "  make test          Run CTest + pytest"
\t@echo "  make bench         Run C + Python benchmarks"
\t@echo "  make bench-save    Save baseline (git describe tag)"
\t@echo "  make bench-compare Compare against last saved baseline"
\t@echo "  make clean         Remove build artifacts"
\t@echo ""
"""

# ── pyproject.toml ───────────────────────────────────────────────────────────

PYPROJECT_TOML = """\
[build-system]
requires = ["just-buildit", "numpy"]
build-backend = "just_buildit"

[project]
name = "<<project>>"
version = "<<version>>"
description = "TODO: describe your project."
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "numpy",
    "pytest-benchmark",
]

[tool.just-buildit]
command = "make just-build"

[tool.pytest.ini_options]
testpaths = ["src"]
markers = [
    "benchmark: mark test as a benchmark (requires pytest-benchmark)",
]
"""

# ── Python package ───────────────────────────────────────────────────────────

PACKAGE_INIT_PY_MINIMAL = """\
\"\"\"<<package>> package.\"\"\"
"""

PACKAGE_INIT_PY = """\
\"\"\"<<package>> — <<Component>> component.\"\"\"

from .<<component>> import <<Component>>

__all__ = ["<<Component>>"]
"""

PACKAGE_INIT_PY_PURE_SCALAR = """\
\"\"\"<<package>> — <<component>> pure-function component.\"\"\"

from .<<component>> import <<component>>, <<component>>_steps

<<component>>.steps = <<component>>_steps

__all__ = ["<<component>>"]
"""

COMPONENT_PYI = """\
import numpy as np
from numpy.typing import NDArray

class <<Component>>:
    \"\"\"<<Component>> component.

    Parameters
    ----------
<<pyi_param_docs>>
    \"\"\"

    def __init__(self, <<init_params_pyi>>) -> None: ...
    def reset(self) -> None:
        \"\"\"Reset state to post-create defaults.\"\"\"
    def step(self, x: <<in_py_hint>>) -> <<out_py_hint>>:
        \"\"\"Process one sample.\"\"\"
    def steps(self, x: NDArray[<<in_np_dtype>>]) -> NDArray[<<out_np_dtype>>]:
        \"\"\"Process a samples array. Returns ndarray.\"\"\"
<<getter_setter_stubs_pyi>>
    def destroy(self) -> None:
        \"\"\"Release C resources immediately.\"\"\"
    def __enter__(self) -> "<<Component>>": ...
    def __exit__(self, *args: object) -> None: ...
"""

# ── tests package init ───────────────────────────────────────────────────────

TESTS_INIT_PY = ""

# ── pytest test ──────────────────────────────────────────────────────────────

PYTEST_TEST = """\
import unittest
import numpy as np
from <<package>> import <<Component>>

# ---------------------------------------------------------------------------
# pytest compatibility shim — tests run under both pytest and unittest discover
# ---------------------------------------------------------------------------
try:
    import pytest as _pytest

    _approx = _pytest.approx
    _raises = _pytest.raises
except ImportError:
    import contextlib, math

    class _Approx:
        def __init__(self, expected, rel=1e-6):
            self._exp = expected
            self._tol = rel * (abs(expected) if expected else 1e-12)

        def __eq__(self, other):
            import cmath
            return cmath.isclose(complex(other), complex(self._exp),
                                 rel_tol=1e-6, abs_tol=1e-12)

        def __repr__(self):
            return f"approx({self._exp!r})"

    @contextlib.contextmanager
    def _raises(exc_type, match=None):
        import re
        try:
            yield
        except exc_type as e:
            if match and not re.search(match, str(e)):
                raise AssertionError(
                    f"Exception message {str(e)!r} did not match {match!r}"
                ) from e
        else:
            raise AssertionError(f"{exc_type.__name__} was not raised")

    _approx = _Approx
# ---------------------------------------------------------------------------


class Test<<Component>>(unittest.TestCase):
    def test_create(self):
        obj = <<Component>>(<<py_create_args>>)
        self.assertIsNotNone(obj)

    def test_step_runs(self):
        obj = <<Component>>(<<py_create_args>>)
        y = obj.step(<<in_py_test_val>>)
        assert isinstance(y, <<out_py_isinstance>>)

    def test_steps_shape_dtype(self):
        obj = <<Component>>(<<py_create_args>>)
        x = np.ones(64, dtype=<<in_np_dtype>>)
        y = obj.steps(x)
        self.assertEqual(y.shape, (64,))
        self.assertEqual(y.dtype, <<out_np_dtype>>)

    def test_getter_setter(self):
<<getter_setter_test_py>>

    def test_reset(self):
<<reset_test_py>>

    def test_context_manager(self):
        with <<Component>>(<<py_create_args>>) as obj:
            y = obj.step(<<in_py_test_val>>)
        assert isinstance(y, <<out_py_isinstance>>)

    def test_destroy(self):
        obj = <<Component>>(<<py_create_args>>)
        obj.destroy()
        with _raises(RuntimeError, match="destroyed"):
            obj.step(<<in_py_test_val>>)
"""

# ── .gitignore ───────────────────────────────────────────────────────────────

GITIGNORE = """\
build/
dist/
*.egg-info/
__pycache__/
*.pyc
*.so
*.pyd
.venv/
compile_commands.json
"""

# ── README.md ────────────────────────────────────────────────────────────────

README_MD = """\
# <<project>>

TODO: describe your project.

## Quickstart

Install and build in one step (recommended):

```bash
pip install -e .
```

## Development build

```bash
make                     # cmake configure + build (installs numpy if needed)
make test                # CTest + pytest
```

## Package

```bash
pip install just-buildit
just-makeit build        # wheel → dist/
```
"""
