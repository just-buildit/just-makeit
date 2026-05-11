/*
 * engine_ext.c — Python C extension for engine_core.h
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>

#include "engine/engine_core.h"

/* ======================================================== */
/* EngineObject — wraps engine_state_t *       */
/* ======================================================== */

typedef struct {
    PyObject_HEAD
    engine_state_t *handle;
} EngineObject;

static void
Engine_dealloc(EngineObject *self)
{
    if (self->handle)
        engine_destroy(self->handle);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
Engine_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    EngineObject *self = (EngineObject *)type->tp_alloc(type, 0);
    if (self)
        self->handle = NULL;
    return (PyObject *)self;
}

static int
Engine_init(EngineObject *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"gain", NULL};
    double gain = 1.0;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|d", kwlist,
                                     &gain))
        return -1;
    self->handle = engine_create(gain);
    if (!self->handle) {
        PyErr_SetString(PyExc_MemoryError,
                        "engine_create returned NULL");
        return -1;
    }
    return 0;
}

static PyObject *
Engine_reset(EngineObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    engine_reset(self->handle);
    Py_RETURN_NONE;
}

static PyObject *
Engine_step(EngineObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    Py_complex x_raw = {0.0, 0.0};
    if (!PyArg_ParseTuple(args, "D", &x_raw))
        return NULL;
    float complex x = (float)x_raw.real + (float)x_raw.imag * I;
    float complex y = engine_step(self->handle, x);
    return PyComplex_FromDoubles((double)crealf(y), (double)cimagf(y));
}

static PyObject *
Engine_steps(EngineObject *self, PyObject *args)
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

    engine_steps(
        self->handle,
        (const float complex *)PyArray_DATA(in_arr),
        (float complex *)PyArray_DATA((PyArrayObject *)out_arr),
        (size_t)n);

    Py_DECREF(in_arr);
    return out_arr;
}

static PyObject *
Engine_get_gain(
    EngineObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    return PyFloat_FromDouble(engine_get_gain(self->handle));
}

static PyObject *
Engine_set_gain(
    EngineObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    double v = 0.0;
    if (!PyArg_ParseTuple(args, "d", &v))
        return NULL;
    engine_set_gain(self->handle, v);
    Py_RETURN_NONE;
}

static PyObject *
Engine_destroy(EngineObject *self, PyObject *Py_UNUSED(ignored))
{
    if (self->handle) {
        engine_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *
Engine_enter(EngineObject *self, PyObject *Py_UNUSED(ignored))
{
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *
Engine_exit(EngineObject *self, PyObject *args)
{
    (void)args;
    if (self->handle) {
        engine_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef Engine_methods[] = {
    {"reset",    (PyCFunction)Engine_reset,    METH_NOARGS,
     "Reset state to post-create defaults."},
    {"step",     (PyCFunction)Engine_step,     METH_VARARGS,
     "Process one sample. Returns a scalar."},
    {"steps",    (PyCFunction)Engine_steps,    METH_VARARGS,
     "Process a samples array. Returns an ndarray."},
    {"get_gain",
     (PyCFunction)Engine_get_gain, METH_NOARGS,
     "Get gain."},
    {"set_gain",
     (PyCFunction)Engine_set_gain, METH_VARARGS,
     "Set gain."},
    {"destroy",  (PyCFunction)Engine_destroy,  METH_NOARGS,
     "Release resources."},
    {"__enter__", (PyCFunction)Engine_enter,   METH_NOARGS,  NULL},
    {"__exit__",  (PyCFunction)Engine_exit,    METH_VARARGS, NULL},
    {NULL}
};

static PyTypeObject EngineType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "engine.Engine",
    .tp_basicsize = sizeof(EngineObject),
    .tp_dealloc   = (destructor)Engine_dealloc,
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_doc       = "Engine component. Wraps engine_state_t.",
    .tp_methods   = Engine_methods,
    .tp_new       = Engine_new,
    .tp_init      = (initproc)Engine_init,
};

/* ======================================================== */
/* Module definition                                         */
/* ======================================================== */

static PyModuleDef engine_module = {
    PyModuleDef_HEAD_INIT,
    .m_name    = "engine",
    .m_doc     = "Python binding for engine_core.h.",
    .m_size    = -1,
    .m_methods = NULL,
};

PyMODINIT_FUNC
PyInit_engine(void)
{
    import_array();
    if (PyType_Ready(&EngineType) < 0)
        return NULL;

    PyObject *m = PyModule_Create(&engine_module);
    if (!m)
        return NULL;

    Py_INCREF(&EngineType);
    if (PyModule_AddObject(m, "Engine",
                           (PyObject *)&EngineType) < 0) {
        Py_DECREF(&EngineType);
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
