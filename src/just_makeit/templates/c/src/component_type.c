/* ======================================================== */
/* /*<<Component>>*/Object — wraps /*<<csym>>*/_state_t *       */
/* ======================================================== */

/*<<type_core_include>>*/typedef struct {
    PyObject_HEAD
    /*<<csym>>*/_state_t *handle;
/*<<destroy_fields>>*//*<<extra_buf_fields>>*//*<<capsule_owner_fields>>*/} /*<<Component>>*/Object;

static void
/*<<ComponentW>>*/_dealloc(/*<<Component>>*/Object *self)
{
/*<<destroy_dealloc_call>>*//*<<extra_buf_free>>*//*<<capsule_owner_free>>*/    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
/*<<ComponentW>>*/_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    /* tp_new allocates only; __init__ reads the arguments. */
    (void)args;
    (void)kwds;
    /*<<Component>>*/Object *self = (/*<<Component>>*/Object *)type->tp_alloc(type, 0);
    if (self)
        self->handle = NULL;
    return (PyObject *)self;
}

static int
/*<<ComponentW>>*/_init(/*<<Component>>*/Object *self, PyObject *args, PyObject *kwds)
{
/*<<init_parse_block>>*//*<<array_args_parse_block>>*//*<<create_line>>*//*<<array_args_decref>>*//*<<create_fail_block>>*//*<<extra_buf_alloc>>*//*<<init_warn_block>>*/    return 0;
}

/*<<builtin_reset_c>>*/

/*<<step_ext_fn>>*/

/*<<steps_ext_fn>>*/

/*<<enum_tables>>*/
/*<<getter_setter_methods_c>>*/
/*<<extra_methods_c>>*/
/*<<getset_def>>*/
static PyObject *
/*<<ComponentW>>*/_destroy(/*<<Component>>*/Object *self, PyObject *Py_UNUSED(ignored))
{
/*<<destroy_method_body>>*/}

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
/*<<destroy_exit_body>>*/}

/*<<stream_iter_block>>*/static PyMethodDef /*<<ComponentW>>*/_methods[] = {
/*<<builtin_reset_pmd>>*//*<<step_pymethoddef_entry>>*//*<<steps_def_entry>>*/
/*<<getter_setter_pymethoddef>>*//*<<extra_methods_pymethoddef>>*//*<<stream_def_entry>>*//*<<destroy_pymethoddef>>*/    {"__enter__", (PyCFunction)/*<<ComponentW>>*/_enter,   METH_NOARGS,
     /*<<cm_enter_doc>>*/},
    {"__exit__",  (PyCFunction)/*<<ComponentW>>*/_exit,    METH_VARARGS,
     /*<<cm_exit_doc>>*/},
    {NULL, NULL, 0, NULL}
};

static PyTypeObject /*<<ComponentW>>*/Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "/*<<module_tp>>*/./*<<Component>>*/",
    .tp_basicsize = sizeof(/*<<Component>>*/Object),
    .tp_dealloc   = (destructor)/*<<ComponentW>>*/_dealloc,
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_doc       = /*<<tp_doc>>*/,
    .tp_methods   = /*<<ComponentW>>*/_methods,/*<<tp_getset_decl>>*//*<<stream_tp_iter>>*//*<<stream_tp_async>>*/
    .tp_new       = /*<<ComponentW>>*/_new,
    .tp_init      = (initproc)/*<<ComponentW>>*/_init,
};
