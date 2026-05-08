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

/* x86 SIMD intrinsics (SSE through AVX-512) */
#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__) || defined(_M_IX86)
#  include <immintrin.h>
#endif

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
#ifdef __AVX512F__
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
#  define _JM_STEPS_SIMD_(fn, st, samp, LENGTH, BATCH, CHUNK)  /* no SIMD */
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
 * float complex y = <<component>>_step(obj, 1.0f + 0.0f * I);
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
<<step_qualifier>> float complex
<<component>>_step(const <<component>>_state_t *state, float complex x)
{
    (void)state; /* TODO: implement DSP using state variables */
    return x;
}

/**
 * @brief Process a block of complex samples.
 *
 * @param state   Component state.
 * @param input   Input array (length >= n).
 * @param output  Output array (length >= n; may alias input for in-place).
 * @param n       Number of samples.
 * @note Output buffer must be pre-allocated by caller.
 */
void <<component>>_steps(
    <<component>>_state_t *state,
    const float complex    *input,
    float complex          *output,
    size_t                  n);

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
    const float complex    *input,
    float complex          *output,
    size_t                  n)
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
    Py_complex pyx;
    if (!PyArg_ParseTuple(args, "D", &pyx))
        return NULL;

    float complex x = (float)pyx.real + (float)pyx.imag * I;
    float complex y = <<component>>_step(self->handle, x);
    return PyComplex_FromDoubles((double)crealf(y), (double)cimagf(y));
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
        in_obj, NPY_COMPLEX64, NPY_ARRAY_C_CONTIGUOUS);
    if (!in_arr)
        return NULL;

    Py_ssize_t n = PyArray_SIZE(in_arr);
    npy_intp dims[] = {n};
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_COMPLEX64);
    if (!out_arr) {
        Py_DECREF(in_arr);
        return NULL;
    }

    <<component>>_steps(
        self->handle,
        (const float complex *)PyArray_DATA(in_arr),
        (float complex *)PyArray_DATA((PyArrayObject *)out_arr),
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
     "Process one complex sample. Returns complex."},
    {"steps",    (PyCFunction)<<Component>>_steps,    METH_VARARGS,
     "Process a complex64 ndarray. Returns complex64 ndarray."},
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
    (void)<<component>>_step(obj, 1.0f + 0.0f * I);

<<getter_setter_test_c>>

<<reset_test_c>>

    <<component>>_destroy(obj);
    printf("test_<<component>>_core PASSED\\n");
    return 0;
}
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

enable_testing()

# ── Components ───────────────────────────────────────────────────────────────
# Added by: just-makeit init <component>
"""

CMAKE_LISTS_COMPONENT = """\
add_library(<<component>>_core STATIC <<component>>_core.c)
target_include_directories(<<component>>_core PUBLIC
    ${CMAKE_SOURCE_DIR}/native/inc)

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
#   make             Configure + build (Release)
#   make test        CTest + pytest
#   make just-build  PEP 517 hook for just-buildit
#   make clean       Remove build artifacts
#   make help        Show this message

SHELL      = /bin/sh
BUILD_DIR  ?= build
BUILD_TYPE ?= Release
NPROC      ?= $(shell nproc 2>/dev/null || echo 4)
PYTHON     ?= $(or $(JUST_BUILDIT_PYTHON),$(shell which python3))

.PHONY: all build test just-build clean help

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
\t@echo "  make          Configure + build"
\t@echo "  make test     Run CTest + pytest"
\t@echo "  make clean    Remove build artifacts"
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
]

[tool.just-buildit]
command = "make just-build"

[tool.pytest.ini_options]
testpaths = ["src"]
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
    def step(self, x: complex) -> complex:
        \"\"\"Process one complex sample.\"\"\"
    def steps(self, x: NDArray[np.complex64]) -> NDArray[np.complex64]:
        \"\"\"Process a complex64 ndarray, return complex64 ndarray.\"\"\"
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
        y = obj.step(3.0 + 4.0j)
        assert isinstance(y, complex)

    def test_steps_shape_dtype(self):
        obj = <<Component>>(<<py_create_args>>)
        x = np.ones(64, dtype=np.complex64)
        y = obj.steps(x)
        self.assertEqual(y.shape, (64,))
        self.assertEqual(y.dtype, np.complex64)

    def test_getter_setter(self):
<<getter_setter_test_py>>

    def test_reset(self):
<<reset_test_py>>

    def test_context_manager(self):
        with <<Component>>(<<py_create_args>>) as obj:
            y = obj.step(1.0 + 1.0j)
        assert isinstance(y, complex)

    def test_destroy(self):
        obj = <<Component>>(<<py_create_args>>)
        obj.destroy()
        with _raises(RuntimeError, match="destroyed"):
            obj.step(1.0 + 0.0j)
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
