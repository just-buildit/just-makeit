/*
 * dsp_ext.c — Python extension module dsp
 *
 * Objects: Fir
 * GENERATED — do not hand-edit. Patches belong in the _ext_<obj>.c fragments.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include <complex.h>

#include "dsp/dsp_core.h"

#include "dsp_ext_fir.c"

static PyObject *
_bind_energy(PyObject *self, PyObject *args, PyObject *kwds)
{
    (void)self;
    static char *_kwlist[] = {"n", "y", NULL};
    int n = 0;
    PyObject *y_obj = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "iO",
            _kwlist, &n, &y_obj))
        return NULL;
    /* Require the exact output dtype — no silent cast (a cast writes
     * into a temp copy instead of the caller's buffer). */
    if (!PyArray_Check(y_obj) ||
        PyArray_TYPE((PyArrayObject *)y_obj) != NPY_FLOAT ||
        !PyArray_ISWRITEABLE((PyArrayObject *)y_obj)) {
        PyErr_SetString(PyExc_TypeError,
            "y must be a writable ndarray of the output dtype");
        return NULL;
    }
    PyArrayObject *y_arr = (PyArrayObject *)PyArray_FROM_OTF(
        y_obj, NPY_FLOAT, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE);
    if (!y_arr) { return NULL; }
    float *y = (float *)PyArray_DATA(y_arr);
    size_t y_len = (size_t)PyArray_SIZE(y_arr);
    Py_DECREF(y_arr);
    return PyFloat_FromDouble(energy(n, y, y_len));
}


/* ======================================================== */
/* Module                                                    */
/* ======================================================== */

static PyMethodDef dsp_module_methods[] = {
    {"energy", (PyCFunction)(void *)_bind_energy, METH_VARARGS | METH_KEYWORDS, "energy."},
    {NULL, NULL, 0, NULL}
};

static PyModuleDef dsp_moduledef = {
    PyModuleDef_HEAD_INIT,
    .m_name    = "dsp",
    .m_doc     = "Dsp module.",
    .m_size    = -1,
    .m_methods = dsp_module_methods,
};

PyMODINIT_FUNC
PyInit_dsp(void)
{
    import_array();
    if (PyType_Ready(&FirType) < 0) return NULL;
    PyObject *m = PyModule_Create(&dsp_moduledef);
    if (!m) return NULL;
    Py_INCREF(&FirType);
    if (PyModule_AddObject(m, "Fir", (PyObject *)&FirType) < 0) {
        Py_DECREF(&FirType); Py_DECREF(m); return NULL;
    }
    return m;
}
