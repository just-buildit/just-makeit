/*
 * filter_configure_core.c — varargs Python binding for filter.configure().
 *
 * Compiled into the Python extension DSO, not the pure-C core.
 * To access the C state inside this function:
 *   typedef struct { PyObject_HEAD; filter_state_t *handle; } Obj;
 *   filter_state_t *state = ((Obj *)self)->handle;
 */
#define PY_SSIZE_T_CLEAN
#include "filter/filter_core.h"
#include <Python.h>

PyObject *
filter_configure (PyObject *self, PyObject *args, PyObject *kwargs)
{
  typedef struct
  {
    PyObject_HEAD;
    filter_state_t *handle;
  } Obj;
  filter_state_t *state = ((Obj *)self)->handle;
  if (!state)
    {
      PyErr_SetString (PyExc_RuntimeError, "destroyed");
      return NULL;
    }
  double       gain     = state->gain;
  static char *kwlist[] = { "gain", NULL };
  if (!PyArg_ParseTupleAndKeywords (args, kwargs, "|d", kwlist, &gain))
    return NULL;
  state->gain = gain;
  Py_RETURN_NONE;
}
