"""_context/_parse.py — shared parse-block builders.

Functions that construct C parse/argument-handling fragments used by
multiple make_*_ctx() builders.
"""

from __future__ import annotations

from .. import _coerce
from .._types import (
    _CTYPE_META,
    _CTYPE_TO_NPY,
    _join_fmt_with_optional,
    is_array_param_type,
    array_elem_ctype,
    _ctype_display,
)


def capsule_unwrap_c(
    name: str,
    ctype: str,
    capsule_name: str,
    obj_var: str,
    fail: str,
    indent: str = "    ",
    allow_none: bool = True,
) -> str:
    """C that turns *obj_var* into a borrowed C pointer named *name*.

    gh-432 introduced this for a **method** parameter; gh-790 needs the
    identical unwrap in ``tp_init`` so an object can be *constructed* from
    another module's capsule. Two copies of a name-checked
    ``PyCapsule_GetPointer`` plus its duck-typed fallback is exactly the kind
    of pair that drifts — one side gains a check the other never hears about
    — so there is one emitter and two callers.

    Accepts either form, which is the whole point of the ``_capsule``
    convention: the capsule itself, or any object exposing it as a
    ``_capsule`` attribute, so a caller passes the friendly wrapper rather
    than digging the capsule out of it.

    Parameters
    ----------
    name : str
        The C local to declare and fill with the unwrapped pointer.
    ctype : str
        The pointer's C type, e.g. ``"dp_tlm_t *"``.
    capsule_name : str
        The name the capsule must carry. A mismatch is a ``ValueError`` from
        ``PyCapsule_GetPointer``, which is the check that stops a pointer
        from one module being handed to another.
    obj_var : str
        The ``PyObject *`` local holding what the caller passed.
    fail : str
        The statement to execute on failure — ``"return NULL;"`` in a method
        wrapper, ``"return -1;"`` in ``tp_init``. Passing this in is what
        lets one emitter serve both; a hard-coded ``return NULL`` inside an
        ``initproc`` compiles with a warning and returns "success".
    indent : str
        Leading whitespace for each emitted line.
    allow_none : bool
        When True (the method default), ``None`` yields a ``NULL`` pointer —
        the C-side detach idiom. When False (the constructor case), the
        pointer is mandatory: there is no meaningful object to build around a
        handle that is not there, and a ``NULL`` would surface later as a
        failed ``create()`` with nothing pointing at the cause.
    """
    disp = _ctype_display(ctype)
    if not disp.endswith("*"):
        disp += " "
    cap = f"{name}_cap"
    i = indent
    inner = i + "    " if allow_none else i
    # A missing `_capsule` attribute surfaces as AttributeError from
    # PyObject_GetAttrString. On a METHOD that is fine and is what gh-432 has
    # always raised. On a CONSTRUCTOR it is the first thing a caller hits
    # after passing the wrong object, and "'int' object has no attribute
    # '_capsule'" names an implementation detail rather than the requirement
    # — so the mandatory form replaces it with a TypeError that says what to
    # pass. Scoped to the new path deliberately: gh-432's method behaviour is
    # unchanged, byte for byte.
    _no_attr = (
        f"{inner}    if (!{cap}) {{\n"
        f"{inner}        PyErr_Clear();\n"
        f"{inner}        PyErr_Format(PyExc_TypeError,\n"
        f'{inner}            "{name} must be the {capsule_name} capsule"\n'
        f'{inner}            " or an object exposing it as ._capsule,"\n'
        f'{inner}            " not %s", Py_TYPE({obj_var})->tp_name);\n'
        f"{inner}        {fail}\n"
        f"{inner}    }}\n"
        if not allow_none
        else f"{inner}    if (!{cap})\n{inner}        {fail}\n"
    )
    body = (
        f"{inner}PyObject *{cap} = {obj_var};\n"
        f"{inner}Py_INCREF({cap});\n"
        f"{inner}if (!PyCapsule_CheckExact({cap})) {{\n"
        f"{inner}    Py_DECREF({cap});\n"
        f'{inner}    {cap} = PyObject_GetAttrString({obj_var}, "_capsule");\n'
        f"{_no_attr}"
        f"{inner}}}\n"
        f"{inner}{name} = ({disp})PyCapsule_GetPointer({cap},"
        f' "{capsule_name}");\n'
        f"{inner}Py_DECREF({cap});\n"
        f"{inner}if (!{name})\n"
        f"{inner}    {fail}\n"
    )
    if allow_none:
        return (
            f"{i}{disp}{name} = NULL;\n"
            f"{i}if ({obj_var} != Py_None) {{\n"
            f"{body}"
            f"{i}}}"
        )
    # Mandatory: reject None up front rather than letting a NULL reach
    # create() and reporting the failure one layer away from its cause.
    return (
        f"{i}{disp}{name} = NULL;\n"
        f"{i}if ({obj_var} == Py_None || {obj_var} == NULL) {{\n"
        f"{i}    PyErr_SetString(PyExc_TypeError,\n"
        f'{i}        "{name} is required and cannot be None;"\n'
        f'{i}        " pass the {capsule_name} capsule or an object"\n'
        f'{i}        " exposing it as ._capsule");\n'
        f"{i}    {fail}\n"
        f"{i}}}\n"
        f"{body}".rstrip("\n")
    )


def _build_ml_doc(lines: list[str]) -> str:
    """Render a list of logical doc lines into adjacent C string literals.

    Each item in *lines* becomes one C string literal ending with ``\\n``.
    Continuation lines are indented with 5 spaces so the result slots
    cleanly into the 4th element of a PyMethodDef entry::

        pmd = (
            f'    {{"step", (PyCFunction)Nco_step, METH_VARARGS,\\n'
            f'     {_build_ml_doc(lines)}}},\\n'
        )

    Special characters (backslashes, double-quotes) inside *lines* are
    escaped for the C string literal automatically, and an item that is itself
    multi-line is split first (gh-633).

    That last part is not a convenience. An item carrying a raw ``\\n`` used to
    be emitted verbatim into the literal, producing an **unterminated C string**
    and a module that does not compile — quotes and backslashes were escaped on
    the very same path, so the escaping step existed and simply did not cover
    the newline. Splitting here rather than at each call site means a caller
    that hands over prose (a manifest ``doc =`` triple-quoted string, say)
    cannot reintroduce it; callers that already split see no change.

    Examples
    --------
    >>> print(_build_ml_doc(["one", "two"]))
    "one\\n"
         "two\\n"
    >>> print(_build_ml_doc(["one" + chr(10) + "two"]))
    "one\\n"
         "two\\n"
    >>> _build_ml_doc(['say "hi"'])
    '"say \\\\"hi\\\\"\\\\n"'
    """

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    flat: list[str] = []
    for item in lines:
        flat.extend(item.split("\n") if "\n" in item else [item])
    return "\n     ".join(f'"{_esc(ln)}\\n"' for ln in flat)


def _build_params_parse(
    params: list[dict],
) -> tuple[str, str, str]:
    """Build parse block + C call args + cleanup for a named multi-param method.

    params: list of {"name": str, "type": str}
      Scalar types come from _CTYPE_META.
      Array types end with '[]', e.g. "float _Complex[]"; their element type
      must be in _CTYPE_TO_NPY.  Array params expand to two C args:
      (const elem_t *name, size_t name_len).

    Returns (parse_block, call_args_c, cleanup):
      parse_block  — indented C code: declarations, PyArg_ParseTuple, array
                     conversion and error-exit paths with partial cleanup
      call_args_c  — comma-sep C variables/expressions for the downstream call
      cleanup      — Py_DECREF lines for all acquired numpy arrays (empty string
                     when no array params); caller must emit before every return
    """
    decl_lines: list[str] = []  # before PyArg_ParseTuple
    addr_exprs: list[str] = []  # &name args for PyArg_ParseTuple
    fmt_chars: list[str] = []  # format characters
    conv_lines: list[str] = []  # after PyArg_ParseTuple (scalars needing to_c)
    arr_acq: list[str] = []  # array acquisition lines (after ParseTuple)
    call_args: list[str] = []  # final C args to pass
    arr_names: list[str] = []  # arr variable names for Py_DECREF cleanup

    for p in params:
        pname = p["name"]
        ptype = p["type"]

        if is_array_param_type(ptype):
            elem_ct = array_elem_ctype(ptype)
            npy_enum = _CTYPE_TO_NPY[elem_ct]
            elem_disp = _ctype_display(elem_ct)
            obj_var = f"{pname}_obj"
            arr_var = f"{pname}_arr"

            decl_lines.append(f"    PyObject *{obj_var} = NULL;")
            fmt_chars.append("O")
            addr_exprs.append(f"&{obj_var}")

            # Build error path: decref all arrays acquired so far.
            prior_decrefs = "".join(f" Py_DECREF({a});" for a in arr_names)
            is_out = bool(p.get("out"))
            npy_flags = (
                "NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_WRITEABLE"
                if is_out
                else "NPY_ARRAY_C_CONTIGUOUS"
            )
            const_qual = "" if is_out else "const "
            # gh-581: an `out` param names the caller's own buffer, so require
            # the exact dtype before FROM_OTF gets a chance to cast it into a
            # temp the callee fills and we then discard.
            if is_out:
                arr_acq.append(
                    _coerce.out_buffer_guard(
                        obj_var,
                        npy_enum,
                        label=pname,
                        decrefs=prior_decrefs.strip(),
                    ).rstrip("\n")
                )
            arr_acq.append(
                f"    PyArrayObject *{arr_var} = (PyArrayObject *)"
                f"PyArray_FROM_OTF(\n"
                f"        {obj_var}, {npy_enum}, {npy_flags});\n"
                f"    if (!{arr_var}) {{{prior_decrefs} return NULL; }}"
            )
            arr_acq.append(
                f"    {const_qual}{elem_disp} *{pname} = "
                f"({const_qual}{elem_disp} *)PyArray_DATA({arr_var});\n"
                f"    size_t {pname}_len = (size_t)PyArray_SIZE({arr_var});"
            )
            arr_names.append(arr_var)
            call_args.extend([pname, f"{pname}_len"])
        elif p.get("capsule"):
            # gh-432: a foreign C pointer crossing as a named PyCapsule.
            # Parsed as a raw object; unwrapped after ParseTuple (before any
            # array acquisition, so an unwrap error needs no cleanup):
            #   None                        -> NULL (the C-side detach idiom)
            #   PyCapsule("<name>")         -> its pointer (name-checked)
            #   anything with a `_capsule`  -> that attribute, then as above
            # The `_capsule` duck-typing lets callers pass the friendly
            # wrapper object rather than digging the capsule out themselves.
            obj_var = f"{pname}_obj"
            decl_lines.append(f"    PyObject *{obj_var} = Py_None;")
            fmt_chars.append("O")
            addr_exprs.append(f"&{obj_var}")
            # gh-790: the unwrap moved to `capsule_unwrap_c` so tp_init can
            # reuse it verbatim. A method param keeps `allow_none=True` (the
            # detach idiom) and returns NULL on failure.
            conv_lines.append(
                capsule_unwrap_c(
                    pname,
                    ptype,
                    p["capsule"],
                    obj_var,
                    "return NULL;",
                )
            )
            call_args.append(pname)
        else:
            meta = _CTYPE_META[ptype]
            disp = _ctype_display(ptype)
            fmt_chars.append(meta["fmt"])

            if "parse_type" in meta:
                raw = f"{pname}_raw"
                # gh-432 drive-by: seed the raw local with the gh-240
                # default (not parse_zero) so an omitted defaulted arg
                # yields the default — previously only the non-parse_type
                # branch honoured `default`, so e.g. `decim: uint32_t = 1`
                # silently parsed as 0 when omitted.
                _raw_init = p.get("default") or meta["parse_zero"]
                decl_lines.append(
                    f"    {meta['parse_type']} {raw} = {_raw_init};"
                )
                addr_exprs.append(f"&{raw}")
                conv_lines.append(
                    f"    {disp} {pname} = {meta['to_c'](pname)};"
                )
            else:
                # gh-240: a scalar with a `default` is optional — seed its C
                # local with the default literal so an omitted arg yields it.
                init = p.get("default") or meta["zero"]
                decl_lines.append(f"    {disp} {pname} = {init};")
                addr_exprs.append(f"&{pname}")

            call_args.append(pname)

    # gh-238/gh-240: named methods are positional-OR-keyword (matching functions
    # and constructors). Each param name is a kwarg; a param with a `default`
    # goes after the `|` (optional). The wrapper must take a `PyObject *kwds`.
    kwnames = "".join(f'"{p["name"]}", ' for p in params)
    fmt_str = _join_fmt_with_optional(fmt_chars, params)
    addr_str = ", ".join(addr_exprs)
    lines = (
        [f"    static char *_kwlist[] = {{{kwnames}NULL}};"]
        + decl_lines
        + [
            f'    if (!PyArg_ParseTupleAndKeywords(args, kwds, "{fmt_str}",',
            f"            _kwlist, {addr_str}))",
            "        return NULL;",
        ]
        + conv_lines
        + arr_acq
    )
    cleanup = "".join(f"    Py_DECREF({a});\n" for a in arr_names)
    return "\n".join(lines) + "\n", ", ".join(call_args), cleanup


def _step_parse_block(
    sample_type: str,
    samp: dict,
    ctrl: "list[tuple[str, str, str]]" = (),
) -> str:
    """4-space-indented parse block for step(); ends without trailing newline.

    Uses 'x_raw' as the intermediate parse variable so to_c("x") works
    (the to_c lambdas append '_raw' to the base name they receive).

    ``ctrl`` is the controllable per-call override list (gh-240): each entry
    ``(name, c_display_type, pyarg_fmt)`` becomes a **positional-optional**
    argument after ``x`` — ``step(x[, gain])``. step() stays positional-only
    (never METH_KEYWORDS) because a keyword call costs ~3.4x the call on this
    per-sample hot path. Each override defaults to the live ``self->handle->``
    field, so omitting it is free and the override is non-persistent. Empty
    ``ctrl`` reproduces the original byte-for-byte (the ``|`` is absent).
    """
    disp = _ctype_display(sample_type)
    ctrl_locals = "".join(
        f"    {cdisp} {name} = self->handle->{name};\n"
        for name, cdisp, _ in ctrl
    )
    ctrl_fmt = "|" + "".join(f for _, _, f in ctrl) if ctrl else ""
    ctrl_refs = "".join(f", &{name}" for name, _, _ in ctrl)
    if "parse_type" in samp:
        parse_type = samp["parse_type"]
        parse_zero = samp["parse_zero"]
        fmt = samp["fmt"]
        to_c_expr = samp["to_c"]("x")
        return (
            f"    {parse_type} x_raw = {parse_zero};\n"
            f"{ctrl_locals}"
            f'    if (!PyArg_ParseTuple(args, "{fmt}{ctrl_fmt}",'
            f" &x_raw{ctrl_refs}))\n"
            f"        return NULL;\n"
            f"    {disp} x = {to_c_expr};"
        )
    else:
        fmt = samp["fmt"]
        return (
            f"    {disp} x;\n"
            f"{ctrl_locals}"
            f'    if (!PyArg_ParseTuple(args, "{fmt}{ctrl_fmt}",'
            f" &x{ctrl_refs}))\n"
            f"        return NULL;"
        )
