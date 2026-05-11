"""
_method.py — `just-makeit method` command.

Adds a named execute method to an existing object:

    just-makeit method nco execute_cf32 --module dsp \\
        --arg-type void --return-type "float _Complex" --variable-output

For --variable-output methods:
  - Pre-allocates an output buffer in the Python Object struct (not in _state_t)
  - Returns a zero-copy NumPy view via PyArray_SimpleNewFromData — no per-call malloc
  - Adds <<component>>_<name>_max_out() + <<component>>_<name>() stubs to _methods.c
  - Declarations go into _core.h via <<method_decls>> placeholder (regenerated)

For fixed-output methods:
  - Generates a simple wrapper calling <<component>>_<name>() in _methods.c
"""

import sys
from pathlib import Path

from . import _config as C
from . import _templates as T
from ._init import _make_component_ctx, _to_title, _write
from ._object import _make_object_ctx, _regenerate_module


_METHODS_C_HEADER = """\
#include "{component}/{component}_core.h"
#include <complex.h>
#include <stdlib.h>

"""


def _methods_c_stub_variable(
    component: str,
    name: str,
    arg_type: str,
    return_type: str,
    multi_output: list[str],
) -> str:
    """Generate _core-level C stubs for a variable-output method."""
    ret_disp = T._ctype_display(return_type)
    has_arg = arg_type != "void"

    if has_arg:
        arg_disp = T._ctype_display(arg_type)
        step_param = f", const {arg_disp} *in, size_t n_in"
    else:
        step_param = ", size_t n"

    all_extra = list(multi_output)
    extra_out_params = "".join(
        f", {T._ctype_display(rt)} *out{i+1}" for i, rt in enumerate(all_extra)
    )

    lines = [
        f"/* <<IMPLEMENT: return maximum possible output samples for {name}"
        f" given current state >> */",
        f"size_t",
        f"{component}_{name}_max_out({component}_state_t *state)",
        "{",
        "    (void)state;",
        "    return 0; /* placeholder */",
        "}",
        "",
        f"/* <<IMPLEMENT: process{' input and' if has_arg else ''} write results"
        f" into out[0..n_out-1]; return actual output count >> */",
        f"size_t",
        f"{component}_{name}({component}_state_t *state"
        f"{step_param}, {ret_disp} *out{extra_out_params})",
        "{",
        f"    (void)state;",
    ]
    if has_arg:
        lines.append("    (void)in; (void)n_in;")
    else:
        lines.append("    (void)n;")
    lines += ["    (void)out;", "    return 0; /* placeholder */", "}"]
    return "\n".join(lines) + "\n"


def _methods_c_stub_fixed(
    component: str,
    name: str,
    arg_type: str,
    return_type: str,
    multi_output: list[str] | None = None,
) -> str:
    """Generate a _core-level C stub for a fixed-output method."""
    ret_disp = T._ctype_display(return_type)
    has_arg = arg_type != "void"
    multi_output = multi_output or []

    extra_params = "".join(
        f", {T._ctype_display(rt)} *out{i + 1}"
        for i, rt in enumerate(multi_output)
    )
    extra_suppress = "".join(
        f" (void)out{i + 1};" for i in range(len(multi_output))
    )

    if has_arg:
        arg_disp = T._ctype_display(arg_type)
        params = f"{component}_state_t *state, {arg_disp} x{extra_params}"
        suppress = f"    (void)state; (void)x;{extra_suppress}"
    else:
        params = f"{component}_state_t *state{extra_params}"
        suppress = f"    (void)state;{extra_suppress}"

    zero = T._CTYPE_META[return_type]["zero"] if return_type in T._CTYPE_META else "0"
    lines = [
        f"/* <<IMPLEMENT: {name} >> */",
        f"{ret_disp}",
        f"{component}_{name}({params})",
        "{",
        suppress,
        f"    return ({ret_disp}){zero};",
        "}",
    ]
    return "\n".join(lines) + "\n"


def _append_to_methods_c(path: Path, component: str, stub: str) -> None:
    """Create or append to native/src/{comp}/{comp}_methods.c."""
    if not path.exists():
        header = _METHODS_C_HEADER.replace("{component}", component)
        path.write_text(header + stub, encoding="utf-8")
        print(f"  create  {path}")
    else:
        existing = path.read_text(encoding="utf-8")
        path.write_text(existing + "\n" + stub, encoding="utf-8")
        print(f"  update  {path}")


def _update_cmake_for_methods(cmake_path: Path, component: str) -> None:
    """Add {component}_methods.c to the OBJECT library in CMakeLists.txt."""
    text = cmake_path.read_text(encoding="utf-8")
    old = f"add_library({component}_core OBJECT {component}_core.c)"
    new = f"add_library({component}_core OBJECT {component}_core.c {component}_methods.c)"
    if old in text and new not in text:
        cmake_path.write_text(text.replace(old, new), encoding="utf-8")
        print(f"  update  {cmake_path}")


def run(
    root: Path,
    object_name: str,
    method_name: str,
    module: str | None,
    arg_type: str,
    return_type: str,
    variable_output: bool,
    multi_output: list[str],
) -> None:
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)

    # Resolve which component this belongs to
    all_comps = C.components(cfg)
    if module:
        mod_objs = C.module_objects(cfg, module)
        if object_name not in mod_objs:
            print(
                f"error: object '{object_name}' not found in module '{module}'.",
                file=sys.stderr,
            )
            sys.exit(1)
    elif object_name not in all_comps:
        print(
            f"error: object '{object_name}' not found. Available: {all_comps}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Check for duplicate method name
    existing = [m["name"] for m in C.methods(cfg, object_name)]
    if method_name in existing:
        print(
            f"error: method '{method_name}' already exists on '{object_name}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = C.project_name(cfg)
    Component = _to_title(object_name)

    print(
        f"just-makeit: adding method '{method_name}' to '{object_name}'"
        + (f" in module '{module}'" if module else "")
    )
    print()

    # 1. Generate C stubs in _methods.c
    methods_c = root / "native" / "src" / object_name / f"{object_name}_methods.c"
    if variable_output:
        stub = _methods_c_stub_variable(
            object_name, method_name, arg_type, return_type, multi_output
        )
    else:
        stub = _methods_c_stub_fixed(
            object_name, method_name, arg_type, return_type, multi_output
        )
    _append_to_methods_c(methods_c, object_name, stub)

    # 2. Wire _methods.c into CMakeLists
    cmake_path = root / "native" / "src" / object_name / "CMakeLists.txt"
    if cmake_path.exists():
        _update_cmake_for_methods(cmake_path, object_name)

    # 3. Update config
    method_entry: dict = {
        "name": method_name,
        "arg_type": arg_type,
        "return_type": return_type,
    }
    if variable_output:
        method_entry["variable_output"] = True
    if multi_output:
        method_entry["multi_output"] = multi_output

    C.add_method(cfg, object_name, method_entry)
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    # 4. Regenerate ext.c (with updated method wrappers)
    if module:
        _regenerate_module(root, cfg, module, pkg)
    else:
        # Standalone: regenerate _core.h (adds method_decls) + _ext.c
        from . import _init as _init_mod
        from . import _add as _add_mod

        state_vars_list = C.state_vars(cfg, object_name)
        arg_type_ = C.arg_type(cfg, object_name)
        return_type_ = C.return_type(cfg, object_name)
        pure_style = C.pure_style(cfg, object_name)
        perf = C.is_perf(cfg)
        version = C.project_version(cfg)

        ctx = _make_component_ctx(object_name)
        ctx.update({
            "package": pkg,
            "PACKAGE": pkg.upper(),
            "project": pkg.replace("_", "-"),
            "project_underscore": pkg,
            "version": version,
        })
        ctx.update(T.make_sample_ctx(arg_type_, return_type_))
        if pure_style:
            ctx.update(T.make_pure_ctx(object_name, Component, state_vars_list, arg_type_))
        else:
            ctx.update(T.make_state_ctx(object_name, Component, state_vars_list,
                                        array_args=C.array_args(cfg, object_name)))
        ctx.update(T.make_perf_ctx(perf))
        if not pure_style:
            ctx.update(T.make_step_ctx(ctx, arg_type_, return_type_))
        ctx.update(T.make_methods_ctx(object_name, Component, C.methods(cfg, object_name)))
        ctx.update(T.make_properties_ctx(object_name, Component, C.properties(cfg, object_name),
                                         frozenset(n for n, _, _ in state_vars_list)))

        def r(tmpl):
            return T.render(tmpl, ctx)

        # Re-render _core.h (to update method_decls) and _ext.c
        core_h = root / "native" / "inc" / object_name / f"{object_name}_core.h"
        ext_c = root / "native" / "src" / object_name / f"{object_name}_ext.c"
        if core_h.exists():
            core_h.write_text(r(T.COMPONENT_CORE_H), encoding="utf-8")
            print(f"  update  {core_h}")
        if ext_c.exists():
            ext_c.write_text(r(T.COMPONENT_EXT_C), encoding="utf-8")
            print(f"  update  {ext_c}")

    print()
    print(f"Done!  Implement {object_name}_{method_name}() in {methods_c.name}")
