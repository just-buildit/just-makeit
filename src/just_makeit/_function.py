"""
_function.py — `just-makeit function` command.

Adds a module-level Python function (no type object, no handle) to an existing
module.  Canonical use cases: FFT module-level API, window utility functions,
global setup calls.

    just-makeit function fft_global_setup --module fft
    just-makeit function window_kaiser    --module fft

On first call for a module, creates native/src/{module}/{module}_functions.c
with a file header and the first stub.  Subsequent calls append stubs to the
existing file.  The functions file is never regenerated — user code is safe.

The module ext.c is regenerated (via _object._regenerate_module) each time to
update the PyMethodDef array and .m_methods.  The header section gains
#include "{module}_functions.c" on the first function.
"""

import sys
from pathlib import Path

from . import _config as C
from . import _templates as T
from ._init import _write
from ._object import _regenerate_module


_FUNCTIONS_C_HEADER = """\
/*
 * {module}_functions.c — module-level function stubs.
 *
 * This file is #included from {module}_ext.c after the Python/NumPy headers.
 * Add any extra #includes you need here (your C library headers, etc.).
 */

"""

_FUNCTION_STUB_UNTYPED = """\
/* <<IMPLEMENT: {fn_name}>> */
static PyObject *
{fn_name}(PyObject *self, PyObject *args)
{{
    (void)self; (void)args;
    Py_RETURN_NONE;
}}
"""


def _function_stub_typed(
    fn_name: str,
    params: list[tuple[str, str]],
    return_type: str,
) -> str:
    """Stub for a typed function: C helper + Python wrapper with parse block."""
    from . import _templates as T

    ret_disp = T._ctype_display(return_type)
    ret_meta = T._CTYPE_META.get(return_type)

    # C-level helper signature.
    # Array params ("type[]") expand to (const elem_t *name, size_t name_len).
    c_param_parts: list[str] = []
    suppress_parts: list[str] = []
    for n, t in params:
        if T.is_array_param_type(t):
            elem_ct = T.array_elem_ctype(t)
            elem_disp = T._ctype_display(elem_ct)
            c_param_parts.append(f"const {elem_disp} *{n}")
            c_param_parts.append(f"size_t {n}_len")
            suppress_parts.append(f"(void){n};")
            suppress_parts.append(f"(void){n}_len;")
        else:
            c_param_parts.append(f"{T._ctype_display(t)} {n}")
            suppress_parts.append(f"(void){n};")

    if c_param_parts:
        c_param_str = ", ".join(c_param_parts)
        suppress = "    " + " ".join(suppress_parts)
    else:
        c_param_str = "void"
        suppress = ""

    if ret_meta:
        zero = ret_meta["zero"]
        c_ret_line = f"    return ({ret_disp}){zero}; /* placeholder */"
    else:
        c_ret_line = ""

    c_helper = (
        f"/* <<IMPLEMENT: {fn_name}>> */\n"
        f"static {ret_disp}\n"
        f"_{fn_name}_impl({c_param_str})\n"
        f"{{\n"
        + (suppress + "\n" if suppress else "")
        + (c_ret_line + "\n" if c_ret_line else "")
        + "}\n"
    )

    # Python wrapper
    if params:
        parse_block, call_args, cleanup = T._build_params_parse(
            [{"name": n, "type": t} for n, t in params]
        )
        meth_flags = "METH_VARARGS"
        py_args = "PyObject *args"
    else:
        parse_block = "    (void)args;\n"
        call_args = ""
        cleanup = ""
        meth_flags = "METH_NOARGS"
        py_args = "PyObject *Py_UNUSED(args)"

    if ret_meta:
        ret_expr = ret_meta["to_py"](f"_{fn_name}_impl({call_args})")
        ret_line = f"{cleanup}    return {ret_expr};"
    else:
        call_line = (
            f"    _{fn_name}_impl({call_args});" if call_args
            else f"    _{fn_name}_impl();"
        )
        ret_line = call_line + f"\n{cleanup}    Py_RETURN_NONE;"

    wrapper = (
        f"static PyObject *\n"
        f"{fn_name}(PyObject *self, {py_args})\n"
        f"{{\n"
        f"    (void)self;\n"
        + parse_block
        + f"{ret_line}\n"
        + "}"
    )

    return c_helper + "\n" + wrapper + "\n"


def _append_to_functions_c(
    path: Path,
    module: str,
    fn_name: str,
    params: list[tuple[str, str]] | None = None,
    return_type: str = "void",
) -> None:
    params = params or []
    if params or return_type != "void":
        stub = _function_stub_typed(fn_name, params, return_type)
    else:
        stub = _FUNCTION_STUB_UNTYPED.format(fn_name=fn_name)
    if not path.exists():
        header = _FUNCTIONS_C_HEADER.format(module=module)
        path.write_text(header + stub, encoding="utf-8")
        print(f"  create  {path}")
    else:
        existing = path.read_text(encoding="utf-8")
        path.write_text(existing + "\n" + stub, encoding="utf-8")
        print(f"  update  {path}")



def run(
    root: Path,
    fn_name: str,
    module: str,
    doc: str = "",
    params: list[tuple[str, str]] | None = None,
    return_type: str = "void",
) -> None:
    if not fn_name.replace("_", "").isalnum() or fn_name[0].isdigit():
        print(
            f"error: '{fn_name}' is not a valid function name.\n"
            "Use lowercase letters, digits, and underscores only; "
            "must not start with a digit.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)

    if module not in C.modules(cfg):
        print(
            f"error: module '{module}' not found. "
            f"Run 'just-makeit module {module}' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    existing = [f["name"] for f in C.module_functions(cfg, module)]
    if fn_name in existing:
        print(
            f"error: function '{fn_name}' already exists in module '{module}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = C.project_name(cfg)
    print(
        f"just-makeit: adding function '{fn_name}' to module '{module}' "
        f"in project '{pkg}'"
    )
    print()

    params = params or []

    # Create or append to {module}_functions.c
    functions_c = root / "native" / "src" / module / f"{module}_functions.c"
    _append_to_functions_c(functions_c, module, fn_name, params, return_type)

    # Update config
    fn_entry: dict = {"name": fn_name}
    if doc:
        fn_entry["doc"] = doc
    if params:
        fn_entry["params"] = [{"name": n, "type": t} for n, t in params]
    if return_type != "void":
        fn_entry["return_type"] = return_type
    C.add_module_function(cfg, module, fn_entry)
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    # Regenerate module ext.c (updates PyMethodDef + #include)
    _regenerate_module(root, cfg, module, pkg)

    print()
    print(f"Done!  Implement {fn_name}() in {functions_c.name}")
