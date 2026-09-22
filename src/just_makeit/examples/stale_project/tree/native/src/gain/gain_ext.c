/*
 * gain_ext.c — Python C extension for gain
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>

#include "gain/gain_core.h"

/* ======================================================== */
/* GainObject — wraps gain_state_t *       */
/* ======================================================== */

typedef struct {
    PyObject_HEAD
    gain_state_t *handle;
} GainObject;

static void
Gain_dealloc(GainObject *self)
{
    if (self->handle)
        gain_destroy(self->handle);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
Gain_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    GainObject *self = (GainObject *)type->tp_alloc(type, 0);
    if (self)
        self->handle = NULL;
    return (PyObject *)self;
}

static int
Gain_init(GainObject *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"level", NULL};
    float level = 1.0;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|f", kwlist,
                                     &level))
        return -1;
    self->handle = gain_create(level);
    if (!self->handle) {
        PyErr_SetString(PyExc_MemoryError,
                        "gain_create returned NULL");
        return -1;
    }
    return 0;
}

static PyObject *
Gain_reset(GainObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    gain_reset(self->handle);
    Py_RETURN_NONE;
}

static PyObject *
Gain_step(GainObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    float x;
    if (!PyArg_ParseTuple(args, "f", &x))
        return NULL;
    float y = gain_step(self->handle, x);
    return PyFloat_FromDouble((double)y);
}

static PyObject *
Gain_steps(GainObject *self, PyObject *args, PyObject *kwds)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    static char *kwlist[] = {"x", "out", NULL};
    PyObject *in_obj  = NULL;
    PyObject *out_obj = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds,
            "O|O", kwlist,
            &in_obj, &out_obj))
        return NULL;

    PyArrayObject *in_arr = (PyArrayObject *)PyArray_FROM_OTF(
        in_obj, NPY_FLOAT, NPY_ARRAY_C_CONTIGUOUS);
    if (!in_arr)
        return NULL;

    Py_ssize_t n = PyArray_SIZE(in_arr);

    if (out_obj && out_obj != Py_None) {
        /* Require the exact output dtype — no silent cast (a cast writes
         * into a temp copy instead of the caller's buffer). */
        if (!PyArray_Check(out_obj) ||
            PyArray_TYPE((PyArrayObject *)out_obj) != NPY_FLOAT ||
            !PyArray_ISWRITEABLE((PyArrayObject *)out_obj)) {
            PyErr_SetString(PyExc_TypeError,
                "out must be a writable ndarray of the output dtype");
            Py_DECREF(in_arr);
            return NULL;
        }
        PyArrayObject *out_arr = (PyArrayObject *)PyArray_FROM_OTF(
            out_obj, NPY_FLOAT,
            NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE);
        if (!out_arr) { Py_DECREF(in_arr); return NULL; }
        if (PyArray_SIZE(out_arr) != n) {
            PyErr_Format(PyExc_ValueError,
                "out length %zd != input length %zd",
                (Py_ssize_t)PyArray_SIZE(out_arr), (Py_ssize_t)n);
            Py_DECREF(out_arr);
            Py_DECREF(in_arr);
            return NULL;
        }
        gain_steps(
            self->handle,
            (const float *)PyArray_DATA(in_arr),
            (float *)PyArray_DATA(out_arr),
            (size_t)n);
        Py_DECREF(in_arr);
        return (PyObject *)out_arr;
    }

    npy_intp dims[] = {n};
    PyObject *out_arr = PyArray_SimpleNew(1, dims, NPY_FLOAT);
    if (!out_arr) {
        Py_DECREF(in_arr);
        return NULL;
    }

    gain_steps(
        self->handle,
        (const float *)PyArray_DATA(in_arr),
        (float *)PyArray_DATA((PyArrayObject *)out_arr),
        (size_t)n);

    Py_DECREF(in_arr);
    return out_arr;
}

static PyObject *
Gain_get_level(
    GainObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    return PyFloat_FromDouble((double)gain_get_level(self->handle));
}

static PyObject *
Gain_set_level(
    GainObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    float v = 0.0f;
    if (!PyArg_ParseTuple(args, "f", &v))
        return NULL;
    gain_set_level(self->handle, v);
    Py_RETURN_NONE;
}


static PyObject *
Gain_destroy(GainObject *self, PyObject *Py_UNUSED(ignored))
{
    if (self->handle) {
        gain_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *
Gain_enter(GainObject *self, PyObject *Py_UNUSED(ignored))
{
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *
Gain_exit(GainObject *self, PyObject *args)
{
    (void)args;
    if (self->handle) {
        gain_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef Gain_methods[] = {
    {"reset",    (PyCFunction)Gain_reset,    METH_NOARGS,
     "Reset state to post-create defaults."},
    {"step",     (PyCFunction)Gain_step,     METH_VARARGS,
     "step(x) -> float\n"
     "\n"
     "Process one input sample.\n"
     "\n"
     "    >>> from stale import Gain\n"
     "    >>> obj = Gain(1.0)\n"
     "    >>> obj.step(1.0)\n"
     "    0.0\n"},
    {"steps",    (PyCFunction)(void *)Gain_steps,    METH_VARARGS | METH_KEYWORDS,
     "steps(x[, out]) -> ndarray\n"
     "\n"
     "Process a block of samples in batch.\n"
     "\n"
     "    >>> import numpy as np\n"
     "    >>> from stale import Gain\n"
     "    >>> obj = Gain(1.0)\n"
     "    >>> y = obj.steps(np.zeros(4, dtype=np.float32))\n"
     "    >>> y.shape\n"
     "    (4,)\n"
     "    >>> y.dtype\n"
     "    dtype('float32')\n"},

    {"get_level",
     (PyCFunction)Gain_get_level, METH_NOARGS,
     "Get level."},
    {"set_level",
     (PyCFunction)Gain_set_level, METH_VARARGS,
     "Set level."},
    {"destroy",  (PyCFunction)Gain_destroy,  METH_NOARGS,
     "Release resources."},
    {"__enter__", (PyCFunction)Gain_enter,   METH_NOARGS,  NULL},
    {"__exit__",  (PyCFunction)Gain_exit,    METH_VARARGS, NULL},
    {NULL}
};

static PyTypeObject GainType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "gain.Gain",
    .tp_basicsize = sizeof(GainObject),
    .tp_dealloc   = (destructor)Gain_dealloc,
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_doc       = "Gain component. Wraps gain_state_t.",
    .tp_methods   = Gain_methods,
    .tp_new       = Gain_new,
    .tp_init      = (initproc)Gain_init,
};


/* ======================================================== */
/* Module definition                                         */
/* ======================================================== */

static PyModuleDef gain_module = {
    PyModuleDef_HEAD_INIT,
    .m_name    = "gain",
    .m_doc     = "Python binding for gain_core.h.",
    .m_size    = -1,
    .m_methods = NULL,
};

PyMODINIT_FUNC
PyInit_gain(void)
{
    import_array();
    if (PyType_Ready(&GainType) < 0)
        return NULL;

    PyObject *m = PyModule_Create(&gain_module);
    if (!m)
        return NULL;

    Py_INCREF(&GainType);
    if (PyModule_AddObject(m, "Gain",
                           (PyObject *)&GainType) < 0) {
        Py_DECREF(&GainType);
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
