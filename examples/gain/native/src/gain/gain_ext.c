/*
 * gain_ext.c — Python C extension for gain_core.h
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <complex.h>
#include <numpy/arrayobject.h>

#include "gain/gain_core.h"

/* ======================================================== */
/* GainObject — wraps gain_state_t *       */
/* ======================================================== */

typedef struct {
    PyObject_HEAD gain_state_t *handle;
} GainObject;

static void Gain_dealloc(GainObject *self) {
    if (self->handle)
        gain_destroy(self->handle);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *Gain_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    GainObject *self = (GainObject *)type->tp_alloc(type, 0);
    if (self)
        self->handle = NULL;
    return (PyObject *)self;
}

static int Gain_init(GainObject *self, PyObject *args, PyObject *kwds) {
    static char *kwlist[] = {"gain", NULL};
    double       gain     = 0.0;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "d", kwlist, &gain))
        return -1;

    self->handle = gain_create(gain);
    if (!self->handle) {
        PyErr_SetString(PyExc_MemoryError, "gain_create returned NULL");
        return -1;
    }
    return 0;
}

static PyObject *Gain_reset(GainObject *self, PyObject *Py_UNUSED(ignored)) {
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    gain_reset(self->handle);
    Py_RETURN_NONE;
}

static PyObject *Gain_step(GainObject *self, PyObject *args) {
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    Py_complex pyx;
    if (!PyArg_ParseTuple(args, "D", &pyx))
        return NULL;

    float complex x = (float)pyx.real + (float)pyx.imag * I;
    float complex y = gain_step(self->handle, x);
    return PyComplex_FromDoubles((double)crealf(y), (double)cimagf(y));
}

static PyObject *Gain_steps(GainObject *self, PyObject *args) {
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    PyObject *in_obj = NULL;
    if (!PyArg_ParseTuple(args, "O", &in_obj))
        return NULL;

    PyArrayObject *in_arr =
        (PyArrayObject *)PyArray_FROM_OTF(in_obj, NPY_COMPLEX64, NPY_ARRAY_C_CONTIGUOUS);
    if (!in_arr)
        return NULL;

    Py_ssize_t n       = PyArray_SIZE(in_arr);
    npy_intp   dims[]  = {n};
    PyObject  *out_arr = PyArray_SimpleNew(1, dims, NPY_COMPLEX64);
    if (!out_arr) {
        Py_DECREF(in_arr);
        return NULL;
    }

    gain_steps(self->handle, (const float complex *)PyArray_DATA(in_arr),
               (float complex *)PyArray_DATA((PyArrayObject *)out_arr), (size_t)n);

    Py_DECREF(in_arr);
    return out_arr;
}

static PyObject *Gain_get_gain(GainObject *self, PyObject *Py_UNUSED(ignored)) {
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    return PyFloat_FromDouble(gain_get_gain(self->handle));
}

static PyObject *Gain_set_gain(GainObject *self, PyObject *args) {
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    double v = 0.0;
    if (!PyArg_ParseTuple(args, "d", &v))
        return NULL;
    gain_set_gain(self->handle, v);
    Py_RETURN_NONE;
}

static PyObject *Gain_destroy(GainObject *self, PyObject *Py_UNUSED(ignored)) {
    if (self->handle) {
        gain_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Gain_enter(GainObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *Gain_exit(GainObject *self, PyObject *args) {
    (void)args;
    if (self->handle) {
        gain_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef Gain_methods[] = {
    {"reset", (PyCFunction)Gain_reset, METH_NOARGS, "Reset state to post-create defaults."},
    {"step", (PyCFunction)Gain_step, METH_VARARGS, "Process one complex sample. Returns complex."},
    {"steps", (PyCFunction)Gain_steps, METH_VARARGS,
     "Process a complex64 ndarray. Returns complex64 ndarray."},
    {"get_gain", (PyCFunction)Gain_get_gain, METH_NOARGS, "Get gain."},
    {"set_gain", (PyCFunction)Gain_set_gain, METH_VARARGS, "Set gain."},
    {"destroy", (PyCFunction)Gain_destroy, METH_NOARGS, "Release resources."},
    {"__enter__", (PyCFunction)Gain_enter, METH_NOARGS, NULL},
    {"__exit__", (PyCFunction)Gain_exit, METH_VARARGS, NULL},
    {NULL}};

static PyTypeObject GainType = {
    PyVarObject_HEAD_INIT(NULL, 0).tp_name = "gain.Gain",
    .tp_basicsize                          = sizeof(GainObject),
    .tp_dealloc                            = (destructor)Gain_dealloc,
    .tp_flags                              = Py_TPFLAGS_DEFAULT,
    .tp_doc                                = "Gain component. Wraps gain_state_t.",
    .tp_methods                            = Gain_methods,
    .tp_new                                = Gain_new,
    .tp_init                               = (initproc)Gain_init,
};

/* ======================================================== */
/* Module definition                                         */
/* ======================================================== */

static PyModuleDef gain_module = {
    PyModuleDef_HEAD_INIT, .m_name = "gain",  .m_doc = "Python binding for gain_core.h.",
    .m_size = -1,          .m_methods = NULL,
};

PyMODINIT_FUNC PyInit_gain(void) {
    import_array();
    if (PyType_Ready(&GainType) < 0)
        return NULL;

    PyObject *m = PyModule_Create(&gain_module);
    if (!m)
        return NULL;

    Py_INCREF(&GainType);
    if (PyModule_AddObject(m, "Gain", (PyObject *)&GainType) < 0) {
        Py_DECREF(&GainType);
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
