/* Hand-written: jm includes this file after the generated types and never
 * modifies it. The PyMethodDef row and the .pyi line come from
 * [[module.playlist.extra_methods]]; jm also forward-declares the function
 * with the signature METH_NOARGS implies, so write exactly that one. */

static PyObject *
Mix_total_samples (PyObject *self, PyObject *Py_UNUSED (ignored))
{
  PyObject *segments = PyObject_GetAttrString (self, "segments");
  if (!segments)
    return NULL;

  size_t     total = 0;
  Py_ssize_t n     = PySequence_Size (segments);
  for (Py_ssize_t i = 0; i < n; i++)
    {
      PyObject *track = PySequence_GetItem (segments, i);
      PyObject *dur   = track ? PyObject_GetAttrString (track, "dur") : NULL;
      Py_XDECREF (track);
      if (!dur)
        {
          Py_DECREF (segments);
          return NULL;
        }
      total += PyLong_AsSize_t (dur);
      Py_DECREF (dur);
    }
  Py_DECREF (segments);
  if (PyErr_Occurred ())
    return NULL;
  return PyLong_FromSize_t (total);
}
