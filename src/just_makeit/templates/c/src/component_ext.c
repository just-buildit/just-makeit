/*
 * /*<<component>>*/_ext.c — Python C extension for /*<<component>>*/
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>
#include "/*<<inc_prefix>>*/clib_common.h"

#include "/*<<inc_prefix>>*//*<<component>>*///*<<component>>*/_core.h"

/*<<jm_array_arg_c>>*/

/*<<component_type_section>>*/

/*<<extra_include>>*/
/* ======================================================== */
/* Module definition                                         */
/* ======================================================== */

static PyModuleDef /*<<csym>>*/_module = {
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
/*<<record_type_ready>>*/
    PyObject *m = PyModule_Create(&/*<<csym>>*/_module);
    if (!m)
        return NULL;

    Py_INCREF(&/*<<ComponentW>>*/Type);
    if (PyModule_AddObject(m, "/*<<Component>>*/",
                           (PyObject *)&/*<<ComponentW>>*/Type) < 0) {
        Py_DECREF(&/*<<ComponentW>>*/Type);
        Py_DECREF(m);
        return NULL;
    }
/*<<record_add_object>>*//*<<procglobal>>*/    return m;
}
