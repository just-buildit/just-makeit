"""_context/_methods.py — method/property context builders.

Contains _bench_method_block, make_methods_ctx, and make_properties_ctx.
"""

from __future__ import annotations

import re

from .. import _types as T
from .._types import (
    _CTYPE_META,
    _NP_ENUM,
    _CTYPE_TO_NPY,
    _KIND_PY_TEST_VAL,
    _PYBUILD_FMT,
    _ctype_display,
    is_array_param_type,
    array_elem_ctype,
)
from ._parse import _build_ml_doc, _build_params_parse, _step_parse_block


# Scalar C-kind -> Python annotation, shared by make_methods_ctx's param/
# return stubs and make_properties_ctx's property stubs — keyed off
# _CTYPE_META's "kind" rather than a parallel ctype table, so a new ctype
# only needs its _CTYPE_META entry (see gh-450, where a second table in
# _stubs.py drifted out of sync with this one).
def _pyi_scalar(ctype: str) -> str:
    # Thin alias for the canonical scalar→Python-builtin mapping in _types, so
    # the method-return and state-accessor annotations cannot drift.
    return T.scalar_py_annotation(ctype)


def _pyi_ndarray(ctype: str) -> str:
    elem = ctype[:-2] if ctype.endswith("[]") else ctype
    meta = _CTYPE_META.get(elem)
    return f"NDArray[{meta['py_type']}]" if meta else "NDArray[Any]"


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
    ):
        return ""

    name: str = m["name"]
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
                f"{component}_{name}(obj, {name}_in, BENCH_N,"
                f" {name}_results, {max_results})"
            )
        else:
            call = f"{component}_{name}(obj, {name}_results, {max_results})"
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
        call = f"{component}_{name}(obj,{in_arg} BENCH_N, {name}_out)"
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
        call = f"{component}_{name}(obj, {name}_in, BENCH_N{param_args})"
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
        call = f"{component}_{name}(obj{in_arg}{param_args})"
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
    pmd = (
        f'    {{"state_bytes", (PyCFunction){_W}_state_bytes, METH_NOARGS,\n'
        f'     "Serialized state size in bytes."}},\n'
        f'    {{"get_state", (PyCFunction){_W}_get_state, METH_NOARGS,\n'
        f'     "Serialize the engine\'s mutable state to bytes."}},\n'
        f'    {{"set_state", (PyCFunction){_W}_set_state, METH_O,\n'
        f'     "Restore mutable state from a get_state() blob."}},\n'
    )
    pyi = (
        "    def state_bytes(self) -> int:\n"
        '        """Serialized state size in bytes."""\n'
        "    def get_state(self) -> bytes:\n"
        '        """Serialize the engine\'s mutable state to bytes."""\n'
        "    def set_state(self, blob: bytes) -> None:\n"
        '        """Restore mutable state from a get_state() blob."""'
    )
    return c_funcs, pmd, pyi


def make_methods_ctx(
    component: str,
    Component: str,
    methods: list[dict],
    pkg: str = "",
    py_create_args: str = "",
    no_state: bool = False,
    doc_blocks: dict | None = None,
    serializable: bool = False,
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

        # Summary precedence: TOML `doc` override > header @brief > name
        # fallback. Param/return prose comes from the header; doctest examples
        # are always synthesized below regardless of source.
        _block = (doc_blocks or {}).get(f"{component}_{name}")
        _brief = m.get("doc") or (
            _block.brief if (_block and _block.brief) else ""
        )

        def _pdesc(pname: str, _b=_block) -> str:
            d = _b.param_desc(pname) if _b else None
            return d or "Input."

        _ret_desc_txt = (
            _block.returns if (_block and _block.returns) else "Output."
        )

        # ── varargs method (*args, **kwargs) ─────────────────────────────
        if m.get("varargs"):
            binding_file = f"{component}_{name}_core.c"
            varargs_binding_files.append(binding_file)
            extern_decl = (
                f"/* varargs binding — body in {binding_file} */\n"
                f"extern PyObject *\n"
                f"{component}_{name}"
                f"(PyObject *, PyObject *, PyObject *);\n"
            )
            method_c_parts.append(extern_decl)
            pmd_lines.append(
                f'    {{"{name}",'
                f" (PyCFunction)(void *){component}_{name},"
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
        # gh-257: optional chosen public name for the single-record structseq,
        # overriding the C-return-type derivation below.
        record_name: str = m.get("record_name", "")
        # gh-261: optional module qualifier for the structseq's __module__ —
        # by default Python derives it from the C component name; a project can
        # set this to its import path (e.g. "doppler.measure") so a record's
        # type(r).__module__ / repr matches where it is imported from.
        record_module: str = m.get("record_module", "")
        none_on_empty: bool = m.get("none_on_empty", False)
        # Opt-in GIL release around the pure-C kernel (thread-per-shard
        # scaling). v1 covers the variable_output execute shapes.
        nogil: bool = m.get("nogil", False)
        # gh-432: the C `int` return is a status code (0 = OK, non-zero =
        # failure) — bound as `-> None`, raising ValueError on failure (the
        # same contract the serializable set_state glue emits). Fixed-output
        # methods only.
        status_return: bool = m.get("status_return", False)
        # gh-138: opt into the 5-arg `(..., out, size_t max_out)` form for a
        # variable_output method whose C API forwards an explicit output
        # capacity (the buffer cap jm already tracks for grow-on-demand).
        pass_capacity: bool = m.get("pass_capacity", False)
        _cap_param = ", size_t max_out" if pass_capacity else ""
        _cap_arg = f", self->_{name}_buf_cap" if pass_capacity else ""

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
        _vo_out_src = (
            out_type if (variable_output and out_type) else return_type
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

        _param_docs = " * @param state  Must be non-NULL.\n"
        if has_arg:
            _param_docs += (
                f" * @param x      Input ({_ctype_display(arg_type)}).\n"
            )
        for _p in params:
            _pdisp = _ctype_display(_p["type"])
            _param_docs += f" * @param {_p['name']}  {_pdisp} parameter.\n"
        _doc_ret_disp = _vo_out_disp if variable_output else ret_disp
        _ret_doc = (
            f" * @return Result ({_doc_ret_disp}).\n"
            if return_type != "void"
            else ""
        )
        _method_doc = f"/**\n * @brief {name}.\n *\n{_param_docs}{_ret_doc} */"
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

        # ── batch method ─────────────────────────────────────────────────
        if batch:
            if has_arg:
                decl_lines.append(
                    f"void {component}_{name}({component}_state_t *state,"
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
                    f"        {component}_{name}(self->handle,\n"
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
                    f"    {component}_{name}(self->handle,\n"
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
                    f"void {component}_{name}({component}_state_t *state,"
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
                    f"    Py_ssize_t n = 1;\n"
                    f"    PyObject *out_obj = NULL;\n"
                    f"    if (!PyArg_ParseTupleAndKeywords("
                    f'args, kwds, "|nO",\n'
                    f"            _kwlist, &n, &out_obj))\n"
                    f"        return NULL;\n"
                    f"    if (out_obj && out_obj != Py_None) {{\n"
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
                    f"        {component}_{name}(self->handle,\n"
                    f"            (size_t)n,\n"
                    f"            ({ret_disp} *)PyArray_DATA(out_arr));\n"
                    f"        return (PyObject *)out_arr;\n"
                    f"    }}\n"
                    f"    npy_intp dims[] = {{n}};\n"
                    f"    PyObject *out ="
                    f" PyArray_SimpleNew(1, dims, {ret_np});\n"
                    f"    if (!out) return NULL;\n"
                    f"    {component}_{name}(self->handle,\n"
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
                f"{name}({'x' if has_arg else 'n'}, out=None) -> ndarray"
            )
            _batch_doc_lines = [
                _batch_sig,
                "",
                _brief
                or f"1:1-rate batch transform. Returns an ndarray of dtype {_ret_np_str}.",
                "",
                "    >>> import numpy as np",
                *_from_line,
                _obj_line,
            ]
            if has_arg:
                _batch_doc_lines += [
                    f"    >>> x = np.zeros(4, dtype={_in_dtype_str})",
                    f"    >>> y = obj.{name}(x)",
                ]
            else:
                _batch_doc_lines.append(f"    >>> y = obj.{name}(4)")
            _batch_doc_lines += [
                "    >>> y.shape",
                "    (4,)",
                "    >>> y.dtype",
                f"    dtype('{_ret_np_str}')",
            ]
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction){wrapper_prefix}_{name},'
                f" METH_VARARGS | METH_KEYWORDS,\n"
                f"     {_build_ml_doc(_batch_doc_lines)}}},\n"
            )
            for _j in range(_ndecl, len(decl_lines)):
                decl_lines[_j] = _method_doc + "\n" + decl_lines[_j]
            continue

        # ── declarations for _core.h ─────────────────────────────────────
        if result_fields:
            if has_arg:
                decl_lines.append(
                    f"size_t {component}_{name}"
                    f"({component}_state_t *state,"
                    f" const {arg_disp} *in, size_t n_in,"
                    f" {ret_disp} *result, size_t max_results);"
                )
            else:
                decl_lines.append(
                    f"size_t {component}_{name}"
                    f"({component}_state_t *state,"
                    f" {ret_disp} *result, size_t max_results);"
                )
        elif variable_output:
            extra_params = "".join(
                f", {_ctype_display(rt)} *out{i + 1}"
                for i, rt in enumerate(multi_output)
            )
            if has_arg:
                decl_lines.append(
                    f"size_t {component}_{name}_max_out"
                    f"({component}_state_t *state);\n"
                    f"size_t {component}_{name}"
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
                    f"size_t {component}_{name}_max_out"
                    f"({component}_state_t *state);\n"
                    f"size_t {component}_{name}"
                    f"({component}_state_t *state,"
                    f" {', '.join(_vp_parts)},"
                    f" {_vo_out_disp} *out{extra_params}{_cap_param});"
                )
            else:
                decl_lines.append(
                    f"size_t {component}_{name}_max_out"
                    f"({component}_state_t *state);\n"
                    f"size_t {component}_{name}"
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
                    f"{ret_disp} {component}_{name}"
                    f"({component}_state_t *state,"
                    f" {c_param_str}{extra_params}{out_type_param});"
                )
            elif has_arg:
                if is_array_param_type(arg_type):
                    _e_disp = _ctype_display(array_elem_ctype(arg_type))
                    decl_lines.append(
                        f"{ret_disp} {component}_{name}"
                        f"({component}_state_t *state,"
                        f" const {_e_disp} *x, size_t x_len"
                        f"{extra_params}{out_type_param});"
                    )
                else:
                    decl_lines.append(
                        f"{ret_disp} {component}_{name}"
                        f"({component}_state_t *state,"
                        f" {arg_disp} x{extra_params}{out_type_param});"
                    )
            else:
                decl_lines.append(
                    f"{ret_disp} {component}_{name}"
                    f"({component}_state_t *state"
                    f"{extra_params}{out_type_param});"
                )

        for _j in range(_ndecl, len(decl_lines)):
            decl_lines[_j] = _method_doc + "\n" + decl_lines[_j]

        # ── pre-allocated buffer fields + alloc + free ───────────────────
        if variable_output:
            all_return_types = [_vo_out_elem] + list(multi_output)
            _malloc_lines: list[str] = []
            for i, rt in enumerate(all_return_types):
                suffix = f"_{i}" if i > 0 else ""
                rt_disp = _ctype_display(rt)
                field_name = f"_{name}_buf{suffix}"
                buf_fields.append(
                    f"    {rt_disp} *{field_name};"
                    f"  /* pre-allocated output for {name} */\n"
                )
                buf_free.append(f"    free(self->{field_name});\n")
                _malloc_lines.append(
                    f"        self->{field_name} = malloc("
                    f"_max * sizeof({rt_disp}));\n"
                    f"        if (!self->{field_name}) {{"
                    f" PyErr_NoMemory(); return -1; }}\n"
                )
            buf_fields.append(
                f"    size_t _{name}_buf_cap;  /* allocated capacity for {name} */\n"
            )
            if not multi_output:
                # Deferred-free freelist (gh-219): on grow we retire the old
                # buffer here instead of freeing it, because a previously
                # returned array may still alias it (SetBaseObject pins self,
                # not the buffer).  Retired buffers are freed at dealloc, so
                # they outlive any array that referenced them.  Empty on the
                # fixed-block hot path (no growth after warmup -> zero cost).
                buf_fields.append(
                    f"    void **_{name}_retired;  /* gh-219 deferred free */\n"
                    f"    size_t _{name}_retired_n;\n"
                    f"    size_t _{name}_retired_cap;\n"
                )
                # gh-437: weakref to the last returned view.  While the
                # caller still holds that view, the next call must not
                # reuse the buffer in place (a same-size call would
                # silently overwrite the caller's data) — it retires the
                # buffer instead, exactly like a grow.
                buf_fields.append(
                    f"    PyObject *_{name}_view_ref;"
                    f"  /* gh-437 last returned view */\n"
                )
                buf_free.append(
                    f"    for (size_t _i = 0;"
                    f" _i < self->_{name}_retired_n; _i++)\n"
                    f"        free(self->_{name}_retired[_i]);\n"
                    f"    free(self->_{name}_retired);\n"
                    f"    Py_XDECREF(self->_{name}_view_ref);\n"
                )
            buf_alloc.append(
                f"    {{\n"
                f"        size_t _max ="
                f" {component}_{name}_max_out(self->handle);\n"
                f"        if (_max) {{\n"
                + "".join(_malloc_lines)
                + f"            self->_{name}_buf_cap = _max;\n"
                + "        }\n"
                "    }\n"
            )

        # ── Python wrapper in ext.c ──────────────────────────────────────
        # gh-219: single-output variable_output methods accept an optional
        # `out=` buffer (zero-alloc, caller-owned, safe to retain) — parity
        # with blockwise steps(x, out=).  Multi-output and multi-param execute
        # keep their positional-only signatures for now.
        _enable_out = (
            variable_output
            and not multi_output
            and (not has_params or _single_array_param)
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
                    f" (size_t)n, self->_{name}_buf"
                )
                decref_in = "    Py_DECREF(in_arr);\n"
                _lazy_fallback = "(size_t)n"
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
                _cd_parts.append(f"self->_{name}_buf")
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
                    else f"{component}_{name}_max_out(self->handle)"
                )
            else:
                if _enable_out:
                    parse_block = (
                        "    static char *_kwlist[] ="
                        ' {"count", "out", NULL};\n'
                        "    Py_ssize_t n = 1;\n"
                        "    PyObject *out_obj = NULL;\n"
                        "    if (!PyArg_ParseTupleAndKeywords("
                        'args, kwds, "|nO",\n'
                        "            _kwlist, &n, &out_obj))\n"
                        "        return NULL;\n"
                    )
                else:
                    parse_block = (
                        "    Py_ssize_t n = 1;\n"
                        '    if (!PyArg_ParseTuple(args, "|n", &n))\n'
                        "        return NULL;\n"
                    )
                call_data = f"self->handle, (size_t)n, self->_{name}_buf"
                decref_in = ""
                _lazy_fallback = "(size_t)n"

            if multi_output:
                all_rts = [return_type] + list(multi_output)
                call_extra = "".join(
                    f", self->_{name}_buf_{i}" for i in range(1, len(all_rts))
                )
                np_enums = [
                    _NP_ENUM[
                        _CTYPE_META[rt[:-2] if rt.endswith("[]") else rt][
                            "py_type"
                        ]
                    ]
                    for rt in all_rts
                ]
                arr_decls = "\n".join(
                    f"    PyObject *arr{i} ="
                    f" PyArray_SimpleNewFromData(\n"
                    f"        1, &dim, {np_enums[i]},"
                    f" self->_{name}_buf"
                    f"{'_' + str(i) if i > 0 else ''});"
                    for i in range(len(all_rts))
                )
                incref_lines = "\n".join(
                    f"    PyArray_SetBaseObject("
                    f"(PyArrayObject *)arr{i},"
                    f" (PyObject *)self); Py_INCREF(self);"
                    for i in range(len(all_rts))
                )
                null_checks = " || ".join(
                    f"!arr{i}" for i in range(len(all_rts))
                )
                decref_cleanup = " ".join(
                    f"Py_XDECREF(arr{i});" for i in range(len(all_rts))
                )
                pack_args = ", ".join(f"arr{i}" for i in range(len(all_rts)))
                decref_after = "\n".join(
                    f"    Py_DECREF(arr{i});" for i in range(len(all_rts))
                )
                _kernel_mo = _kernel_call_block(
                    f"{component}_{name}({call_data}{call_extra}{_cap_arg})",
                    nogil,
                )
                wrapper = (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}"
                    f"({Component}Object *self, PyObject *args)\n"
                    f"{{\n"
                    f"{guard}"
                    f"{parse_block}"
                    f"{_kernel_mo}"
                    f"    npy_intp dim = (npy_intp)n_out;\n"
                    f"{arr_decls}\n"
                    f"    if ({null_checks}) {{\n"
                    f"        {decref_cleanup} return NULL;\n"
                    f"    }}\n"
                    f"{incref_lines}\n"
                    f"    PyObject *result = PyTuple_Pack("
                    f"{len(all_rts)}, {pack_args});\n"
                    f"{decref_after}\n"
                    f"{decref_in}"
                    f"    return result;\n"
                    f"}}"
                )
            else:
                _none_on_empty_line = (
                    "    if (!n_out) Py_RETURN_NONE;\n"
                    if none_on_empty
                    else ""
                )
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
                # Grow-on-demand with deferred free (gh-219): retire the old
                # buffer to the freelist (malloc-new, never realloc-in-place)
                # so any already-returned array still aliasing it stays valid
                # until dealloc.  Reserve the retired slot before allocating
                # the new buffer, so an OOM leaves the live buffer untouched
                # (no use-after-free, no leak).
                # gh-437: a still-referenced view of _buf forbids
                # in-place reuse — a same-size call would overwrite the
                # caller's array. Probe the weakref and, when the view is
                # alive, retire + allocate fresh exactly like a grow.
                _lazy_alloc_vo = (
                    f"    size_t _need = {_lazy_fallback};\n"
                    f"    int _view_live = 0;\n"
                    f"    if (self->_{name}_view_ref) {{\n"
                    f"#if PY_VERSION_HEX >= 0x030D0000\n"
                    f"        PyObject *_lv = NULL;\n"
                    f"        if (PyWeakref_GetRef("
                    f"self->_{name}_view_ref, &_lv) == 1) {{\n"
                    f"            Py_DECREF(_lv);\n"
                    f"            _view_live = 1;\n"
                    f"        }}\n"
                    f"#else\n"
                    f"        _view_live = PyWeakref_GetObject("
                    f"self->_{name}_view_ref) != Py_None;\n"
                    f"#endif\n"
                    f"    }}\n"
                    f"    if (!self->_{name}_buf"
                    f" || self->_{name}_buf_cap < _need"
                    f" || _view_live) {{\n"
                    f"        size_t _max ="
                    f" {component}_{name}_max_out(self->handle);\n"
                    f"        if (!_max || _max < _need) _max = _need;\n"
                    f"        if (self->_{name}_buf"
                    f" && self->_{name}_retired_n"
                    f" == self->_{name}_retired_cap) {{\n"
                    f"            size_t _rcap = self->_{name}_retired_cap"
                    f" ? self->_{name}_retired_cap * 2 : 4;\n"
                    f"            void **_rt = realloc("
                    f"self->_{name}_retired, _rcap * sizeof(void *));\n"
                    f"            if (!_rt) {{"
                    f" {_decref_early_vo}PyErr_NoMemory();"
                    f" return NULL; }}\n"
                    f"            self->_{name}_retired = _rt;\n"
                    f"            self->_{name}_retired_cap = _rcap;\n"
                    f"        }}\n"
                    f"        {_vo_out_disp} *_tmp = malloc("
                    f"_max * sizeof({_vo_out_disp}));\n"
                    f"        if (!_tmp) {{"
                    f" {_decref_early_vo}PyErr_NoMemory();"
                    f" return NULL; }}\n"
                    f"        if (self->_{name}_buf)\n"
                    f"            self->_{name}_retired"
                    f"[self->_{name}_retired_n++] = self->_{name}_buf;\n"
                    f"        self->_{name}_buf = _tmp;\n"
                    f"        self->_{name}_buf_cap = _max;\n"
                    f"    }}\n"
                )
                _kernel_vo = _kernel_call_block(
                    f"{component}_{name}({call_data}{_cap_arg})", nogil
                )
                # gh-219: the optional `out=` branch fills the caller's buffer
                # instead of the internal one and returns a view of the filled
                # prefix pinned to *their* array — zero-alloc, safe to retain.
                # The kernel writes <= max_out by contract, so requiring
                # out.size >= max_out is sufficient whether or not the C API
                # takes an explicit capacity.
                if _enable_out:
                    _reindent = lambda blk: "".join(  # noqa: E731
                        (("    " + ln) if ln.strip() else ln) + "\n"
                        for ln in blk.splitlines()
                    )
                    _out_call_data = call_data.replace(
                        f"self->_{name}_buf",
                        f"({_vo_out_disp} *)PyArray_DATA(out_arr)",
                    )
                    _out_cap_arg = ", _cap" if pass_capacity else ""
                    _out_kernel = _reindent(
                        _kernel_call_block(
                            f"{component}_{name}"
                            f"({_out_call_data}{_out_cap_arg})",
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
                    _out_branch = (
                        f"    if (out_obj && out_obj != Py_None) {{\n"
                        f"        PyArrayObject *out_arr ="
                        f" (PyArrayObject *)PyArray_FROM_OTF(\n"
                        f"            out_obj, {_vo_out_np},\n"
                        f"            NPY_ARRAY_C_CONTIGUOUS"
                        f" | NPY_ARRAY_WRITEABLE);\n"
                        f"        if (!out_arr) {{"
                        f" {_decref_early_vo}return NULL; }}\n"
                        f"        size_t _cap = (size_t)PyArray_SIZE(out_arr);\n"
                        f"        size_t _omax ="
                        f" {component}_{name}_max_out(self->handle);\n"
                        # gh-219 follow-up: max_out() alone is not always a
                        # true call-independent upper bound — some kernels
                        # (e.g. a generator's steps(count)) write exactly the
                        # caller's requested size, which can exceed max_out.
                        # Require capacity for whichever is larger, mirroring
                        # the internal buffer-growth path's own fallback
                        # (`if (!_max || _max < _need) _max = _need;`) so the
                        # two paths agree instead of the out= path silently
                        # under-validating and overflowing the caller's array.
                        f"        size_t _min_cap = _omax > {_lazy_fallback}"
                        f" ? _omax : ({_lazy_fallback});\n"
                        f"        if (_cap < _min_cap) {{\n"
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
                wrapper = (
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}"
                    f"{_vo_sig}"
                    f"{{\n"
                    f"{guard}"
                    f"{parse_block}"
                    f"{_out_branch}"
                    f"{_lazy_alloc_vo}"
                    f"{_kernel_vo}"
                    f"{_none_on_empty_line}"
                    f"    npy_intp dim = (npy_intp)n_out;\n"
                    f"    PyObject *arr = PyArray_SimpleNewFromData(\n"
                    f"        1, &dim, {_vo_out_np},"
                    f" self->_{name}_buf);\n"
                    f"    if (!arr) return NULL;\n"
                    f"    PyArray_SetBaseObject("
                    f"(PyArrayObject *)arr, (PyObject *)self);\n"
                    f"    Py_INCREF(self);\n"
                    f"    /* gh-437: remember this view — while the caller"
                    f" holds it the next\n"
                    f"     * call retires the buffer instead of reusing it"
                    f" in place. */\n"
                    f"    Py_XDECREF(self->_{name}_view_ref);\n"
                    f"    self->_{name}_view_ref ="
                    f" PyWeakref_NewRef(arr, NULL);\n"
                    f"    if (!self->_{name}_view_ref) {{"
                    f" Py_DECREF(arr); return NULL; }}\n"
                    f"{decref_in}"
                    f"    return arr;\n"
                    f"}}"
                )
            _all_rts_vo = [_vo_out_elem] + list(multi_output)
            _dtype_strs_vo = [
                _CTYPE_META[rt[:-2] if rt.endswith("[]") else rt][
                    "py_type"
                ].replace("np.", "")
                for rt in _all_rts_vo
            ]
            _ret_hint_vo = (
                f"tuple[{', '.join('ndarray' for _ in _all_rts_vo)}]"
                if len(_all_rts_vo) > 1
                else "ndarray"
            )
            if has_arg:
                _vo_sig_arg = "x"
                _vo_call_example = f"obj.{name}({_in_example})"
            elif has_params:
                _first_ap = next(
                    (p for p in params if is_array_param_type(p["type"])),
                    None,
                )
                _vo_sig_arg = _first_ap["name"] if _first_ap else "n=1"
                _vo_call_example = f"obj.{name}(np.zeros(4))"
            else:
                _vo_sig_arg = "n=1"
                _vo_call_example = f"obj.{name}(4)"
            _vo_doc_lines = [
                f"{name}({_vo_sig_arg}) -> {_ret_hint_vo}",
                "",
                _brief
                or "Zero-copy view into an internally managed buffer;"
                " safe to keep across calls (a still-referenced buffer is"
                " retired, never reused in place).",
                "",
                "    >>> import numpy as np",
                *_from_line,
                _obj_line,
            ]
            _vo_doc_lines.append(f"    >>> y = {_vo_call_example}")
            _vo_doc_lines += [
                f"    >>> y{'[0]' if len(_all_rts_vo) > 1 else ''}.dtype",
                f"    dtype('{_dtype_strs_vo[0]}')",
            ]
            _vo_flags = (
                "METH_VARARGS | METH_KEYWORDS"
                if _enable_kw
                else "METH_VARARGS"
            )
            pmd_lines.append(
                f'    {{"{name}", (PyCFunction){wrapper_prefix}_{name},'
                f" {_vo_flags},\n"
                f"     {_build_ml_doc(_vo_doc_lines)}}},\n"
            )
            if _enable_out:
                # gh-219: expose <verb>_max_out() so callers can size the
                # `out=` buffer they pass in.
                _mo_doc = (
                    f"{name}_max_out() -> int\\n\\n"
                    f"Max output length {name}() can produce for the current"
                    f" state.\\nUse to size the ``out=`` buffer."
                )
                method_c_parts.append(
                    f"static PyObject *\n"
                    f"{wrapper_prefix}_{name}_max_out"
                    f"({Component}Object *self,"
                    f" PyObject *Py_UNUSED(ignored))\n"
                    f"{{\n"
                    f"{guard}"
                    f"    return PyLong_FromSize_t(\n"
                    f"        {component}_{name}_max_out(self->handle));\n"
                    f"}}"
                )
                pmd_lines.append(
                    f'    {{"{name}_max_out",'
                    f" (PyCFunction){wrapper_prefix}_{name}_max_out,\n"
                    f'     METH_NOARGS, "{_mo_doc}"}},\n'
                )
        elif result_fields and single_record:
            # gh-244: return ONE named record as a PyStructSequence (named,
            # unpackable) instead of a list[tuple]. The C kernel returns the
            # record struct by value; the structseq type is created lazily and
            # cached in this translation unit, so module-init/aggregator wiring
            # is untouched.
            _sid = f"{wrapper_prefix}_{name}"
            _rec_base = (
                return_type[:-2] if return_type.endswith("_t") else return_type
            )
            # gh-257: a manifest `record_name` picks the public structseq name
            # independent of the C return type; else derive from return_type.
            _rec_name = record_name or (
                "".join(w.capitalize() for w in _rec_base.split("_") if w)
                or "Record"
            )
            _seq_fields_c = "".join(
                f'    {{"{_f["name"]}", NULL}},\n' for _f in result_fields
            )
            _descriptor = (
                f"static PyStructSequence_Field {_sid}_fields[] = {{\n"
                f"{_seq_fields_c}"
                f"    {{NULL, NULL}},\n"
                f"}};\n"
                f"static PyStructSequence_Desc {_sid}_desc = {{\n"
                f'    "{record_module or component}.{_rec_name}", NULL,'
                f" {_sid}_fields, {len(result_fields)}\n"
                f"}};\n"
                f"static PyTypeObject *{_sid}_type = NULL;\n\n"
            )
            # Method params (scalars; a `default` makes it an optional kwarg,
            # gh-240). Array params in a single method are not supported.
            _sp_decls: list[str] = []
            _sp_fmt = ""
            _sp_addrs: list[str] = []
            _sp_callargs: list[str] = []
            _sp_kwnames: list[str] = []
            _sp_seen_default = False
            for _p in params:
                _pn = _p["name"]
                _pt = _p["type"]
                _pmeta = _CTYPE_META.get(_pt, {})
                _pdefault = _p.get("default", "")
                if _pdefault and not _sp_seen_default:
                    _sp_fmt += "|"
                    _sp_seen_default = True
                _sp_decls.append(
                    f"    {_ctype_display(_pt)} {_pn} = {_pdefault or '0'};"
                )
                _sp_fmt += _pmeta.get("fmt", "d")
                _sp_addrs.append(f"&{_pn}")
                _sp_callargs.append(_pn)
                _sp_kwnames.append(_pn)
            _has_kw = bool(params)
            _call_tail = "".join(f", {a}" for a in _sp_callargs)
            _decl_block = ("\n".join(_sp_decls) + "\n") if _sp_decls else ""

            if has_arg and _has_kw:
                _kw = "".join(f'"{n}", ' for n in (["x"] + _sp_kwnames))
                _s_parse = (
                    f"    PyObject *in_obj = NULL;\n"
                    f"{_decl_block}"
                    f"    static char *_kwlist[] = {{{_kw}NULL}};\n"
                    f"    if (!PyArg_ParseTupleAndKeywords(args, kwds,"
                    f' "O{_sp_fmt}",\n'
                    f"            _kwlist, &in_obj, {', '.join(_sp_addrs)}))\n"
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
                        f"{component}_{name}(self->handle,\n"
                        f"        (const {arg_disp} *)PyArray_DATA(in_arr),"
                        f" n_in{_call_tail})",
                        nogil,
                    )
                    + "    Py_DECREF(in_arr);\n"
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
                        f"{component}_{name}(self->handle,\n"
                        f"        (const {arg_disp} *)PyArray_DATA(in_arr),"
                        f" n_in)",
                        nogil,
                    )
                    + "    Py_DECREF(in_arr);\n"
                )
            elif _has_kw:
                _kw = "".join(f'"{n}", ' for n in _sp_kwnames)
                _s_parse = (
                    f"{_decl_block}"
                    f"    static char *_kwlist[] = {{{_kw}NULL}};\n"
                    f"    if (!PyArg_ParseTupleAndKeywords(args, kwds,"
                    f' "{_sp_fmt}",\n'
                    f"            _kwlist, {', '.join(_sp_addrs)}))\n"
                    f"        return NULL;\n"
                )
                _s_ensure = (
                    f"    if (!{_sid}_type) {{\n"
                    f"        {_sid}_type ="
                    f" PyStructSequence_NewType(&{_sid}_desc);\n"
                    f"        if (!{_sid}_type) return NULL;\n"
                    f"    }}\n"
                )
                _s_call = _single_kernel_block(
                    ret_disp,
                    f"{component}_{name}(self->handle{_call_tail})",
                    nogil,
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
                    f"{component}_{name}(self->handle)",
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
            _rf_fmt_parts: list[str] = []
            _rf_arg_parts: list[str] = []
            for _rf in result_fields:
                _rft = _rf["type"]
                _rfn = _rf["name"]
                _fmt_c, _cast = _PYBUILD_FMT.get(_rft, ("i", ""))
                _rf_fmt_parts.append(_fmt_c)
                _rft_val = f"results[i].{_rfn}"
                if _cast:
                    _rft_val = f"({_cast}){_rft_val}"
                _rf_arg_parts.append(_rft_val)
            _bvfmt = '"(' + "".join(_rf_fmt_parts) + ')"'
            _bvargs = ", ".join(_rf_arg_parts)
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
                        f"{component}_{name}(self->handle, "
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
                        f"{component}_{name}(self->handle, "
                        f"results, {max_results})",
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
                f" Py_BuildValue({_bvfmt}, {_bvargs});\n"
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
                f"{name}({'x' if has_arg else ''}) -> list[tuple]",
                "",
                f"Returns list of ({_rf_field_names},) tuples.",
                "",
                "    >>> import numpy as np",
                *_from_line,
                _obj_line,
                f"    >>> results = obj.{name}({_rf_call_arg})",
                "    >>> isinstance(results, list)",
                "    True",
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

            if status_return:
                # gh-432: status-code return — 0 = OK -> None, non-zero
                # raises ValueError carrying the method name and rc.
                ret_body = (
                    f"    int _rc = {component}_{name}({call_args_c});\n"
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
                        f" {component}_{name}"
                        f"({call_args_c}{extra_call});\n"
                    )
                    py_primary = ret_meta["to_py"]("y")
                else:
                    call_line = (
                        f"    {component}_{name}({call_args_c}{extra_call});\n"
                    )
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
                    f"    {component}_{name}({call_args_c},"
                    f" ({out_disp} *)PyArray_DATA"
                    f"((PyArrayObject *)_out));\n"
                    f"{_p_cleanup}"
                    f"    return _out;\n"
                )
            elif ret_meta:
                ret_expr = ret_meta["to_py"]("y")
                ret_body = (
                    f"    {ret_disp} y ="
                    f" {component}_{name}({call_args_c});\n"
                    f"{_p_cleanup}"
                    f"    return {ret_expr};\n"
                )
            else:
                ret_body = (
                    f"    {component}_{name}({call_args_c});\n"
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
            _fix_sig_in = (
                f"{'x' if has_arg else ''}"
                + (", " if has_arg and has_params else "")
                + ", ".join(p["name"] for p in params)
            )
            _fix_ret_hint = (
                "ndarray"
                if out_type or multi_output
                else _pyi_scalar(return_type)
            )
            _fix_doc_lines = [
                f"{name}({_fix_sig_in}) -> {_fix_ret_hint}".rstrip(),
                "",
                _brief or f"{name}.",
            ]
            if has_arg or has_params:
                _fix_doc_lines += ["", "    >>> import numpy as np"]
            else:
                _fix_doc_lines.append("")
            _fix_doc_lines += [*_from_line, _obj_line]
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
                _fix_doc_lines.append(f"    >>> y = obj.{name}({_call_str})")
                _fix_doc_lines.append("    >>> y.ndim")
                _fix_doc_lines.append("    1")
            elif return_type != "void" and return_type in _CTYPE_META:
                _py_z = _CTYPE_META[return_type].get("py_zero", "0")
                _fix_doc_lines.append(f"    >>> obj.{name}({_call_str})")
                _fix_doc_lines.append(f"    {_py_z}")
            else:
                _fix_doc_lines.append(f"    >>> obj.{name}({_call_str})")
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
        param_parts: list[str] = []
        if arg_type != "void":
            if arg_type.endswith("[]"):
                elem = arg_type[:-2]
                param_parts.append(f"x: {_pyi_ndarray(elem)}")
            else:
                param_parts.append(f"x: {_pyi_scalar(arg_type)}")
        for p in params:
            pt = p["type"]
            if pt.endswith("[]"):
                param_parts.append(f"{p['name']}: {_pyi_ndarray(pt[:-2])}")
            elif p.get("capsule"):
                # gh-432: a capsule-typed param takes the named PyCapsule,
                # any wrapper exposing `_capsule`, or None (detach).
                param_parts.append(f"{p['name']}: object | None")
            else:
                # gh-240: a defaulted scalar renders as an optional kwarg.
                _suffix = (
                    f" = {p['default']}"
                    if p.get("default") not in (None, "")
                    else ""
                )
                param_parts.append(f"{p['name']}: {_pyi_scalar(pt)}{_suffix}")
        if status_return:
            # gh-432: status returns bind as None (raise on failure).
            ret_ann = "None"
        elif result_fields and single_record:
            # gh-244: one named record — a PyStructSequence (a tuple subclass).
            # Type it as a tuple of the field types: unpacking type-checks and
            # named attribute access works at runtime. (A full NamedTuple stub
            # is a possible refinement.)
            ret_ann = (
                "tuple["
                + ", ".join(_pyi_scalar(f["type"]) for f in result_fields)
                + "]"
            )
        elif result_fields:
            ret_ann = "list[tuple]"
        elif m_var:
            all_rts = [return_type] + list(m_multi)
            ndarrays = [_pyi_ndarray(rt) for rt in all_rts]
            ret_ann = (
                f"tuple[{', '.join(ndarrays)}]"
                if len(ndarrays) > 1
                else ndarrays[0]
            )
        elif out_type:
            # gh-529: `out_type` on a method allocates a fresh output array
            # per call and returns it -- the C wrapper (the `elif out_type`
            # branch above) does exactly what a function's out_type does, and
            # the PyMethodDef docstring already says `-> ndarray`. Only this
            # annotation lagged, reporting the scalar `return_type` and so
            # contradicting both. `_stubs._obj_stub` carries the peer of this
            # branch for the module-aggregated stub; the two must move
            # together (see tests/test_gh529_method_out_type_pyi.py).
            ret_ann = _pyi_ndarray(out_type)
        else:
            ret_ann = _pyi_scalar(return_type)
        # gh-219: single-output variable_output methods take an optional
        # `out=` buffer and expose a <verb>_max_out() sibling. A
        # single-array-param method (params=[{array}], no other params) is
        # eligible too -- see _single_array_param above.
        _stub_enable_out = (
            m_var and not m_multi and (not params or _single_array_param)
        )
        # gh-527: a variable_output method with no input to size from is the
        # generator shape -- the parse block above emits `Py_ssize_t n = 1`
        # for it and binds it as the leading `count` (kwlist {"count", "out"}
        # when an out= is offered, a positional "|n" otherwise). The stub
        # omitted it entirely, so `obj.run(4)` -- the call that actually works
        # -- failed to type-check while `obj.run(out=...)` passed. `count`
        # precedes `out` to match the kwlist order. The peer generator in
        # _stubs.py (the module-aggregated .pyi) carries the same rule.
        _stub_count_arg = m_var and arg_type == "void" and not params
        if _stub_count_arg:
            param_parts.append("count: int = 1")
        if _stub_enable_out:
            param_parts.append(f"out: {ret_ann} | None = None")
        sig = ", ".join(param_parts)
        _pyi_ret_desc = (
            f"Returns\n        -------\n        {ret_ann}\n"
            f"            {_ret_desc_txt}\n        "
            if ret_ann != "None"
            else ""
        )
        _pyi_param_desc = ""
        for _pp in (["x"] if has_arg else []) + [p["name"] for p in params]:
            _pyi_param_desc += f"        {_pp}\n            {_pdesc(_pp)}\n"
        _pyi_params_section = (
            f"        Parameters\n        ----------\n{_pyi_param_desc}        "
            if _pyi_param_desc
            else "        "
        )
        _pyi_doc = (
            f'        """{_brief or f"{name}."}\n\n'
            f"{_pyi_params_section}\n"
            f"        {_pyi_ret_desc}\n"
            f'        """\n'
            if (sig or ret_ann != "None")
            else f'        """{name}."""\n'
        )
        stub = (
            f"    def {name}(self, {sig}) -> {ret_ann}:\n{_pyi_doc}"
            if sig
            else f"    def {name}(self) -> {ret_ann}:\n{_pyi_doc}"
        )
        pyi_lines.append(stub)
        if _stub_enable_out:
            pyi_lines.append(
                f"    def {name}_max_out(self) -> int:\n"
                f'        """Max output length {name}() can produce'
                f' for the current state."""\n'
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
    if vtype == T.OBJECT_VALUE_TYPE:
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


def _container_pyi(p: dict) -> str:
    """Annotation for a container property (gh-543)."""
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

        if container:
            getter, _c_decls = _container_getter(
                component, Component, p, guard
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
            _expr = p["expr"]
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

        # Property __doc__ precedence: TOML `doc` > getter @brief > name.
        # PyGetSetDef's 4th field is the doc.
        _pblk = (doc_blocks or {}).get(f"{component}_get_{pname}")
        _pdoc = (
            p.get("doc")
            or (_pblk.brief if (_pblk and _pblk.brief) else "")
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
        elif container:
            py_t = _container_pyi(p)
        elif buf_field:
            py_t = _pyi_ndarray(ctype)
        else:
            py_t = _pyi_scalar(ctype)
        pyi_block = [
            "",
            "    @property",
            f"    def {pname}(self) -> {py_t}:",
            f'        """{_pdoc}"""',
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
