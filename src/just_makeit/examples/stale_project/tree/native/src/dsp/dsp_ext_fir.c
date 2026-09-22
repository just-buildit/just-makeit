/*
 * dsp_ext_fir.c — Fir type for the dsp module.
 *
 * Included by dsp_ext.c (the module aggregator).
 * Hand-patches to this file are preserved across jm commands.
 * Do NOT compile this file directly — only dsp_ext.c is compiled.
 */
/* ======================================================== */
/* FirObject — wraps fir_state_t *       */
/* ======================================================== */

#include "fir/fir_core.h"

typedef struct {
    PyObject_HEAD
    fir_state_t *handle;
    float *_decimate_buf;  /* pre-allocated output for decimate */
    size_t _decimate_buf_cap;  /* allocated capacity for decimate */
    void **_decimate_retired;  /* gh-219 deferred free */
    size_t _decimate_retired_n;
    size_t _decimate_retired_cap;
    PyObject *_decimate_view_ref;  /* gh-437 last returned view */
} FirObject;

static void
Fir_dealloc(FirObject *self)
{
    if (self->handle)
        fir_destroy(self->handle);
    free(self->_decimate_buf);
    for (size_t _i = 0; _i < self->_decimate_retired_n; _i++)
        free(self->_decimate_retired[_i]);
    free(self->_decimate_retired);
    Py_XDECREF(self->_decimate_view_ref);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
Fir_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    FirObject *self = (FirObject *)type->tp_alloc(type, 0);
    if (self)
        self->handle = NULL;
    return (PyObject *)self;
}

static int
Fir_init(FirObject *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"scale", NULL};
    float scale = 1.0;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|f", kwlist,
                                     &scale))
        return -1;
    self->handle = fir_create(scale);
    if (!self->handle) {
        PyErr_SetString(PyExc_MemoryError,
                        "fir_create returned NULL");
        return -1;
    }
    {
        size_t _max = fir_decimate_max_out(self->handle);
        if (_max) {
        self->_decimate_buf = malloc(_max * sizeof(float));
        if (!self->_decimate_buf) { PyErr_NoMemory(); return -1; }
            self->_decimate_buf_cap = _max;
        }
    }
    return 0;
}

static PyObject *
Fir_reset(FirObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    fir_reset(self->handle);
    Py_RETURN_NONE;
}

static PyObject *
Fir_step(FirObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    float x;
    if (!PyArg_ParseTuple(args, "f", &x))
        return NULL;
    float y = fir_step(self->handle, x);
    return PyFloat_FromDouble((double)y);
}

static PyObject *
Fir_steps(FirObject *self, PyObject *args, PyObject *kwds)
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
        fir_steps(
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

    fir_steps(
        self->handle,
        (const float *)PyArray_DATA(in_arr),
        (float *)PyArray_DATA((PyArrayObject *)out_arr),
        (size_t)n);

    Py_DECREF(in_arr);
    return out_arr;
}

static PyObject *
Fir_get_scale(
    FirObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    return PyFloat_FromDouble((double)fir_get_scale(self->handle));
}

static PyObject *
Fir_set_scale(
    FirObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    float v = 0.0f;
    if (!PyArg_ParseTuple(args, "f", &v))
        return NULL;
    fir_set_scale(self->handle, v);
    Py_RETURN_NONE;
}
static PyObject *
Fir_decimate_max_out(FirObject *self, PyObject *Py_UNUSED(ignored))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    return PyLong_FromSize_t(
        fir_decimate_max_out(self->handle));
}

static PyObject *
Fir_decimate(FirObject *self, PyObject *args, PyObject *kwds)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    static char *_kwlist[] = {"x", "out", NULL};
    PyObject *in_obj = NULL;
    PyObject *out_obj = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|O",
            _kwlist, &in_obj, &out_obj))
        return NULL;
    PyArrayObject *in_arr = (PyArrayObject *)PyArray_FROM_OTF(
        in_obj, NPY_FLOAT, NPY_ARRAY_C_CONTIGUOUS);
    if (!in_arr) return NULL;
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
        size_t _cap = (size_t)PyArray_SIZE(out_arr);
        size_t _omax = fir_decimate_max_out(self->handle);
        size_t _min_cap = _omax > (size_t)n ? _omax : ((size_t)n);
        if (_cap < _min_cap) {
            PyErr_Format(PyExc_ValueError,
                "out has %zu elements, need >= %zu",
                _cap, _min_cap);
            Py_DECREF(out_arr); Py_DECREF(in_arr); return NULL;
        }
        size_t n_out = fir_decimate(self->handle, (const float *)PyArray_DATA(in_arr), (size_t)n, (float *)PyArray_DATA(out_arr));
        Py_DECREF(in_arr);
        npy_intp _odim = (npy_intp)n_out;
        PyObject *_oview = PyArray_SimpleNewFromData(
            1, &_odim, NPY_FLOAT, PyArray_DATA(out_arr));
        if (!_oview) { Py_DECREF(out_arr); return NULL; }
        PyArray_SetBaseObject((PyArrayObject *)_oview, (PyObject *)out_arr);
        return _oview;
    }
    size_t _need = (size_t)n;
    int _view_live = 0;
    if (self->_decimate_view_ref) {
#if PY_VERSION_HEX >= 0x030D0000
        PyObject *_lv = NULL;
        if (PyWeakref_GetRef(self->_decimate_view_ref, &_lv) == 1) {
            Py_DECREF(_lv);
            _view_live = 1;
        }
#else
        _view_live = PyWeakref_GetObject(self->_decimate_view_ref) != Py_None;
#endif
    }
    if (!self->_decimate_buf || self->_decimate_buf_cap < _need || _view_live) {
        size_t _max = fir_decimate_max_out(self->handle);
        if (!_max || _max < _need) _max = _need;
        if (self->_decimate_buf && self->_decimate_retired_n == self->_decimate_retired_cap) {
            size_t _rcap = self->_decimate_retired_cap ? self->_decimate_retired_cap * 2 : 4;
            void **_rt = realloc(self->_decimate_retired, _rcap * sizeof(void *));
            if (!_rt) { Py_DECREF(in_arr); PyErr_NoMemory(); return NULL; }
            self->_decimate_retired = _rt;
            self->_decimate_retired_cap = _rcap;
        }
        float *_tmp = malloc(_max * sizeof(float));
        if (!_tmp) { Py_DECREF(in_arr); PyErr_NoMemory(); return NULL; }
        if (self->_decimate_buf)
            self->_decimate_retired[self->_decimate_retired_n++] = self->_decimate_buf;
        self->_decimate_buf = _tmp;
        self->_decimate_buf_cap = _max;
    }
    size_t n_out = fir_decimate(self->handle, (const float *)PyArray_DATA(in_arr), (size_t)n, self->_decimate_buf);
    npy_intp dim = (npy_intp)n_out;
    PyObject *arr = PyArray_SimpleNewFromData(
        1, &dim, NPY_FLOAT, self->_decimate_buf);
    if (!arr) return NULL;
    PyArray_SetBaseObject((PyArrayObject *)arr, (PyObject *)self);
    Py_INCREF(self);
    /* gh-437: remember this view — while the caller holds it the next
     * call retires the buffer instead of reusing it in place. */
    Py_XDECREF(self->_decimate_view_ref);
    self->_decimate_view_ref = PyWeakref_NewRef(arr, NULL);
    if (!self->_decimate_view_ref) { Py_DECREF(arr); return NULL; }
    Py_DECREF(in_arr);
    return arr;
}

static PyObject *
Fir_shape(FirObject *self, PyObject *args)
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    float x;
    if (!PyArg_ParseTuple(args, "f", &x))
        return NULL;
    npy_intp _dims[] = {(npy_intp)0};
    PyObject *_out = PyArray_EMPTY(1, _dims, NPY_FLOAT, 0);
    if (!_out) { return NULL; }
    fir_shape(self->handle, x, (float *)PyArray_DATA((PyArrayObject *)_out));
    return _out;
}
static PyObject *
Fir_getprop_scale(FirObject *self, void *Py_UNUSED(closure))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    return PyFloat_FromDouble((double)self->handle->scale);
}
static int
Fir_setprop_scale(FirObject *self, PyObject *value, void *Py_UNUSED(closure))
{
    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return -1;
    }
    float v = 0.0f;
    if (!PyArg_Parse(value, "f", &v)) return -1;
    self->handle->scale = v;
    return 0;
}

static PyGetSetDef Fir_getset[] = {
    { "scale", (getter)Fir_getprop_scale, (setter)Fir_setprop_scale, "Scale.\n", NULL },
    { NULL }
};

static PyObject *
Fir_destroy(FirObject *self, PyObject *Py_UNUSED(ignored))
{
    if (self->handle) {
        fir_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *
Fir_enter(FirObject *self, PyObject *Py_UNUSED(ignored))
{
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *
Fir_exit(FirObject *self, PyObject *args)
{
    (void)args;
    if (self->handle) {
        fir_destroy(self->handle);
        self->handle = NULL;
    }
    Py_RETURN_NONE;
}

static PyMethodDef Fir_methods[] = {
    {"reset",    (PyCFunction)Fir_reset,    METH_NOARGS,
     "Reset state to post-create defaults."},
    {"step",     (PyCFunction)Fir_step,     METH_VARARGS,
     "step(x) -> float\n"
     "\n"
     "Process one input sample.\n"
     "\n"
     "    >>> from stale import Fir\n"
     "    >>> obj = Fir(1.0)\n"
     "    >>> obj.step(1.0)\n"
     "    0.0\n"},
    {"steps",    (PyCFunction)(void *)Fir_steps,    METH_VARARGS | METH_KEYWORDS,
     "steps(x[, out]) -> ndarray\n"
     "\n"
     "Process a block of samples in batch.\n"
     "\n"
     "    >>> import numpy as np\n"
     "    >>> from stale import Fir\n"
     "    >>> obj = Fir(1.0)\n"
     "    >>> y = obj.steps(np.zeros(4, dtype=np.float32))\n"
     "    >>> y.shape\n"
     "    (4,)\n"
     "    >>> y.dtype\n"
     "    dtype('float32')\n"},

    {"get_scale",
     (PyCFunction)Fir_get_scale, METH_NOARGS,
     "Get scale."},
    {"set_scale",
     (PyCFunction)Fir_set_scale, METH_VARARGS,
     "Set scale."},
    {"decimate", (PyCFunction)Fir_decimate, METH_VARARGS | METH_KEYWORDS,
     "decimate(x) -> ndarray\n"
     "\n"
     "Zero-copy view into an internally managed buffer; safe to keep across calls (a still-referenced buffer is retired, never reused in place).\n"
     "\n"
     "    >>> import numpy as np\n"
     "    >>> from stale import Fir\n"
     "    >>> obj = Fir(1.0)\n"
     "    >>> y = obj.decimate(1.0)\n"
     "    >>> y.dtype\n"
     "    dtype('float32')\n"},
    {"decimate_max_out", (PyCFunction)Fir_decimate_max_out,
     METH_NOARGS, "decimate_max_out() -> int\n\nMax output length decimate() can produce for the current state.\nUse to size the ``out=`` buffer."},
    {"shape", (PyCFunction)Fir_shape, METH_VARARGS,
     "shape(x) -> ndarray\n"
     "\n"
     "shape.\n"
     "\n"
     "    >>> import numpy as np\n"
     "    >>> from stale import Fir\n"
     "    >>> obj = Fir(1.0)\n"
     "    >>> y = obj.shape(1.0)\n"
     "    >>> y.ndim\n"
     "    1\n"},
    {"destroy",  (PyCFunction)Fir_destroy,  METH_NOARGS,
     "Release resources."},
    {"__enter__", (PyCFunction)Fir_enter,   METH_NOARGS,  NULL},
    {"__exit__",  (PyCFunction)Fir_exit,    METH_VARARGS, NULL},
    {NULL}
};

static PyTypeObject FirType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "dsp.Fir",
    .tp_basicsize = sizeof(FirObject),
    .tp_dealloc   = (destructor)Fir_dealloc,
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_doc       = "Fir type.\n",
    .tp_methods   = Fir_methods,
    .tp_getset    = Fir_getset,
    .tp_new       = Fir_new,
    .tp_init      = (initproc)Fir_init,
};
