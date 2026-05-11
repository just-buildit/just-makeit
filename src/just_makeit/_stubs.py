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
"""
from . import _config as C

# ── annotation maps ──────────────────────────────────────────────────────────

_CTYPE_TO_PY: dict[str, str] = {
    "float":          "float",
    "double":         "float",
    "float _Complex": "complex",
    "double _Complex":"complex",
    "int":            "int",
    "int8_t":         "int",
    "int16_t":        "int",
    "int32_t":        "int",
    "int64_t":        "int",
    "uint8_t":        "int",
    "uint16_t":       "int",
    "uint32_t":       "int",
    "uint64_t":       "int",
    "size_t":         "int",
}

_CTYPE_TO_NP: dict[str, str] = {
    "float":          "np.float32",
    "double":         "np.float64",
    "float _Complex": "np.complex64",
    "double _Complex":"np.complex128",
    "int":            "np.int32",
    "int8_t":         "np.int8",
    "int16_t":        "np.int16",
    "int32_t":        "np.int32",
    "int64_t":        "np.int64",
    "uint8_t":        "np.uint8",
    "uint16_t":       "np.uint16",
    "uint32_t":       "np.uint32",
    "uint64_t":       "np.uint64",
    "size_t":         "np.uintp",
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


def _obj_stub(cfg: dict, obj: str) -> str:
    Component = _title(obj)
    state_vars   = C.state_vars(cfg, obj)
    arg_type     = C.arg_type(cfg, obj)
    return_type  = C.return_type(cfg, obj)
    obj_methods  = C.methods(cfg, obj)
    obj_props    = C.properties(cfg, obj)
    state_names  = {n for n, _, _ in state_vars}

    lines: list[str] = [f"class {Component}:"]

    # __init__
    if state_vars:
        init_params = ", ".join(
            f"{n}: {_py(t)} = ..." for n, t, _ in state_vars
        )
        lines.append(f"    def __init__(self, {init_params}) -> None: ...")
    else:
        lines.append("    def __init__(self) -> None: ...")

    # step() / steps()
    if arg_type.endswith("[]"):
        # Array-buffer object: step takes a whole numpy array, no steps().
        lines.append(
            f"    def step(self, x: {_py(arg_type)}) -> {_py(return_type)}: ..."
        )
    elif arg_type != "void":
        lines.append(
            f"    def step(self, x: {_py(arg_type)}) -> {_py(return_type)}: ..."
        )
        if return_type != "void":
            lines.append(
                f"    def steps(self, x: NDArray[{_np(arg_type)}]) -> "
                f"NDArray[{_np(return_type)}]: ..."
            )
        else:
            lines.append(
                f"    def steps(self, x: NDArray[{_np(arg_type)}]) -> None: ..."
            )
    else:
        lines.append(f"    def step(self) -> {_py(return_type)}: ...")
        if return_type != "void":
            lines.append(
                f"    def steps(self, n: int) -> "
                f"NDArray[{_np(return_type)}]: ..."
            )
        else:
            lines.append("    def steps(self, n: int) -> None: ...")

    # extra methods
    for m in obj_methods:
        m_name   = m["name"]
        m_ret    = m.get("return_type", "void")
        m_params = m.get("params", [])
        m_arg    = m.get("arg_type", "void")

        param_parts: list[str] = []
        if m_arg != "void":
            param_parts.append(f"x: {_py(m_arg)}")
        for p in m_params:
            param_parts.append(f"{p['name']}: {_py(p['type'])}")

        sig = ", ".join(param_parts)
        if sig:
            lines.append(
                f"    def {m_name}(self, {sig}) -> {_py(m_ret)}: ..."
            )
        else:
            lines.append(f"    def {m_name}(self) -> {_py(m_ret)}: ...")

    # properties
    for prop in obj_props:
        p_name    = prop["name"]
        p_ctype   = prop["ctype"]
        p_write   = prop.get("writable", False) or (p_name in state_names)
        py_t      = _py(p_ctype)
        lines.append("    @property")
        lines.append(f"    def {p_name}(self) -> {py_t}: ...")
        if p_write:
            lines.append(f"    @{p_name}.setter")
            lines.append(
                f"    def {p_name}(self, value: {py_t}) -> None: ..."
            )

    return "\n".join(lines)


# ── module-level function stub ────────────────────────────────────────────────

def _fn_stub(fn: dict) -> str:
    name   = fn["name"]
    ret    = _py(fn.get("return_type", "void"))
    params = fn.get("params", [])
    parts  = [f"{p['name']}: {_py(p['type'])}" for p in params]
    return f"def {name}({', '.join(parts)}) -> {ret}: ..."


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
        f"# {module}/__init__.pyi — type stubs for the {module} C extension."
    ]
    if needs_numpy:
        parts.append("import numpy as np")
        parts.append("from numpy.typing import NDArray")

    objects   = C.module_objects(cfg, module)
    functions = C.module_functions(cfg, module)

    if objects:
        parts.append("")
    for obj in objects:
        parts.append(_obj_stub(cfg, obj))
        parts.append("")

    for fn in functions:
        parts.append(_fn_stub(fn))
        parts.append("")

    # strip trailing blank line
    while parts and parts[-1] == "":
        parts.pop()

    return "\n".join(parts) + "\n"
