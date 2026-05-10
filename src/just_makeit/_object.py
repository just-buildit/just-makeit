"""
_object.py — `just-makeit object` command.

Adds a new Python type ("object") to an existing module:
  1. Generates the C library for the object (_core.h, _core.c, test, bench,
     object-only CMakeLists with no Python module target)
  2. Regenerates the module's _ext.c from all its objects (including the new one)
  3. Regenerates the module's CMakeLists to link the new _core OBJECT lib
  4. Updates the module's Python subpackage __init__.py
  5. Updates just-makeit.toml

Usage:
  just-makeit object fir                     # inferred if only one module
  just-makeit object fir --module filter
"""

import sys
from pathlib import Path

from . import _config as C
from . import _templates as T
from ._init import (
    _make_component_ctx,
    _to_title,
    _write,
    _write_compile_commands,
)


def _make_object_ctx(
    component: str,
    module: str,
    pkg: str,
    version: str,
    state_vars: list[tuple[str, str, str]],
    arg_type: str = "float _Complex",
    return_type: str | None = None,
    perf: bool = False,
    pure: bool = False,
) -> tuple[dict, str | None]:
    """Build the render ctx for an object.  Returns (ctx, pure_style)."""
    ctx = _make_component_ctx(component)
    ctx.update(
        {
            "module": module,
            "Module": _to_title(module),
            "package": pkg,
            "PACKAGE": pkg.upper(),
            "project": pkg.replace("_", "-"),
            "project_underscore": pkg,
            "version": version,
        }
    )
    ctx.update(T.make_sample_ctx(arg_type, return_type))

    if pure:
        pure_ctx = T.make_pure_ctx(ctx["component"], ctx["Component"], state_vars, arg_type)
        ctx.update(pure_ctx)
        pure_style = pure_ctx["pure_style"]
    else:
        ctx.update(T.make_state_ctx(ctx["component"], ctx["Component"], state_vars))
        pure_style = None

    ctx.update(T.make_perf_ctx(perf))
    return ctx, pure_style


def _regenerate_module(root: Path, cfg: dict, module: str, pkg: str) -> None:
    """Regenerate module_ext.c, module CMakeLists, and subpackage __init__."""
    object_names = C.module_objects(cfg, module)
    Module = _to_title(module)

    comp_ctxs: list[dict] = []
    for obj in object_names:
        state_vars = C.state_vars(cfg, obj)
        arg_type_ = C.arg_type(cfg, obj)
        return_type_ = C.return_type(cfg, obj)
        perf = C.is_perf(cfg)
        pure_style = C.pure_style(cfg, obj)
        ctx, _ = _make_object_ctx(
            obj, module, pkg,
            C.project_version(cfg),
            state_vars, arg_type_, return_type_,
            perf=perf,
            pure=(pure_style is not None),
        )
        comp_ctxs.append(ctx)

    # Module ext.c
    ext_c = T.render_module_ext_c(module, comp_ctxs)
    _write(root / "native" / "src" / module / f"{module}_ext.c", ext_c, "update")

    # Module CMakeLists
    object_core_libs = "\n    ".join(f"{obj}_core" for obj in object_names)
    object_list = ", ".join(ctx["Component"] for ctx in comp_ctxs)
    cmake_ctx = {
        "module": module,
        "Module": Module,
        "object_list": object_list,
        "object_core_libs": object_core_libs,
    }
    _write(
        root / "native" / "src" / module / "CMakeLists.txt",
        T.render(T.CMAKE_LISTS_MODULE, cmake_ctx),
        "update",
    )

    # Subpackage __init__.py
    Components = [ctx["Component"] for ctx in comp_ctxs]
    object_imports = ", ".join(Components)
    object_all = ", ".join(f'"{C_}"' for C_ in Components)
    init_ctx = {
        "module": module,
        "Module": Module,
        "object_imports": object_imports,
        "object_all": object_all,
    }
    pkg_module_dir = root / "src" / pkg / module
    _write(pkg_module_dir / "__init__.py", T.render(T.MODULE_INIT_PY, init_ctx), "update")


def run(
    root: Path,
    object_name: str,
    module: str | None,
    state_vars: list[tuple[str, str, str]] | None = None,
    perf: bool | None = None,
    pure: bool = False,
    arg_type: str = "float _Complex",
    return_type: str | None = None,
) -> None:
    if not object_name.replace("_", "").isalnum() or object_name[0].isdigit():
        print(
            f"error: '{object_name}' is not a valid object name.\n"
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

    # Resolve module
    mods = C.modules(cfg)
    if module is None:
        if len(mods) == 1:
            module = mods[0]
        elif len(mods) == 0:
            print(
                "error: no modules defined. Run 'just-makeit module <name>' first.",
                file=sys.stderr,
            )
            sys.exit(1)
        else:
            print(
                f"error: multiple modules {mods}. Use --module to specify one.",
                file=sys.stderr,
            )
            sys.exit(1)
    elif module not in mods:
        print(
            f"error: module '{module}' not found. "
            f"Run 'just-makeit module {module}' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    if object_name in C.module_objects(cfg, module):
        print(
            f"error: object '{object_name}' already exists in module '{module}'.",
            file=sys.stderr,
        )
        sys.exit(1)
    if object_name in C.components(cfg):
        print(
            f"error: '{object_name}' already exists as a standalone component.",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = C.project_name(cfg)
    version = C.project_version(cfg)
    if perf is None:
        perf = C.is_perf(cfg)

    vars_ = state_vars or [("gain", "double", "0.0")]
    ctx, pure_style = _make_object_ctx(
        object_name, module, pkg, version, vars_, arg_type, return_type,
        perf=perf, pure=pure,
    )

    def r(tmpl):
        return T.render(tmpl, ctx)

    comp = ctx["component"]
    print(f"just-makeit: adding object '{comp}' to module '{module}' in project '{pkg}'")
    print()

    # C library files (OBJECT lib only — no standalone Python module)
    _write(root / "native" / "inc" / comp / f"{comp}_core.h", r(T.COMPONENT_CORE_H))
    _write(root / "native" / "src" / comp / f"{comp}_core.c", r(T.COMPONENT_CORE_C))
    _write(
        root / "native" / "src" / comp / "CMakeLists.txt",
        r(T.CMAKE_LISTS_OBJECT_CORE),
    )
    _write(root / "native" / "tests" / f"test_{comp}_core.c", r(T.COMPONENT_TEST_C))
    _write(
        root / "native" / "benchmarks" / f"bench_{comp}_core.c",
        r(T.COMPONENT_BENCH_C),
    )

    # Update config before regenerating module (so module_objects is up-to-date)
    C.add_to_module(cfg, module, comp)
    C.add_component(cfg, comp, vars_, pure=pure_style, arg_type_=arg_type,
                    return_type_=return_type)

    # Regenerate module ext.c + CMakeLists + subpackage __init__
    _regenerate_module(root, cfg, module, pkg)

    # Root CMakeLists: add_subdirectory + wire core into combined C library
    cmake_path = root / "CMakeLists.txt"
    if cmake_path.exists():
        cmake_text = cmake_path.read_text(encoding="utf-8")
        sub = f"add_subdirectory(native/src/{comp})\n"
        if sub not in cmake_text:
            cmake_text += sub
            if f"{pkg}_lib" in cmake_text:
                cmake_text += (
                    f"target_sources({pkg}_lib PRIVATE"
                    f" $<TARGET_OBJECTS:{comp}_core>)\n"
                )
            cmake_path.write_text(cmake_text, encoding="utf-8")
            print(f"  update  {cmake_path}")

    # Umbrella header
    umbrella = root / "native" / "inc" / f"{pkg}.h"
    include_line = f'#include "{comp}/{comp}_core.h"\n'
    if umbrella.exists():
        umbrella_text = umbrella.read_text(encoding="utf-8")
        if include_line not in umbrella_text:
            umbrella_text = umbrella_text.replace(
                "#ifdef __cplusplus\n}\n#endif\n\n#endif",
                f"{include_line}\n#ifdef __cplusplus\n}}\n#endif\n\n#endif",
            )
            umbrella.write_text(umbrella_text, encoding="utf-8")
            print(f"  update  {umbrella}")

    # compile_commands.json
    all_comps = C.components(cfg)
    _write_compile_commands(root, all_comps)

    # Save config
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    print()
    print(f"Done!  Rebuild with: cmake --build build")
