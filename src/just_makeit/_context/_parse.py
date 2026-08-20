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


def enum_symbols(Component: str, name: str) -> tuple[str, str]:
    """C symbol names for one object-scoped enum, namespaced by *Component*.

    gh-519 introduced this for properties; gh-1021 gave method parameters the
    same feature, and both index the SAME tables in the same translation unit
    — so the naming lives here, below both consumers, rather than in
    `_context._methods` where only one of them could reach it.

    A module's ``_ext.c`` ``#include``s every object's fragment into a single
    TU, and a view (gh-504) adds another type over the same ``component``. Two
    types in that TU may each reference the same ``[[enum]]``, and module-level
    ``function`` enums (gh-353) already own the bare ``_enum_index`` /
    ``_enum_<name>`` symbols. ``Component`` is the one name unique per type
    section, so it is what namespaces them.

    Returns ``(index_fn, table)``.
    """
    return (f"_enum_index_{Component}", f"_enum_{Component}_{name}")


def capsule_new_c(
    ptr_expr: str,
    capsule_name: str,
    owner: str,
    indent: str = "    ",
) -> str:
    """The body that publishes *ptr_expr* as a borrowed named ``PyCapsule``.

    The producing half of the capsule triangle, and the peer of
    :func:`capsule_unwrap_c`. gh-788 gap 4 introduced it for an **object**
    property; gh-794 needs the identical two lines on a ``kind = "handle"``
    type, which is the shape most likely to be on the giving end of a capsule
    and was the only one that could not give one.

    Extracted for the same reason the unwrap was: this is a *contract*, not a
    call. The NULL destructor is the whole of it, and a second copy is a place
    for that to be quietly changed on one side only.

    Parameters
    ----------
    ptr_expr : str
        C expression for the pointer to lend (``self->handle``, ``self->h``,
        or something reached through either).
    capsule_name : str
        The name the capsule carries. Consumers name-check it, so it is
        load-bearing string data rather than a label.
    owner : str
        Display name of the owning type, for the comment only.
    indent : str
        Leading whitespace for each emitted line.

    Notes
    -----
    **The destructor is NULL and is not configurable.** The capsule lends a
    pointer the owner still owns; a capsule with a destructor would free it on
    garbage collection and the owner would free it again in its deallocator. A
    capsule that *owns* its pointer is a different feature and should look
    different.

    Examples
    --------
    >>> print(capsule_new_c("self->h", "doppler.wfm.clk", "SampleClock"))
        /* Borrowed: NULL destructor, so the capsule never
           frees a pointer SampleClock still owns. */
        return PyCapsule_New((void *)(self->h),
                             "doppler.wfm.clk", NULL);
    """
    i = indent
    return (
        f"{i}/* Borrowed: NULL destructor, so the capsule never\n"
        f"{i}   frees a pointer {owner} still owns. */\n"
        f"{i}return PyCapsule_New((void *)({ptr_expr}),\n"
        f'{i}                     "{capsule_name}", NULL);'
    )


def capsule_unwrap_c(
    name: str,
    ctype: str,
    capsule_name: str,
    obj_var: str,
    fail: str,
    indent: str = "    ",
    allow_none: bool = True,
    explain_type_error: bool = False,
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
        the C-side detach idiom. When False, the pointer is mandatory and
        ``None`` is rejected up front rather than surfacing later as a failed
        ``create()`` with nothing pointing at the cause.
    explain_type_error : bool
        Replace the raw ``AttributeError`` from the ``._capsule`` lookup with
        a ``TypeError`` naming what to pass. True for a **constructor**, where
        it is the first thing a caller hits after passing the wrong object.

        gh-805 §H split this out of ``allow_none``. Until then the two were
        the same question by accident — every constructor was mandatory, so
        ``allow_none=False`` could stand in for "this is a ``tp_init``". A
        nullable constructor param breaks that coupling, and leaving them
        fused silently downgraded exactly the message this exists to give:
        ``'int' object has no attribute '_capsule'`` names an implementation
        detail instead of the requirement. Two questions, two parameters.
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
    # — so a constructor replaces it with a TypeError that says what to pass.
    # gh-432's method behaviour is unchanged, byte for byte.
    #
    # Keyed on `explain_type_error`, NOT on `allow_none`. Those were the same
    # question only while every constructor param was mandatory (gh-805 §H).
    #
    # gh-794 made that replacement conditional. `_capsule` used to be a plain
    # attribute, so AttributeError was the ONLY way this lookup could fail and
    # clearing unconditionally was safe. A handle's `_capsule` is a getter that
    # raises RuntimeError("<T> is closed") over a closed handle — a real
    # diagnosis, and the one the caller most needs. Clearing it and reporting
    # "not a SampleClock" about an object that IS a SampleClock is worse than
    # unhelpful: it is false, and it sends the reader looking at the argument's
    # type instead of its lifetime. So only an AttributeError is upgraded;
    # anything else propagates untouched.
    _no_attr = (
        f"{inner}    if (!{cap}) {{\n"
        f"{inner}        if (!PyErr_ExceptionMatches(PyExc_AttributeError))\n"
        f"{inner}            {fail}\n"
        f"{inner}        PyErr_Clear();\n"
        f"{inner}        PyErr_Format(PyExc_TypeError,\n"
        f'{inner}            "{name} must be the {capsule_name} capsule"\n'
        f'{inner}            " or an object exposing it as ._capsule,"\n'
        f'{inner}            " not %s", Py_TYPE({obj_var})->tp_name);\n'
        f"{inner}        {fail}\n"
        f"{inner}    }}\n"
        if explain_type_error
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
    Component: str = "",
    enums: dict[str, list[str]] | None = None,
) -> tuple[str, str, str]:
    """Build parse block + C call args + cleanup for a named multi-param method.

    params: list of {"name": str, "type": str}
      Scalar types come from _CTYPE_META.
      A param carrying ``enum`` is a string-enum (gh-1021): its Python
      argument is the choice STRING, validated to the ``[[enum]]`` SSOT int
      before the C call. `Component` namespaces the tables it indexes — see
      `enum_symbols` — so it is required for those and unused otherwise.
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

        if p.get("enum"):
            # gh-1021: parse the choice string with `s` and validate it to its
            # SSOT int, mirroring the module-function emitter in
            # `_render._build_params_parse` — the difference is only the
            # symbol namespace (`enum_symbols`), because an object's tables
            # live in a TU that a module's bare `_enum_index` also occupies.
            #
            # No cleanup on the failure path: `conv_lines` are emitted BEFORE
            # `arr_acq`, so no array has been acquired yet when this runs. The
            # capsule branch below relies on the same ordering.
            # gh-1021 follow-up: `enum` is checked FIRST, so it silently won
            # over both of these — an array param generated a scalar string
            # parse against a kernel expecting `(const T *, size_t)`, and a
            # capsule param dropped its unwrap entirely. The property path
            # already refuses the same pair (`enum` + `buf_field`) with the
            # same reasoning: a sequence of enum strings has no decoded form.
            if is_array_param_type(ptype):
                raise ValueError(
                    f"param '{pname}': `enum` is not supported on an array "
                    f"parameter ({ptype}) — a sequence of enum strings has "
                    f"no decoded form. Drop `enum`, or make the parameter a "
                    f"scalar `int`."
                )
            if p.get("capsule"):
                raise ValueError(
                    f"param '{pname}': `enum` and `capsule` are two different "
                    f"parameters. One takes a choice string, the other a "
                    f"foreign pointer. Declare two, or drop one."
                )
            ename = p["enum"]
            index_fn, table = enum_symbols(Component, ename)
            # Name the choices, exactly as the PROPERTY setter does for the
            # same enum on the same object — a caller who hits both should not
            # meet two styles of the same refusal. Absent registry (the `bind`
            # path) simply drops the suffix.
            _choices = ", ".join((enums or {}).get(ename, []))
            _suffix = f" (choices: {_choices})" if _choices else ""
            fmt_chars.append("s")
            # gh-240: a defaulted enum is optional, and its C local seeds to
            # the default CHOICE STRING so an omitted argument validates to
            # that choice. A required one seeds to "" — an invalid choice, but
            # PyArg fills it before the lookup runs.
            decl_lines.append(
                f'    const char *{pname} = "{p.get("default") or ""}";'
            )
            addr_exprs.append(f"&{pname}")
            conv_lines.append(
                f"    int _arg_{pname} = {index_fn}({table}, {pname});\n"
                f"    if (_arg_{pname} < 0) {{\n"
                f"        PyErr_Format(PyExc_ValueError,\n"
                f"            \"invalid {pname} '%s'{_suffix}\","
                f" {pname});\n"
                f"        return NULL;\n"
                f"    }}"
            )
            call_args.append(f"_arg_{pname}")
        elif is_array_param_type(ptype):
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
            # gh-805 §C: an opt-in rank guard, before the length is taken —
            # `PyArray_SIZE` on a 2-D array silently yields its total element
            # count, which is a valid-looking length for a 1-D contract.
            _rank = p.get("rank")
            if _rank:
                arr_acq.append(
                    _coerce.array_rank_guard(
                        pname, arr_var, int(_rank), prior_decrefs.strip()
                    ).rstrip("\n")
                )
            arr_acq.append(
                f"    {const_qual}{elem_disp} *{pname} = "
                f"({const_qual}{elem_disp} *)PyArray_DATA({arr_var});\n"
                + _coerce.array_len_c(
                    pname, arr_var, int(p.get("elements_per_sample", 1) or 1)
                )
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
            # gh-1021: `type = "enum:<name>"` is the INIT-PARAM spelling. It
            # reached `_CTYPE_META[ptype]` and raised a bare `KeyError` naming
            # only the string, from a stack frame deep inside the renderer —
            # a traceback where a diagnostic belongs. A method parameter
            # spells its enum as the `enum` KEY, which keeps the enum's NAME
            # (the init-param form flattens to `string_enum:a,b,c` and loses
            # it), so say so rather than crash.
            if ptype.startswith("enum:"):
                raise ValueError(
                    f"param '{pname}': `type = \"{ptype}\"` is the "
                    f"init_param spelling and is not read on a method "
                    f"parameter.\n"
                    f"Declare it as the type it is in C, plus the enum by "
                    f"name:\n"
                    f'    {{ name = "{pname}", type = "int", '
                    f'enum = "{ptype[len("enum:") :]}" }}'
                )
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
