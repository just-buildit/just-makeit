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

_CTYPE_META: dict[str, dict] = {
    "double": {
        "fmt": "d",
        "zero": "0.0",
        "py_type": "float",
        "to_py": lambda v: f"PyFloat_FromDouble({v})",
    },
    "float": {
        "fmt": "f",
        "zero": "0.0f",
        "py_type": "float",
        "to_py": lambda v: f"PyFloat_FromDouble((double){v})",
    },
    "int": {
        "fmt": "i",
        "zero": "0",
        "py_type": "int",
        "to_py": lambda v: f"PyLong_FromLong((long){v})",
    },
}

SUPPORTED_TYPES: frozenset[str] = frozenset(_CTYPE_META)


def _c_set_val(ctype: str) -> str:
    return "2.0f" if ctype == "float" else ("2" if ctype == "int" else "2.0")


def _py_default(ctype: str, default: str) -> str:
    """Convert a C default literal to a valid Python literal."""
    if ctype in ("double", "float"):
        s = default.rstrip("fF")
        if "." not in s and "e" not in s.lower():
            s += ".0"
        return s
    return default


def make_state_ctx(
    component: str,
    Component: str,
    state_vars: list[tuple[str, str, str]],
) -> dict[str, str]:
    """Return template context keys derived from the state variable list.

    Each entry in state_vars is (name, ctype, default), where default is a
    C literal used for both reset and as the Python __init__ default value.
    """
    for name, ct, _ in state_vars:
        if ct not in _CTYPE_META:
            supported = ", ".join(sorted(SUPPORTED_TYPES))
            raise ValueError(
                f"unsupported type '{ct}' for '{name}'. Supported: {supported}"
            )

    # ── CORE_H ───────────────────────────────────────────────────────────────

    state_struct_fields = "\n".join(f"    {ct} {name};" for name, ct, _ in state_vars)

    create_params = ", ".join(f"{ct} {name}" for name, ct, _ in state_vars)

    create_param_docs = "\n".join(
        f" * @param {name}  Initial {name} (default: {dflt})."
        for name, _, dflt in state_vars
    )

    decl_parts = []
    for name, ct, _ in state_vars:
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
    getter_setter_decls = "\n\n".join(decl_parts)

    # ── CORE_C ───────────────────────────────────────────────────────────────

    create_assignments = "\n".join(
        f"    state->{name} = {name};" for name, _, __ in state_vars
    )

    reset_assignments = "\n".join(
        f"    state->{name} = {dflt};" for name, _, dflt in state_vars
    )

    impl_parts = []
    for name, ct, _ in state_vars:
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
    getter_setter_impls = "\n\n".join(impl_parts)

    # ── EXT_C ────────────────────────────────────────────────────────────────

    kwlist_items = [f'"{name}"' for name, _, __ in state_vars] + ["NULL"]
    init_kwlist = ", ".join(kwlist_items)

    # Use user defaults; "|" prefix makes all params optional in Python
    init_locals = "\n".join(
        f"    {ct} {name} = {dflt};" for name, ct, dflt in state_vars
    )
    init_parse_fmt = "|" + "".join(_CTYPE_META[ct]["fmt"] for _, ct, __ in state_vars)

    init_parse_args = ", ".join(f"&{name}" for name, _, __ in state_vars)
    create_call_args = ", ".join(name for name, _, __ in state_vars)

    method_parts = []
    for name, ct, _ in state_vars:
        meta = _CTYPE_META[ct]
        to_py = meta["to_py"](f"{component}_get_{name}(self->handle)")
        method_parts.append(
            f"static PyObject *\n"
            f"{Component}_get_{name}(\n"
            f"    {Component}Object *self, PyObject *Py_UNUSED(ignored))\n"
            f"{{\n"
            f"    if (!self->handle) {{\n"
            f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
            f"        return NULL;\n"
            f"    }}\n"
            f"    return {to_py};\n"
            f"}}\n"
            f"\n"
            f"static PyObject *\n"
            f"{Component}_set_{name}(\n"
            f"    {Component}Object *self, PyObject *args)\n"
            f"{{\n"
            f"    if (!self->handle) {{\n"
            f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
            f"        return NULL;\n"
            f"    }}\n"
            f"    {ct} v = {meta['zero']};\n"
            f'    if (!PyArg_ParseTuple(args, "{meta["fmt"]}", &v))\n'
            f"        return NULL;\n"
            f"    {component}_set_{name}(self->handle, v);\n"
            f"    Py_RETURN_NONE;\n"
            f"}}"
        )
    getter_setter_methods_c = "\n\n".join(method_parts)

    pmd_lines = []
    for name, _, __ in state_vars:
        pmd_lines += [
            f'    {{"get_{name}",',
            f"     (PyCFunction){Component}_get_{name}, METH_NOARGS,",
            f'     "Get {name}."}},',
            f'    {{"set_{name}",',
            f"     (PyCFunction){Component}_set_{name}, METH_VARARGS,",
            f'     "Set {name}."}},',
        ]
    getter_setter_pymethoddef = "\n".join(pmd_lines)

    # ── PYI ──────────────────────────────────────────────────────────────────

    init_params_pyi = ", ".join(
        f"{name}: {_CTYPE_META[ct]['py_type']} = {_py_default(ct, dflt)}"
        for name, ct, dflt in state_vars
    )

    pyi_param_docs = "\n".join(
        f"    {name} : {_CTYPE_META[ct]['py_type']}, default {_py_default(ct, dflt)}\n"
        f"        {name} state variable."
        for name, ct, dflt in state_vars
    )

    stub_lines: list[str] = []
    for name, ct, _ in state_vars:
        py_type = _CTYPE_META[ct]["py_type"]
        stub_lines += [
            f"    def get_{name}(self) -> {py_type}:",
            f'        """Return current {name}."""',
            f"    def set_{name}(self, value: {py_type}) -> None:",
            f'        """Set {name}."""',
        ]
    getter_setter_stubs_pyi = "\n".join(stub_lines)

    # ── Shared ───────────────────────────────────────────────────────────────

    py_create_args = ", ".join(_py_default(ct, dflt) for _, ct, dflt in state_vars)
    c_create_args = ", ".join(dflt for _, _, dflt in state_vars)

    # ── PYTEST ───────────────────────────────────────────────────────────────

    gs_lines = [f"        obj = {Component}({py_create_args})"]
    for name, ct, dflt in state_vars:
        py_type = _CTYPE_META[ct]["py_type"]
        iv = _py_default(ct, dflt)
        sv = "2" if py_type == "int" else "2.0"
        if py_type == "int":
            gs_lines += [
                f"        assert obj.get_{name}() == {iv}",
                f"        obj.set_{name}({sv})",
                f"        assert obj.get_{name}() == {sv}",
            ]
        else:
            gs_lines += [
                f"        assert obj.get_{name}() == _approx({iv})",
                f"        obj.set_{name}({sv})",
                f"        assert obj.get_{name}() == _approx({sv})",
            ]
    getter_setter_test_py = "\n".join(gs_lines)

    rs_lines = [f"        obj = {Component}({py_create_args})"]
    for name, ct, _ in state_vars:
        sv = "2" if _CTYPE_META[ct]["py_type"] == "int" else "2.0"
        rs_lines.append(f"        obj.set_{name}({sv})")
    rs_lines.append("        obj.reset()")
    for name, ct, dflt in state_vars:
        py_type = _CTYPE_META[ct]["py_type"]
        iv = _py_default(ct, dflt)
        if py_type == "int":
            rs_lines.append(f"        assert obj.get_{name}() == {iv}")
        else:
            rs_lines.append(f"        assert obj.get_{name}() == _approx({iv})")
    reset_test_py = "\n".join(rs_lines)

    # ── CTEST ────────────────────────────────────────────────────────────────

    cgs_lines: list[str] = []
    for name, ct, dflt in state_vars:
        sv = _c_set_val(ct)
        cgs_lines += [
            f"    /* {name}: getter / setter */",
            f"    assert({component}_get_{name}(obj) == {dflt});",
            f"    {component}_set_{name}(obj, {sv});",
            f"    assert({component}_get_{name}(obj) == {sv});",
            "",
        ]
    getter_setter_test_c = "\n".join(cgs_lines).rstrip()

    rst_lines = ["    /* reset restores defaults */"]
    for name, ct, _ in state_vars:
        rst_lines.append(f"    {component}_set_{name}(obj, {_c_set_val(ct)});")
    rst_lines.append(f"    {component}_reset(obj);")
    for name, _, dflt in state_vars:
        rst_lines.append(f"    assert({component}_get_{name}(obj) == {dflt});")
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
        "init_parse_fmt": init_parse_fmt,
        "init_parse_args": init_parse_args,
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
#pragma once

#include <complex.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
"""

PYEX_COMMON_H = """\
/**
 * pyex_common.h — common Python extension includes for <<package>>.
 */
#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
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
#pragma once

#include "clib_common.h"

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
static inline float complex
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
    for (size_t i = 0; i < n; i++)
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
    static char *kwlist[] = {<<init_kwlist>>};
<<init_locals>>

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "<<init_parse_fmt>>", kwlist,
                                     <<init_parse_args>>))
        return -1;

    self->handle = <<component>>_create(<<create_call_args>>);
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

    /* step: pass-through */
    float complex y = <<component>>_step(obj, 1.0f + 0.0f * I);
    assert(crealf(y) == 1.0f);
    assert(cimagf(y) == 0.0f);

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

# ── Makefile ─────────────────────────────────────────────────────────────────

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
\t@$(PYTHON) -c "import numpy" 2>/dev/null || \
\t\t{ echo "error: numpy not found. Run: pip install numpy"; exit 1; }
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
\t$(PYTHON) -m pytest src/ -v

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
\"\"\"<<package>> — <<Component>> component.

Classes
-------
<<Component>>
    Core <<component>> processor.

Examples
--------
>>> from <<package>> import <<Component>>
>>> obj = <<Component>>(<<py_create_args>>)
>>> result = obj.step(1.0 + 0.0j)
>>> abs(result - (1.0 + 0.0j)) < 1e-6
True
\"\"\"

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
            return math.isclose(other, self._exp, rel_tol=1e-6, abs_tol=1e-12)

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

    def test_step_passthrough(self):
        obj = <<Component>>(<<py_create_args>>)
        y = obj.step(3.0 + 4.0j)
        assert abs(y - (3.0 + 4.0j)) < 1e-6

    def test_steps_shape_dtype(self):
        obj = <<Component>>(<<py_create_args>>)
        x = np.ones(64, dtype=np.complex64)
        y = obj.steps(x)
        self.assertEqual(y.shape, (64,))
        self.assertEqual(y.dtype, np.complex64)

    def test_steps_passthrough(self):
        obj = <<Component>>(<<py_create_args>>)
        x = np.ones(8, dtype=np.complex64)
        y = obj.steps(x)
        np.testing.assert_allclose(y.real, 1.0, rtol=1e-5)
        np.testing.assert_allclose(y.imag, 0.0, atol=1e-6)

    def test_getter_setter(self):
<<getter_setter_test_py>>

    def test_reset(self):
<<reset_test_py>>

    def test_context_manager(self):
        with <<Component>>(<<py_create_args>>) as obj:
            y = obj.step(1.0 + 1.0j)
        assert abs(y - (1.0 + 1.0j)) < 1e-6

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
pip install numpy        # required by the C extensions
make                     # cmake configure + build
make test                # CTest + pytest
```

## Package

```bash
pip install just-buildit
just-makeit build        # wheel → dist/
```
"""
