"""Generate __init__.pyi type stubs for scaffolded C-extension modules.

Each module gets a stub alongside its __init__.py that mirrors every
class, method, property, and module-level function with proper Python
annotations.  The stubs are regenerated in full every time any command
mutates the module (object, method, property, function).

C-type → Python annotation rules
---------------------------------
  float / double               -> float
  *_Complex                    -> complex
  int* / uint* / size_t        -> int
  void                         -> None
  <elem_ctype>[]  (array)      -> NDArray[<numpy_dtype>]

Docstring convention
--------------------
All class docstrings use numpy-style format with ``Parameters`` and
``Examples`` sections.  The ``Examples`` section contains runnable
doctests:  ``python -m doctest -v src/<pkg>/<module>/<module>.pyi``
"""

from __future__ import annotations

import re as _re

from . import _config as C

# ── annotation maps ──────────────────────────────────────────────────────────

_CTYPE_TO_PY: dict[str, str] = {
    "float": "float",
    "double": "float",
    "float _Complex": "complex",
    "double _Complex": "complex",
    "bool": "bool",
    "int": "int",
    "int8_t": "int",
    "int16_t": "int",
    "int32_t": "int",
    "int64_t": "int",
    "uint8_t": "int",
    "uint16_t": "int",
    "uint32_t": "int",
    "uint64_t": "int",
    "size_t": "int",
}

_CTYPE_TO_NP: dict[str, str] = {
    "bool": "np.bool_",
    "float": "np.float32",
    "double": "np.float64",
    "float _Complex": "np.complex64",
    "double _Complex": "np.complex128",
    "int": "np.int32",
    "int8_t": "np.int8",
    "int16_t": "np.int16",
    "int32_t": "np.int32",
    "int64_t": "np.int64",
    "uint8_t": "np.uint8",
    "uint16_t": "np.uint16",
    "uint32_t": "np.uint32",
    "uint64_t": "np.uint64",
    "size_t": "np.uintp",
}

_DTYPE_TO_CTYPE: dict[str, str] = {
    "float32": "float",
    "float64": "double",
    "complex64": "float _Complex",
    "complex128": "double _Complex",
    "int8": "int8_t",
    "int16": "int16_t",
    "int32": "int32_t",
    "int64": "int64_t",
    "uint8": "uint8_t",
    "uint16": "uint16_t",
    "uint32": "uint32_t",
    "uint64": "uint64_t",
    "uintp": "size_t",
    "intp": "ptrdiff_t",
}


def _py(ctype: str) -> str:
    """Return the Python annotation string for a C type."""
    if ctype == "void":
        return "None"
    if ctype.endswith("[]"):
        # Strip all [] suffixes to handle both 1-D (float[]) and 2-D (float[][]).
        elem = ctype
        while elem.endswith("[]"):
            elem = elem[:-2]
        npt = _CTYPE_TO_NP.get(elem, "Any")
        return f"NDArray[{npt}]"
    if ctype.startswith("string_enum:"):
        choices = ctype[len("string_enum:") :].split(",")
        return "Literal[" + ", ".join(f'"{c}"' for c in choices) + "]"
    return _CTYPE_TO_PY.get(ctype, "Any")


def _np(ctype: str) -> str:
    """Return the numpy dtype string for a C type (scalar or array) for NDArray hints."""
    elem = ctype
    while elem.endswith("[]"):
        elem = elem[:-2]
    return _CTYPE_TO_NP.get(elem, "Any")


# ── per-object class stub ─────────────────────────────────────────────────────


def _title(name: str) -> str:
    return "".join(w.title() for w in name.split("_"))


_ARRAY_RE = _re.compile(r"^([\w\s_]+)\[(\d+)\]$")


def _py_default_stub(ctype: str, default: str) -> str:
    """Convert a C default literal to a Python literal (stub helper)."""
    if ctype not in _CTYPE_TO_PY:
        return "..."
    kind_map = {
        "float": "float",
        "double": "float",
        "float _Complex": "complex",
        "double _Complex": "complex",
    }
    kind = kind_map.get(ctype, "int")
    if kind == "float":
        s = default.rstrip("fF")
        if "." not in s and "e" not in s.lower():
            s += ".0"
        return s
    if kind == "complex":
        return "0j"
    return default


def _doctest_out(ctype: str, default: str) -> str | None:
    """Expected repr from a getter call, or None if not safe for doctests."""
    m = _ARRAY_RE.match(ctype.strip())
    if m:
        return None  # array fields: no scalar getter
    if ctype not in _CTYPE_TO_PY:
        return None
    kind_map = {
        "float": "float",
        "double": "float",
        "float _Complex": "complex",
        "double _Complex": "complex",
    }
    kind = kind_map.get(ctype, "int")
    if kind == "int":
        val = _py_default_stub(ctype, default)
        try:
            int(val)
            return val
        except ValueError:
            return None
    if kind == "float":
        s = default.rstrip("fF")
        try:
            v = float(s)
            if v == int(v):
                return repr(v)
        except ValueError:
            pass
        return None
    if kind == "complex":
        return "0j"
    return None


def _build_class_docstring(
    Component: str,
    state_vars: list,
    no_state: bool,
    init_params: list,
    import_line: str,
    py_create_args: str,
    brief: str = "",
    custom_reset: bool = False,
) -> list[str]:
    """Return lines for a numpy-style class docstring (indented 4 spaces).

    *brief* — when supplied (from the create()'s ``@brief`` in the sacred
    header) — becomes the summary line in place of the generic
    ``"<Component> component."``.
    """
    summary = brief or f"{Component} component."
    lines: list[str] = [f'    """{summary}', ""]

    # Parameters section. init_params win when present (they are what create()
    # actually takes — the #69 contract); state vars are documented only for a
    # plain --state object with no init_params.
    param_lines: list[str] = []
    if init_params:
        for name, ctype, dflt, *rest in init_params:
            optional = rest[4] if len(rest) >= 5 else False
            py_t = _py(ctype)
            if optional:
                py_t = f"{py_t} or None"
            if ctype.startswith("string_enum:"):
                py_d = f'"{dflt}"' if dflt else "..."
            else:
                py_d = _py_default_stub(ctype, dflt)
            param_lines += [
                f"    {name} : {py_t}, default {py_d}",
                f"        {name} constructor parameter.",
            ]
    elif state_vars and not no_state:
        for name, ctype, dflt in state_vars:
            m = _ARRAY_RE.match(ctype.strip())
            if m:
                elem, size = m.group(1).rstrip(), m.group(2)
                npt = _CTYPE_TO_NP.get(elem, "Any")
                param_lines += [
                    f"    {name} : NDArray[{npt}]",
                    f"        Length-{size} array, zero-initialised.",
                ]
            else:
                py_t = _CTYPE_TO_PY.get(ctype, "Any")
                py_d = _py_default_stub(ctype, dflt)
                param_lines += [
                    f"    {name} : {py_t}, default {py_d}",
                    f"        {name} state variable.",
                ]

    if param_lines:
        lines += ["    Parameters", "    ----------"] + param_lines + [""]

    # Examples section: construction + safe getter calls + reset demo
    scalar_getters: list[tuple[str, str]] = []
    if state_vars and not no_state:
        for name, ctype, dflt in state_vars:
            out = _doctest_out(ctype, dflt)
            if out is not None:
                scalar_getters.append((name, out))

    # Only emit a runnable construction example when every constructor
    # argument has a safe literal. An array/no-default arg renders as `...`
    # (ellipsis), which is not a valid call — emitting it would produce a
    # doctest that raises TypeError. In that case, skip the Examples block
    # entirely rather than ship a broken example.
    if "..." in py_create_args:
        lines.append('    """')
        return lines

    ex: list[str] = [
        "    Examples",
        "    --------",
        "    Create with defaults:",
        "",
        f"    >>> {import_line}",
        f"    >>> obj = {Component}({py_create_args})",
    ]
    for name, out in scalar_getters[:3]:
        ex += [f"    >>> obj.get_{name}()", f"    {out}"]

    # The "reset restores defaults" demo assumes reset() zeroes the first state
    # var. A custom reset_impl (#51) may deliberately preserve config (e.g. a
    # waveform `type` set by create_impl), so skip the demo there.
    if scalar_getters and not custom_reset:
        first_name, first_out = scalar_getters[0]
        first_ct = next(ct for n, ct, _ in state_vars if n == first_name)
        kind_map = {
            "float": "float",
            "double": "float",
            "float _Complex": "complex",
            "double _Complex": "complex",
        }
        kind = kind_map.get(first_ct, "int")
        set_val = (
            "0"
            if (kind == "int" and first_out != "0")
            else "42"
            if kind == "int"
            else "0.0"
            if first_out != "0.0"
            else "1.0"
        )
        ex += [
            "",
            "    Reset restores defaults:",
            "",
            f"    >>> obj.set_{first_name}({set_val})",
            "    >>> obj.reset()",
            f"    >>> obj.get_{first_name}()",
            f"    {first_out}",
        ]

    ex.append("")
    lines += ex
    lines.append('    """')
    return lines


def _method_doc_lines(
    block,
    m_name: str,
    py_params: list[tuple[str, str]],
    ret_ann: str,
    override: str = "",
) -> list[str]:
    """Return indented `.pyi` docstring lines for a method.

    Summary precedence: *override* (TOML ``doc``) > the Doxygen *block*'s
    ``@brief`` > name fallback. With an override or a block, emit a
    numpy-style docstring (summary + Parameters + Returns); otherwise fall
    back to the historical one-line name-based stub.
    """
    fallback = f'        """{m_name.replace("_", " ").capitalize()}."""'
    if block is None and not override:
        return [fallback]
    from ._docstring import _wrap, render_numpy_method_doc

    if block is not None:
        summary, body, descs, ret, examples = render_numpy_method_doc(
            block, py_params
        )
    else:
        summary, body, descs, ret, examples = "", [], {}, "", []
    summary = override or summary
    if not summary:
        summary = m_name.replace("_", " ").capitalize() + "."
    out = [f'        """{summary}']
    for para in body:  # extended description — flowing, wrapped paragraphs
        out.append("")
        out += [f"        {w}" for w in _wrap(para, 72)]
    if py_params:
        out += ["", "        Parameters", "        ----------"]
        for pname, ann in py_params:
            out.append(f"        {pname} : {ann}")
            out.append(f"            {descs.get(pname) or 'Input.'}")
    if ret_ann != "None":
        out += [
            "",
            "        Returns",
            "        -------",
            f"        {ret_ann}",
            f"            {ret or 'Output.'}",
        ]
    if examples:  # @code ... @endcode -> runnable doctest
        out += ["", "        Examples", "        --------"]
        out += [f"        {ex}".rstrip() for ex in examples]
        # Trailing blank: under pytest --doctest-glob the .pyi is parsed as a
        # text file, where expected output runs until a blank line — without
        # this the closing `"""` is swallowed into the last example's output.
        out.append("")
    out.append('        """')
    return out


def _obj_stream_pyi(cfg: dict, obj: str) -> str:
    """Return the ``stream()`` / ``__iter__`` ``.pyi`` block for *obj*.

    Empty string when the object is not ``--streamable`` or has no resolvable
    block producer.  Reuses ``make_stream_ctx`` so the module ``.pyi`` matches
    the standalone ``component.pyi`` stub exactly (gh-203).
    """
    from ._context import make_stream_ctx

    Component = C.class_name(cfg, obj) or _title(obj)
    return make_stream_ctx(
        obj,
        Component,
        Component,
        streamable=C.is_streamable(cfg, obj),
        methods=C.methods(cfg, obj),
        arg_type=C.arg_type(cfg, obj),
        return_type=C.return_type(cfg, obj),
        default_block=C.stream_block_default(cfg, obj),
    )["pyi_stream_methods"]


def _obj_stub(cfg: dict, obj: str, pkg: str = "", module: str = "") -> str:
    Component = C.class_name(cfg, obj) or _title(obj)
    state_vars = C.state_vars(cfg, obj)
    arg_type = C.arg_type(cfg, obj)
    return_type = C.return_type(cfg, obj)
    obj_methods = C.methods(cfg, obj)
    obj_props = C.properties(cfg, obj)
    # Doxygen blocks parsed from the sacred header, stashed on cfg by
    # _object._regenerate_module. Maps C function name -> DoxyBlock.
    doc_blocks = cfg.get(obj, {}).get("_doc_blocks", {}) or {}
    state_names = {n for n, _, _ in state_vars}
    ip = C.init_params(cfg, obj)
    no_step = C.is_no_step(cfg, obj)
    no_state = C.is_no_state(cfg, obj)

    def _builtin_doc(cfn, py_params, ret_ann, fallback_doc):
        """Docstring lines for a built-in method: the header Doxygen for *cfn*
        when present (so reset/step/steps are documentable), else the canned
        one-liner *fallback_doc*."""
        blk = doc_blocks.get(cfn)
        if blk is not None:
            return _method_doc_lines(blk, cfn, py_params, ret_ann)
        return [f'        """{fallback_doc}"""']

    # Constructor arg string for doctest. init_params drive create() when
    # present (the #69 contract — even when scalar state vars also exist, which
    # are then hidden/bridged), so the example must use them; a string_enum
    # default renders as its quoted string, not the enum index.
    scalar_vars = [
        (n, ct, dflt)
        for n, ct, dflt in state_vars
        if not _ARRAY_RE.match(ct.strip())
    ]

    def _ctor_literal(ct: str, dflt: str) -> str:
        if ct.startswith("string_enum:"):
            return f'"{dflt}"' if dflt else "..."
        return _py_default_stub(ct, dflt)

    py_create_args = (
        # keyword args: order-independent against the binding's parse order, and
        # self-documenting (string_enums show their chosen string).
        ", ".join(f"{n}={_ctor_literal(ct, dflt)}" for n, ct, dflt, *_ in ip)
        if ip
        else (
            ", ".join(
                _py_default_stub(ct, dflt) for _, ct, dflt in scalar_vars
            )
            if (scalar_vars and not no_state)
            else ""
        )
    )

    import_line = (
        f"from {pkg}.{module} import {Component}"
        if pkg and module
        else f"from {pkg} import {Component}"
        if pkg
        else f"from ... import {Component}"
    )

    # Class docstring
    _create_blk = doc_blocks.get(f"{obj}_create")
    _class_brief = cfg.get(obj, {}).get("doc") or (
        _create_blk.brief if (_create_blk and _create_blk.brief) else ""
    )
    doc_lines = _build_class_docstring(
        Component,
        state_vars,
        no_state,
        list(ip),
        import_line,
        py_create_args,
        brief=_class_brief,
        # init_params imply a create_impl that derives state from the params
        # (the #69 contract), so the first state var is config — not guaranteed
        # zeroed by reset(). Skip the "reset restores defaults" demo there.
        # (init_params survive the apply-path cfg; reset_impl/create_impl don't.)
        custom_reset=bool(ip),
    )
    lines: list[str] = [f"class {Component}:"] + doc_lines

    # __init__
    if state_vars and not no_state:
        init_params_str = ", ".join(
            f"{n}: {_py(t)} = ..." for n, t, _ in scalar_vars
        )
        lines.append(f"    def __init__(self, {init_params_str}) -> None: ...")
    elif ip:
        parts_init: list[str] = []
        for param in ip:
            n, t = param[0], param[1]
            optional = param[6] if len(param) > 6 else False
            if optional:
                parts_init.append(f"{n}: {_py(t)} | None = None")
            elif t.startswith("string_enum:"):
                dflt = param[2] if len(param) > 2 else ""
                parts_init.append(
                    f'{n}: {_py(t)} = "{dflt}"'
                    if dflt
                    else f"{n}: {_py(t)} = ..."
                )
            else:
                parts_init.append(f"{n}: {_py(t)} = ...")
        init_params_str = ", ".join(parts_init)
        lines.append(f"    def __init__(self, {init_params_str}) -> None: ...")
    else:
        lines.append("    def __init__(self, /, *args, **kwargs) -> None: ...")

    # gh-131: skip the built-in reset() stub when the user declared a
    # [[methods]] entry named "reset"; that entry's stub appears below in
    # the extra-methods loop and must not be duplicated here.
    _user_has_reset = any(m["name"] == "reset" for m in obj_methods)
    if not _user_has_reset:
        lines += ["", "    def reset(self) -> None:"]
        lines += _builtin_doc(
            f"{obj}_reset", [], "None", "Reset state to post-create defaults."
        )

    # step() / steps()
    if no_step:
        pass
    elif arg_type.endswith("[]"):
        lines += [
            "",
            f"    def step(self, x: {_py(arg_type)}) -> {_py(return_type)}:",
        ]
        lines += _builtin_doc(
            f"{obj}_step",
            [("x", _py(arg_type))],
            _py(return_type),
            "Process one buffer of samples.",
        )
    elif arg_type != "void":
        lines += [
            "",
            f"    def step(self, x: {_py(arg_type)}) -> {_py(return_type)}:",
        ]
        lines += _builtin_doc(
            f"{obj}_step",
            [("x", _py(arg_type))],
            _py(return_type),
            "Process one input sample.",
        )
        if return_type != "void":
            lines += [
                "",
                f"    def steps(self, x: NDArray[{_np(arg_type)}],"
                f" out: NDArray[{_np(return_type)}] | None = None)"
                f" -> NDArray[{_np(return_type)}]:",
            ]
            lines += _builtin_doc(
                f"{obj}_steps",
                [("x", f"NDArray[{_np(arg_type)}]")],
                f"NDArray[{_np(return_type)}]",
                "Process a samples array.",
            )
        else:
            lines += [
                "",
                f"    def steps(self, x: NDArray[{_np(arg_type)}]) -> None:",
            ]
            lines += _builtin_doc(
                f"{obj}_steps",
                [("x", f"NDArray[{_np(arg_type)}]")],
                "None",
                "Process a samples array.",
            )
    else:
        lines += ["", f"    def step(self) -> {_py(return_type)}:"]
        lines += _builtin_doc(
            f"{obj}_step", [], _py(return_type), "Generate one output sample."
        )
        if return_type != "void":
            lines += [
                "",
                f"    def steps(self, n: int) -> NDArray[{_np(return_type)}]:",
            ]
            lines += _builtin_doc(
                f"{obj}_steps",
                [("n", "int")],
                f"NDArray[{_np(return_type)}]",
                "Generate n output samples.",
            )
        else:
            lines += ["", "    def steps(self, n: int) -> None:"]
            lines += _builtin_doc(
                f"{obj}_steps",
                [("n", "int")],
                "None",
                "Advance state by n ticks.",
            )

    # extra methods
    for m in obj_methods:
        m_name = m["name"]
        _blk = doc_blocks.get(f"{obj}_{m_name}")
        if m.get("varargs"):
            _va_doc = (
                m.get("doc")
                or (_blk.brief if (_blk and _blk.brief) else "")
                or f"{m_name.replace('_', ' ').capitalize()}."
            )
            lines += [
                "",
                f"    def {m_name}(self, *args: Any, **kwargs: Any) -> Any:",
                f'        """{_va_doc}"""',
            ]
            continue
        m_ret = m.get("return_type", "void")
        m_params = m.get("params", [])
        m_arg = m.get("arg_type", "void")
        m_var = m.get("variable_output", False)
        m_multi = m.get("multi_output", [])
        m_result_fields = m.get("result_fields", [])
        m_py_return_type = m.get("py_return_type", "")

        param_parts: list[str] = []
        if m_arg != "void":
            param_parts.append(f"x: {_py(m_arg)}")
        for p in m_params:
            param_parts.append(f"{p['name']}: {_py(p['type'])}")

        if m_py_return_type:
            ret_ann = m_py_return_type
        elif m_result_fields:
            field_types = ", ".join(_py(f["type"]) for f in m_result_fields)
            ret_ann = f"list[tuple[{field_types}]]"
        elif m_var:
            all_rts = [m_ret] + list(m_multi)
            ndarrays = [f"NDArray[{_np(rt)}]" for rt in all_rts]
            ret_ann = (
                f"tuple[{', '.join(ndarrays)}]"
                if len(ndarrays) > 1
                else ndarrays[0]
            )
        else:
            ret_ann = _py(m_ret)

        sig = ", ".join(param_parts)
        # (name, annotation) for the Python-facing args, for the doc builder.
        _py_params: list[tuple[str, str]] = []
        if m_arg != "void":
            _py_params.append(("x", _py(m_arg)))
        for p in m_params:
            _py_params.append((p["name"], _py(p["type"])))
        _doc = _method_doc_lines(
            _blk, m_name, _py_params, ret_ann, override=m.get("doc", "")
        )
        header = (
            f"    def {m_name}(self, {sig}) -> {ret_ann}:"
            if sig
            else f"    def {m_name}(self) -> {ret_ann}:"
        )
        lines += ["", header, *_doc]

    # properties
    for prop in obj_props:
        p_name = prop["name"]
        p_ctype = prop.get("type") or prop.get("ctype", "size_t")
        p_write = prop.get("writable", False) or (p_name in state_names)
        py_t = _py(p_ctype)
        _pblk = doc_blocks.get(f"{obj}_get_{p_name}")
        _pdoc = (
            prop.get("doc")
            or (_pblk.brief if (_pblk and _pblk.brief) else "")
            or f"{p_name.replace('_', ' ').capitalize()}."
        )
        lines += [
            "",
            "    @property",
            f"    def {p_name}(self) -> {py_t}:",
            f'        """{_pdoc}"""',
        ]
        if p_write:
            lines += [
                f"    @{p_name}.setter",
                f"    def {p_name}(self, value: {py_t}) -> None: ...",
            ]

    # Stream generator (gh-203): a streamable object grows stream()/__iter__.
    _stream_pyi = _obj_stream_pyi(cfg, obj)
    if _stream_pyi:
        lines.append(_stream_pyi.rstrip("\n"))

    lines += [
        "",
        "    def destroy(self) -> None:",
        '        """Release C resources immediately."""',
        "",
        '    def __enter__(self) -> "' + Component + '": ...',
        "",
        "    def __exit__(self, *args: object) -> None: ...",
    ]

    return "\n".join(lines)


# ── module-level function stub ────────────────────────────────────────────────


def _fn_stub(fn: dict) -> str:
    name = fn["name"]
    out_type = fn.get("out_type")
    if out_type:
        # Strip optional [param_name] length suffix (e.g. "float64[M]" → "float64")
        _ot_base = _re.sub(r"\[[A-Za-z_][A-Za-z_0-9]*\]$", "", out_type)
        # Resolve numpy dtype aliases (e.g. "float64" → "double") for _py().
        _ot_ctype = _DTYPE_TO_CTYPE.get(_ot_base, _ot_base)
        ret = _py(f"{_ot_ctype}[]")
    else:
        ret = _py(fn.get("return_type", "void"))
    params = fn.get("params", [])
    doc = fn.get("doc", "")
    parts = [f"{p['name']}: {_py(p['type'])}" for p in params]
    sig = f"def {name}({', '.join(parts)}) -> {ret}:"
    one_liner = (
        doc.split("\n")[0]
        if doc
        else name.replace("_", " ").capitalize() + "."
    )
    return f'{sig}\n    """{one_liner}"""'


# ── numpy import decision ─────────────────────────────────────────────────────


def _uses_any(cfg: dict, module: str) -> bool:
    """Return True if any object in this module has a varargs method."""
    for obj in C.module_objects(cfg, module):
        for m in C.methods(cfg, obj):
            if m.get("varargs"):
                return True
    return False


def _uses_literal(cfg: dict, module: str) -> bool:
    """Return True if any object in this module has a string_enum init param."""
    for obj in C.module_objects(cfg, module):
        for param in C.init_params(cfg, obj):
            if param[1].startswith("string_enum:"):
                return True
    return False


def _uses_numpy(cfg: dict, module: str) -> bool:
    """Return True if any object in this module uses numpy (steps or arrays)."""
    for obj in C.module_objects(cfg, module):
        at = C.arg_type(cfg, obj)
        rt = C.return_type(cfg, obj)
        # Any non-void arg/return → steps() uses NDArray
        if at not in ("void",) or rt not in ("void",):
            return True
        for m in C.methods(cfg, obj):
            if m.get("variable_output"):
                return True
            for p in m.get("params", []):
                if p["type"].endswith("[]"):
                    return True
    for fn in C.module_functions(cfg, module):
        if fn.get("out_type"):
            return True
        for p in fn.get("params", []):
            if p["type"].endswith("[]"):
                return True
    return False


# ── public entry point ────────────────────────────────────────────────────────


def make_module_pyi(cfg: dict, module: str) -> str:
    """Return the full __init__.pyi content for *module*.

    Example output (module='dsp', objects=['filt'], functions=['apply'])::

        # dsp/__init__.pyi — type stubs for the dsp C extension.
        import numpy as np
        from numpy.typing import NDArray

        class Filt:
            def __init__(self, coeff: float = ...) -> None: ...
            def step(self, x: float) -> float: ...
            def steps(self, x: NDArray[np.float32]) -> NDArray[np.float32]: ...
            def reset(self) -> None: ...
            @property
            def gain(self) -> float: ...
            @gain.setter
            def gain(self, value: float) -> None: ...

        def apply(x: float) -> float: ...
    """
    pkg = C.project_name(cfg)
    objects = C.module_objects(cfg, module)

    needs_numpy = _uses_numpy(cfg, module)
    needs_literal = _uses_literal(cfg, module)
    needs_any = _uses_any(cfg, module)
    # gh-203: a streamable object's stub references Callable + Iterator.
    needs_stream = any(_obj_stream_pyi(cfg, o) for o in objects)
    parts: list[str] = [
        f"# {module}/{module}.pyi — type stubs for the {module} C extension."
    ]
    if needs_literal or needs_any or needs_stream:
        typing_imports = ", ".join(
            x
            for x in [
                "Any" if needs_any else "",
                "Callable" if needs_stream else "",
                "Iterator" if needs_stream else "",
                "Literal" if needs_literal else "",
            ]
            if x
        )
        parts.append(f"from typing import {typing_imports}")
    if needs_numpy:
        parts.append("import numpy as np")
        parts.append("from numpy.typing import NDArray")
    functions = C.module_functions(cfg, module)

    if objects:
        parts.append("")
    for obj in objects:
        parts.append(_obj_stub(cfg, obj, pkg=pkg, module=module))
        parts.append("")

    for fn in functions:
        parts.append(_fn_stub(fn))
        parts.append("")

    # strip trailing blank line
    while parts and parts[-1] == "":
        parts.pop()

    return "\n".join(parts) + "\n"
