"""
_object.py — `just-makeit object` command.

Adds a Python type to an existing project:

  Standalone (own .so):
    just-makeit object gain                  # no --module -> standalone
    from my_pkg import Gain

  In-module (shared .so subpackage):
    just-makeit object fir --module filter   # grouped under filter subpackage
    from my_pkg.filter import Fir
"""

import sys
from pathlib import Path

from . import _config as C
from . import _stubs as S
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
    array_args: list[tuple[str, str]] = (),
    no_state: bool = False,
    no_step: bool = False,
    init_params: list[tuple[str, str, str]] = (),
) -> dict:
    """Build the render ctx for an object."""
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
    ctx.update(T.make_state_ctx(ctx["component"], ctx["Component"], state_vars,
                                array_args=array_args, no_state=no_state,
                                init_params=init_params))
    ctx.update(T.make_perf_ctx(perf))
    ctx.update(T.make_step_ctx(ctx, arg_type, return_type or arg_type, no_step=no_step))
    return ctx


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
        ctx = _make_object_ctx(
            obj, module, pkg,
            C.project_version(cfg),
            state_vars, arg_type_, return_type_,
            perf=perf,
            array_args=C.array_args(cfg, obj),
            no_state=C.is_no_state(cfg, obj),
            no_step=C.is_no_step(cfg, obj),
            init_params=C.init_params(cfg, obj),
        )
        ctx.update(T.make_methods_ctx(ctx["component"], ctx["Component"],
                                      C.methods(cfg, obj)))
        ctx.update(T.make_properties_ctx(ctx["component"], ctx["Component"],
                                         C.properties(cfg, obj),
                                         frozenset(n for n, _, _ in state_vars)))
        comp_ctxs.append(ctx)

    # Module ext.c
    functions = C.module_functions(cfg, module)
    ext_c = T.render_module_ext_c(module, comp_ctxs, functions)
    _write(root / "native" / "src" / module / f"{module}_ext.c", ext_c, "update")

    # Module CMakeLists
    object_list = ", ".join(ctx["Component"] for ctx in comp_ctxs)
    # Collocated case: when an object shares the module name (e.g. module="fft",
    # object="fft"), CMAKE_LISTS_OBJECT_CORE is prepended and already defines
    # <mod>_core.  Non-collocated: we define <mod>_core separately so that
    # module-level functions in <mod>_core.c are compiled and linked in.
    has_collocated = module in object_names
    if has_collocated:
        # <mod>_core is the collocated object's OBJECT lib; it's already in
        # object_names so it will appear in object_core_libs below.
        module_core_lib_block = ""
        libs_parts = [f"{obj}_core" for obj in object_names]
    else:
        module_core_lib_block = (
            f"add_library({module}_core OBJECT {module}_core.c)\n"
            f"target_include_directories({module}_core PRIVATE"
            f" ${{CMAKE_SOURCE_DIR}}/native/inc)\n\n"
        )
        libs_parts = [f"{module}_core"] + [f"{obj}_core" for obj in object_names]
    object_core_libs = "\n    ".join(libs_parts)
    cmake_ctx = {
        "module": module,
        "Module": Module,
        "object_list": object_list,
        "object_core_libs": object_core_libs,
        "module_core_lib_block": module_core_lib_block,
    }
    # Collocated objects share the same CMakeLists file as the module itself;
    # their OBJECT library cmake is prepended before CMAKE_LISTS_MODULE.
    # Migration: if a legacy _methods.c exists on disk, preserve it in the
    # CMakeLists so old projects don't break on regen.  New projects never
    # have _methods.c — stubs go in _core.c.
    collocated_cmake = ""
    for obj, ctx_ in zip(object_names, comp_ctxs):
        if obj == module:
            obj_cmake = T.render(T.CMAKE_LISTS_OBJECT_CORE, ctx_)
            methods_c = root / "native" / "src" / obj / f"{obj}_methods.c"
            if methods_c.exists():
                old_lib = (
                    f"add_library({obj}_core OBJECT {obj}_core.c)"
                )
                new_lib = (
                    f"add_library({obj}_core OBJECT"
                    f" {obj}_core.c {obj}_methods.c)"
                )
                obj_cmake = obj_cmake.replace(old_lib, new_lib)
            collocated_cmake += obj_cmake
    _write(
        root / "native" / "src" / module / "CMakeLists.txt",
        collocated_cmake + T.render(T.CMAKE_LISTS_MODULE, cmake_ctx),
        "update",
    )

    # Subpackage __init__.py
    Components = [ctx["Component"] for ctx in comp_ctxs]
    fn_names = [f["name"] for f in functions]
    all_exports = Components + fn_names
    object_imports = ", ".join(all_exports)
    object_all = ", ".join(f'"{name}"' for name in all_exports)
    init_ctx = {
        "module": module,
        "Module": Module,
        "object_imports": object_imports,
        "object_all": object_all,
    }
    pkg_module_dir = root / "src" / pkg / module
    _write(pkg_module_dir / "__init__.py", T.render(T.MODULE_INIT_PY, init_ctx), "update")

    # Type stubs — regenerated in full every time the module changes.
    _write(pkg_module_dir / f"{module}.pyi", S.make_module_pyi(cfg, module), "update")


def run(
    root: Path,
    object_name: str,
    module: str | None,
    state_vars: list[tuple[str, str, str]] | None = None,
    perf: bool | None = None,
    arg_type: str = "float _Complex",
    return_type: str | None = None,
    array_args: list[tuple[str, str]] = (),
    no_state: bool = False,
    no_step: bool = False,
    impl_body: str | None = None,
    init_params: list[tuple[str, str, str]] = (),
    _hint: bool = True,
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

    # No --module -> standalone object (own .so)
    if module is None:
        from . import _init
        _init.run(root, object_name, state_vars, perf=perf,
                  arg_type=arg_type, return_type=return_type,
                  array_args=array_args, no_state=no_state, no_step=no_step,
                  impl_body=impl_body, init_params=init_params, _hint=_hint)
        return

    # --module given -> in-module path
    mods = C.modules(cfg)
    if module not in mods:
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

    vars_ = [] if no_state else (state_vars or [("gain", "double", "0.0")])
    ctx = _make_object_ctx(
        object_name, module, pkg, version, vars_, arg_type, return_type,
        perf=perf, array_args=array_args,
        no_state=no_state, no_step=no_step,
        init_params=init_params,
    )

    def r(tmpl):
        return T.render(tmpl, ctx)

    comp = ctx["component"]
    print(f"just-makeit: adding object '{comp}' to module '{module}' in project '{pkg}'")
    print()

    # C library files (OBJECT lib only — no standalone Python module)
    _write(root / "native" / "inc" / comp / f"{comp}_core.h", r(T.COMPONENT_CORE_H))
    if impl_body is not None and not no_step:
        from . import _impl as I
        h_path = root / "native" / "inc" / comp / f"{comp}_core.h"
        h_text = h_path.read_text(encoding="utf-8")
        h_text = I.patch_function_body(h_text, f"{comp}_step", impl_body)
        h_path.write_text(h_text, encoding="utf-8")
    _write(root / "native" / "src" / comp / f"{comp}_core.c", r(T.COMPONENT_CORE_C))
    _write(
        root / "native" / "src" / comp / "CMakeLists.txt",
        r(T.CMAKE_LISTS_OBJECT_CORE),
    )
    _write(root / "native" / "tests" / f"test_{comp}_core.c", r(T.COMPONENT_TEST_C))
    _write(
        root / "native" / "benchmarks" / f"bench_{comp}_core.c",
        r(T.NO_STEP_BENCH_C if no_step else T.COMPONENT_BENCH_C),
    )

    # Update config before regenerating module (so module_objects is up-to-date)
    C.add_to_module(cfg, module, comp)
    C.add_component(cfg, comp, vars_, arg_type_=arg_type,
                    return_type_=return_type, array_args_=array_args,
                    no_state_=no_state, no_step_=no_step,
                    init_params_=init_params)

    # Regenerate module ext.c + CMakeLists + subpackage __init__
    _regenerate_module(root, cfg, module, pkg)

    # Root CMakeLists: insert add_subdirectory into Components sentinel section,
    # then wire OBJECT library into both shared and static C library targets.
    cmake_path = root / "CMakeLists.txt"
    if cmake_path.exists():
        cmake_text = cmake_path.read_text(encoding="utf-8")
        sub = f"add_subdirectory(native/src/{comp})\n"
        if sub not in cmake_text:
            sentinel = "# ── Components"
            obj_lines = (
                f"target_sources({pkg}_lib PRIVATE $<TARGET_OBJECTS:{comp}_core>)\n"
                f"target_sources({pkg}_lib_static PRIVATE $<TARGET_OBJECTS:{comp}_core>)\n"
            )
            if sentinel in cmake_text:
                # Insert after the sentinel comment line
                cmake_text = cmake_text.replace(
                    sentinel,
                    sentinel,
                    1,
                )
                idx = cmake_text.index(sentinel)
                idx = cmake_text.index("\n", idx) + 1
                cmake_text = cmake_text[:idx] + sub + obj_lines + cmake_text[idx:]
            else:
                cmake_text += sub + obj_lines
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
    _write_compile_commands(root, all_comps, C.modules(cfg))

    # Save config
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    print()
    print(f"Done!  Rebuild with: cmake --build build")
