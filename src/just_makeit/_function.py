"""
_function.py — `just-makeit function` command.

Adds a module-level C function to an existing module.

    just-makeit function fft_global_setup --module fft
    just-makeit function window_kaiser    --module fft

The C implementation stub is written to its own sacred source file at
native/src/{module}/{fn_name}.c and the declaration is injected into
native/inc/{module}/{module}_core.h.  The module CMakeLists compiles every
such per-function .c into the module's OBJECT library.  The Python wrapper
(_bind_{fn_name}) is generated into {module}_ext.c the next time
_regenerate_module runs (called here after updating the config).
"""

import sys
from pathlib import Path

from . import _config as C
from . import _render as T
from ._init import _write_compile_commands
from ._object import _regenerate_module


def _write_function_c(
    path: Path,
    module: str,
    fn_name: str,
    params: list[tuple[str, str]],
    return_type: str,
    out_type: str = "",
    result_fields: list[dict] | None = None,
    max_results_param: str = "",
    impl_body: str | None = None,
) -> None:
    """Write the standalone <fn_name>.c for a module-level function.

    Each module function lives in its own sacred translation unit that
    includes the module's public header and carries exactly one definition.
    """
    stub = T.fn_c_stub(
        fn_name,
        params,
        return_type,
        out_type=out_type,
        result_fields=result_fields,
        max_results_param=max_results_param,
    )
    if impl_body is not None:
        from . import _impl as I

        stub = I.inject_body_into_stub(stub, impl_body)
    text = (
        f"/*\n"
        f" * {fn_name}.c — {module} module-level function.\n"
        f" */\n"
        f'#include "{module}/{module}_core.h"\n\n'
        f"{stub}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"  create  {path}")


def _inject_into_core_h(
    path: Path,
    fn_name: str,
    params: list[tuple[str, str]],
    return_type: str,
    module: str,
    out_type: str = "",
    result_fields: list[dict] | None = None,
    max_results_param: str = "",
) -> None:
    decl = T.fn_c_decl(
        fn_name,
        params,
        return_type,
        out_type=out_type,
        result_fields=result_fields,
        max_results_param=max_results_param,
    )
    existing = path.read_text(encoding="utf-8")
    # Inject inside the extern "C" block so the declaration has C linkage in
    # C++ translation units.  Preferred anchor: the closing #ifdef __cplusplus
    # guard.  Fall back to the #endif guard if the header omits the C++ block.
    cplusplus_end = "#ifdef __cplusplus\n}\n#endif"
    if cplusplus_end in existing:
        existing = existing.replace(
            cplusplus_end, f"{decl}\n\n{cplusplus_end}"
        )
    else:
        marker = f"#endif /* {module.upper()}_CORE_H */"
        existing = existing.replace(marker, f"{decl}\n{marker}")
    path.write_text(existing, encoding="utf-8")
    print(f"  update  {path}")


def _inject_inline_into_core_h(
    path: Path,
    fn_name: str,
    params: list[tuple[str, str]],
    return_type: str,
    module: str,
) -> None:
    """Inject a ``static inline`` body stub into ``_core.h``.

    Used when ``inline=True``: the full definition goes into the header so
    every translation unit that includes it sees the body and the compiler can
    inline at call sites.  No entry is written to ``_core.c``.
    """
    stub = T.fn_c_inline_stub(fn_name, params, return_type)
    existing = path.read_text(encoding="utf-8")
    cplusplus_end = "#ifdef __cplusplus\n}\n#endif"
    if cplusplus_end in existing:
        existing = existing.replace(
            cplusplus_end, f"{stub}\n\n{cplusplus_end}"
        )
    else:
        marker = f"#endif /* {module.upper()}_CORE_H */"
        existing = existing.replace(marker, f"{stub}\n{marker}")
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
    out_type: str = "",
    result_fields: list[dict] | None = None,
    max_results_param: str = "",
    inline: bool = False,
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

    fn_c = root / "native" / "src" / module / f"{fn_name}.c"
    core_h = root / "native" / "inc" / module / f"{module}_core.h"

    if inline:
        # Inline functions live entirely in the header — no .c entry.
        _inject_inline_into_core_h(
            core_h, fn_name, params, return_type, module
        )
    else:
        # Each function gets its own sacred <fn_name>.c translation unit.
        _write_function_c(
            fn_c,
            module,
            fn_name,
            params,
            return_type,
            out_type=out_type,
            result_fields=result_fields,
            max_results_param=max_results_param,
            impl_body=impl_body,
        )

        # Inject declaration into <module>_core.h
        _inject_into_core_h(
            core_h,
            fn_name,
            params,
            return_type,
            module,
            out_type=out_type,
            result_fields=result_fields,
            max_results_param=max_results_param,
        )

    # Update config
    fn_entry: dict = {"name": fn_name}
    if doc:
        fn_entry["doc"] = doc
    if params:
        # Round-trip the optional `out` flag from 3-tuples so it survives
        # apply / script regeneration (gh-72).
        _entries: list[dict] = []
        for p in params:
            n, t = p[0], p[1]
            entry = {"name": n, "type": t}
            if len(p) > 2 and p[2]:
                entry["out"] = True
            _entries.append(entry)
        fn_entry["params"] = _entries
    if return_type != "void":
        fn_entry["return_type"] = return_type
    if out_type:
        fn_entry["out_type"] = out_type
    if result_fields:
        fn_entry["result_fields"] = result_fields
    if max_results_param:
        fn_entry["max_results_param"] = max_results_param
    if inline:
        fn_entry["inline"] = True
    C.add_module_function(cfg, module, fn_entry)
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    # Regenerate module ext.c (updates _bind_ wrappers + PyMethodDef)
    _regenerate_module(root, cfg, module, pkg)

    # compile_commands.json
    _write_compile_commands(root, C.components(cfg), C.modules(cfg))

    print()
    if inline:
        print(f"Done!  Implement {fn_name}() in {core_h.name}")
    else:
        print(f"Done!  Implement {fn_name}() in {fn_c.name}")
