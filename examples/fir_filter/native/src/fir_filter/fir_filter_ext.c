/*
 * fir_filter_ext.c — Python C extension for fir_filter_core.h
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>

#include "fir_filter/fir_filter_core.h"

/* ======================================================== */
/* FirFilterObject — wraps fir_filter_state_t *       */
/* ======================================================== */

typedef struct {
    PyObject_HEAD
    fir_filter_state_t *handle;
} FirFilterObject;

static void
FirFilter_dealloc(FirFilterObject *self)
{
    if (self->handle)
        fir_filter_destroy(self->handle);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
FirFilter_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    FirFilterObject *self = (FirFilterObject *)type->tp_alloc(type, 0);
    if (self)
        self->handle = NULL;
    return (PyObject *)self;
}

static int
FirFilter_init(FirFilterObject *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"gain", NULL};
    float gain = 1.0f;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|f", kwlist,
                                     &gain))
        return -1;
    self->handle = fir_filter_create(gain);
    if (!self->handle) {
        PyErr_SetString(PyExc_MemoryError,
                        "fir_filter_create returned NULL");
        return -1;
    }
    return 0;
}

static PyObject *
FirFilter_reset(FirFilterObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    fir_filter_reset(self->handle);
    Py_RETURN_NONE;
}

static PyObject *
FirFilter_step(FirFilterObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    Py_complex pyx;
    if (!PyArg_ParseTuple(args, "D", &pyx))
        return NULL;

    float complex x = (float)pyx.real + (float)pyx.imag * I;
    float complex y = fir_filter_step(self->handle, x);
    return PyComplex_FromDoubles((double)crealf(y), (double)cimagf(y));
}

static PyObject *
FirFilter_steps(FirFilterObject *self, PyObject *args)
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

    fir_filter_steps(
        self->handle,
        (const float complex *)PyArray_DATA(in_arr),
        (float complex *)PyArray_DATA((PyArrayObject *)out_arr),
        (size_t)n);

    Py_DECREF(in_arr);
    return out_arr;
}

static PyObject *
FirFilter_get_gain(
    FirFilterObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    return PyFloat_FromDouble((double)fir_filter_get_gain(self->handle));
}

static PyObject *
FirFilter_set_gain(
    FirFilterObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    float v = 0.0f;
    if (!PyArg_ParseTuple(args, "f", &v))
        return NULL;
    fir_filter_set_gain(self->handle, v);
    Py_RETURN_NONE;
}

static PyObject *
FirFilter_get_coeffs(
    FirFilterObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    npy_intp dims[] = {16};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_FLOAT);
    if (!arr) return NULL;
    fir_filter_get_coeffs(self->handle,
        (float *)PyArray_DATA((PyArrayObject *)arr));
    return arr;
}

static PyObject *
FirFilter_get_coeffs_view(
    FirFilterObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    npy_intp dims[] = {16};
    PyObject *arr = PyArray_SimpleNewFromData(
        1, dims, NPY_FLOAT,
        (void *)fir_filter_get_coeffs_view(self->handle));
    if (!arr) return NULL;
    PyArray_CLEARFLAGS((PyArrayObject *)arr, NPY_ARRAY_WRITEABLE);
    return arr;
}

static PyObject *
FirFilter_set_coeffs(
    FirFilterObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    PyObject *in_obj = NULL;
    if (!PyArg_ParseTuple(args, "O", &in_obj))
        return NULL;
    PyArrayObject *arr = (PyArrayObject *)PyArray_FROM_OTF(
        in_obj, NPY_FLOAT, NPY_ARRAY_C_CONTIGUOUS);
    if (!arr) return NULL;
    if (PyArray_SIZE(arr) != 16) {
        PyErr_Format(PyExc_ValueError,
            "coeffs requires exactly 16 elements, got %zd",
            (Py_ssize_t)PyArray_SIZE(arr));
        Py_DECREF(arr);
        return NULL;
    }
    fir_filter_set_coeffs(self->handle,
        (const float *)PyArray_DATA(arr));
    Py_DECREF(arr);
    Py_RETURN_NONE;
}

static PyObject *
FirFilter_get_delay(
    FirFilterObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    npy_intp dims[] = {16};
    PyObject *arr = PyArray_SimpleNew(1, dims, NPY_COMPLEX64);
    if (!arr) return NULL;
    fir_filter_get_delay(self->handle,
        (float _Complex *)PyArray_DATA((PyArrayObject *)arr));
    return arr;
}

static PyObject *
FirFilter_get_delay_view(
    FirFilterObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    npy_intp dims[] = {16};
    PyObject *arr = PyArray_SimpleNewFromData(
        1, dims, NPY_COMPLEX64,
        (void *)fir_filter_get_delay_view(self->handle));
    if (!arr) return NULL;
    PyArray_CLEARFLAGS((PyArrayObject *)arr, NPY_ARRAY_WRITEABLE);
    return arr;
}

static PyObject *
FirFilter_set_delay(
    FirFilterObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    PyObject *in_obj = NULL;
    if (!PyArg_ParseTuple(args, "O", &in_obj))
        return NULL;
    PyArrayObject *arr = (PyArrayObject *)PyArray_FROM_OTF(
        in_obj, NPY_COMPLEX64, NPY_ARRAY_C_CONTIGUOUS);
    if (!arr) return NULL;
    if (PyArray_SIZE(arr) != 16) {
        PyErr_Format(PyExc_ValueError,
            "delay requires exactly 16 elements, got %zd",
            (Py_ssize_t)PyArray_SIZE(arr));
        Py_DECREF(arr);
        return NULL;
    }
    fir_filter_set_delay(self->handle,
        (const float _Complex *)PyArray_DATA(arr));
    Py_DECREF(arr);
    Py_RETURN_NONE;
}

static PyObject *
FirFilter_destroy(FirFilterObject *self, PyObject *Py_UNUSED(ignored))
{
    if (self->handle) {
        fir_filter_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *
FirFilter_enter(FirFilterObject *self, PyObject *Py_UNUSED(ignored))
{
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *
FirFilter_exit(FirFilterObject *self, PyObject *args)
{
    (void)args;
    if (self->handle) {
        fir_filter_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef FirFilter_methods[] = {
    {"reset",    (PyCFunction)FirFilter_reset,    METH_NOARGS,
     "Reset state to post-create defaults."},
    {"step",     (PyCFunction)FirFilter_step,     METH_VARARGS,
     "Process one complex sample. Returns complex."},
    {"steps",    (PyCFunction)FirFilter_steps,    METH_VARARGS,
     "Process a complex64 ndarray. Returns complex64 ndarray."},
    {"get_gain",
     (PyCFunction)FirFilter_get_gain, METH_NOARGS,
     "Get gain."},
    {"set_gain",
     (PyCFunction)FirFilter_set_gain, METH_VARARGS,
     "Set gain."},
    {"get_coeffs",
     (PyCFunction)FirFilter_get_coeffs, METH_NOARGS,
     "Return a copy of coeffs as np.float32 ndarray (length 16)."},
    {"get_coeffs_view",
     (PyCFunction)FirFilter_get_coeffs_view, METH_NOARGS,
     "Return read-only view of coeffs. Valid until destroy()."},
    {"set_coeffs",
     (PyCFunction)FirFilter_set_coeffs, METH_VARARGS,
     "Set coeffs from np.float32 array of length 16."},
    {"get_delay",
     (PyCFunction)FirFilter_get_delay, METH_NOARGS,
     "Return a copy of delay as np.complex64 ndarray (length 16)."},
    {"get_delay_view",
     (PyCFunction)FirFilter_get_delay_view, METH_NOARGS,
     "Return read-only view of delay. Valid until destroy()."},
    {"set_delay",
     (PyCFunction)FirFilter_set_delay, METH_VARARGS,
     "Set delay from np.complex64 array of length 16."},
    {"destroy",  (PyCFunction)FirFilter_destroy,  METH_NOARGS,
     "Release resources."},
    {"__enter__", (PyCFunction)FirFilter_enter,   METH_NOARGS,  NULL},
    {"__exit__",  (PyCFunction)FirFilter_exit,    METH_VARARGS, NULL},
    {NULL}
};

static PyTypeObject FirFilterType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "fir_filter.FirFilter",
    .tp_basicsize = sizeof(FirFilterObject),
    .tp_dealloc   = (destructor)FirFilter_dealloc,
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_doc       = "FirFilter component. Wraps fir_filter_state_t.",
    .tp_methods   = FirFilter_methods,
    .tp_new       = FirFilter_new,
    .tp_init      = (initproc)FirFilter_init,
};

/* ======================================================== */
/* Module definition                                         */
/* ======================================================== */

static PyModuleDef fir_filter_module = {
    PyModuleDef_HEAD_INIT,
    .m_name    = "fir_filter",
    .m_doc     = "Python binding for fir_filter_core.h.",
    .m_size    = -1,
    .m_methods = NULL,
};

PyMODINIT_FUNC
PyInit_fir_filter(void)
{
    import_array();
    if (PyType_Ready(&FirFilterType) < 0)
        return NULL;

    PyObject *m = PyModule_Create(&fir_filter_module);
    if (!m)
        return NULL;

    Py_INCREF(&FirFilterType);
    if (PyModule_AddObject(m, "FirFilter",
                           (PyObject *)&FirFilterType) < 0) {
        Py_DECREF(&FirFilterType);
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
