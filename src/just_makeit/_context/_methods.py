"""_context/_methods.py — method/property context builders.

Contains _bench_method_block, make_methods_ctx, and make_properties_ctx.
"""

from __future__ import annotations

import re

from .. import _codec as _codec
from .. import _coerce
from .. import _record
from .. import _types as T
from .._types import (
    _CTYPE_META,
    _NP_ENUM,
    _CTYPE_TO_NPY,
    _KIND_PY_TEST_VAL,
    record_tuple_build,
    _ctype_display,
    is_array_param_type,
    array_elem_ctype,
    c_param_parts,
)
from .._docstring import (
    max_out_is_state_only,
    member_doc,
    render_numpy_doc,
    render_runtime_doc,
    scaffold_doc_block,
    summary_docstring,
)
from dataclasses import replace

from .._gluedoc import glue_methods, max_out_method
from ._parse import (
    _build_ml_doc,
    _build_params_parse,
    _step_parse_block,
    capsule_new_c as _capsule_new_c,
)


# Scalar C-kind -> Python annotation, shared by make_methods_ctx's param/
# return stubs and make_properties_ctx's property stubs — keyed off
# _CTYPE_META's "kind" rather than a parallel ctype table, so a new ctype
# only needs its _CTYPE_META entry (see gh-450, where a second table in
# _stubs.py drifted out of sync with this one).
def _pyi_scalar(ctype: str) -> str:
    # Thin alias for the canonical scalar→Python-builtin mapping in _types, so
    # the method-return and state-accessor annotations cannot drift.
    return T.scalar_py_annotation(ctype)


def _pyi_records(methods: list[dict], doc_blocks: dict | None) -> str:
    """The `.pyi` record-class block for a component's methods (gh-646).

    Rendered above the component class, since the class annotates a return with
    a record's name. `_stubs.make_module_pyi` emits the same block from the
    same builder for the module-aggregated stub.
    """
    body = _record.pyi_classes(methods, doc_blocks)
    return f"\n{body}\n" if body else ""


def _pyi_ndarray(ctype: str) -> str:
    elem = ctype[:-2] if ctype.endswith("[]") else ctype
    meta = _CTYPE_META.get(elem)
    return f"NDArray[{meta['py_type']}]" if meta else "NDArray[Any]"


def _stub_params(
    arg_type: str, params: list[dict]
) -> tuple[list[str], list[tuple[str, str]]]:
    """The Python-facing arguments of one method, for signature and prose.

    Returns both renderings of the same list so they cannot disagree
    (gh-642). The runtime ``PyMethodDef`` doc and the ``.pyi`` stub must
    document the identical arguments in the identical order; before this was
    hoisted, the stub built its list several hundred lines below the point
    where each method shape emitted its runtime literal, so the runtime face
    had nothing to share and carried the ``@brief`` alone.

    Binding-level arguments (``count``, ``out=``) are deliberately absent:
    they belong to the signature, which appends them, and not to the
    ``Parameters`` section, which documents what the algorithm takes.

    Parameters
    ----------
    arg_type : str
        The method's primary input type, or ``"void"``. A non-void one is
        always Python-visible as ``x``.
    params : list of dict
        Declared extra parameters, in manifest order.

    Returns
    -------
    tuple
        ``(signature_parts, doc_params)``. ``signature_parts`` are
        ``"name: annotation"`` strings carrying any default; ``doc_params``
        are ``(name, annotation)`` pairs with the default stripped, which is
        the form both docstring renderers take.

    Examples
    --------
    >>> _stub_params("float", [])
    (['x: float'], [('x', 'float')])
    >>> _stub_params("void", [{"name": "mu", "type": "double",
    ...                        "default": "0.5"}])
    (['mu: float = 0.5'], [('mu', 'float')])
    """
    # (name, annotation, signature-only default suffix)
    fields: list[tuple[str, str, str]] = []
    if arg_type != "void":
        ann = (
            _pyi_ndarray(arg_type[:-2])
            if arg_type.endswith("[]")
            else _pyi_scalar(arg_type)
        )
        fields.append(("x", ann, ""))
    for p in params:
        pt = p["type"]
        if pt.endswith("[]"):
            fields.append((p["name"], _pyi_ndarray(pt[:-2]), ""))
        elif p.get("capsule"):
            # gh-432: a capsule-typed param takes the named PyCapsule, any
            # wrapper exposing `_capsule`, or None (detach).
            fields.append((p["name"], "object | None", ""))
        else:
            # gh-240: a defaulted scalar renders as an optional kwarg.
            suffix = (
                f" = {p['default']}"
                if p.get("default") not in (None, "")
                else ""
            )
            fields.append((p["name"], _pyi_scalar(pt), suffix))
    return (
        [f"{n}: {a}{s}" for n, a, s in fields],
        [(n, a) for n, a, _ in fields],
    )


# A cast-prefixed numpy buffer accessor inside a kernel call argument, e.g.
# ``(const float *)PyArray_DATA(x_arr)`` or ``(size_t)PyArray_SIZE(x_arr)``.
# The leading cast carries the C type, so it doubles as the hoisted-local type.
_NOGIL_PYARRAY_RE = re.compile(
    r"\((?P<cast>[^()]+?)\)\s*PyArray_(?:DATA|SIZE)\(\w+\)"
)


def _hoist_for_nogil(call_expr: str) -> tuple[str, str]:
    """Hoist every ``PyArray_DATA/SIZE`` out of a kernel-call expression.

    Releasing the GIL around the C kernel is only sound if **no Python C-API**
    runs while it is dropped — but the generated call inlines
    ``PyArray_DATA``/``PyArray_SIZE`` directly. This lifts each such (cast)
    expression into a local declared *before* the ``Py_BEGIN_ALLOW_THREADS``
    block and rewrites the call to reference it. Everything else
    (``self->handle``, ``self->_<m>_buf``, scalar params, ``(size_t)n``) is
    plain memory and is left in place.

    Returns ``(hoist_decls, rewritten_call_expr)``.
    """
    decls: list[str] = []

    def _sub(m: "re.Match[str]") -> str:
        local = f"_ng{len(decls)}"
        decls.append(f"    {m.group('cast')} {local} = {m.group(0)};\n")
        return local

    rewritten = _NOGIL_PYARRAY_RE.sub(_sub, call_expr)
    return "".join(decls), rewritten


def _kernel_call_block(call_expr: str, nogil: bool) -> str:
    """Emit ``size_t n_out = <call>;`` — GIL-released when *nogil*.

    With *nogil*, numpy accessors are hoisted above the
    ``Py_BEGIN_ALLOW_THREADS`` block (see :func:`_hoist_for_nogil`) so the
    kernel runs lock-free — valid only when the object is not shared across
    threads concurrently (one object per stream). The caller's realloc /
    error-raising stays under the GIL, above this block.
    """
    if not nogil:
        return f"    size_t n_out = {call_expr};\n"
    hoist, rewritten = _hoist_for_nogil(call_expr)
    return (
        "    /* nogil: GIL released across the pure-C kernel — sound only when\n"
        "     * this object is not shared across threads concurrently (one\n"
        "     * object per stream); the kernel touches only this object's\n"
        "     * state/buffers and the caller's input. */\n"
        f"{hoist}"
        "    size_t n_out;\n"
        "    Py_BEGIN_ALLOW_THREADS\n"
        f"    n_out = {rewritten};\n"
        "    Py_END_ALLOW_THREADS\n"
    )


def _single_kernel_block(ret_disp: str, call_expr: str, nogil: bool) -> str:
    """Emit ``<ret> _r = <call>;`` for a single-record method — GIL-released
    when *nogil* (gh-261).

    Mirrors :func:`_kernel_call_block` but for a by-value struct return: ``_r``
    is declared *outside* the ``Py_BEGIN_ALLOW_THREADS`` block so it survives to
    the ``SET_ITEM`` loop, and numpy accessors in the call are hoisted above the
    block (the input array stays alive — its ``Py_DECREF`` runs after, under the
    GIL). Non-nogil reproduces the original single line by line."""
    if not nogil:
        return f"    {ret_disp} _r = {call_expr};\n"
    hoist, rewritten = _hoist_for_nogil(call_expr)
    return (
        "    /* nogil: GIL released across the pure-C kernel — sound only when\n"
        "     * this object is not shared across threads concurrently (one\n"
        "     * object per stream). */\n"
        f"{hoist}"
        f"    {ret_disp} _r;\n"
        "    Py_BEGIN_ALLOW_THREADS\n"
        f"    _r = {rewritten};\n"
        "    Py_END_ALLOW_THREADS\n"
    )


# ---------------------------------------------------------------------------
# _bench_method_block
# ---------------------------------------------------------------------------


def _bench_method_block(component: str, m: dict) -> str:
    """Return a self-contained C bench timing block for method *m*.

    Returns an empty string when the method should not be benchmarked
    (``bench == False`` in the method dict, or ``variable_output`` / ``out_type``
    methods whose output size is indeterminate at bench time).

    gh-529: an ``out_type`` method allocates a fresh output buffer per call and
    takes a trailing ``*out`` pointer the C signature (and the ext wrapper)
    supply, but the timing loop here has no such buffer to pass — only the
    ``batch`` shape allocates one. Every other branch would emit a call missing
    the ``out`` argument and the generated benchmark would not compile. Its
    output is sized from a runtime parameter, exactly the ``variable_output``
    situation, so it is skipped the same way rather than benched with a
    fabricated buffer.

    The generated block is wrapped in ``{}`` for scope isolation so that
    per-method locals (buffers, sink variables) do not conflict with each
    other or with the surrounding ``step()``/``steps()`` bench variables.

    Parameters
    ----------
    component : str
        Snake-case component name (e.g. ``"fir"``).
    m : dict
        Method dict with keys: name, arg_type, return_type, variable_output,
        batch, params.  Same shape as the dicts in ``[[comp.methods]]``.

    Returns
    -------
    str
        C source fragment (indented 4 spaces, blank line before/after) ready
        to paste into ``main()`` of the bench executable, or ``""`` to skip.
    """
    if (
        m.get("bench") is False
        or m.get("variable_output")
        or m.get("varargs")
        or m.get("out_type")  # gh-529: no out buffer in the timing loop
        or m.get("codec")  # gh-554: a codec-pack method has no C core to time
    ):
        return ""

    name: str = m["name"]
    # gh-805 §A2: benchmark the C symbol the method actually binds. The local
    # variable names below stay keyed on `name` (they are C identifiers in the
    # generated bench, and `fn` may repeat across methods).
    c_fn: str = m.get("fn", "") or f"{component}_{name}"
    arg_type: str = m.get("arg_type", "void")
    return_type: str = m.get("return_type", "float _Complex")
    batch: bool = m.get("batch", False)
    params: list[dict] = m.get("params", [])
    result_fields: list[dict] = m.get("result_fields", [])
    single_record: bool = m.get("single", False)
    max_results: int = int(m.get("max_results", 64))

    has_arg = arg_type != "void"
    has_ret = return_type != "void"
    is_array_arg = arg_type.endswith("[]")

    if has_arg:
        arg_elem = arg_type[:-2] if is_array_arg else arg_type
        arg_meta = _CTYPE_META[arg_elem]
        arg_elem_disp = _ctype_display(arg_elem)
        arg_zero = arg_meta["zero"]

    if has_ret:
        ret_meta = _CTYPE_META.get(return_type)
        ret_disp = _ctype_display(return_type) if ret_meta else return_type

    param_args = ""
    for p in params:
        pt = p["type"]
        if is_array_param_type(pt):
            param_args += ", NULL, 0"
        else:
            pm = _CTYPE_META.get(pt, {})
            param_args += f", {pm.get('zero', '0')}"

    lines: list[str] = [f"    /* bench: {name}() */", "    {"]
    lines.append(f"        double _times_{name}[ITERATIONS];")

    if result_fields and not single_record:
        # gh-244: a results[]/max_results method returns a count (size_t),
        # not `return_type` directly — the call signature and sink differ
        # from every other shape below, so this is handled first and skips
        # the generic branches entirely.
        rf_disp = _ctype_display(return_type)
        lines.append(f"        {rf_disp} {name}_results[{max_results}];")
        if has_arg:
            lines += [
                f"        {arg_elem_disp} *{name}_in ="
                f" ({arg_elem_disp} *)calloc(BENCH_N,"
                f" sizeof({arg_elem_disp}));",
                f'        if (!{name}_in) {{ fprintf(stderr, "OOM\\n"); return 1; }}',
            ]
            call = (
                f"{c_fn}(obj, {name}_in, BENCH_N,"
                f" {name}_results, {max_results})"
            )
        else:
            call = f"{c_fn}(obj, {name}_results, {max_results})"
        lines.append(f"        volatile size_t {name}_sink;")
        lines += [
            f"        for (int i = 0; i < 4; i++) {name}_sink = {call};",
            "        for (int r = 0; r < ITERATIONS; r++) {",
            "            clock_gettime(CLOCK_MONOTONIC, &t0);",
            f"            {name}_sink = {call};",
            "            clock_gettime(CLOCK_MONOTONIC, &t1);",
            f"            _times_{name}[r] = elapsed_sec(&t0, &t1);",
            "        }",
        ]
        if has_arg:
            lines.append(f"        free({name}_in);")
    elif batch:
        if has_arg:
            # gh-139: the input buffer holds elements; an array arg_type
            # (`T[]`) must use the element display, not `T[] *…`.
            lines += [
                f"        {arg_elem_disp} *{name}_in ="
                f" ({arg_elem_disp} *)calloc(BENCH_N,"
                f" sizeof({arg_elem_disp}));",
            ]
        ret_disp_b = _ctype_display(return_type)
        lines += [
            f"        {ret_disp_b} *{name}_out ="
            f" ({ret_disp_b} *)malloc("
            f"BENCH_N * sizeof({ret_disp_b}));",
        ]
        chk_vars = f"{name}_in && {name}_out" if has_arg else f"{name}_out"
        lines += [
            f'        if (!({chk_vars})) {{ fprintf(stderr, "OOM\\n"); return 1; }}',
        ]
        in_arg = f" {name}_in," if has_arg else ""
        call = f"{c_fn}(obj,{in_arg} BENCH_N, {name}_out)"
        lines += [
            "        for (int i = 0; i < 4; i++)",
            f"            {call};",
            "        for (int r = 0; r < ITERATIONS; r++) {",
            "            clock_gettime(CLOCK_MONOTONIC, &t0);",
            f"            {call};",
            "            clock_gettime(CLOCK_MONOTONIC, &t1);",
            f"            _times_{name}[r] = elapsed_sec(&t0, &t1);",
            "        }",
        ]
        if has_arg:
            lines.append(f"        free({name}_in);")
        lines.append(f"        free({name}_out);")

    elif is_array_arg:
        lines += [
            f"        {arg_elem_disp} *{name}_in ="
            f" ({arg_elem_disp} *)calloc("
            f"BENCH_N, sizeof({arg_elem_disp}));",
            f'        if (!{name}_in) {{ fprintf(stderr, "OOM\\n"); return 1; }}',
        ]
        if has_ret:
            lines.append(f"        volatile {ret_disp} {name}_sink;")
        sink = f"{name}_sink = " if has_ret else ""
        call = f"{c_fn}(obj, {name}_in, BENCH_N{param_args})"
        lines += [
            "        for (int i = 0; i < 4; i++)",
            f"            {sink}{call};",
            "        for (int r = 0; r < ITERATIONS; r++) {",
            "            clock_gettime(CLOCK_MONOTONIC, &t0);",
            f"            {sink}{call};",
            "            clock_gettime(CLOCK_MONOTONIC, &t1);",
            f"            _times_{name}[r] = elapsed_sec(&t0, &t1);",
            "        }",
            f"        free({name}_in);",
        ]

    else:
        if has_ret:
            lines.append(f"        volatile {ret_disp} {name}_sink;")
        sink = f"{name}_sink = " if has_ret else ""
        in_arg = f", {arg_zero}" if has_arg else ""
        call = f"{c_fn}(obj{in_arg}{param_args})"
        lines += [
            f"        for (int i = 0; i < 16; i++) {sink}{call};",
            "        for (int r = 0; r < ITERATIONS; r++) {",
            "            clock_gettime(CLOCK_MONOTONIC, &t0);",
            "            for (int i = 0; i < BENCH_N; i++)",
            f"                {sink}{call};",
            "            clock_gettime(CLOCK_MONOTONIC, &t1);",
            f"            _times_{name}[r] = elapsed_sec(&t0, &t1);",
            "        }",
        ]

    add_line = f'        jm_bench_add(&_bench, "{name}", _times_{name}, ITERATIONS, BENCH_N);'
    lines += [
        add_line,
        "        {",
        "            double _s = 0.0;",
        f"            for (int r = 0; r < ITERATIONS; r++) _s += _times_{name}[r];",
        f'            printf("  {name}()  %8.1f MSa/s\\n",',
        "                   (double)BENCH_N / (_s / ITERATIONS) / 1e6);",
        "        }",
    ]

    lines.append("    }")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# make_methods_ctx
# ---------------------------------------------------------------------------


def serializable_triplet_parts(
    component: str, Component: str, wrapper_prefix: str
) -> tuple[list[str], str, str]:
    """The gh-400 state-blob binding for one object, as reusable text.

    Returns ``(c_funcs, pymethoddef_rows, pyi_stubs)``: the three
    ``state_bytes``/``get_state``/``set_state`` CPython wrapper functions, the
    three ``PyMethodDef`` rows, and the ``.pyi`` stub block. Shared by the
    regenerate path (:func:`make_methods_ctx`) and the sacred-fragment
    transplant (:mod:`_docsync`, gh-404) so both emit byte-identical glue.

    ``wrapper_prefix`` is the Python function-name prefix (e.g. ``FooObj``);
    the C calls use ``component`` (``foo_state_bytes`` over ``self->handle``).
    """
    _W = wrapper_prefix
    guard = (
        "    if (!self->handle) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
    )
    c_funcs = [
        (
            f"static PyObject *\n"
            f"{_W}_state_bytes"
            f"({Component}Object *self, PyObject *Py_UNUSED(ignored))\n"
            f"{{\n{guard}"
            f"    return PyLong_FromSize_t("
            f"{component}_state_bytes(self->handle));\n"
            f"}}"
        ),
        (
            f"static PyObject *\n"
            f"{_W}_get_state"
            f"({Component}Object *self, PyObject *Py_UNUSED(ignored))\n"
            f"{{\n{guard}"
            f"    size_t _n = {component}_state_bytes(self->handle);\n"
            f"    PyObject *_b = PyBytes_FromStringAndSize"
            f"(NULL, (Py_ssize_t)_n);\n"
            f"    if (!_b)\n"
            f"        return NULL;\n"
            f"    {component}_get_state(self->handle, PyBytes_AS_STRING(_b));\n"
            f"    return _b;\n"
            f"}}"
        ),
        (
            f"static PyObject *\n"
            f"{_W}_set_state({Component}Object *self, PyObject *arg)\n"
            f"{{\n{guard}"
            f"    if (!PyBytes_Check(arg)) {{\n"
            f"        PyErr_SetString(PyExc_TypeError,"
            f' "set_state expects bytes");\n'
            f"        return NULL;\n"
            f"    }}\n"
            f"    if ((size_t)PyBytes_GET_SIZE(arg)"
            f" != {component}_state_bytes(self->handle)) {{\n"
            f"        PyErr_SetString(PyExc_ValueError,"
            f' "state blob size mismatch");\n'
            f"        return NULL;\n"
            f"    }}\n"
            f"    if ({component}_set_state(self->handle,"
            f" PyBytes_AS_STRING(arg)) != 0) {{\n"
            f"        PyErr_SetString(PyExc_ValueError,"
            f' "set_state rejected the blob");\n'
            f"        return NULL;\n"
            f"    }}\n"
            f"    Py_RETURN_NONE;\n"
            f"}}"
        ),
    ]
    # gh-647: one definition of this prose, rendered to both faces. The
    # literals these replaced had drifted -- get_state named "the engine",
    # a component from a long-gone example rather than the object it
    # documents.
    _glue = glue_methods(_W)
    _meth_c = {
        "state_bytes": "METH_NOARGS",
        "get_state": "METH_NOARGS",
        "set_state": "METH_O",
    }
    pmd = "".join(
        f'    {{"{n}", (PyCFunction){_W}_{n}, {flags},\n'
        f"     {_build_ml_doc(_glue[n].c_doc_lines())}}},\n"
        for n, flags in _meth_c.items()
    )
    _sigs = {
        "state_bytes": "self) -> int",
        "get_state": "self) -> bytes",
        "set_state": "self, blob: bytes) -> None",
    }
    pyi = "\n".join(
        f"    def {n}({sig}:\n" + "\n".join(_glue[n].pyi_doc())
        for n, sig in _sigs.items()
    )
    return c_funcs, pmd, pyi


def _max_out_doc(
    component, name, count_param, max_out_const, block_of, c_fn=""
):
    """The doc for ``<name>_max_out``: header block if authored, else jm's.

    gh-684. Unlike the glue in :mod:`_gluedoc`, ``max_out``'s *value* is
    object-specific and its C body is an ``IMPLEMENT`` stub the author writes
    unless the manifest declared the constant -- so the header always wins and
    jm's prose is only the fallback.

    gh-805 §A2: *c_fn* is the method's resolved C symbol, which the header
    documents. *name* stays the PYTHON face — ``max_out_method`` below renders
    ``<name>_max_out`` for the reader — so the two are deliberately separate
    arguments rather than one. Defaults to the derived symbol.
    """
    blk = block_of(f"{c_fn or f'{component}_{name}'}_max_out")
    gm = max_out_method(name, count_param or "", int(max_out_const or 0))
    if blk is not None:
        gm = replace(gm, block=blk)
    return gm


def _count_default_parts(expr: str, component: str) -> tuple[str, str]:
    r"""Return ``(initialiser, state_alias_line)`` for a ``count_default``.

    gh-657. A void-input ``variable_output`` method binds its capacity as the
    ``count`` keyword, and jm has always initialised it to ``1``. That default
    is the method's entire zero-arg behaviour, and ``1`` is almost never the
    right snapshot size — but the right size (the object's ring length, its
    buffered depth) lives in the user's C, not in the manifest, so it has to
    be declared rather than derived.

    *expr* is C, evaluated once before ``PyArg_ParseTupleAndKeywords`` and
    overridden by any count the caller actually passes. An expression that
    mentions ``state`` gets a local alias for ``self->handle`` so the natural
    ``state->num_taps`` reads correctly; one that does not (a plain integer,
    say) gets no alias, which keeps ``-Wunused-variable`` quiet.

    Parameters
    ----------
    expr : str
        The manifest's ``count_default``. Empty restores the historical ``1``.
    component : str
        Component name, used to spell the state type for the alias.

    Returns
    -------
    tuple of (str, str)
        The initialiser expression, and either an empty string or a complete
        declaration line (newline included) to emit before it.

    Examples
    --------
    >>> _count_default_parts("", "delay")
    ('1', '')
    >>> _count_default_parts("64", "delay")
    ('(Py_ssize_t)(64)', '')
    >>> _count_default_parts("state->num_taps", "delay")
    ('(Py_ssize_t)(state->num_taps)', '    delay_state_t *state = self->handle;\n')
    """
    if not expr:
        return "1", ""
    alias = ""
    if re.search(r"\bstate\b", expr):
        alias = f"    {component}_state_t *state = self->handle;\n"
    return f"(Py_ssize_t)({expr})", alias


def _max_out_count_param_ctx(
    has_arg: bool, has_params: bool, params: "list[dict]"
) -> "tuple[str, str | None]":
    """gh-607: peer of ``_method._max_out_count_param`` — the same per-shape
    count parameter ``*_max_out()`` takes, adapted for this module's
    dict-shaped ``params`` (vs. ``_method.py``'s ``(name, type)`` tuples).
    Duplicated rather than imported: importing ``_method.py`` here would
    cycle (``_method`` -> ``_object`` -> ``_context`` -> ``_context._methods``).
    Keep the two in sync.
    """
    if has_arg:
        return ", size_t n_in", "n_in"
    if has_params:
        for p in params:
            if is_array_param_type(p["type"]):
                return f", size_t {p['name']}_len", f"{p['name']}_len"
        return "", None
    return ", size_t n", "n"


def make_methods_ctx(
    component: str,
    Component: str,
    methods: list[dict],
    pkg: str = "",
    py_create_args: str = "",
    no_state: bool = False,
    doc_blocks: dict | None = None,
    serializable: bool = False,
    codecs: dict | None = None,
) -> dict[str, str]:
    """Generate template context keys for extra named methods.

    Each method dict has: name, arg_type ("void" or a _CTYPE_META key),
    return_type (a _CTYPE_META key), variable_output (bool),
    batch (bool), and optionally multi_output (list of additional return
    ctypes).

    batch=True generates a 1:1-rate array method:
      C: void comp_name(state_t *, [const arg_t *in,] size_t n, ret_t *out)
      Python: allocates output array each call with PyArray_SimpleNew.

    pkg and py_create_args are used in the generated PyMethodDef docstrings
    to produce working doctests; omitting them produces functional but
    package-anonymous examples.
    """

    _EMPTY: dict = {
        "method_decls": "",
        "extra_buf_fields": "",
        "extra_buf_free": "",
        "extra_buf_alloc": "",
        "extra_methods_c": "",
        "extra_methods_pymethoddef": "",
        "pyi_extra_methods": "",
        "pyi_records": "",
        "bench_methods_timing_block": "",
        "varargs_binding_files": [],
    }
    if not methods and not serializable:
        return _EMPTY

    wrapper_prefix = f"{Component}Obj" if no_state else Component

    guard = (
        "    if (!self->handle) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
    )

    decl_lines: list[str] = []
    buf_fields: list[str] = []
    buf_free: list[str] = []
    buf_alloc: list[str] = []
    method_c_parts: list[str] = []
    pmd_lines: list[str] = []
    pyi_lines: list[str] = []
    varargs_binding_files: list[str] = []
    user_has_reset: bool = any(m["name"] == "reset" for m in methods)

    for m in methods:
        name: str = m["name"]
        # gh-805 §A2: the C symbol this method binds. Derived from the
        # component by default — `fn` overrides it so existing C with its own
        # established prefix can be adopted without renaming a public API, and
        # so a hot-path function and its validating variant can coexist
        # (`name = "emit"`, `fn = "dp_tlm_emit_checked"`).
        #
        # Resolved ONCE, here, and used for every C symbol this method forms
        # below — including the `_max_out` companion, so the pair cannot
        # disagree. `name` remains the PYTHON face throughout: the PyMethodDef
        # row, the .pyi and the docstrings are unaffected by `fn`.
        #
        # `fn` is already the spelling on properties, getters, setters,
        # composer fields and handle methods; this is that key reaching one
        # more place, not a new concept.
        c_fn: str = m.get("fn", "") or f"{component}_{name}"

        # Summary precedence: TOML `doc` override > header @brief > name
        # fallback. This one is still resolved here because the *runtime*
        # PyMethodDef doc is brief-only (gh-642 is the parity ask); the .pyi's
        # param/return prose is resolved inside render_numpy_doc.
        #
        # Keyed on the C symbol, not the derived name: the header documents
        # the function actually declared, so an `fn`-overridden method finds
        # its Doxygen block under `fn`. Same fallback shape `_handle.py`
        # already uses for its own `fn`-carrying methods.
        _block = (doc_blocks or {}).get(c_fn) or (doc_blocks or {}).get(
            f"{component}_{name}"
        )
        _brief = m.get("doc") or (
            _block.brief if (_block and _block.brief) else ""
        )

        # ── varargs method (*args, **kwargs) ─────────────────────────────
        if m.get("varargs"):
            binding_file = f"{c_fn}_core.c"
            varargs_binding_files.append(binding_file)
            extern_decl = (
                f"/* varargs binding — body in {binding_file} */\n"
                f"extern PyObject *\n"
                f"{c_fn}"
                f"(PyObject *, PyObject *, PyObject *);\n"
            )
            method_c_parts.append(extern_decl)
            pmd_lines.append(
                f'    {{"{name}",'
                f" (PyCFunction)(void *){c_fn},"
                f" METH_VARARGS | METH_KEYWORDS,\n"
                f'     "{name}(*args, **kwargs)."}},\n'
            )
            pyi_lines.append(
                f"    def {name}(self, *args: Any, **kwargs: Any)"
                f" -> Any:\n"
                f'        """{name.replace("_", " ").capitalize()}."""\n'
            )
            continue

        # ── manual_stub method (hand-written C binding, jm emits nothing
        # C-side; only a placeholder .pyi entry the splice engine preserves
        # verbatim across regen — gh-428) ─────────────────────────────────
        if m.get("manual_stub"):
            pyi_lines.append(
                f"    def {name}(self, *args: Any, **kwargs: Any)"
                f" -> Any:\n"
                f'        """<<MANUAL_STUB>> hand-write this signature/'
                f"docstring in the .pyi — jm preserves it verbatim on"
                f' future regens."""\n'
            )
            continue

        # ── codec-pack method (variant-typed input; gh-554) ──────────────
        # jm generates the whole binding — parse, per-code pack of a
        # scalar-or-sequence, the sink call, and rc->error — from the
        # declared `[codec.X]` table. No hand marshaler.
        if _codec.is_codec_method(m):
            cdc = (codecs or {}).get(m["codec"])
            if cdc is None:
                raise _codec.CodecError(
                    f"{component}.{name}: method references codec "
                    f"'{m['codec']}', which is not declared in [codec.*]."
                )
            c_body, pmd, pyi = _codec.render_pack(
                component, Component, wrapper_prefix, m, cdc, guard
            )
            method_c_parts.append(c_body)
            pmd_lines.append(pmd)
            pyi_lines.append(pyi)
            continue

        arg_type: str = m.get("arg_type", "void")
        return_type: str = m.get("return_type", "float _Complex")
        variable_output: bool = m.get("variable_output", False)
        batch: bool = m.get("batch", False)
        multi_output: list[str] = m.get("multi_output", [])
        params: list[dict] = m.get("params", [])
        result_fields: list[dict] = m.get("result_fields", [])
        max_results: int = int(m.get("max_results", 64))
        # gh-244: return ONE named record (PyStructSequence) rather than a
        # list[tuple]. The C kernel returns the record struct by value.
        single_record: bool = m.get("single", False)
        # gh-257 (`record_name`, the chosen public name) and gh-261
        # (`record_module`, the qualifier for the structseq's __module__) are
        # both read straight off `m` by `_record`, which the .pyi writers share
        # — see the descriptor emit below.
        none_on_empty: bool = m.get("none_on_empty", False)
        # Opt-in GIL release around the pure-C kernel (thread-per-shard
        # scaling). v1 covers the variable_output execute shapes.
        nogil: bool = m.get("nogil", False)
        # gh-432: the C `int` return is a status code (0 = OK, non-zero =
        # failure) — bound as `-> None`, raising ValueError on failure (the
        # same contract the serializable set_state glue emits). Fixed-output
        # methods only.
        status_return: bool = m.get("status_return", False)
        # gh-805 §B: the int return is a VALUE unless it is negative, in
        # which case it is an error code — `open`/`read`/`snprintf` and every
        # registry-style lookup work this way. Distinct from `status_return`,
        # which claims the whole int: here a successful call still has a
        # number to give back, so the `.pyi` return annotation is unchanged
        # and only the failure path differs. The CLI rejects the two together.
        error_negative: bool = m.get("error_negative", False)
        # The exception category and text for that failure. `error` reuses
        # gh-482's ERROR_CATEGORIES (already validated at declaration time),
        # so this renders a name jm has vetted rather than arbitrary C.
        error_category: str = m.get("error", "") or "ValueError"
        error_message: str = m.get("error_message", "")
        # gh-138: opt into the 5-arg `(..., out, size_t max_out)` form for a
        # variable_output method whose C API forwards an explicit output
        # capacity (the buffer cap jm already tracks for grow-on-demand).
        pass_capacity: bool = m.get("pass_capacity", False)
        # gh-788 gap 1: the method writes rows of a POD C struct, and the
        # result is a numpy STRUCTURED array whose dtype IS that struct's
        # layout. The key names the struct (`dp_tlm_rec_t`); `result_fields`
        # names its members. Everything downstream then treats the struct as
        # the output element type, so the C prototype (`dp_tlm_rec_t *out`)
        # and the data-pointer cast fall out of the existing variable_output
        # machinery -- only the numpy allocation differs, because a struct
        # has no `_CTYPE_META` entry and so no single NPY_ enum.
        record_dtype: str = str(m.get("record_dtype", "") or "").strip()
        # gh-657: a void-input variable_output method's `count` is the whole
        # user-facing knob — its default IS the method's zero-arg behaviour.
        # jm's own `1` was inert until gh-607 started feeding that count to
        # max_out() and dropped the clamp that had been rescuing it, at which
        # point `obj.ptr()` silently went from "snapshot everything" to "give
        # me one sample". jm cannot derive the natural capacity (it lives in
        # the user's C), so the manifest declares it.
        _count_default: str = str(m.get("count_default", "") or "").strip()
        _count_init, _count_alias = _count_default_parts(
            _count_default, component
        )
        _cap_param = ", size_t max_out" if pass_capacity else ""
        # Placeholder that the three `call_data` builders below drop into the
        # output-argument slot; each emit site then substitutes whatever it
        # actually writes into (a per-call NumPy allocation, or the caller's
        # `out=` array). Since gh-604 there is no instance buffer of this name
        # — the token is purely a splice point, so it is defined once here
        # rather than spelled out at five call sites.
        _VO_BUF_TOKEN = f"self->_{name}_buf"
        # The translation-unit-unique prefix for this method's file-scope
        # statics. Shared by the structseq descriptor (gh-244) and the
        # structured dtype (gh-788) so both name their statics the same way
        # and `_docsync`/`_apply` have one shape to look for.
        _sid = f"{wrapper_prefix}_{name}"

        ret_disp = _ctype_display(return_type)
        _ret_elem = (
            return_type[:-2] if return_type.endswith("[]") else return_type
        )
        ret_meta = _CTYPE_META.get(_ret_elem)
        ret_np = _NP_ENUM.get(ret_meta["py_type"]) if ret_meta else "NPY_FLOAT"

        out_type: str | None = m.get("out_type")
        out_divisor: int = int(m.get("out_divisor", 1))
        # The variable-output buffer holds *elements*, so a `T[]` return type
        # (or out_type) must be reduced to its element type `T`; otherwise the
        # buffer field, the `*out` param, sizeof(), and the NumPy enum all
        # render the invalid `T[] *out` / `sizeof(T[])` (gh-201 follow-up).
        # gh-788: a record_dtype names the output element outright -- it is
        # the struct the kernel writes rows of. It wins over `out_type` and
        # `return_type` so the `*out` parameter, the `sizeof`, the data
        # pointer cast and the `.pyi` all describe the same struct without
        # the manifest having to spell it three times.
        _vo_out_src = (
            record_dtype
            if (variable_output and record_dtype)
            else (out_type if (variable_output and out_type) else return_type)
        )
        _vo_out_elem = (
            _vo_out_src[:-2] if _vo_out_src.endswith("[]") else _vo_out_src
        )
        _vo_out_disp = _ctype_display(_vo_out_elem)
        _vo_out_meta = _CTYPE_META.get(_vo_out_elem)
        _vo_out_np = (
            _NP_ENUM.get(_vo_out_meta["py_type"])
            if _vo_out_meta
            else "NPY_FLOAT"
        )
        has_params = bool(params)
        has_arg = arg_type != "void"
        # gh-642: the Python-facing argument list, resolved once here so the
        # runtime PyMethodDef literal each shape branch emits below and the
        # .pyi stub built at the end of this loop document the same arguments
        # with the same annotations. It used to be built only at the stub, so
        # the runtime face had nothing to share and carried the @brief alone.
        _sig_parts, _doc_params = _stub_params(arg_type, params)
        # ...and the return annotation with it, for the same reason: it is the
        # type line of the `Returns` section on both faces.
        if status_return:
            # gh-432: status returns bind as None (raise on failure).
            _ret_ann = "None"
        elif result_fields and single_record:
            # gh-244/gh-646: one named record — a PyStructSequence (a tuple
            # subclass). This used to annotate as a bare tuple of the field
            # types, which types unpacking but leaves `r.enob` unknown to the
            # checker and undocumented to the reader. The stub now declares the
            # record class itself (see `pyi_records` below) and names it here.
            _ret_ann = _record.public_name(m)
        elif result_fields and not record_dtype:
            # gh-788: a record_dtype method also carries `result_fields`, but
            # they describe the dtype's columns, not a list of per-row tuples
            # -- it returns ONE structured ndarray. Without this guard the
            # richer shape is shadowed by the older one that merely mentions
            # the same key.
            _ret_ann = "list[tuple]"
        elif variable_output:
            # Only the record case reads the resolved element type here; the
            # `out_type` case is left reporting `return_type` exactly as
            # before, since changing it is a separate behaviour question and
            # not this issue's.
            _all_rts = [record_dtype or return_type] + list(multi_output)
            _ndarrays = [_pyi_ndarray(rt) for rt in _all_rts]
            _ret_ann = (
                f"tuple[{', '.join(_ndarrays)}]"
                if len(_ndarrays) > 1
                else _ndarrays[0]
            )
        elif out_type:
            # gh-529: `out_type` on a method allocates a fresh output array
            # per call and returns it -- the C wrapper (the `elif out_type`
            # branch below) does exactly what a function's out_type does, and
            # the PyMethodDef docstring already says `-> ndarray`. Only this
            # annotation lagged, reporting the scalar `return_type` and so
            # contradicting both. `_stubs._obj_stub` carries the peer of this
            # branch for the module-aggregated stub; the two must move
            # together (see tests/test_gh529_method_out_type_pyi.py).
            _ret_ann = _pyi_ndarray(out_type)
        else:
            _ret_ann = _pyi_scalar(return_type)

        # The Python-facing argument names, for the signature line each shape
        # puts at the top of its runtime doc. Derived from the same list as
        # the Parameters section so the two cannot contradict each other —
        # which they did: the variable_output shape hard-coded `x` and
        # dropped every declared param, so a documented `run(x, gain)`
        # advertised `run(x)` directly above a Parameters block listing
        # `gain`. Same shape as the gh-657 report, where help() advertised
        # `ptr(n=1)` against a kwlist that bound `count`.
        _doc_names = [n for n, _ in _doc_params]
        _has_header_examples = bool(_block and _block.examples)

        def _demo(lines: list[str]) -> list[str]:
            """The synthesized doctest, dropped when the header wrote one.

            gh-642 renders the header's ``@code`` as a real ``Examples``
            section at runtime, so emitting jm's placeholder demo underneath
            it would put two example blocks in one docstring — the second one
            constructing the object a different way than the author just
            showed. The author's wins; jm's is the fallback it always was.
            """
            return [] if _has_header_examples else lines

        def _runtime_doc(default_summary: str) -> list[str]:
            """This method's runtime numpy block, summary resolved.

            Closes over the loop's per-method state so each shape branch below
            passes only what differs: the sentence to use when neither the
            manifest nor the header supplies one. Precedence is unchanged —
            TOML ``doc`` > header ``@brief`` > *default_summary* — because
            ``_brief`` already resolved the first two.
            """
            return render_runtime_doc(
                _block, name, _doc_params, _ret_ann, _brief or default_summary
            )

        # gh-219 follow-up: a method's primary array input is sometimes
        # declared as the sole entry in `params` (arg_type="void" +
        # params=[{array}]) rather than via `arg_type` directly -- doppler's
        # universal idiom for this shape. That's functionally the same as
        # `has_arg` for the purposes of the optional `out=` buffer feature;
        # only genuine *extra* params (e.g. Farrow.delay(x, mu)) should stay
        # ineligible (gh-412 kept those positional-or-keyword, no `out=`).
        _single_array_param = (
            not has_arg
            and len(params) == 1
            and is_array_param_type(params[0]["type"])
        )
        if has_arg:
            _arg_elem = arg_type[:-2] if arg_type.endswith("[]") else arg_type
            # gh-139: a block method's input is `const <elem> *in`. Use the
            # element display so an array arg_type (`T[]`) does not render the
            # invalid `const T[] *in` / `(const T[] *)` cast. Scalar arg types
            # are their own element type, so this is a no-op for them. (The
            # scalar `arg_disp x` decls below are only reached when arg_type is
            # not an array.)
            arg_disp = _ctype_display(_arg_elem)
            arg_meta = _CTYPE_META[_arg_elem]
            arg_np = _NP_ENUM[arg_meta["py_type"]]

        _ndecl = len(decl_lines)

        if not has_arg:
            _in_example = ""
            _in_dtype_str = ""
        elif arg_type.endswith("[]"):
            _elem = arg_type[:-2]
            _in_dtype_str = (
                _CTYPE_META[_elem]["py_type"]
                if _elem in _CTYPE_META
                else "np.float32"
            )
            _in_example = f"np.zeros(4, dtype={_in_dtype_str})"
        elif arg_type in _CTYPE_META:
            _in_dtype_str = _CTYPE_META[arg_type]["py_type"]
            _kind = _CTYPE_META[arg_type]["kind"]
            _in_example = _KIND_PY_TEST_VAL.get(_kind, "1")
        else:
            _in_dtype_str = "np.float32"
            _in_example = "x"
        _from_line = [f"    >>> from {pkg} import {Component}"] if pkg else []
        _obj_line = f"    >>> obj = {Component}({py_create_args})"

        # gh-581: every `out=` branch below requires the exact output dtype
        # before marshaling, so FROM_OTF cannot cast the caller's buffer into a
        # temp that the kernel fills and then discards. Two flavors: with an
        # input array already owned (release it on the reject path) and without.
        _out_guard_in = _coerce.out_buffer_guard(
            "out_obj",
            ret_np,
            decrefs="Py_DECREF(in_arr);",
            indent=" " * 8,
        )
        _out_guard = _coerce.out_buffer_guard(
            "out_obj", ret_np, indent=" " * 8
        )

        # ── batch method ─────────────────────────────────────────────────
        if batch:
            if has_arg:
                decl_lines.append(
                    f"void {c_fn}({component}_state_t *state,"
                    f" const {arg_disp} *in, size_t n, {ret_disp} *out);"
                )
                # gh-222: fixed-size (1:1) batch methods accept an optional
                # `out=` buffer — write in place and return it, else allocate.
                # Always available (no knob), matching the built-in steps(x,
                # out=) path and the variable_output out= sibling.
                wrapper = (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}"
                    f"({Component}Object *self,"
                    f" PyObject *args, PyObject *kwds)\n"
                    f"{{\n"
                    f"{guard}"
                    f'    static char *_kwlist[] = {{"x", "out", NULL}};\n'
                    f"    PyObject *in_obj = NULL;\n"
                    f"    PyObject *out_obj = NULL;\n"
                    f"    if (!PyArg_ParseTupleAndKeywords("
                    f'args, kwds, "O|O",\n'
                    f"            _kwlist, &in_obj, &out_obj))\n"
                    f"        return NULL;\n"
                    f"    PyArrayObject *in_arr ="
                    f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"        in_obj, {arg_np}, NPY_ARRAY_C_CONTIGUOUS);\n"
                    f"    if (!in_arr) return NULL;\n"
                    f"    Py_ssize_t n = PyArray_SIZE(in_arr);\n"
                    f"    if (out_obj && out_obj != Py_None) {{\n"
                    f"{_out_guard_in}"
                    f"        PyArrayObject *out_arr ="
                    f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"            out_obj, {ret_np},\n"
                    f"            NPY_ARRAY_C_CONTIGUOUS"
                    f" | NPY_ARRAY_WRITEABLE);\n"
                    f"        if (!out_arr)"
                    f" {{ Py_DECREF(in_arr); return NULL; }}\n"
                    f"        if (PyArray_SIZE(out_arr) != n) {{\n"
                    f"            PyErr_Format(PyExc_ValueError,\n"
                    f'                "out length %zd != input length %zd",\n'
                    f"                (Py_ssize_t)PyArray_SIZE(out_arr),"
                    f" (Py_ssize_t)n);\n"
                    f"            Py_DECREF(out_arr);"
                    f" Py_DECREF(in_arr); return NULL;\n"
                    f"        }}\n"
                    f"        {c_fn}(self->handle,\n"
                    f"            (const {arg_disp} *)PyArray_DATA(in_arr),\n"
                    f"            (size_t)n,\n"
                    f"            ({ret_disp} *)PyArray_DATA(out_arr));\n"
                    f"        Py_DECREF(in_arr);\n"
                    f"        return (PyObject *)out_arr;\n"
                    f"    }}\n"
                    f"    npy_intp dims[] = {{n}};\n"
                    f"    PyObject *out ="
                    f" PyArray_SimpleNew(1, dims, {ret_np});\n"
                    f"    if (!out) {{ Py_DECREF(in_arr); return NULL; }}\n"
                    f"    {c_fn}(self->handle,\n"
                    f"        (const {arg_disp} *)PyArray_DATA(in_arr),\n"
                    f"        (size_t)n,\n"
                    f"        ({ret_disp} *)PyArray_DATA"
                    f"((PyArrayObject *)out));\n"
                    f"    Py_DECREF(in_arr);\n"
                    f"    return out;\n"
                    f"}}"
                )
            else:
                decl_lines.append(
                    f"void {c_fn}({component}_state_t *state,"
                    f" size_t n, {ret_disp} *out);"
                )
                # gh-222: count-driven batch generator with optional `out=`.
                wrapper = (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}"
                    f"({Component}Object *self,"
                    f" PyObject *args, PyObject *kwds)\n"
                    f"{{\n"
                    f"{guard}"
                    f'    static char *_kwlist[] = {{"count", "out", NULL}};\n'
                    f"{_count_alias}"
                    f"    Py_ssize_t n = {_count_init};\n"
                    f"    PyObject *out_obj = NULL;\n"
                    f"    if (!PyArg_ParseTupleAndKeywords("
                    f'args, kwds, "|nO",\n'
                    f"            _kwlist, &n, &out_obj))\n"
                    f"        return NULL;\n"
                    f"    if (out_obj && out_obj != Py_None) {{\n"
                    f"{_out_guard}"
                    f"        PyArrayObject *out_arr ="
                    f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"            out_obj, {ret_np},\n"
                    f"            NPY_ARRAY_C_CONTIGUOUS"
                    f" | NPY_ARRAY_WRITEABLE);\n"
                    f"        if (!out_arr) return NULL;\n"
                    f"        if (PyArray_SIZE(out_arr) != n) {{\n"
                    f"            PyErr_Format(PyExc_ValueError,\n"
                    f'                "out length %zd != count %zd",\n'
                    f"                (Py_ssize_t)PyArray_SIZE(out_arr),"
                    f" (Py_ssize_t)n);\n"
                    f"            Py_DECREF(out_arr); return NULL;\n"
                    f"        }}\n"
                    f"        {c_fn}(self->handle,\n"
                    f"            (size_t)n,\n"
                    f"            ({ret_disp} *)PyArray_DATA(out_arr));\n"
                    f"        return (PyObject *)out_arr;\n"
                    f"    }}\n"
                    f"    npy_intp dims[] = {{n}};\n"
                    f"    PyObject *out ="
                    f" PyArray_SimpleNew(1, dims, {ret_np});\n"
                    f"    if (!out) return NULL;\n"
                    f"    {c_fn}(self->handle,\n"
                    f"        (size_t)n,\n"
                    f"        ({ret_disp} *)PyArray_DATA"
                    f"((PyArrayObject *)out));\n"
                    f"    return out;\n"
                    f"}}"
                )
            method_c_parts.append(wrapper)
            _ret_np_str = _CTYPE_META[return_type]["py_type"].replace(
                "np.", ""
            )
            _batch_sig = (
                f"{name}({', '.join(_doc_names) if _doc_names else 'n'},"
                f" out=None) -> ndarray"
            )
            _batch_demo = [
                "",
                "    >>> import numpy as np",
                *_from_line,
                _obj_line,
            ]
            if has_arg:
                _batch_demo += [
                    f"    >>> x = np.zeros(4, dtype={_in_dtype_str})",
                    f"    >>> y = obj.{name}(x)",
                ]
            else:
                _batch_demo.append(f"    >>> y = obj.{name}(4)")
            _batch_demo += [
                "    >>> y.shape",
                "    (4,)",
                "    >>> y.dtype",
                f"    dtype('{_ret_np_str}')",
            ]
            _batch_doc_lines = [
                _batch_sig,
                "",
                *_runtime_doc(
                    f"1:1-rate batch transform. Returns an ndarray of dtype"
                    f" {_ret_np_str}."
                ),
                *_demo(_batch_demo),
            ]
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction){wrapper_prefix}_{name},'
                f" METH_VARARGS | METH_KEYWORDS,\n"
                f"     {_build_ml_doc(_batch_doc_lines)}}},\n"
            )
            for _j in range(_ndecl, len(decl_lines)):
                _doc = scaffold_doc_block(decl_lines[_j], name)
                if _doc:
                    decl_lines[_j] = _doc + "\n" + decl_lines[_j]
            continue

        # ── declarations for _core.h ─────────────────────────────────────
        # gh-788: `record_dtype` reuses `result_fields` for the dtype's
        # columns, so it must not be captured by the list-of-records
        # prototype below -- its kernel has the ordinary variable_output
        # signature, writing struct rows into `dp_tlm_rec_t *out`. The
        # wrapper chain already prefers `variable_output`; before this the
        # two chains disagreed and the declaration described a kernel the
        # binding never called.
        if result_fields and not (variable_output and record_dtype):
            # gh-594: this is the peer of _method._build_method_prototype's
            # record branch and must render the identical signature -- params
            # expanded (array -> ptr + `_len`), and `single` returning the
            # record by value instead of the results[]/max_results out-params.
            # It previously did neither, so a record method declared here got
            # a prototype the binding could not call.
            _rf_parts = ["{}_state_t *state".format(component)]
            if has_arg:
                _rf_parts += [f"const {arg_disp} *in", "size_t n_in"]
            _rf_parts += c_param_parts(params)
            if single_record:
                decl_lines.append(
                    f"{ret_disp} {c_fn}({', '.join(_rf_parts)});"
                )
            else:
                _rf_parts += [f"{ret_disp} *result", "size_t max_results"]
                decl_lines.append(f"size_t {c_fn}({', '.join(_rf_parts)});")
        elif variable_output:
            extra_params = "".join(
                f", {_ctype_display(rt)} *out{i + 1}"
                for i, rt in enumerate(multi_output)
            )
            _moc_decl, _ = _max_out_count_param_ctx(
                has_arg, has_params, params
            )
            # gh-761: never re-declare a count over a header that says
            # otherwise. jm splices these back into the sacred `_core.h`, so
            # emitting the count form against a state-only implementation
            # rewrites the author's prototype out from under their code.
            if max_out_is_state_only(doc_blocks, f"{c_fn}_max_out"):
                _moc_decl = ""
            if has_arg:
                decl_lines.append(
                    f"size_t {c_fn}_max_out"
                    f"({component}_state_t *state{_moc_decl});\n"
                    f"size_t {c_fn}"
                    f"({component}_state_t *state,"
                    f" const {arg_disp} *in, size_t n_in,"
                    f" {_vo_out_disp} *out{extra_params}{_cap_param});"
                )
            elif has_params:
                _vp_parts: list[str] = []
                for _p in params:
                    if is_array_param_type(_p["type"]):
                        _e = _ctype_display(array_elem_ctype(_p["type"]))
                        _vp_parts.append(f"const {_e} *{_p['name']}")
                        _vp_parts.append(f"size_t {_p['name']}_len")
                    else:
                        _vp_parts.append(
                            f"{_ctype_display(_p['type'])} {_p['name']}"
                        )
                decl_lines.append(
                    f"size_t {c_fn}_max_out"
                    f"({component}_state_t *state{_moc_decl});\n"
                    f"size_t {c_fn}"
                    f"({component}_state_t *state,"
                    f" {', '.join(_vp_parts)},"
                    f" {_vo_out_disp} *out{extra_params}{_cap_param});"
                )
            else:
                decl_lines.append(
                    f"size_t {c_fn}_max_out"
                    f"({component}_state_t *state{_moc_decl});\n"
                    f"size_t {c_fn}"
                    f"({component}_state_t *state, size_t n,"
                    f" {_vo_out_disp} *out{extra_params}{_cap_param});"
                )
        else:
            extra_params = "".join(
                f", {_ctype_display(rt)} *out{i + 1}"
                for i, rt in enumerate(multi_output)
            )
            out_type_param = (
                f", {_ctype_display(out_type)} *out" if out_type else ""
            )
            if has_params:
                p_parts: list[str] = []
                if has_arg:
                    if is_array_param_type(arg_type):
                        _e_disp = _ctype_display(array_elem_ctype(arg_type))
                        p_parts.append(f"const {_e_disp} *x")
                        p_parts.append("size_t x_len")
                    else:
                        p_parts.append(f"{arg_disp} x")
                for p in params:
                    if is_array_param_type(p["type"]):
                        e_disp = _ctype_display(array_elem_ctype(p["type"]))
                        p_parts.append(f"const {e_disp} *{p['name']}")
                        p_parts.append(f"size_t {p['name']}_len")
                    else:
                        p_parts.append(
                            f"{_ctype_display(p['type'])} {p['name']}"
                        )
                c_param_str = ", ".join(p_parts)
                decl_lines.append(
                    f"{ret_disp} {c_fn}"
                    f"({component}_state_t *state,"
                    f" {c_param_str}{extra_params}{out_type_param});"
                )
            elif has_arg:
                if is_array_param_type(arg_type):
                    _e_disp = _ctype_display(array_elem_ctype(arg_type))
                    decl_lines.append(
                        f"{ret_disp} {c_fn}"
                        f"({component}_state_t *state,"
                        f" const {_e_disp} *x, size_t x_len"
                        f"{extra_params}{out_type_param});"
                    )
                else:
                    decl_lines.append(
                        f"{ret_disp} {c_fn}"
                        f"({component}_state_t *state,"
                        f" {arg_disp} x{extra_params}{out_type_param});"
                    )
            else:
                decl_lines.append(
                    f"{ret_disp} {c_fn}"
                    f"({component}_state_t *state"
                    f"{extra_params}{out_type_param});"
                )

        for _j in range(_ndecl, len(decl_lines)):
            _doc = scaffold_doc_block(decl_lines[_j], name)
            if _doc:
                decl_lines[_j] = _doc + "\n" + decl_lines[_j]

        # ── output storage ───────────────────────────────────────────────
        # gh-604: nothing to declare. Every variable_output shape now lets
        # NumPy own each call's arrays (see the wrapper below), so there is no
        # instance buffer to size at __init__, grow, retire, weakref-track or
        # free. That removed `_<name>_buf`, `_<name>_buf_cap`,
        # `_<name>_retired{,_n,_cap}` and `_<name>_view_ref` — the gh-219
        # deferred-free freelist and the gh-437 live-view probe existed solely
        # to make sharing one buffer across calls safe, and nothing is shared
        # any more. gh-600 had already done this for multi-output; the
        # benchmarks in gh-604 retired it for single-output too.
        # ── Python wrapper in ext.c ──────────────────────────────────────
        # gh-219: single-output variable_output methods accept an optional
        # `out=` buffer (zero-alloc, caller-owned, safe to retain) — parity
        # with blockwise steps(x, out=).  Multi-output and multi-param execute
        # keep their positional-only signatures for now.
        _enable_out = (
            variable_output
            and not multi_output
            and (not has_params or _single_array_param)
            # gh-788: not offered for a structured result yet. The out=
            # branch acquires the caller's buffer with PyArray_FROM_OTF and a
            # scalar NPY_ enum, which for a record would silently CAST the
            # caller's structured array to that scalar type rather than
            # reject it -- the exact silent-reinterpretation this feature
            # exists to prevent. Doing it properly needs PyArray_EquivTypes
            # against the cached descr; tracked separately rather than left
            # as an undocumented carve-out.
            and not record_dtype
        )
        # gh-412: keyword parsing is independent of the `out=` buffer feature.
        # A variable_output method with named params (e.g. Farrow.delay(x, mu))
        # is positional-OR-keyword — matching its `.pyi` and the fixed-output
        # path — even though it gets no `out=` buffer. Previously such methods
        # fell through to a positional-only PyArg_ParseTuple, so `delay(x,
        # mu=0.3)` raised TypeError despite the stub advertising the keyword.
        _enable_kw = _enable_out or (variable_output and has_params)
        if variable_output:
            if has_arg:
                if _enable_out:
                    _kwlist_decl = (
                        '    static char *_kwlist[] = {"x", "out", NULL};\n'
                    )
                    _out_decl = "    PyObject *out_obj = NULL;\n"
                    _parse_call = (
                        "    if (!PyArg_ParseTupleAndKeywords("
                        'args, kwds, "O|O",\n'
                        "            _kwlist, &in_obj, &out_obj))\n"
                        "        return NULL;\n"
                    )
                else:
                    _kwlist_decl = ""
                    _out_decl = ""
                    _parse_call = (
                        '    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                        "        return NULL;\n"
                    )
                parse_block = (
                    f"{_kwlist_decl}"
                    f"    PyObject *in_obj = NULL;\n"
                    f"{_out_decl}"
                    f"{_parse_call}"
                    f"    PyArrayObject *in_arr ="
                    f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"        in_obj, {arg_np}, NPY_ARRAY_C_CONTIGUOUS);\n"
                    f"    if (!in_arr) return NULL;\n"
                    f"    Py_ssize_t n = PyArray_SIZE(in_arr);\n"
                )
                call_data = (
                    f"self->handle,"
                    f" (const {arg_disp} *)PyArray_DATA(in_arr),"
                    f" (size_t)n, {_VO_BUF_TOKEN}"
                )
                decref_in = "    Py_DECREF(in_arr);\n"
                _lazy_fallback = "(size_t)n"
                # gh-607: the count `*_max_out()` is called with — the same
                # value about to be passed to the kernel as n_in.
                _moc_arg: str | None = _lazy_fallback
            elif has_params:
                _pb_lines: list[str] = []
                _cd_parts: list[str] = ["self->handle"]
                _dr_lines: list[str] = []
                _fmt = ""
                _fmt_args: list[str] = []
                _first_arr: str | None = None
                for _p in params:
                    _pn = _p["name"]
                    _pt = _p["type"]
                    if is_array_param_type(_pt):
                        _pe = array_elem_ctype(_pt)
                        _pe_np = _NP_ENUM[_CTYPE_META[_pe]["py_type"]]
                        _pe_disp = _ctype_display(_pe)
                        _pb_lines += [
                            f"    PyObject *{_pn}_obj = NULL;",
                        ]
                        _fmt += "O"
                        _fmt_args.append(f"&{_pn}_obj")
                        _pb_lines += [
                            f"    PyArrayObject *{_pn}_arr = NULL;",
                        ]
                        _cd_parts.append(
                            f"(const {_pe_disp} *)PyArray_DATA({_pn}_arr)"
                        )
                        _cd_parts.append(f"(size_t)PyArray_SIZE({_pn}_arr)")
                        _dr_lines.append(f"    Py_DECREF({_pn}_arr);")
                        if _first_arr is None:
                            _first_arr = _pn
                    else:
                        _pt_meta = _CTYPE_META.get(_pt, {})
                        _fmt_char = _pt_meta.get("fmt", "d")
                        _has_parse = "parse_type" in _pt_meta
                        _parse_t = _pt_meta.get(
                            "parse_type", _ctype_display(_pt)
                        )
                        _parse_zero = _pt_meta.get("parse_zero", "0")
                        if _has_parse:
                            _raw = f"{_pn}_raw"
                            _pb_lines.append(
                                f"    {_parse_t} {_raw} = {_parse_zero};"
                            )
                            _fmt += _fmt_char
                            _fmt_args.append(f"&{_raw}")
                        else:
                            _pb_lines.append(
                                f"    {_parse_t} {_pn} = {_parse_zero};"
                            )
                            _fmt += _fmt_char
                            _fmt_args.append(f"&{_pn}")
                        _cd_parts.append(_pn)
                _cd_parts.append(_VO_BUF_TOKEN)
                # gh-412: positional-OR-keyword (kwlist from the param names),
                # so `obj.method(x, mu=…)` works and matches the .pyi.
                _kwnames = "".join(f'"{_p["name"]}", ' for _p in params)
                if _enable_out:
                    # gh-219 follow-up: the single-array-param case is
                    # otherwise identical to the has_arg out= branch below —
                    # extend the same optional out= kwarg. _fmt is exactly
                    # "O" here (one required array param, nothing else, by
                    # the _single_array_param definition), so "|O" makes
                    # `out` the first optional argument.
                    _pb_lines.append("    PyObject *out_obj = NULL;")
                    _fmt += "|O"
                    _fmt_args.append("&out_obj")
                    _kwnames += '"out", '
                parse_block = (
                    f"    static char *_kwlist[] = {{{_kwnames}NULL}};\n"
                    + "\n".join(_pb_lines)
                    + "\n"
                    + "    if (!PyArg_ParseTupleAndKeywords(args, kwds, "
                    + f'"{_fmt}",\n'
                    + "            _kwlist, "
                    + ", ".join(_fmt_args)
                    + "))\n"
                    "        return NULL;\n"
                )
                _conv_lines: list[str] = []
                for _p in params:
                    _pn = _p["name"]
                    _pt = _p["type"]
                    if is_array_param_type(_pt):
                        _pe = array_elem_ctype(_pt)
                        _pe_np = _NP_ENUM[_CTYPE_META[_pe]["py_type"]]
                        _conv_lines += [
                            f"    {_pn}_arr = (PyArrayObject *)PyArray_FROM_OTF(",
                            f"        {_pn}_obj, {_pe_np}, NPY_ARRAY_C_CONTIGUOUS);",
                            f"    if (!{_pn}_arr) return NULL;",
                        ]
                    elif "parse_type" in _CTYPE_META.get(_pt, {}):
                        _pm = _CTYPE_META[_pt]
                        _pt_disp = _ctype_display(_pt)
                        _conv_lines.append(
                            f"    {_pt_disp} {_pn} = {_pm['to_c'](_pn)};"
                        )
                parse_block += (
                    "\n".join(_conv_lines) + "\n" if _conv_lines else ""
                )
                call_data = ", ".join(_cd_parts)
                decref_in = "\n".join(_dr_lines) + "\n" if _dr_lines else ""
                # gh-421: with no array param to size from, a scalar param
                # (e.g. Delay.push_ptr(x), where x is the value being pushed,
                # not a count) has no "count" semantics jm can derive from its
                # raw value -- casting it as one silently mis-sizes the
                # buffer. Fall back to the method's own <name>_max_out(),
                # always available per the standard variable_output triplet.
                _lazy_fallback = (
                    f"(size_t)PyArray_SIZE({_first_arr}_arr)"
                    if _first_arr is not None
                    else f"{c_fn}_max_out(self->handle)"
                )
                # gh-607: an all-scalar params shape (no array to size from)
                # has no count for the kernel to take either — max_out()
                # stays zero-arg for this one shape, matching
                # `_max_out_count_param`'s design (there is nothing to pass).
                _moc_arg = _lazy_fallback if _first_arr is not None else None
            else:
                if _enable_out:
                    parse_block = (
                        "    static char *_kwlist[] ="
                        ' {"count", "out", NULL};\n'
                        f"{_count_alias}"
                        f"    Py_ssize_t n = {_count_init};\n"
                        "    PyObject *out_obj = NULL;\n"
                        "    if (!PyArg_ParseTupleAndKeywords("
                        'args, kwds, "|nO",\n'
                        "            _kwlist, &n, &out_obj))\n"
                        "        return NULL;\n"
                    )
                else:
                    parse_block = (
                        f"{_count_alias}"
                        f"    Py_ssize_t n = {_count_init};\n"
                        '    if (!PyArg_ParseTuple(args, "|n", &n))\n'
                        "        return NULL;\n"
                    )
                call_data = f"self->handle, (size_t)n, {_VO_BUF_TOKEN}"
                decref_in = ""
                _lazy_fallback = "(size_t)n"
                # gh-607: same value about to be passed to the kernel as n.
                _moc_arg = _lazy_fallback

            # gh-607: `*_max_out()`'s call-site argument list — empty only
            # for the all-scalar-params shape, whose kernel has no count to
            # mirror (see `_max_out_count_param_ctx`).
            _moc_call_arg = f", {_moc_arg}" if _moc_arg else ""

            # gh-604: NumPy owns every variable-output result, for one
            # output or many. Each call allocates its arrays at
            # max(max_out(), n), the kernel writes straight into them, and a
            # trimmed view of the filled prefix is returned pinned to the full
            # array.
            #
            # This replaced a per-instance reuse buffer that was grown on
            # demand, retired to a freelist when a previously returned view
            # was still alive (gh-219), and tracked by a weakref to detect
            # that liveness (gh-437). The measurements that retired it:
            # binding the result to a name — i.e. any loop that actually uses
            # the block — took the retire path on *every* call, and retired
            # buffers were freed only in tp_dealloc, so a 3000-iteration loop
            # over 65536 samples grew RSS by ~1.5 GB (~514 KiB/call). It was
            # also 6-8x SLOWER on that path (a fresh malloc plus a page fault
            # per call against a monotonically growing heap), while saving
            # nothing measurable in the case it was designed for: -0.2% at
            # 64k, -0.6% at 1M, and 12 ns at n=1024.
            #
            # `out=` remains the explicit zero-allocation contract, and is the
            # one that can actually promise it — a caller-owned buffer cannot
            # silently alias a previous result the way the reuse buffer could.
            _all_out_rts = [_vo_out_elem] + list(multi_output)
            _n_out_arrays = len(_all_out_rts)
            # gh-788: a record element has no `_CTYPE_META` entry and so no
            # single NPY_ enum -- its whole point is that the row is a struct.
            # It is allocated from a PyArray_Descr instead (below), so it
            # contributes no enum here; this stays a plain subscript for
            # every other type, because a `.get(..., "NPY_FLOAT")` fallback
            # is how an unregistered ctype silently becomes an array of
            # floats.
            _out_np_enums = [
                ""
                if (record_dtype and i == 0)
                else _NP_ENUM[
                    _CTYPE_META[rt[:-2] if rt.endswith("[]") else rt][
                        "py_type"
                    ]
                ]
                for i, rt in enumerate(_all_out_rts)
            ]
            _none_on_empty_line = (
                "    if (!n_out) Py_RETURN_NONE;\n" if none_on_empty else ""
            )
            # Any pre-call failure must still release the input arrays.
            _decref_early_vo = (
                " ".join(
                    line.strip()
                    for line in decref_in.splitlines()
                    if line.strip()
                )
                + " "
                if decref_in.strip()
                else ""
            )

            # ── optional out= buffer (single output only) ────────────────
            if _enable_out:
                _reindent = lambda blk: "".join(  # noqa: E731
                    (("    " + ln) if ln.strip() else ln) + "\n"
                    for ln in blk.splitlines()
                )
                _out_call_data = call_data.replace(
                    _VO_BUF_TOKEN,
                    f"({_vo_out_disp} *)PyArray_DATA(out_arr)",
                )
                _out_cap_arg = ", _cap" if pass_capacity else ""
                _out_kernel = _reindent(
                    _kernel_call_block(
                        f"{c_fn}({_out_call_data}{_out_cap_arg})",
                        nogil,
                    )
                )
                _out_decref = _reindent(decref_in) if decref_in else ""
                _out_none = (
                    "        if (!n_out)"
                    " { Py_DECREF(out_arr); Py_RETURN_NONE; }\n"
                    if none_on_empty
                    else ""
                )
                _vo_out_guard = _coerce.out_buffer_guard(
                    "out_obj",
                    _vo_out_np,
                    decrefs=_decref_early_vo.strip(),
                    indent=" " * 8,
                )
                _out_branch = (
                    f"    if (out_obj && out_obj != Py_None) {{\n"
                    f"{_vo_out_guard}"
                    f"        PyArrayObject *out_arr ="
                    f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"            out_obj, {_vo_out_np},\n"
                    f"            NPY_ARRAY_C_CONTIGUOUS"
                    f" | NPY_ARRAY_WRITEABLE);\n"
                    f"        if (!out_arr) {{"
                    f" {_decref_early_vo}return NULL; }}\n"
                    f"        size_t _cap = (size_t)PyArray_SIZE(out_arr);\n"
                    f"        size_t _omax ="
                    f" {c_fn}_max_out(self->handle{_moc_call_arg});\n"
                    # Without pass_capacity, max_out() alone is not always a
                    # true call-independent upper bound — a generator's
                    # steps(count) writes exactly the caller's requested
                    # size, which can exceed it. Require capacity for
                    # whichever is larger, matching the internal path's own
                    # `_cap < _need` fallback so the two agree instead of
                    # out= silently under-validating.
                    #
                    # With pass_capacity the kernel is handed `_cap` below
                    # and trusted to respect it exactly -- that is the same
                    # trust already extended on the internal-allocation path
                    # (`_vo_alloc`'s clamp is dropped there under the same
                    # condition). Requiring anything more than _omax here
                    # would reject a caller-owned buffer sized to the exact
                    # bound the binding itself would have allocated.
                    + (
                        "        size_t _min_cap = _omax;\n"
                        if pass_capacity
                        else (
                            f"        size_t _min_cap = _omax >"
                            f" {_lazy_fallback}"
                            f" ? _omax : ({_lazy_fallback});\n"
                        )
                    )
                    + f"        if (_cap < _min_cap) {{\n"
                    f"            PyErr_Format(PyExc_ValueError,\n"
                    f'                "out has %zu elements,'
                    f' need >= %zu",\n'
                    f"                _cap, _min_cap);\n"
                    f"            Py_DECREF(out_arr);"
                    f" {_decref_early_vo}return NULL;\n"
                    f"        }}\n"
                    f"{_out_kernel}"
                    f"{_out_decref}"
                    f"{_out_none}"
                    f"        npy_intp _odim = (npy_intp)n_out;\n"
                    f"        PyObject *_oview = PyArray_SimpleNewFromData(\n"
                    f"            1, &_odim, {_vo_out_np},"
                    f" PyArray_DATA(out_arr));\n"
                    f"        if (!_oview)"
                    f" {{ Py_DECREF(out_arr); return NULL; }}\n"
                    f"        PyArray_SetBaseObject("
                    f"(PyArrayObject *)_oview,"
                    f" (PyObject *)out_arr);\n"
                    f"        return _oview;\n"
                    f"    }}\n"
                )
                _vo_sig = (
                    f"({Component}Object *self,"
                    f" PyObject *args, PyObject *kwds)\n"
                )
            else:
                _out_branch = ""
                # gh-412: params methods still take kwds (keyword parsing)
                # even without the out= buffer branch.
                _vo_sig = (
                    f"({Component}Object *self,"
                    f" PyObject *args, PyObject *kwds)\n"
                    if _enable_kw
                    else f"({Component}Object *self, PyObject *args)\n"
                )

            # ── allocate the outputs, call, trim, return ─────────────────
            _idx = range(_n_out_arrays)
            _xdecref_arrs = " ".join(f"Py_XDECREF(arr{i});" for i in _idx)
            _decref_arrs = " ".join(f"Py_DECREF(arr{i});" for i in _idx)
            _vo_alloc = (
                f"    size_t _need = {_lazy_fallback};\n"
                f"    size_t _cap ="
                f" {c_fn}_max_out(self->handle{_moc_call_arg});\n"
                # gh-607: without pass_capacity, the kernel is never told its
                # capacity, so max_out() is only a sizing HINT and the alloc
                # is clamped to at least what the call needs — a mechanically
                # migrated `return 0;` still allocates `_need` and is safe.
                # With pass_capacity the kernel enforces the bound itself
                # (it's handed `_cap` below), so the exact bound is trusted
                # and the clamp is dropped — that's the entire point of
                # opting in: exact allocation instead of a defensive one.
                + (
                    "    (void)_need;\n"
                    if pass_capacity
                    else "    if (!_cap || _cap < _need) _cap = _need;\n"
                )
                + "    npy_intp _adim = (npy_intp)_cap;\n"
                + "".join(
                    (
                        # gh-788: the structured output. `_get_dtype()` hands
                        # back a NEW reference and PyArray_NewFromDescr STEALS
                        # one, on the failure path as well as the success
                        # path — so the two balance exactly and there is no
                        # decref of `_descr` anywhere below. Getting this
                        # wrong is a refcount bug that only shows up under
                        # sustained load, so it is spelled out rather than
                        # left to the reader.
                        f"    PyArray_Descr *_descr = {_sid}_get_dtype();\n"
                        f"    if (!_descr)"
                        f" {{ {_decref_early_vo}return NULL; }}\n"
                        f"    PyObject *arr{i} = PyArray_NewFromDescr(\n"
                        f"        &PyArray_Type, _descr, 1, &_adim,\n"
                        f"        NULL, NULL, 0, NULL);\n"
                    )
                    if (record_dtype and i == 0)
                    else (
                        f"    PyObject *arr{i} ="
                        f" PyArray_SimpleNew(1, &_adim,"
                        f" {_out_np_enums[i]});\n"
                    )
                    for i in _idx
                )
                + (
                    f"    if (!arr0) {{ {_decref_early_vo}return NULL; }}\n"
                    if _n_out_arrays == 1
                    else f"    if ({' || '.join(f'!arr{i}' for i in _idx)}) {{\n"
                    f"        {_xdecref_arrs} {_decref_early_vo}return NULL;\n"
                    f"    }}\n"
                )
                # Hoist each data pointer into a typed local. Two reasons:
                # the kernel call then contains no Python C-API at all, which
                # is what makes `nogil` sound (PyArray_DATA inlined into the
                # call would sit inside Py_BEGIN_ALLOW_THREADS); and the
                # generated call reads as plain C.
                + "".join(
                    f"    {_ctype_display(_all_out_rts[i])} *_d{i} ="
                    f" ({_ctype_display(_all_out_rts[i])} *)"
                    f"PyArray_DATA((PyArrayObject *)arr{i});\n"
                    for i in _idx
                )
            )
            _vo_call_data = call_data.replace(_VO_BUF_TOKEN, "_d0")
            _vo_call_extra = "".join(
                f", _d{i}" for i in range(1, _n_out_arrays)
            )
            _vo_cap_arg = ", _cap" if pass_capacity else ""
            _kernel_vo = _kernel_call_block(
                f"{c_fn}({_vo_call_data}{_vo_call_extra}{_vo_cap_arg})",
                nogil,
            )
            _vo_empty = (
                f"    if (!n_out) {{ {_decref_arrs} Py_RETURN_NONE; }}\n"
                if none_on_empty
                else ""
            )
            # n_out <= _cap. Shrink each array in place rather than
            # returning a view pinned to the full allocation: the array is
            # fresh, unshared and refcount-1, so PyArray_Resize reallocs the
            # buffer down and RELEASES the tail.
            #
            # gh-604 follow-up: the view form retained the whole allocation
            # for as long as the caller held the result, which is governed by
            # how tight max_out() is, not by the data. A kernel whose
            # max_out() is a fixed internal cap (a resampler at 65536 emitting
            # 512) retained 128x the samples it returned. Shrinking makes
            # retention proportional to the data instead of the cap. See
            # gh-607 for making max_out() a per-call bound, which removes the
            # over-allocation itself rather than just its retention.
            _vo_views = "    npy_intp _odim = (npy_intp)n_out;\n" + "".join(
                f"    PyArray_Dims _rs{i} = {{&_odim, 1}};\n"
                f"    PyObject *v{i} = PyArray_Resize(\n"
                f"        (PyArrayObject *)arr{i}, &_rs{i}, 0,"
                f" NPY_CORDER);\n"
                f"    if (!v{i}) {{ {_decref_arrs} return NULL; }}\n"
                f"    Py_DECREF(v{i});\n"
                for i in _idx
            )
            if _n_out_arrays == 1:
                _vo_exact_return = "        return arr0;\n"
                _vo_return = "    return arr0;\n"
            else:
                _vo_exact_return = (
                    f"        PyObject *_exact = PyTuple_Pack("
                    f"{_n_out_arrays},"
                    f" {', '.join(f'arr{i}' for i in _idx)});\n"
                    + "".join(f"        Py_DECREF(arr{i});\n" for i in _idx)
                    + "        return _exact;\n"
                )
                # The arrays were shrunk in place, so THEY are the results
                # -- PyArray_Resize returns None, not a new array.
                _vo_return = (
                    f"    PyObject *result = PyTuple_Pack("
                    f"{_n_out_arrays},"
                    f" {', '.join(f'arr{i}' for i in _idx)});\n"
                    + "".join(f"    Py_DECREF(arr{i});\n" for i in _idx)
                    + "    return result;\n"
                )
            # Exact-fill fast path: when the kernel filled the whole
            # allocation there is nothing to trim, so hand the array back
            # directly and skip a per-call PyObject. This is the common case
            # for a generator shape (steps(n) returns exactly n), and without
            # it the trim view is pure overhead on the hot path.
            _vo_exact = (
                f"    if ((size_t)n_out == _cap) {{\n"
                f"{_vo_exact_return}"
                f"    }}\n"
            )
            # gh-788: the cached descr builder is file-scope, so it is
            # prepended to the wrapper the way the structseq descriptor is —
            # the two travel together or the fragment does not compile.
            _vo_dtype_helper = (
                _record.dtype_c(
                    _sid, record_dtype, _record.fields(m, doc_blocks)
                )
                if record_dtype
                else ""
            )
            wrapper = _vo_dtype_helper + (
                f"static PyObject *\n"
                f"{wrapper_prefix}_{name}"
                f"{_vo_sig}"
                f"{{\n"
                f"{guard}"
                f"{parse_block}"
                f"{_out_branch}"
                f"{_vo_alloc}"
                f"{_kernel_vo}"
                f"{decref_in}"
                f"{_vo_empty}"
                f"{_vo_exact}"
                f"{_none_on_empty_line if not none_on_empty else ''}"
                f"{_vo_views}"
                f"{_vo_return}"
                f"}}"
            )
            _all_rts_vo = [_vo_out_elem] + list(multi_output)
            _dtype_strs_vo = [
                # A record row has no scalar dtype string to print; the demo
                # below shows its field NAMES instead, which is both stable
                # output and the thing a reader wants from a structured
                # result.
                ""
                if (record_dtype and i == 0)
                else _CTYPE_META[rt[:-2] if rt.endswith("[]") else rt][
                    "py_type"
                ].replace("np.", "")
                for i, rt in enumerate(_all_rts_vo)
            ]
            _ret_hint_vo = (
                f"tuple[{', '.join('ndarray' for _ in _all_rts_vo)}]"
                if len(_all_rts_vo) > 1
                else "ndarray"
            )
            if has_arg:
                _vo_sig_arg = ", ".join(_doc_names)
                _vo_call_example = f"obj.{name}({_in_example})"
            elif has_params:
                # Every declared param, not just the first array one: the
                # signature line has to match what the binding accepts.
                _vo_sig_arg = ", ".join(_doc_names) if _doc_names else "n=1"
                _vo_call_example = f"obj.{name}(np.zeros(4))"
            else:
                # gh-657: the kwlist binds this as `count`; the doc said
                # `n`, which is what sent the reporter looking for a rename
                # that never happened.
                # A declared default is C, not a Python literal, so the
                # Python-facing signature shows `...` rather than leaking it.
                _vo_sig_arg = f"count={'...' if _count_default else 1}"
                _vo_call_example = f"obj.{name}(4)"
            # gh-788: a numpydoc-shaped block naming each column of the
            # structured result and its Python type. Only the documented
            # fields get prose; an undocumented one still gets its name and
            # type, because that much is derived rather than invented.
            _vo_field_doc_lines: list[str] = []
            if record_dtype:
                _vo_flds = _record.fields(m, doc_blocks)
                _vo_field_doc_lines = ["", "Fields", "------"]
                for _vf in _vo_flds:
                    _vo_field_doc_lines.append(
                        f"{_vf.name} : {_pyi_scalar(_vf.ctype)}"
                    )
                    if _vf.doc:
                        _vo_field_doc_lines.append(f"    {_vf.doc}")
            _vo_doc_lines = [
                f"{name}({_vo_sig_arg}) -> {_ret_hint_vo}",
                "",
                # gh-604: this default ships into the user's compiled .so as
                # the method's __doc__, so it has to describe what the binding
                # actually does. It previously advertised the reuse buffer
                # ("zero-copy view into an internally managed buffer … a
                # still-referenced buffer is retired, never reused in place")
                # and was wrong on both clauses once that buffer was deleted.
                *_runtime_doc(
                    (
                        # gh-788: a structured result gets its own sentence.
                        # The generic one promises `out=`, which this shape
                        # deliberately does not offer, and says nothing about
                        # the property that makes the shape worth having.
                        "Returns a new NumPy-owned structured array each"
                        f" call, one row per {record_dtype} — the dtype is"
                        " that struct's own layout, so a row and the C"
                        " record are the same bytes. Independent of every"
                        " other result, and safe to keep."
                        if record_dtype
                        else "Returns a new NumPy-owned array each call —"
                        " independent of every other result, and safe to"
                        " keep. Pass out= to write into your own buffer"
                        " instead."
                    )
                ),
                # gh-788: the columns, documented on the SAME face as the
                # signature. This is the point of the migration — a
                # hand-written module carries its field docs in a header
                # comment, a PyMethodDef literal and a .pyi with nothing
                # linking the three, and they drift. Here `_record.fields`
                # resolves each doc from the manifest or, failing that, the
                # sacred header's own `///<` member doc (gh-671), so one
                # source feeds every face.
                *_vo_field_doc_lines,
                *_demo(
                    [
                        "",
                        "    >>> import numpy as np",
                        *_from_line,
                        _obj_line,
                        f"    >>> y = {_vo_call_example}",
                        # A structured row has no scalar dtype to print, and
                        # the full repr is long and numpy-version-sensitive;
                        # the field names are the stable, useful answer.
                        *(
                            [
                                "    >>> y.dtype.names",
                                # Python's own repr, so the one-field case
                                # keeps its trailing comma and the doctest is
                                # copy-pasteable rather than nearly right.
                                "    "
                                + repr(
                                    tuple(_f["name"] for _f in result_fields)
                                ),
                            ]
                            if record_dtype
                            else [
                                f"    >>> y"
                                f"{'[0]' if len(_all_rts_vo) > 1 else ''}"
                                f".dtype",
                                f"    dtype('{_dtype_strs_vo[0]}')",
                            ]
                        ),
                    ]
                ),
            ]
            _vo_flags = (
                "METH_VARARGS | METH_KEYWORDS"
                if _enable_kw
                else "METH_VARARGS"
            )
            # A METH_KEYWORDS wrapper is a PyCFunctionWithKeywords, so it must
            # launder through `void *` — casting it straight to PyCFunction is
            # an incompatible function-pointer cast (a warning today, an error
            # under -Werror and stricter C standards). Every other keyword
            # PyMethodDef in this generator already does this.
            _vo_cast = (
                "(PyCFunction)(void *)" if _enable_kw else "(PyCFunction)"
            )
            pmd_lines.append(
                f'    {{"{name}", {_vo_cast}{wrapper_prefix}_{name},'
                f" {_vo_flags},\n"
                f"     {_build_ml_doc(_vo_doc_lines)}}},\n"
            )
            if _enable_out:
                # gh-219: expose <verb>_max_out() so callers can size the
                # `out=` buffer they pass in.
                # gh-607: it takes the same count the binding is about to
                # pass to the kernel (see `_max_out_count_param_ctx`) — a
                # caller who wants to size a buffer for `execute(x, out=…)`
                # calls `execute_max_out(len(x))`, mirroring the call
                # they're about to make. The all-scalar-params shape has no
                # count to mirror and stays the original zero-arg form.
                _pymo_decl, _pymo_name = _max_out_count_param_ctx(
                    has_arg, has_params, params
                )
                # gh-761: the header's own prototype wins. jm assumed every
                # `_max_out` takes a count (gh-607); most kernels bound their
                # output by the state, not by the block, and declare
                # `size_t <c>_<m>_max_out(<c>_state_t *)`. Emitting a
                # count-taking binding against that is both a compile error
                # and — where an older jm already wrote the no-arg form — a
                # stub that disagrees with the binding beside it.
                if max_out_is_state_only(doc_blocks, f"{c_fn}_max_out"):
                    _pymo_decl, _pymo_name = "", None
                if _pymo_name:
                    _mo_doc = _build_ml_doc(
                        [f"{name}_max_out({_pymo_name}) -> int", ""]
                        + _max_out_doc(
                            component,
                            name,
                            _pymo_name,
                            m.get("max_out", 0),
                            lambda k: (doc_blocks or {}).get(k),
                            c_fn=c_fn,
                        ).c_doc_lines()
                    )
                    method_c_parts.append(
                        f"static PyObject *\n"
                        f"{wrapper_prefix}_{name}_max_out"
                        f"({Component}Object *self, PyObject *args)\n"
                        f"{{\n"
                        f"{guard}"
                        f"    Py_ssize_t {_pymo_name} = 0;\n"
                        f'    if (!PyArg_ParseTuple(args, "n",'
                        f" &{_pymo_name}))\n"
                        f"        return NULL;\n"
                        f"    return PyLong_FromSize_t(\n"
                        f"        {c_fn}_max_out(self->handle,"
                        f" (size_t){_pymo_name}));\n"
                        f"}}"
                    )
                    pmd_lines.append(
                        f'    {{"{name}_max_out",'
                        f" (PyCFunction){wrapper_prefix}_{name}_max_out,\n"
                        f"     METH_VARARGS, {_mo_doc}}},\n"
                    )
                else:
                    _mo_doc = _build_ml_doc(
                        [f"{name}_max_out() -> int", ""]
                        + _max_out_doc(
                            component,
                            name,
                            "",
                            m.get("max_out", 0),
                            lambda k: (doc_blocks or {}).get(k),
                            c_fn=c_fn,
                        ).c_doc_lines()
                    )
                    method_c_parts.append(
                        f"static PyObject *\n"
                        f"{wrapper_prefix}_{name}_max_out"
                        f"({Component}Object *self,"
                        f" PyObject *Py_UNUSED(ignored))\n"
                        f"{{\n"
                        f"{guard}"
                        f"    return PyLong_FromSize_t(\n"
                        f"        {c_fn}_max_out(self->handle));\n"
                        f"}}"
                    )
                    pmd_lines.append(
                        f'    {{"{name}_max_out",'
                        f" (PyCFunction){wrapper_prefix}_{name}_max_out,\n"
                        f"     METH_NOARGS, {_mo_doc}}},\n"
                    )
        elif result_fields and single_record:
            # gh-244: return ONE named record as a PyStructSequence (named,
            # unpackable) instead of a list[tuple]. The C kernel returns the
            # record struct by value; the structseq type is created lazily and
            # cached in this translation unit, so module-init/aggregator wiring
            # is untouched. (`_sid` is hoisted to the top of the loop —
            # gh-788's dtype statics use the same prefix.)
            # gh-257/gh-261/gh-646: the record's public name, its qualified
            # name, and its documented fields all come from _record, which the
            # two .pyi writers read too -- the descriptor emitted here and the
            # class they emit describe one type, so they derive it once.
            _rec_name = _record.public_name(m)
            _rec_fields = _record.fields(m, doc_blocks)
            _descriptor = _record.descriptor_c(
                _sid,
                _record.qualified_name(m, component),
                _record.type_doc(m, _rec_fields),
                _rec_fields,
            )
            # gh-594: method params go through the SAME builder every other
            # method shape uses (`_build_params_parse`), rather than the
            # scalar-only loop that used to live here. That loop emitted
            # `_ctype_display(type)` as a declaration and `_CTYPE_META.get(
            # type, {}).get("fmt", "d")` as a format char, so an array param
            # rendered the invalid `float complex[] rx = 0;`, parsed as a
            # scalar double, and passed no length -- three compile errors from
            # one omission. The shared builder already handles arrays (ptr +
            # `_len`), capsules, `parse_type` scalars and gh-240 defaults, and
            # keeping one copy is what stops this shape drifting from the rest
            # again.
            _has_kw = bool(params)
            _sp_kwnames = [_p["name"] for _p in params]

            if _has_kw:
                # A record method's `arg_type` is always the block input
                # (`const T *in, size_t n_in` in the prototype -- see
                # _method._methods_c_stub_result_single), so the primary arg
                # joins the param list in its array form regardless of whether
                # the manifest spelled the `[]`.
                _x_type = (
                    arg_type if arg_type.endswith("[]") else f"{arg_type}[]"
                )
                _pp_params = (
                    [{"name": "x", "type": _x_type}] if has_arg else []
                ) + [dict(_p) for _p in params]
                _s_parse, _p_call, _p_cleanup = _build_params_parse(_pp_params)
                # Any array acquired above must be released on the structseq
                # type-creation failure path too, not just after the call.
                _cleanup_inline = _p_cleanup.replace("\n    ", " ").strip()
                _fail = f" {{{' ' + _cleanup_inline if _cleanup_inline else ''} return NULL; }}"
                _s_ensure = (
                    f"    if (!{_sid}_type) {{\n"
                    f"        {_sid}_type ="
                    f" PyStructSequence_NewType(&{_sid}_desc);\n"
                    f"        if (!{_sid}_type){_fail}\n"
                    f"    }}\n"
                )
                _s_call = (
                    _single_kernel_block(
                        ret_disp,
                        f"{c_fn}(self->handle, {_p_call})",
                        nogil,
                    )
                    + _p_cleanup
                )
            elif has_arg:
                _s_parse = (
                    f"    PyObject *in_obj = NULL;\n"
                    f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                    f"        return NULL;\n"
                    f"    PyArrayObject *in_arr ="
                    f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"        in_obj, {arg_np}, NPY_ARRAY_C_CONTIGUOUS);\n"
                    f"    if (!in_arr) return NULL;\n"
                    f"    size_t n_in = (size_t)PyArray_SIZE(in_arr);\n"
                )
                _s_ensure = (
                    f"    if (!{_sid}_type) {{\n"
                    f"        {_sid}_type ="
                    f" PyStructSequence_NewType(&{_sid}_desc);\n"
                    f"        if (!{_sid}_type)"
                    f" {{ Py_DECREF(in_arr); return NULL; }}\n"
                    f"    }}\n"
                )
                _s_call = (
                    _single_kernel_block(
                        ret_disp,
                        f"{c_fn}(self->handle,\n"
                        f"        (const {arg_disp} *)PyArray_DATA(in_arr),"
                        f" n_in)",
                        nogil,
                    )
                    + "    Py_DECREF(in_arr);\n"
                )
            else:
                _s_parse = ""
                _s_ensure = (
                    f"    if (!{_sid}_type) {{\n"
                    f"        {_sid}_type ="
                    f" PyStructSequence_NewType(&{_sid}_desc);\n"
                    f"        if (!{_sid}_type) return NULL;\n"
                    f"    }}\n"
                )
                _s_call = _single_kernel_block(
                    ret_disp,
                    f"{c_fn}(self->handle)",
                    nogil,
                )
            _set_lines = []
            for _i, _f in enumerate(result_fields):
                _topy = _CTYPE_META[_f["type"]]["to_py"](f"_r.{_f['name']}")
                _set_lines.append(
                    f"    PyStructSequence_SET_ITEM(_o, {_i}, {_topy});\n"
                )
            _wrap_sig = (
                f"({Component}Object *self, PyObject *args, PyObject *kwds)"
                if _has_kw
                else f"({Component}Object *self, PyObject *args)"
            )
            wrapper = _descriptor + (
                f"static PyObject *\n"
                f"{wrapper_prefix}_{name}"
                f"{_wrap_sig}\n"
                f"{{\n"
                f"{guard}"
                f"{_s_parse}"
                f"{_s_ensure}"
                f"{_s_call}"
                f"    PyObject *_o = PyStructSequence_New({_sid}_type);\n"
                f"    if (!_o) return NULL;\n"
                f"{''.join(_set_lines)}"
                f"    return _o;\n"
                f"}}"
            )
            _s_names = ", ".join(_f["name"] for _f in result_fields)
            _sig_args = ", ".join((["x"] if has_arg else []) + _sp_kwnames)
            _md_cast = "(PyCFunction)(void *)" if _has_kw else "(PyCFunction)"
            _md_flags = (
                "METH_VARARGS | METH_KEYWORDS" if _has_kw else "METH_VARARGS"
            )
            pmd_lines.append(
                f'    {{"{name}", {_md_cast}{wrapper_prefix}_{name},'
                f" {_md_flags},\n"
                f'     "{name}({_sig_args}) ->'
                f' {_rec_name} record ({_s_names})."}},\n'
            )
        elif result_fields:
            # gh-598: peer of _render's list-of-records builder — both go
            # through record_tuple_build so a field type converts via
            # _CTYPE_META's to_py rather than a cast-less "i" fallback.
            _bv = record_tuple_build(result_fields, "results[i]")
            if has_arg:
                _rf_parse = (
                    f"    PyObject *in_obj = NULL;\n"
                    f'    if (!PyArg_ParseTuple(args, "O", &in_obj))\n'
                    f"        return NULL;\n"
                    f"    PyArrayObject *in_arr"
                    f" = (PyArrayObject *)PyArray_FROM_OTF(\n"
                    f"        in_obj, {arg_np},"
                    f" NPY_ARRAY_C_CONTIGUOUS);\n"
                    f"    if (!in_arr) return NULL;\n"
                    f"    size_t n_in ="
                    f" (size_t)PyArray_SIZE(in_arr);\n"
                )
                _rf_call = (
                    f"    {ret_disp} results[{max_results}];\n"
                    + _kernel_call_block(
                        f"{c_fn}(self->handle, "
                        f"(const {arg_disp} *)PyArray_DATA(in_arr), n_in, "
                        f"results, {max_results})",
                        nogil,
                    )
                    + "    Py_DECREF(in_arr);\n"
                )
            else:
                _rf_parse = ""
                _rf_call = (
                    f"    {ret_disp} results[{max_results}];\n"
                    + _kernel_call_block(
                        f"{c_fn}(self->handle, results, {max_results})",
                        nogil,
                    )
                )
            wrapper = (
                f"static PyObject *\n"
                f"{wrapper_prefix}_{name}"
                f"({Component}Object *self, PyObject *args)\n"
                f"{{\n"
                f"{guard}"
                f"{_rf_parse}"
                f"{_rf_call}"
                f"    PyObject *lst ="
                f" PyList_New((Py_ssize_t)n_out);\n"
                f"    if (!lst) return NULL;\n"
                f"    for (size_t i = 0; i < n_out; i++) {{\n"
                f"        PyObject *tup ="
                f" Py_BuildValue({_bv});\n"
                f"        if (!tup)"
                f" {{ Py_DECREF(lst); return NULL; }}\n"
                f"        PyList_SET_ITEM(lst, (Py_ssize_t)i, tup);\n"
                f"    }}\n"
                f"    return lst;\n"
                f"}}"
            )
            _rf_field_names = ", ".join(f["name"] for f in result_fields)
            _rf_call_arg = (
                f"np.zeros(4, dtype={_in_dtype_str})" if has_arg else ""
            )
            _rf_doc_lines = [
                f"{name}({', '.join(_doc_names)}) -> list[tuple]",
                "",
                # gh-642: this shape used to hard-code its summary, so a
                # documented record method's @brief reached the .pyi and not
                # help() — the only one of the four that ignored the header
                # outright rather than merely stopping at the brief.
                *_runtime_doc(f"Returns list of ({_rf_field_names},) tuples."),
                *_demo(
                    [
                        "",
                        "    >>> import numpy as np",
                        *_from_line,
                        _obj_line,
                        f"    >>> results = obj.{name}({_rf_call_arg})",
                        "    >>> isinstance(results, list)",
                        "    True",
                    ]
                ),
            ]
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction){wrapper_prefix}_{name},'
                f" METH_VARARGS,\n"
                f"     {_build_ml_doc(_rf_doc_lines)}}},\n"
            )
        else:
            # Fixed-output wrapper
            _p_cleanup = ""
            # gh-238/gh-240: branches that parse via _build_params_parse are
            # positional-OR-keyword (the parse uses PyArg_ParseTupleAndKeywords
            # + a kwlist), so their wrapper takes `kwds` and the PyMethodDef
            # entry is METH_VARARGS | METH_KEYWORDS. The bare scalar `step`-shape
            # arg (no params) and the no-arg case stay positional / NOARGS.
            _kw_sig = (
                f"{Component}Object *self, PyObject *args, PyObject *kwds"
            )
            _kw_flags = "METH_VARARGS | METH_KEYWORDS"
            if has_params and has_arg:
                _x_param = {"name": "x", "type": arg_type}
                _combined = [_x_param] + list(params)
                parse_block, _p_call, _p_cleanup = _build_params_parse(
                    _combined
                )
                call_args_c = f"self->handle, {_p_call}"
                fn_sig = _kw_sig
                meth_flags = _kw_flags
            elif has_params:
                parse_block, _p_call, _p_cleanup = _build_params_parse(params)
                call_args_c = f"self->handle, {_p_call}"
                fn_sig = _kw_sig
                meth_flags = _kw_flags
            elif has_arg and arg_type.endswith("[]"):
                _x_param = {"name": "x", "type": arg_type}
                parse_block, _p_call, _p_cleanup = _build_params_parse(
                    [_x_param]
                )
                call_args_c = f"self->handle, {_p_call}"
                fn_sig = _kw_sig
                meth_flags = _kw_flags
            elif has_arg:
                parse_block = _step_parse_block(arg_type, arg_meta) + "\n"
                call_args_c = "self->handle, x"
                fn_sig = f"{Component}Object *self, PyObject *args"
                meth_flags = "METH_VARARGS"
            else:
                parse_block = ""
                call_args_c = "self->handle"
                fn_sig = (
                    f"{Component}Object *self, PyObject *Py_UNUSED(ignored)"
                )
                meth_flags = "METH_NOARGS"

            if error_negative:
                # gh-805 §B: value-or-negative-error. The int IS the result on
                # success, so this returns it; only `< 0` raises.
                #
                # Deliberately a sibling of `status_return` rather than a flag
                # on it: the two make opposite claims about what the int
                # carries, and folding them into one branch is how a method
                # ends up raising on every successful call but id 0.
                #
                # The cleanup runs BEFORE the test, as it does on every other
                # path here — a param converter's borrow must be released
                # whether the call succeeded or failed.
                _en_msg = error_message or f"{name} failed"
                ret_body = (
                    f"    {ret_disp} _rc = {c_fn}({call_args_c});\n"
                    f"{_p_cleanup}"
                    f"    if (_rc < 0) {{\n"
                    f"        PyErr_Format(PyExc_{error_category},\n"
                    f'                     "{_en_msg} (rc=%d)", (int)_rc);\n'
                    f"        return NULL;\n"
                    f"    }}\n"
                    f"    return {ret_meta['to_py']('_rc')};\n"
                )
            elif status_return:
                # gh-432: status-code return — 0 = OK -> None, non-zero
                # raises ValueError carrying the method name and rc.
                ret_body = (
                    f"    int _rc = {c_fn}({call_args_c});\n"
                    f"{_p_cleanup}"
                    f"    if (_rc != 0) {{\n"
                    f"        PyErr_Format(PyExc_ValueError,\n"
                    f'                     "{name} failed (rc=%d)", _rc);\n'
                    f"        return NULL;\n"
                    f"    }}\n"
                    f"    Py_RETURN_NONE;\n"
                )
            elif multi_output:
                extra_decls = "".join(
                    f"    {_ctype_display(rt)} out{i + 1}"
                    f" = {_CTYPE_META[rt]['zero']};\n"
                    for i, rt in enumerate(multi_output)
                )
                extra_call = "".join(
                    f", &out{i + 1}" for i in range(len(multi_output))
                )
                if ret_meta:
                    call_line = (
                        f"    {ret_disp} y ="
                        f" {c_fn}"
                        f"({call_args_c}{extra_call});\n"
                    )
                    py_primary = ret_meta["to_py"]("y")
                else:
                    call_line = f"    {c_fn}({call_args_c}{extra_call});\n"
                    py_primary = "Py_None"
                pack_parts = [py_primary] + [
                    _CTYPE_META[rt]["to_py"](f"out{i + 1}")
                    if rt in _CTYPE_META
                    else f"PyLong_FromLong(out{i + 1})"
                    for i, rt in enumerate(multi_output)
                ]
                n = len(multi_output) + 1
                ret_body = (
                    f"{extra_decls}"
                    f"{call_line}"
                    f"{_p_cleanup}"
                    f"    return PyTuple_Pack({n},"
                    f" {', '.join(pack_parts)});\n"
                )
            elif out_type:
                out_disp = _ctype_display(out_type)
                out_npy = _CTYPE_TO_NPY[out_type]
                first_arr = next(
                    (
                        p["name"]
                        for p in params
                        if is_array_param_type(p["type"])
                    ),
                    None,
                )
                # Buffer size: prefer the length of the first array param.
                # If there is no array param, fall back to the first scalar
                # integer param so methods like ``foo(n: int) -> ndarray`` (n
                # samples requested) allocate an n-sized output rather than
                # an empty one (gh-65).
                if first_arr:
                    raw_len = f"{first_arr}_len"
                else:
                    first_int = next(
                        (
                            p["name"]
                            for p in params
                            if not is_array_param_type(p["type"])
                            and _CTYPE_META.get(p["type"], {}).get("kind")
                            == "int"
                        ),
                        None,
                    )
                    raw_len = first_int if first_int else "0"
                if out_divisor > 1:
                    len_expr = f"({raw_len} / {out_divisor})"
                else:
                    len_expr = raw_len
                cleanup_inline = _p_cleanup.replace("\n    ", " ").strip()
                ret_body = (
                    f"    npy_intp _dims[] ="
                    f" {{(npy_intp){len_expr}}};\n"
                    f"    PyObject *_out ="
                    f" PyArray_EMPTY(1, _dims, {out_npy}, 0);\n"
                    f"    if (!_out)"
                    f" {{{cleanup_inline} return NULL; }}\n"
                    f"    {c_fn}({call_args_c},"
                    f" ({out_disp} *)PyArray_DATA"
                    f"((PyArrayObject *)_out));\n"
                    f"{_p_cleanup}"
                    f"    return _out;\n"
                )
            elif ret_meta:
                ret_expr = ret_meta["to_py"]("y")
                ret_body = (
                    f"    {ret_disp} y ="
                    f" {c_fn}({call_args_c});\n"
                    f"{_p_cleanup}"
                    f"    return {ret_expr};\n"
                )
            else:
                ret_body = (
                    f"    {c_fn}({call_args_c});\n"
                    f"{_p_cleanup}"
                    f"    Py_RETURN_NONE;\n"
                )
            wrapper = (
                f"static PyObject *\n"
                f"{wrapper_prefix}_{name}({fn_sig})\n"
                f"{{\n"
                f"{guard}"
                f"{parse_block}"
                f"{ret_body}"
                f"}}"
            )
            _fix_ret_hint = (
                "ndarray"
                if out_type or multi_output
                else _pyi_scalar(return_type)
            )
            _fix_demo: list[str] = []
            if has_arg or has_params:
                _fix_demo += ["", "    >>> import numpy as np"]
            else:
                _fix_demo.append("")
            _fix_demo += [*_from_line, _obj_line]
            _call_parts: list[str] = []
            if has_arg:
                _call_parts.append(_in_example if _in_example else "x")
            for _p in params:
                _pt = _p["type"]
                if _pt.endswith("[]"):
                    _pe = _pt[:-2]
                    _pe_str = (
                        _CTYPE_META[_pe]["py_type"]
                        if _pe in _CTYPE_META
                        else "np.float32"
                    )
                    _call_parts.append(f"np.zeros(4, dtype={_pe_str})")
                elif _pt in _CTYPE_META:
                    _call_parts.append(_CTYPE_META[_pt].get("py_zero", "0"))
                else:
                    _call_parts.append("0")
            _call_str = ", ".join(_call_parts)
            if out_type or multi_output:
                _fix_demo.append(f"    >>> y = obj.{name}({_call_str})")
                _fix_demo.append("    >>> y.ndim")
                _fix_demo.append("    1")
            elif return_type != "void" and return_type in _CTYPE_META:
                _py_z = _CTYPE_META[return_type].get("py_zero", "0")
                _fix_demo.append(f"    >>> obj.{name}({_call_str})")
                _fix_demo.append(f"    {_py_z}")
            else:
                _fix_demo.append(f"    >>> obj.{name}({_call_str})")
            _fix_doc_lines = [
                f"{name}({', '.join(_doc_names)}) -> {_fix_ret_hint}".rstrip(),
                "",
                *_runtime_doc(f"{name}."),
                *_demo(_fix_demo),
            ]
            # A METH_KEYWORDS wrapper has the 3-arg PyCFunctionWithKeywords
            # signature; cast through `(void *)` to silence -Wcast-function-type.
            _cast = (
                "(PyCFunction)(void *)"
                if "KEYWORDS" in meth_flags
                else "(PyCFunction)"
            )
            pmd_lines.append(
                f'    {{"{name}", {_cast}{wrapper_prefix}_{name},'
                f" {meth_flags},\n"
                f"     {_build_ml_doc(_fix_doc_lines)}}},\n"
            )

        method_c_parts.append(wrapper)

        # pyi stub for this method
        m_var = variable_output
        m_multi = multi_output
        # gh-642: built once, at the top of the loop, and read by both faces.
        # `doc_params` is the args the docstring's Parameters section
        # documents — the signature also carries binding-level args (`count`,
        # `out=`) that are deliberately not documented there.
        param_parts = list(_sig_parts)
        ret_ann = _ret_ann
        # gh-219: single-output variable_output methods take an optional
        # `out=` buffer and expose a <verb>_max_out() sibling. A
        # single-array-param method (params=[{array}], no other params) is
        # eligible too -- see _single_array_param above.
        _stub_enable_out = (
            m_var
            and not m_multi
            and (not params or _single_array_param)
            # gh-788: mirrors `_enable_out` above — a structured result is
            # not offered an out= buffer yet, and a stub that advertised one
            # would type-check a call the binding rejects at runtime.
            and not record_dtype
        )
        # gh-527: a variable_output method with no input to size from is the
        # generator shape -- the parse block above seeds a leading `count` for
        # it (kwlist {"count", "out"} when an out= is offered, a positional
        # "|n" otherwise). The seed is `_count_init`: `1`, or the method's
        # `count_default` (gh-657). The stub
        # omitted it entirely, so `obj.run(4)` -- the call that actually works
        # -- failed to type-check while `obj.run(out=...)` passed. `count`
        # precedes `out` to match the kwlist order. The peer generator in
        # _stubs.py (the module-aggregated .pyi) carries the same rule.
        _stub_count_arg = m_var and arg_type == "void" and not params
        if _stub_count_arg:
            # A declared default is a C expression, not a Python literal.
            param_parts.append(
                f"count: int = {'...' if _count_default else '1'}"
            )
        if _stub_enable_out:
            param_parts.append(f"out: {ret_ann} | None = None")
        sig = ", ".join(param_parts)
        # gh-651: one renderer, shared with the module-aggregated .pyi. This
        # path used to build the numpy layout by hand and disagreed with its
        # peer on three things for the same manifest and the same header —
        # the extended description was dropped entirely, a Parameters entry
        # was a bare `x` with no `: type` (which numpydoc does not read as a
        # parameter at all), and the blank line between sections was eight
        # spaces of trailing whitespace.
        #
        # skeleton_fallback keeps this path's own answer to a *different*
        # question: an undocumented member here has always kept its section
        # skeleton, where the aggregated .pyi collapses to a one-line stub.
        _pyi_doc = (
            "\n".join(
                render_numpy_doc(
                    _block,
                    name,
                    _doc_params,
                    ret_ann,
                    m.get("doc") or "",
                    indent=8,
                    skeleton_fallback=True,
                )
            )
            + "\n"
        )
        stub = (
            f"    def {name}(self, {sig}) -> {ret_ann}:\n{_pyi_doc}"
            if sig
            else f"    def {name}(self) -> {ret_ann}:\n{_pyi_doc}"
        )
        pyi_lines.append(stub)
        if _stub_enable_out:
            # gh-607: mirror the same count parameter as the C wrapper.
            # gh-761: including when the header says there isn't one — the
            # stub must agree with the binding, and the binding follows the
            # prototype.
            _stub_moc_decl, _stub_moc_name = _max_out_count_param_ctx(
                has_arg, has_params, params
            )
            if max_out_is_state_only(doc_blocks, f"{c_fn}_max_out"):
                _stub_moc_decl, _stub_moc_name = "", None
            # gh-684: the header wins; _gluedoc supplies the fallback.
            _mo_doc_lines = _max_out_doc(
                component,
                name,
                _stub_moc_name,
                m.get("max_out", 0),
                lambda k: (doc_blocks or {}).get(k),
                c_fn=c_fn,
            ).pyi_doc()
            _mo_sig = (
                f"self, {_stub_moc_name}: int" if _stub_moc_name else "self"
            )
            pyi_lines.append(
                f"    def {name}_max_out({_mo_sig}) -> int:\n"
                + "\n".join(_mo_doc_lines)
                + "\n"
            )

    # ── serializable: generate the state-blob binding (gh-400) ──────────────
    # Calls the hand-written C triplet (size_t <c>_state_bytes(const T*); void
    # <c>_get_state(const T*, void*); int <c>_set_state(T*, const void*)) — the
    # elastic / pure-transducer face, sibling to reset.
    if serializable:
        _c_funcs, _pmd, _pyi = serializable_triplet_parts(
            component, Component, wrapper_prefix
        )
        method_c_parts.extend(_c_funcs)
        pmd_lines.append(_pmd)
        pyi_lines.append(_pyi)

    method_decls = "\n\n".join(decl_lines) + "\n" if decl_lines else ""

    _method_bench_blocks = [_bench_method_block(component, m) for m in methods]
    _filled = [b for b in _method_bench_blocks if b]
    bench_methods_timing_block = "\n" + "\n\n".join(_filled) if _filled else ""
    return {
        "method_decls": method_decls,
        "extra_buf_fields": "".join(buf_fields),
        "extra_buf_free": "".join(buf_free),
        "extra_buf_alloc": "".join(buf_alloc),
        "extra_methods_c": "\n\n".join(method_c_parts),
        "extra_methods_pymethoddef": "".join(pmd_lines),
        "pyi_extra_methods": (
            "\n" + "\n\n".join(pyi_lines) + "\n" if pyi_lines else ""
        ),
        # gh-646: the record classes a single-record method returns, declared
        # at module level above the component class that returns them. Empty
        # for every project without one, so the slot costs nothing.
        "pyi_records": _pyi_records(methods, doc_blocks),
        "bench_methods_timing_block": bench_methods_timing_block,
        "varargs_binding_files": varargs_binding_files,
        **(
            {
                "builtin_reset_c": "",
                "builtin_reset_pmd": "",
                "builtin_reset_decl": "",
                # gh-131: suppress the template's hardcoded reset() stub
                # when the user declared a [[methods]] entry named "reset";
                # pyi_extra_methods will contain the user-defined variant.
                "builtin_reset_pyi": "",
            }
            if user_has_reset
            else {}
        ),
    }


# ---------------------------------------------------------------------------
# make_properties_ctx
# ---------------------------------------------------------------------------


def _enum_symbols(Component: str, name: str) -> tuple[str, str]:
    """C symbol names for one property enum, namespaced by *Component*.

    gh-519: a module's ``_ext.c`` ``#include``s every object's fragment into a
    *single* translation unit, and a view (gh-504) adds yet another type over
    the same ``component``. Two types in that TU may each declare a property
    on the same ``[[enum]]``, and module-level ``function`` enums (gh-353)
    already own the bare ``_enum_index`` / ``_enum_<name>`` symbols. So the
    per-property tables are namespaced by ``Component`` — the one name that is
    unique per type section (it already namespaces every getter/setter there).

    Returns ``(index_fn, table)``.
    """
    return (f"_enum_index_{Component}", f"_enum_{Component}_{name}")


def _render_property_enum_tables(
    Component: str, used: list[str], enums: dict[str, list[str]]
) -> str:
    """Emit the ``_enum_index_<Component>`` helper + one table per enum in
    *used* (first-reference order).

    Reuses the composer's enum SSOT verbatim — ``_composer._ENUM_INDEX_FN``
    for the lookup body and the same "order is the C int" table layout as
    :func:`_handle.render_enum_tables` — only renaming the symbols into this
    type's namespace (see :func:`_enum_symbols`).
    """
    from .._composer import _ENUM_INDEX_FN

    index_fn, _ = _enum_symbols(Component, "")
    parts = [
        "/* gh-519: strcmp for the enum lookup below. Python.h already",
        " * pulls in <string.h>, but the include is explicit so the block",
        " * stands on its own wherever it is spliced. */",
        "#include <string.h>",
        "",
        _ENUM_INDEX_FN.replace(
            "_enum_index(const char", f"{index_fn}(const char"
        ),
    ]
    for name in used:
        _, table = _enum_symbols(Component, name)
        items = "".join(f'    "{v}",\n' for v in enums[name])
        parts.append(f"static const char *const {table}[] = {{")
        parts.append(items + "    NULL,")
        parts.append("};")
        parts.append("")
    return "\n".join(parts)


def _property_enum(
    component: str,
    Component: str,
    p: dict,
    enums: dict[str, list[str]] | None,
) -> str:
    """Resolve one property's ``enum = "<name>"`` declaration, or ``""``.

    A ``None`` registry means "enums are unsupported on this path" (the
    ``jm bind`` reflection path, which has no manifest to read ``[[enum]]``
    from). Declaring ``enum`` there is inert rather than fatal, so the bound
    render stays byte-identical to before gh-519.

    Raises
    ------
    ValueError
        If the enum names something absent from the ``[[enum]]`` registry, or
        if it is combined with ``buf_field`` (an array of enums has no
        meaning). Raising here turns a typo into a jm diagnostic instead of an
        undeclared ``_enum_<typo>`` identifier in the user's compiler.
    """
    name = p.get("enum") or ""
    if not name or enums is None:
        return ""
    if name not in enums:
        known = ", ".join(sorted(enums)) or "(none declared)"
        raise ValueError(
            f"{component}.{p['name']}: unknown enum '{name}'. "
            f"Declare it as a top-level [[enum]] with that name. "
            f"Known enums: {known}"
        )
    if p.get("buf_field"):
        raise ValueError(
            f"{component}.{p['name']}: `enum` is not supported on a "
            f"buf_field property — an array of enum strings has no "
            f"decoded form. Drop `enum` or drop `buf_field`."
        )
    return name


def container_fn_names(component: str, pname: str, p: dict) -> dict[str, str]:
    """Resolve a container property's three accessor names (gh-543).

    Each defaults from the component and property name -- mirroring how
    ``create_fn`` defaults to ``<backing>_open`` -- so the common declaration
    names nothing at all. ``key_fn`` is meaningful only for a ``dict``.
    """
    return {
        "count_fn": p.get("count_fn") or f"{component}_num_{pname}",
        "key_fn": p.get("key_fn") or f"{component}_{pname}_key",
        "value_fn": p.get("value_fn") or f"{component}_{pname}_value",
    }


def validate_container_property(component: str, p: dict) -> None:
    """Reject an incoherent container property (gh-543).

    Raises ``ValueError`` so the caller turns it into a jm diagnostic. Every
    check here would otherwise surface as a compiler error in generated code
    the user did not write.
    """
    pname = p["name"]
    kind = p.get("type", "")
    where = f"{component}.{pname}"
    vtype = p.get("value_type") or T.OBJECT_VALUE_TYPE
    if not T.is_valid_value_type(vtype):
        supported = ", ".join(sorted(T._CTYPE_META))
        raise ValueError(
            f"{where}: unsupported value_type '{vtype}'. Use "
            f"'{T.OBJECT_VALUE_TYPE}' (value_fn returns a PyObject *) or one "
            f"of: {supported}"
        )
    # A dict always has a key_fn -- `container_fn_names` defaults it -- so
    # there is nothing to require here, only a misuse to reject.
    if kind != "dict" and p.get("key_fn"):
        raise ValueError(
            f"{where}: key_fn is meaningful only for a dict property; "
            f"a {kind} is keyed by position. Drop key_fn, or use type = "
            f'"dict".'
        )
    if p.get("writable"):
        raise ValueError(
            f"{where}: a container property is read-only. jm generates the "
            f"container fresh on every read, so a setter would mutate a copy "
            f"the caller never sees. Expose a method that mutates the core "
            f"instead."
        )
    for clash in ("field", "buf_field", "expr", "enum"):
        if p.get(clash):
            raise ValueError(
                f"{where}: `{clash}` cannot be combined with a "
                f"container property -- the value comes from value_fn, not "
                f"from a struct member or an expression."
            )


def _container_getter(
    component: str,
    Component: str,
    p: dict,
    guard: str,
    cdc: dict | None = None,
) -> tuple[str, list[str]]:
    """Render a container property's getter, plus the decls it needs.

    Returns ``(getter_source, core_header_decls)``. The header decls are the
    accessors that are plain C and so belong in the sacred ``_core.h``; a
    ``PyObject *``-returning ``value_fn`` is *not* among them (it needs
    ``Python.h``) and is instead forward-declared inline, immediately above the
    getter. That forward declaration is mandatory rather than tidy: a
    hand-written ``*_extra.c`` is ``#include``d *after* the object fragment in
    the same translation unit, so the definition is not yet visible here.
    """
    pname = p["name"]
    kind = p["type"]
    fns = container_fn_names(component, pname, p)
    vtype = p.get("value_type") or T.OBJECT_VALUE_TYPE
    state_t = f"const {component}_state_t *"

    decls = [
        f"/**\n"
        f" * @brief Number of entries in {pname}.\n"
        f" * @param state  Must be non-NULL.\n"
        f" */\n"
        f"size_t {fns['count_fn']}({state_t}state);"
    ]
    if kind == "dict":
        decls.append(
            f"/**\n"
            f" * @brief Key of entry @p i of {pname}, or NULL if out of range.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param i      Entry index, 0-based.\n"
            f" */\n"
            f"const char *{fns['key_fn']}({state_t}state, size_t i);"
        )

    fwd = ""
    # Statements that must run before the value conversion (a NULL guard for a
    # pointer-valued accessor); empty for every other value type.
    value_pre = ""
    if cdc is not None:
        # gh-554: a codec property. jm generates the per-entry decode from the
        # [codec.X] table (the read mirror of the write pack), so there is no
        # hand-written value_fn — the value comes from a static decode helper
        # (emitted inline as `fwd`) over the entry_fn cursor.
        fwd, value_expr, _entry_decls = _codec.render_decode(
            component, Component, p, cdc
        )
        decls.extend(_entry_decls)
    elif vtype == T.OBJECT_VALUE_TYPE:
        # The escape hatch: the core owns the conversion, so the value comes
        # back already a PyObject *. It must return a NEW reference and set an
        # exception when it returns NULL.
        value_expr = f"{fns['value_fn']}(self->handle, _i)"
        fwd = (
            f"/* gh-543: implemented by hand (Python-aware, so it cannot live"
            f" in the pure-C\n"
            f" * core). Must return a new reference, or NULL with an"
            f" exception set. */\n"
            f"PyObject *{fns['value_fn']}({state_t}state, size_t i);\n\n"
        )
    else:
        _vmeta = _CTYPE_META[vtype]
        # Same pointer-spacing idiom the parse builders use (_render.py:622):
        # `const char *` already ends in the star, so no separating space.
        _vdisp = _ctype_display(vtype)
        if not _vdisp.endswith("*"):
            _vdisp += " "
        if _vdisp.endswith("*"):
            # A pointer-valued accessor is the one typed case that can hand
            # back NULL, and the conversion would dereference it -- for the
            # only such type today, `const char *`, PyUnicode_FromString(NULL)
            # reaches strlen(NULL) and crashes. Same class as the gh-521
            # unchecked table index, so it gets the same treatment: bind the
            # call to a local, check it, then convert. `_r` is declared inside
            # the loop body, so it cannot collide with the value local.
            value_pre = (
                f"        {_vdisp}_r = "
                f"{fns['value_fn']}(self->handle, _i);\n"
                f"        if (!_r) {{\n"
                f"            PyErr_Format(PyExc_RuntimeError,\n"
                f'                "{pname}: {fns["value_fn"]} returned NULL'
                f' at index %zu", _i);\n'
                f"            Py_DECREF(_c);\n"
                f"            return NULL;\n"
                f"        }}\n"
            )
            value_expr = _vmeta["to_py"]("_r")
        else:
            value_expr = _vmeta["to_py"](
                f"{fns['value_fn']}(self->handle, _i)"
            )
        decls.append(
            f"/**\n"
            f" * @brief Value of entry @p i of {pname}.\n"
            f" * @param state  Must be non-NULL.\n"
            f" * @param i      Entry index, 0-based.\n"
            f" */\n"
            f"{_vdisp}{fns['value_fn']}({state_t}state, size_t i);"
        )

    head = (
        f"{fwd}"
        f"static PyObject *\n"
        f"{Component}_getprop_{pname}"
        f"({Component}Object *self, void *Py_UNUSED(closure))\n"
        f"{{\n"
        f"{guard}"
        f"    size_t _n = {fns['count_fn']}(self->handle);\n"
    )

    if kind == "dict":
        body = (
            f"    PyObject *_c = PyDict_New();\n"
            f"    if (!_c) return NULL;\n"
            f"    for (size_t _i = 0; _i < _n; _i++) {{\n"
            f"        const char *_k = {fns['key_fn']}(self->handle, _i);\n"
            f"        if (!_k) {{\n"
            f"            PyErr_Format(PyExc_RuntimeError,\n"
            f'                "{pname}: {fns["key_fn"]} returned NULL'
            f' at index %zu", _i);\n'
            f"            Py_DECREF(_c);\n"
            f"            return NULL;\n"
            f"        }}\n"
            f"{value_pre}"
            f"        PyObject *_v = {value_expr};\n"
            f"        if (!_v) {{\n"
            f"            Py_DECREF(_c);\n"
            f"            return NULL;\n"
            f"        }}\n"
            f"        if (PyDict_SetItemString(_c, _k, _v) != 0) {{\n"
            f"            Py_DECREF(_v);\n"
            f"            Py_DECREF(_c);\n"
            f"            return NULL;\n"
            f"        }}\n"
            f"        Py_DECREF(_v);\n"
            f"    }}\n"
            f"    return _c;\n"
            f"}}"
        )
    else:
        # PyList_New/PyTuple_New zero every slot, so Py_DECREF on a
        # part-filled container is safe; SET_ITEM steals, so the success path
        # must NOT decref the value.
        _New = "PyList_New" if kind == "list" else "PyTuple_New"
        _SET = "PyList_SET_ITEM" if kind == "list" else "PyTuple_SET_ITEM"
        body = (
            f"    PyObject *_c = {_New}((Py_ssize_t)_n);\n"
            f"    if (!_c) return NULL;\n"
            f"    for (size_t _i = 0; _i < _n; _i++) {{\n"
            f"{value_pre}"
            f"        PyObject *_v = {value_expr};\n"
            f"        if (!_v) {{\n"
            f"            Py_DECREF(_c);\n"
            f"            return NULL;\n"
            f"        }}\n"
            f"        {_SET}(_c, (Py_ssize_t)_i, _v);\n"
            f"    }}\n"
            f"    return _c;\n"
            f"}}"
        )
    return head + body, decls


def _container_pyi(p: dict, cdc: dict | None = None) -> str:
    """Annotation for a container property (gh-543; gh-554 codec)."""
    if cdc is not None:
        # gh-554: the value is the codec's Python union (read form, `list`).
        elem = _codec.property_py_type(p, cdc)
    else:
        vtype = p.get("value_type") or T.OBJECT_VALUE_TYPE
        elem = "Any" if vtype == T.OBJECT_VALUE_TYPE else _pyi_scalar(vtype)
    kind = p["type"]
    if kind == "dict":
        return f"dict[str, {elem}]"
    if kind == "list":
        return f"list[{elem}]"
    return f"tuple[{elem}, ...]"


def make_properties_ctx(
    component: str,
    Component: str,
    properties: list[dict],
    state_var_names: frozenset[str] = frozenset(),
    doc_blocks: dict | None = None,
    enums: dict[str, list[str]] | None = None,
    codecs: dict | None = None,
) -> dict[str, str]:
    """Generate getset_def and tp_getset_decl context keys for Python properties.

    state_var_names: names already declared by make_state_ctx(); those are
    excluded from property_decls to avoid duplicate C declarations.

    Each property dict has: name, type (a _CTYPE_META key), writable (bool).

    enums: the project's ``[[enum]]`` registry (``C.enums(cfg)``). gh-519 — a
    property may declare ``enum = "<name>"``, which makes its Python face an
    ordered string instead of the raw int. ``None`` (the default) means the
    caller has no registry to offer, in which case ``enum`` is ignored and the
    output is byte-identical to the pre-gh-519 render.
    """
    _EMPTY: dict[str, str] = {
        "getset_def": "",
        "tp_getset_decl": "",
        "property_decls": "",
        "property_struct_fields": "",
        "property_stubs_pyi": "",
        "pyi_property_typing": "",
    }
    if not properties:
        return _EMPTY

    guard = (
        "    if (!self->handle) {\n"
        '        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
        "        return NULL;\n"
        "    }\n"
    )

    getter_parts: list[str] = []
    getset_entries: list[str] = []
    decl_lines: list[str] = []
    struct_field_lines: list[str] = []
    pyi_parts: list[str] = []
    # gh-519: enums actually referenced by this component's properties, in
    # first-reference order (mirrors _handle._enums_used) — only those get a
    # table emitted.
    enums_used: list[str] = []

    for p in properties:
        pname: str = p["name"]
        ctype: str = p.get("type") or p.get("ctype", "size_t")
        writable: bool = p.get("writable", False)
        field: bool = p.get("field", False)
        buf_field: str = p.get("buf_field", "")
        len_field: str = p.get("len_field", "n")
        valid_field: str = p.get("valid_field", "")
        # gh-543: a container property is backed by count/key/value accessors
        # rather than a scalar C value, so it never consults _CTYPE_META.
        container: bool = T.is_container_type(ctype)

        meta = _CTYPE_META.get(ctype, _CTYPE_META["size_t"])
        disp = _ctype_display(ctype)

        # gh-543: validate before anything is rendered, so an incoherent
        # declaration is a jm diagnostic rather than a compiler error in code
        # the user never wrote.
        if container:
            validate_container_property(component, p)

        # gh-519: an `enum`-decorated property stores the SSOT int in C but
        # presents the value as its string on the Python side.
        p_enum = _property_enum(component, Component, p, enums)
        if p_enum and p_enum not in enums_used:
            enums_used.append(p_enum)
        enum_index_fn, enum_table = (
            _enum_symbols(Component, p_enum) if p_enum else ("", "")
        )

        def _decode(acc: str, _t: str = enum_table) -> str:
            """PyObject* expression for the value at accessor *acc*."""
            if _t:
                return f"PyUnicode_FromString({_t}[{acc}])"
            return meta["to_py"](acc)

        _n_choices = len(enums[p_enum]) if p_enum else 0

        def _decode_stmts(
            acc: str,
            _t: str = enum_table,
            _n: int = _n_choices,
            _e: str = p_enum,
            _p: str = pname,
        ) -> str:
            """Statements ending in a ``return`` that decode *acc*.

            gh-519: the enum form is range-checked before it indexes the
            table. C owns the stored value — it is typically decoded from an
            external source such as a file header — so an unknown code is
            reachable input, not an internal invariant. Indexing blind read
            past the table (at ``_n`` exactly, the NULL terminator, giving
            ``PyUnicode_FromString(NULL)``), which surfaced as a garbage
            string, a UnicodeDecodeError, or a crash depending on what
            followed in memory. A bounds check turns that into an actionable
            Python error naming the offending value.
            """
            if not _t:
                return f"    return {meta['to_py'](acc)};\n"
            return (
                f"    long _v = (long)({acc});\n"
                f"    if (_v < 0 || _v >= {_n}) {{\n"
                f"        PyErr_Format(PyExc_ValueError,\n"
                f'            "{_p} holds out-of-range {_e} value %ld"\n'
                f'            " (valid: 0..{_n - 1})", _v);\n'
                f"        return NULL;\n"
                f"    }}\n"
                f"    return PyUnicode_FromString({_t}[_v]);\n"
            )

        if p.get("capsule"):
            # gh-788 gap 4: the PRODUCING side of gh-432. That issue taught
            # jm to *consume* a foreign pointer arriving as a named
            # `PyCapsule` (or duck-unwrapped from an object's `_capsule`);
            # nothing could hand one out. doppler's telemetry object is the
            # attach point for every instrumented component — it lends a
            # borrowed `dp_tlm_t *` so their `set_telemetry` can take it —
            # and with no way to declare that, the whole module stayed
            # `no_generate`.
            #
            # gh-286's `kind = "capsule"` is a different shape entirely:
            # free functions over an opaque capsule *as the state*, not a
            # property on a `PyTypeObject`.
            #
            # The destructor is NULL, and that is the load-bearing detail.
            # The capsule lends a pointer the object still owns; a capsule
            # with a destructor would free it on garbage-collection and the
            # owner would free it again on `__dealloc__`. Non-owning is the
            # only correct choice here, so it is not configurable — a
            # capsule that owns its pointer is a different feature and
            # should look different.
            # gh-794: the body moved to `capsule_new_c` so a handle type can
            # publish the identical contract. One emitter, two callers — the
            # NULL destructor is the contract, and a second copy is a place
            # for it to be changed on one side only.
            _cap_expr = p.get("expr") or "self->handle"
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                + _capsule_new_c(_cap_expr, p["capsule"], Component)
                + "\n}"
            )
        elif container:
            _cdc = None
            if _codec.is_codec_property(p):
                _cdc = (codecs or {}).get(p["codec"])
                if _cdc is None:
                    raise _codec.CodecError(
                        f"{component}.{pname}: property references codec "
                        f"'{p['codec']}', not declared in [codec.*]."
                    )
            getter, _c_decls = _container_getter(
                component, Component, p, guard, cdc=_cdc
            )
            if pname not in state_var_names:
                decl_lines.extend(_c_decls)
        elif buf_field:
            _elem_ct = ctype[:-2] if ctype.endswith("[]") else ctype
            _elem_meta = _CTYPE_META.get(
                _elem_ct, _CTYPE_META["float _Complex"]
            )
            _np_enum = _NP_ENUM.get(_elem_meta["py_type"], "NPY_CFLOAT")
            _valid_check = (
                f"    if (!self->handle->{valid_field}) Py_RETURN_NONE;\n"
                if valid_field
                else ""
            )
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"{_valid_check}"
                f"    npy_intp dim ="
                f" (npy_intp)self->handle->{len_field};\n"
                f"    PyObject *arr = PyArray_SimpleNewFromData(\n"
                f"        1, &dim, {_np_enum},"
                f" self->handle->{buf_field});\n"
                f"    if (!arr) return NULL;\n"
                f"    PyArray_SetBaseObject("
                f"(PyArrayObject *)arr, (PyObject *)self);\n"
                f"    Py_INCREF(self);\n"
                f"    return arr;\n"
                f"}}"
            )
        elif p.get("expr"):
            # gh-602: expr is arbitrary author-supplied C, unlike the field/
            # call accessors below (a member access or function call, both
            # already safe under a cast with no parens needed). to_py()'s
            # cast binds tighter than anything lower-precedence, so a ternary
            # or comma expression must be parenthesized before the cast is
            # applied, or the cast silently lands on just the first operand.
            # The enum-decode path (_decode_stmts below, when p_enum is set)
            # already wraps its accessor in its own parens (`(long)(acc)`) —
            # skip adding a second layer there.
            _expr = p["expr"] if p_enum else f"({p['expr']})"
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"{_decode_stmts(_expr)}"
                f"}}"
            )
        elif field:
            # When a property aliases an existing state field (same name), do
            # not re-emit the struct member — make_state_ctx already declared
            # it.  Otherwise the struct ends up with duplicate fields and the
            # compiler errors out (gh-70).
            if pname not in state_var_names:
                struct_field_lines.append(f"    {disp} {pname};")
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"{_decode_stmts(f'self->handle->{pname}')}"
                f"}}"
            )
        else:
            _call = f"{component}_get_{pname}(self->handle)"
            implement_cmt = (
                "    /* <<IMPLEMENT: return the computed or stored value>> */\n"
                if pname not in state_var_names
                else ""
            )
            # gh-519: the enum form binds the call into its own local before
            # the range check, so the C getter is evaluated exactly once.
            getter = (
                f"static PyObject *\n"
                f"{Component}_getprop_{pname}"
                f"({Component}Object *self,"
                f" void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"{guard}"
                f"{implement_cmt}"
                f"{_decode_stmts(_call)}"
                f"}}"
            )
            if pname not in state_var_names:
                decl_lines.append(
                    f"/**\n"
                    f" * @brief Get {pname}.\n"
                    f" * @param state  Must be non-NULL.\n"
                    f" * @return Current {pname} value ({disp}).\n"
                    f" */\n"
                    f"{disp} {component}_get_{pname}"
                    f"(const {component}_state_t *state);"
                )

        getter_parts.append(getter)

        setter_name = "NULL"
        if writable:
            setter_name = f"(setter){Component}_setprop_{pname}"
            if p_enum:
                # gh-519: accept the Python string, resolve it through the
                # SSOT table, and assign the resolved int wherever the plain
                # setter would have assigned `v`. "s" already raises TypeError
                # for a non-str, so only the unknown-choice case needs a
                # hand-written error.
                _choices = ", ".join(enums[p_enum])
                parse_block = (
                    f"    const char *v_str = NULL;\n"
                    f'    if (!PyArg_Parse(value, "s", &v_str)) return -1;\n'
                    f"    int v_idx = {enum_index_fn}"
                    f"({enum_table}, v_str);\n"
                    f"    if (v_idx < 0) {{\n"
                    f"        PyErr_Format(PyExc_ValueError,\n"
                    f"            \"invalid {pname} '%s'"
                    f' (choices: {_choices})", v_str);\n'
                    f"        return -1;\n"
                    f"    }}\n"
                    f"    {disp} v = ({disp})v_idx;\n"
                )
            elif "parse_type" in meta:
                parse_block = (
                    f"    {meta['parse_type']} v_raw ="
                    f" {meta['parse_zero']};\n"
                    f'    if (!PyArg_Parse(value, "{meta["fmt"]}", &v_raw))'
                    f" return -1;\n"
                    f"    {disp} v = {meta['to_c']('v')};\n"
                )
            else:
                parse_block = (
                    f"    {disp} v = {meta['zero']};\n"
                    f'    if (!PyArg_Parse(value, "{meta["fmt"]}", &v))'
                    f" return -1;\n"
                )
            if field:
                assign_line = f"    self->handle->{pname} = v;\n"
            else:
                assign_line = (
                    f"    {component}_set_{pname}(self->handle, v);\n"
                )
                if pname not in state_var_names:
                    decl_lines.append(
                        f"/**\n"
                        f" * @brief Set {pname}.\n"
                        f" * @param state  Must be non-NULL.\n"
                        f" * @param val    New value ({disp}).\n"
                        f" */\n"
                        f"void {component}_set_{pname}"
                        f"({component}_state_t *state, {disp} val);"
                    )
            setter = (
                f"static int\n"
                f"{Component}_setprop_{pname}"
                f"({Component}Object *self,"
                f" PyObject *value, void *Py_UNUSED(closure))\n"
                f"{{\n"
                f"    if (!self->handle) {{\n"
                f'        PyErr_SetString(PyExc_RuntimeError, "destroyed");\n'
                f"        return -1;\n"
                f"    }}\n"
                f"{parse_block}"
                f"{assign_line}"
                f"    return 0;\n"
                f"}}"
            )
            getter_parts.append(setter)

        # Property __doc__ precedence: TOML `doc` > getter @brief > the
        # field's own trailing `/**<` > name. PyGetSetDef's 4th field is the
        # doc.
        #
        # gh-671: the trailing member doc slots in *below* both authored
        # sources deliberately, so nothing already documented changes — it
        # only fills what was falling through to the name stub. For a
        # field-backed property that trailing comment is the most plausible
        # place the documentation already exists: doppler had ~518 documented
        # struct fields against 369 properties documented the redundant way,
        # the same sentence maintained twice and drifting independently.
        _pblk = (doc_blocks or {}).get(f"{component}_get_{pname}")
        _pdoc = (
            p.get("doc")
            or (_pblk.brief if (_pblk and _pblk.brief) else "")
            or member_doc(doc_blocks, pname)
            or f"{pname.replace('_', ' ').capitalize()}."
        )
        getset_entries.append(
            f'    {{ "{pname}", (getter){Component}_getprop_{pname},'
            f" {setter_name}, {_build_ml_doc([_pdoc])}, NULL }},"
        )

        # gh-446: standalone .pyi stubs for a manifest property (PyGetSetDef
        # -> a real `@property` descriptor), independent of the getter/
        # setter *methods* make_state_ctx stubs out for state vars.
        if p_enum:
            # gh-519: the Python face is the ordered choice set, not an int.
            # Reuse _stubs._py's string_enum rendering so both stub writers
            # spell Literal identically (local import: _stubs imports
            # _context, so a module-level import would cycle).
            from .._stubs import _py as _stubs_py

            py_t = _stubs_py("string_enum:" + ",".join(enums[p_enum]))
        elif p.get("capsule"):
            # gh-788: a PyCapsule has no Python type to name — the whole
            # point is that its contents are opaque to Python and meaningful
            # only to the C that unwraps it. `Any` is what gh-432's
            # capsule-typed *params* already annotate to, so the producing
            # and consuming faces of the same pointer read alike.
            py_t = "Any"
        elif container:
            _pcdc = (
                (codecs or {}).get(p["codec"])
                if _codec.is_codec_property(p)
                else None
            )
            py_t = _container_pyi(p, _pcdc)
        elif buf_field:
            py_t = _pyi_ndarray(ctype)
        else:
            py_t = _pyi_scalar(ctype)
        # gh-744: a property's docstring is a bare summary, so it never went
        # through `render_numpy_doc` and was emitted on one line whatever its
        # length. `summary_docstring` keeps the one-line shape when it fits,
        # so only the overlong ones change.
        pyi_block = [
            "",
            "    @property",
            f"    def {pname}(self) -> {py_t}:",
            *summary_docstring(_pdoc, indent=8),
        ]
        if writable:
            pyi_block += [
                f"    @{pname}.setter",
                f"    def {pname}(self, value: {py_t}) -> None: ...",
            ]
        pyi_parts.append("\n".join(pyi_block))

    getset_body = "\n".join(getter_parts)
    # gh-519: the enum tables ride along inside getset_def rather than in a
    # template slot of their own — that keeps every existing render path
    # (standalone _ext.c, module fragment, view fragment) wired with no
    # template churn, and guarantees the tables are defined *above* the
    # getters that index them.
    if enums_used:
        getset_body = (
            _render_property_enum_tables(Component, enums_used, enums or {})
            + "\n"
            + getset_body
        )
    entries_str = "\n".join(getset_entries)
    getset_def = (
        f"{getset_body}\n\n"
        f"static PyGetSetDef {Component}_getset[] = {{\n"
        f"{entries_str}\n"
        f"    {{ NULL }}\n"
        f"}};\n"
    )
    tp_getset_decl = f"\n    .tp_getset    = {Component}_getset,"
    property_decls = "\n".join(decl_lines) + "\n" if decl_lines else ""
    property_struct_fields = (
        "\n" + "\n".join(struct_field_lines) if struct_field_lines else ""
    )
    property_stubs_pyi = "\n".join(pyi_parts) + "\n" if pyi_parts else ""

    return {
        "getset_def": getset_def,
        "tp_getset_decl": tp_getset_decl,
        "property_decls": property_decls,
        "property_struct_fields": property_struct_fields,
        "property_stubs_pyi": property_stubs_pyi,
        # gh-519: the standalone .pyi hardcodes `from typing import Any`; an
        # enum property annotates as Literal[...] and needs it imported.
        "pyi_property_typing": ", Literal" if enums_used else "",
    }
