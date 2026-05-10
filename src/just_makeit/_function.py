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

_FUNCTION_STUB = """\
/* <<IMPLEMENT: {fn_name}>> */
static PyObject *
{fn_name}(PyObject *self, PyObject *args)
{{
    (void)self; (void)args;
    Py_RETURN_NONE;
}}
"""


def _append_to_functions_c(path: Path, module: str, fn_name: str) -> None:
    stub = _FUNCTION_STUB.format(fn_name=fn_name)
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

    # Create or append to {module}_functions.c
    functions_c = root / "native" / "src" / module / f"{module}_functions.c"
    _append_to_functions_c(functions_c, module, fn_name)

    # Update config
    fn_entry: dict = {"name": fn_name}
    if doc:
        fn_entry["doc"] = doc
    C.add_module_function(cfg, module, fn_entry)
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    # Regenerate module ext.c (updates PyMethodDef + #include)
    _regenerate_module(root, cfg, module, pkg)

    print()
    print(f"Done!  Implement {fn_name}() in {functions_c.name}")
