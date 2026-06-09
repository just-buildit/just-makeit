"""_stream — context builder for the generated ``stream()`` / ``__iter__``.

A *streamable* object (``--streamable``) drives its block producer through a
generated C iterator type so callers write::

    for blk in obj.stream(4096):
        consume(blk)

instead of the hand-rolled ``while len(b := obj.execute(4096)): ...`` loop.

The producer is the object's ``variable_output`` method when one exists
(blockwise: ``execute(block) -> array``, which yields a short/empty array when
drained), otherwise the built-in ``steps`` (source: ``steps(n) -> array``,
which never empties, so it streams forever unless ``count`` bounds it).  One
``tp_iternext`` loop covers both: it stops on an empty block or when the cap is
reached.

Every key defaults to ``""`` so a non-streamable object renders byte-identical
to a project built before this feature existed — no golden-output churn.

The emitted strings are fully resolved (component names substituted here), so
``_render.render`` never has to expand a placeholder nested inside another
key's value.
"""

from __future__ import annotations

from .._types import _CTYPE_META

# Keys consumed by templates/c/src/component_ext.c and templates/py/
# component.pyi.  All empty == not streamable (or no resolvable producer).
_EMPTY: dict[str, str] = {
    "stream_iter_block": "",
    "stream_def_entry": "",
    "stream_tp_iter": "",
    "stream_type_ready": "",
    "pyi_stream_typing": "",
    "pyi_stream_methods": "",
}


def _ndarray_hint(elem_ctype: str) -> str:
    """``NDArray[np.float32]``-style annotation for a producer's element type.

    A trailing ``[]`` (array return type) is stripped first; an unknown type
    degrades to ``NDArray[Any]`` rather than raising.
    """
    elem = elem_ctype[:-2] if elem_ctype.endswith("[]") else elem_ctype
    meta = _CTYPE_META.get(elem)
    return f"NDArray[{meta['py_type']}]" if meta else "NDArray[Any]"


def _resolve_producer(
    methods: list[dict], arg_type: str, return_type: str
) -> tuple[str, str] | None:
    """``(producer_method_name, element_ctype)`` or None when no shape fits.

    A ``variable_output`` method wins (blockwise); otherwise a void-arg object
    is a source driven by built-in ``steps``.  Anything else (e.g. a plain
    scalar step with no variable-output method) has no block producer yet, so
    ``stream()`` is simply not emitted until one is added.
    """
    for m in methods:
        if m.get("variable_output"):
            return m["name"], m.get("return_type", "float _Complex")
    if arg_type == "void" and return_type != "void":
        return "steps", return_type
    return None


def make_stream_ctx(
    component: str,
    Component: str,
    ComponentW: str,
    *,
    streamable: bool = False,
    methods: list[dict] | None = None,
    arg_type: str = "void",
    return_type: str = "void",
    default_block: int = 1024,
) -> dict[str, str]:
    """Template keys for the ``stream()`` generator + ``__iter__``.

    Returns all-empty keys (no codegen) unless *streamable* is set and a block
    producer is resolvable.  ``ComponentW`` is the wrapper-function prefix
    (``<Component>Obj`` for ``--no-state`` objects, ``<Component>`` otherwise);
    ``Component`` names the C iterator type so it stays stable across
    no-state/stateful objects.
    """
    if not streamable:
        return dict(_EMPTY)
    producer = _resolve_producer(methods or [], arg_type, return_type)
    if producer is None:
        return dict(_EMPTY)
    name, elem = producer
    nd = _ndarray_hint(elem)
    block = int(default_block)

    iter_t = f"{Component}StreamIter"
    iter_ty = f"{Component}StreamIterType"
    obj_t = f"{Component}Object"

    stream_iter_block = f"""\
/* ---- Block iterator: stream() / __iter__ --------------- */

typedef struct {{
    PyObject_HEAD
    PyObject *src;       /* the {Component} instance (holds a reference) */
    PyObject *on_block;  /* post-yield hook, or NULL */
    PyObject *prev;      /* last block, held for the post-yield hook */
    Py_ssize_t block;    /* producer argument (block / sample count) */
    Py_ssize_t count;    /* block cap, or -1 when unbounded */
    Py_ssize_t emitted;  /* blocks yielded so far */
}} {iter_t};

static PyTypeObject {iter_ty};

static void
{iter_t}_dealloc({iter_t} *it)
{{
    Py_XDECREF(it->src);
    Py_XDECREF(it->on_block);
    Py_XDECREF(it->prev);
    Py_TYPE(it)->tp_free((PyObject *)it);
}}

static PyObject *
{iter_t}_next({iter_t} *it)
{{
    /* Fire on_block AFTER the consumer processed the previous block, so a
       pacing hook can account for the consumer's own time. */
    if (it->on_block && it->prev) {{
        PyObject *r = PyObject_CallFunctionObjArgs(it->on_block,
                                                   it->prev, NULL);
        Py_CLEAR(it->prev);
        if (!r)
            return NULL;
        Py_DECREF(r);
    }}
    if (it->count >= 0 && it->emitted >= it->count)
        return NULL;
    PyObject *blk = PyObject_CallMethod(it->src, "{name}", "n", it->block);
    if (!blk)
        return NULL;
    Py_ssize_t n = PySequence_Size(blk);
    if (n < 0) {{
        Py_DECREF(blk);
        return NULL;
    }}
    if (n == 0) {{          /* producer drained — stop iteration */
        Py_DECREF(blk);
        return NULL;
    }}
    it->emitted++;
    if (it->on_block) {{
        Py_INCREF(blk);
        it->prev = blk;
    }}
    return blk;
}}

static PyTypeObject {iter_ty} = {{
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name      = "{component}.{iter_t}",
    .tp_basicsize = sizeof({iter_t}),
    .tp_dealloc   = (destructor){iter_t}_dealloc,
    .tp_flags     = Py_TPFLAGS_DEFAULT,
    .tp_doc       = "Block iterator over {Component}.",
    .tp_iter      = PyObject_SelfIter,
    .tp_iternext  = (iternextfunc){iter_t}_next,
}};

static PyObject *
{ComponentW}_make_iter({obj_t} *self, Py_ssize_t block,
                       Py_ssize_t count, PyObject *on_block)
{{
    {iter_t} *it = PyObject_New({iter_t}, &{iter_ty});
    if (!it)
        return NULL;
    Py_INCREF(self);
    it->src = (PyObject *)self;
    Py_XINCREF(on_block);
    it->on_block = on_block;
    it->prev = NULL;
    it->block = block;
    it->count = count;
    it->emitted = 0;
    return (PyObject *)it;
}}

static PyObject *
{ComponentW}_stream({obj_t} *self, PyObject *args, PyObject *kwds)
{{
    static char *kwlist[] = {{"block", "count", "on_block", NULL}};
    Py_ssize_t block = {block};
    PyObject *count_obj = Py_None;
    PyObject *on_block = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|nOO", kwlist,
                                     &block, &count_obj, &on_block))
        return NULL;
    Py_ssize_t count = -1;
    if (count_obj != Py_None) {{
        count = PyNumber_AsSsize_t(count_obj, PyExc_OverflowError);
        if (count == -1 && PyErr_Occurred())
            return NULL;
    }}
    if (on_block != Py_None && !PyCallable_Check(on_block)) {{
        PyErr_SetString(PyExc_TypeError, "on_block must be callable");
        return NULL;
    }}
    return {ComponentW}_make_iter(
        self, block, count, on_block == Py_None ? NULL : on_block);
}}

static PyObject *
{ComponentW}_getiter({obj_t} *self)
{{
    return {ComponentW}_make_iter(self, {block}, -1, NULL);
}}

"""

    stream_def_entry = (
        f'    {{"stream", (PyCFunction)(void *){ComponentW}_stream,\n'
        f"     METH_VARARGS | METH_KEYWORDS,\n"
        f'     "stream(block={block}, *, count=None, on_block=None)'
        f' -> iterator.\\n"\n'
        f'     "Yield output blocks; on_block(b) fires after each block'
        f' is consumed."}},\n'
    )

    stream_tp_iter = (
        f"\n    .tp_iter      = (getiterfunc){ComponentW}_getiter,"
    )

    stream_type_ready = (
        f"\n\n    if (PyType_Ready(&{iter_ty}) < 0)\n        return NULL;"
    )

    pyi_stream_typing = ", Callable, Iterator"

    pyi_stream_methods = (
        f"\n    def stream(\n"
        f"        self,\n"
        f"        block: int = {block},\n"
        f"        *,\n"
        f"        count: int | None = None,\n"
        f"        on_block: Callable[[{nd}], None] | None = None,\n"
        f"    ) -> Iterator[{nd}]:\n"
        f'        """Yield output blocks, driving ``{name}`` block by block.\n'
        f"\n"
        f"        Parameters\n"
        f"        ----------\n"
        f"        block : int\n"
        f"            Producer argument for each step (block / sample count).\n"
        f"        count : int or None\n"
        f"            Stop after this many blocks; ``None`` streams until the\n"
        f"            producer drains (or forever, for an inexhaustible"
        f" source).\n"
        f"        on_block : callable or None\n"
        f"            Invoked as ``on_block(block)`` after each block is\n"
        f"            yielded and consumed — the seam for pacing,"
        f" back-pressure,\n"
        f"            progress, or tee-to-sink.\n"
        f"\n"
        f"        Yields\n"
        f"        ------\n"
        f"        {nd}\n"
        f"            One output block per iteration.\n"
        f'        """\n'
        f"\n"
        f"    def __iter__(self) -> Iterator[{nd}]:\n"
        f'        """Iterate output blocks using the default block size."""\n'
    )

    return {
        "stream_iter_block": stream_iter_block,
        "stream_def_entry": stream_def_entry,
        "stream_tp_iter": stream_tp_iter,
        "stream_type_ready": stream_type_ready,
        "pyi_stream_typing": pyi_stream_typing,
        "pyi_stream_methods": pyi_stream_methods,
    }
