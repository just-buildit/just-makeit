/*
 * /*<<component>>*/_ext.c — Python C extension for /*<<component>>*/
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>

#include "/*<<component>>*///*<<component>>*/_core.h"

/* ======================================================== */
/* /*<<Component>>*/Object — wraps /*<<component>>*/_state_t *       */
/* ======================================================== */

typedef struct {
    PyObject_HEAD
    /*<<component>>*/_state_t *handle;
/*<<extra_buf_fields>>*/} /*<<Component>>*/Object;

static void
/*<<ComponentW>>*/_dealloc(/*<<Component>>*/Object *self)
{
    if (self->handle)
        /*<<component>>*/_destroy(self->handle);
/*<<extra_buf_free>>*/    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
/*<<ComponentW>>*/_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    /*<<Component>>*/Object *self = (/*<<Component>>*/Object *)type->tp_alloc(type, 0);
    if (self)
        self->handle = NULL;
    return (PyObject *)self;
}

static int
/*<<ComponentW>>*/_init(/*<<Component>>*/Object *self, PyObject *args, PyObject *kwds)
{
/*<<init_parse_block>>*//*<<array_args_parse_block>>*//*<<create_line>>*//*<<array_args_decref>>*/    if (!self->handle) {
        PyErr_SetString(PyExc_MemoryError,
                        "/*<<component>>*/_create returned NULL");
        return -1;
    }
/*<<extra_buf_alloc>>*//*<<init_warn_block>>*/    return 0;
}

/*<<builtin_reset_c>>*/

/*<<step_ext_fn>>*/

/*<<steps_ext_fn>>*/

/*<<getter_setter_methods_c>>*/
/*<<extra_methods_c>>*/
/*<<getset_def>>*/
static PyObject *
/*<<ComponentW>>*/_destroy(/*<<Component>>*/Object *self, PyObject *Py_UNUSED(ignored))
{
    if (self->handle) {
        /*<<component>>*/_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *
/*<<ComponentW>>*/_enter(/*<<Component>>*/Object *self, PyObject *Py_UNUSED(ignored))
{
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *
/*<<ComponentW>>*/_exit(/*<<Component>>*/Object *self, PyObject *args)
{
    (void)args;
    if (self->handle) {
        /*<<component>>*/_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

/*<<stream_iter_block>>*/static PyMethodDef /*<<ComponentW>>*/_methods[] = {
/*<<builtin_reset_pmd>>*//*<<step_pymethoddef_entry>>*//*<<steps_def_entry>>*/
/*<<getter_setter_pymethoddef>>*//*<<extra_methods_pymethoddef>>*//*<<stream_def_entry>>*/    {"destroy",  (PyCFunction)/*<<ComponentW>>*/_destroy,  METH_NOARGS,
     "Release resources."},
    {"__enter__", (PyCFunction)/*<<ComponentW>>*/_enter,   METH_NOARGS,  NULL},
    {"__exit__",  (PyCFunction)/*<<ComponentW>>*/_exit,    METH_VARARGS, NULL},
    {NULL}
};

static PyTypeObject /*<<ComponentW>>*/Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "/*<<component>>*/./*<<Component>>*/",
    .tp_basicsize = sizeof(/*<<Component>>*/Object),
    .tp_dealloc   = (destructor)/*<<ComponentW>>*/_dealloc,
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_doc       = "/*<<Component>>*/ component. Wraps /*<<component>>*/_state_t.",
    .tp_methods   = /*<<ComponentW>>*/_methods,/*<<tp_getset_decl>>*//*<<stream_tp_iter>>*//*<<stream_tp_async>>*/
    .tp_new       = /*<<ComponentW>>*/_new,
    .tp_init      = (initproc)/*<<ComponentW>>*/_init,
};

/* ======================================================== */
/* Module definition                                         */
/* ======================================================== */

static PyModuleDef /*<<component>>*/_module = {
    PyModuleDef_HEAD_INIT,
    .m_name    = "/*<<component>>*/",
    .m_doc     = "Python binding for /*<<component>>*/_core.h.",
    .m_size    = -1,
    .m_methods = NULL,
};

PyMODINIT_FUNC
PyInit_/*<<component>>*/(void)
{
    import_array();
    if (PyType_Ready(&/*<<ComponentW>>*/Type) < 0)
        return NULL;/*<<stream_type_ready>>*/

    PyObject *m = PyModule_Create(&/*<<component>>*/_module);
    if (!m)
        return NULL;

    Py_INCREF(&/*<<ComponentW>>*/Type);
    if (PyModule_AddObject(m, "/*<<Component>>*/",
                           (PyObject *)&/*<<ComponentW>>*/Type) < 0) {
        Py_DECREF(&/*<<ComponentW>>*/Type);
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
