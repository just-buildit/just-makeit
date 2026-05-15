"""
_function.py — `just-makeit function` command.

Adds a module-level C function to an existing module.

    just-makeit function fft_global_setup --module fft
    just-makeit function window_kaiser    --module fft

The C implementation stub is appended to native/src/{module}/{module}_core.c
and the declaration is injected into native/inc/{module}/{module}_core.h.
The Python wrapper (_bind_{fn_name}) is generated into {module}_ext.c the
next time _regenerate_module runs (called here after updating the config).
"""

import sys
from pathlib import Path

from . import _config as C
from . import _templates as T
from ._init import _write_compile_commands
from ._object import _regenerate_module


def _append_to_core_c(
    path: Path,
    fn_name: str,
    params: list[tuple[str, str]],
    return_type: str,
) -> None:
    stub = T.fn_c_stub(fn_name, params, return_type)
    existing = path.read_text(encoding="utf-8")
    path.write_text(existing + "\n" + stub, encoding="utf-8")
    print(f"  update  {path}")


def _inject_into_core_h(
    path: Path,
    fn_name: str,
    params: list[tuple[str, str]],
    return_type: str,
    module: str,
) -> None:
    decl = T.fn_c_decl(fn_name, params, return_type)
    existing = path.read_text(encoding="utf-8")
    # Inject inside the extern "C" block so the declaration has C linkage in
    # C++ translation units.  Preferred anchor: the closing #ifdef __cplusplus
    # guard.  Fall back to the #endif guard if the header omits the C++ block.
    cplusplus_end = "#ifdef __cplusplus\n}\n#endif"
    if cplusplus_end in existing:
        existing = existing.replace(cplusplus_end, f"{decl}\n\n{cplusplus_end}")
    else:
        marker = f"#endif /* {module.upper()}_CORE_H */"
        existing = existing.replace(marker, f"{decl}\n{marker}")
    path.write_text(existing, encoding="utf-8")
    print(f"  update  {path}")


def run(
    root: Path,
    fn_name: str,
    module: str,
    doc: str = "",
    params: list[tuple[str, str]] | None = None,
    return_type: str = "void",
    impl_body: str | None = None,
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

    # Append C stub to <module>_core.c
    core_c = root / "native" / "src" / module / f"{module}_core.c"
    if impl_body is not None:
        from . import _impl as I

        stub = T.fn_c_stub(fn_name, params, return_type)
        stub = I.inject_body_into_stub(stub, impl_body)
        existing = core_c.read_text(encoding="utf-8")
        core_c.write_text(existing + "\n" + stub, encoding="utf-8")
        print(f"  update  {core_c}")
    else:
        _append_to_core_c(core_c, fn_name, params, return_type)

    # Inject declaration into <module>_core.h
    core_h = root / "native" / "inc" / module / f"{module}_core.h"
    _inject_into_core_h(core_h, fn_name, params, return_type, module)

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

    # Regenerate module ext.c (updates _bind_ wrappers + PyMethodDef)
    _regenerate_module(root, cfg, module, pkg)

    # compile_commands.json
    _write_compile_commands(root, C.components(cfg), C.modules(cfg))

    print()
    print(f"Done!  Implement {fn_name}() in {core_c.name}")
