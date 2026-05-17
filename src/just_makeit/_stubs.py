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


def _py(ctype: str) -> str:
    """Return the Python annotation string for a C type."""
    if ctype == "void":
        return "None"
    if ctype.endswith("[]"):
        elem = ctype[:-2]
        npt = _CTYPE_TO_NP.get(elem, "Any")
        return f"NDArray[{npt}]"
    return _CTYPE_TO_PY.get(ctype, "Any")


def _np(ctype: str) -> str:
    """Return the numpy dtype string for a scalar C type (for NDArray hints)."""
    return _CTYPE_TO_NP.get(ctype, "Any")


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
) -> list[str]:
    """Return lines for a numpy-style class docstring (indented 4 spaces)."""
    lines: list[str] = [f'    """{Component} component.', ""]

    # Parameters section
    param_lines: list[str] = []
    if state_vars and not no_state:
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
    elif init_params:
        for name, ctype, dflt in init_params:
            py_t = _CTYPE_TO_PY.get(ctype, "Any")
            py_d = _py_default_stub(ctype, dflt)
            param_lines += [
                f"    {name} : {py_t}, default {py_d}",
                f"        {name} constructor parameter.",
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

    if scalar_getters:
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


def _obj_stub(cfg: dict, obj: str, pkg: str = "", module: str = "") -> str:
    Component = _title(obj)
    state_vars = C.state_vars(cfg, obj)
    arg_type = C.arg_type(cfg, obj)
    return_type = C.return_type(cfg, obj)
    obj_methods = C.methods(cfg, obj)
    obj_props = C.properties(cfg, obj)
    state_names = {n for n, _, _ in state_vars}
    ip = C.init_params(cfg, obj)
    no_step = C.is_no_step(cfg, obj)
    no_state = C.is_no_state(cfg, obj)

    # Constructor arg string for doctest
    scalar_vars = [
        (n, ct, dflt) for n, ct, dflt in state_vars if not _ARRAY_RE.match(ct.strip())
    ]
    py_create_args = (
        ", ".join(_py_default_stub(ct, dflt) for _, ct, dflt in scalar_vars)
        if (scalar_vars and not no_state)
        else (", ".join(_py_default_stub(ct, dflt) for _, ct, dflt in ip) if ip else "")
    )

    import_line = (
        f"from {pkg}.{module} import {Component}"
        if pkg and module
        else f"from {pkg} import {Component}"
        if pkg
        else f"from ... import {Component}"
    )

    # Class docstring
    doc_lines = _build_class_docstring(
        Component,
        state_vars,
        no_state,
        list(ip),
        import_line,
        py_create_args,
    )
    lines: list[str] = [f"class {Component}:"] + doc_lines

    # __init__
    if state_vars and not no_state:
        init_params_str = ", ".join(f"{n}: {_py(t)} = ..." for n, t, _ in scalar_vars)
        lines.append(f"    def __init__(self, {init_params_str}) -> None: ...")
    elif ip:
        init_params_str = ", ".join(f"{n}: {_py(t)} = ..." for n, t, _ in ip)
        lines.append(f"    def __init__(self, {init_params_str}) -> None: ...")
    else:
        lines.append("    def __init__(self, /, *args, **kwargs) -> None: ...")

    lines += [
        "",
        "    def reset(self) -> None:",
        '        """Reset state to post-create defaults."""',
    ]

    # step() / steps()
    if no_step:
        pass
    elif arg_type.endswith("[]"):
        lines += [
            "",
            f"    def step(self, x: {_py(arg_type)}) -> {_py(return_type)}:",
            '        """Process one buffer of samples."""',
        ]
    elif arg_type != "void":
        lines += [
            "",
            f"    def step(self, x: {_py(arg_type)}) -> {_py(return_type)}:",
            '        """Process one input sample."""',
        ]
        if return_type != "void":
            lines += [
                "",
                f"    def steps(self, x: NDArray[{_np(arg_type)}],"
                f" out: NDArray[{_np(return_type)}] | None = None)"
                f" -> NDArray[{_np(return_type)}]:",
                '        """Process a samples array."""',
            ]
        else:
            lines += [
                "",
                f"    def steps(self, x: NDArray[{_np(arg_type)}]) -> None:",
                '        """Process a samples array."""',
            ]
    else:
        lines += [
            "",
            f"    def step(self) -> {_py(return_type)}:",
            '        """Generate one output sample."""',
        ]
        if return_type != "void":
            lines += [
                "",
                f"    def steps(self, n: int) -> NDArray[{_np(return_type)}]:",
                '        """Generate n output samples."""',
            ]
        else:
            lines += [
                "",
                "    def steps(self, n: int) -> None:",
                '        """Advance state by n ticks."""',
            ]

    # extra methods
    for m in obj_methods:
        m_name = m["name"]
        m_ret = m.get("return_type", "void")
        m_params = m.get("params", [])
        m_arg = m.get("arg_type", "void")
        m_var = m.get("variable_output", False)
        m_multi = m.get("multi_output", [])

        param_parts: list[str] = []
        if m_arg != "void":
            param_parts.append(f"x: {_py(m_arg)}")
        for p in m_params:
            param_parts.append(f"{p['name']}: {_py(p['type'])}")

        if m_var:
            all_rts = [m_ret] + list(m_multi)
            ndarrays = [f"NDArray[{_np(rt)}]" for rt in all_rts]
            ret_ann = (
                f"tuple[{', '.join(ndarrays)}]" if len(ndarrays) > 1 else ndarrays[0]
            )
        else:
            ret_ann = _py(m_ret)

        sig = ", ".join(param_parts)
        if sig:
            lines += [
                "",
                f"    def {m_name}(self, {sig}) -> {ret_ann}:",
                f'        """{m_name.replace("_", " ").capitalize()}."""',
            ]
        else:
            lines += [
                "",
                f"    def {m_name}(self) -> {ret_ann}:",
                f'        """{m_name.replace("_", " ").capitalize()}."""',
            ]

    # properties
    for prop in obj_props:
        p_name = prop["name"]
        p_ctype = prop.get("type") or prop.get("ctype", "size_t")
        p_write = prop.get("writable", False) or (p_name in state_names)
        py_t = _py(p_ctype)
        lines += [
            "",
            "    @property",
            f"    def {p_name}(self) -> {py_t}:",
            f'        """{p_name.replace("_", " ").capitalize()}."""',
        ]
        if p_write:
            lines += [
                f"    @{p_name}.setter",
                f"    def {p_name}(self, value: {py_t}) -> None: ...",
            ]

    lines += [
        "",
        "    def destroy(self) -> None:",
        '        """Release C resources immediately."""',
        '    def __enter__(self) -> "' + Component + '": ...',
        "    def __exit__(self, *args: object) -> None: ...",
    ]

    return "\n".join(lines)


# ── module-level function stub ────────────────────────────────────────────────


def _fn_stub(fn: dict) -> str:
    name = fn["name"]
    ret = _py(fn.get("return_type", "void"))
    params = fn.get("params", [])
    doc = fn.get("doc", "")
    parts = [f"{p['name']}: {_py(p['type'])}" for p in params]
    sig = f"def {name}({', '.join(parts)}) -> {ret}:"
    one_liner = doc.split("\n")[0] if doc else name.replace("_", " ").capitalize() + "."
    return f'{sig}\n    """{one_liner}"""'


# ── numpy import decision ─────────────────────────────────────────────────────


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
    needs_numpy = _uses_numpy(cfg, module)
    parts: list[str] = [
        f"# {module}/{module}.pyi — type stubs for the {module} C extension."
    ]
    if needs_numpy:
        parts.append("import numpy as np")
        parts.append("from numpy.typing import NDArray")

    pkg = C.project_name(cfg)
    objects = C.module_objects(cfg, module)
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
