"""
_method.py — `just-makeit method` command.

Adds a named execute method to an existing object:

    just-makeit method nco execute_cf32 --module dsp \\
        --arg-type void --return-type "float _Complex" --variable-output

For --variable-output methods:
  - Pre-allocates an output buffer in the Python Object struct (not in _state_t)
  - Returns a zero-copy NumPy view via PyArray_SimpleNewFromData — no per-call malloc
  - Appends <<component>>_<name>_max_out() + <<component>>_<name>() stubs to _core.c
  - Declarations go into _core.h via <<method_decls>> placeholder (regenerated)

For fixed-output methods:
  - Appends a simple stub for <<component>>_<name>() to _core.c
"""

import sys
from pathlib import Path

from . import _config as C
from . import _templates as T
from ._init import _make_component_ctx, _to_title
from ._object import _regenerate_module


def _methods_c_stub_variable(
    component: str,
    name: str,
    arg_type: str,
    return_type: str,
    multi_output: list[str],
    params: list[tuple[str, str]] | None = None,
) -> str:
    """Generate _core-level C stubs for a variable-output method."""
    ret_disp = T._ctype_display(return_type)
    has_arg = arg_type != "void"
    params = params or []

    if has_arg:
        arg_disp = T._ctype_display(arg_type)
        step_param = f", const {arg_disp} *in, size_t n_in"
        suppress_in = "    (void)in; (void)n_in;"
    elif params:
        p_parts: list[str] = []
        suppress_parts: list[str] = []
        for pn, pt in params:
            if T.is_array_param_type(pt):
                elem_disp = T._ctype_display(T.array_elem_ctype(pt))
                p_parts.append(f"const {elem_disp} *{pn}")
                p_parts.append(f"size_t {pn}_len")
                suppress_parts += [f"(void){pn};", f"(void){pn}_len;"]
            else:
                p_parts.append(f"{T._ctype_display(pt)} {pn}")
                suppress_parts.append(f"(void){pn};")
        step_param = ", " + ", ".join(p_parts)
        suppress_in = "    " + " ".join(suppress_parts)
    else:
        step_param = ", size_t n"
        suppress_in = "    (void)n;"

    all_extra = list(multi_output)
    extra_out_params = "".join(
        f", {T._ctype_display(rt)} *out{i + 1}" for i, rt in enumerate(all_extra)
    )

    lines = [
        f"/* <<IMPLEMENT: return maximum possible output samples for {name}"
        f" given current state >> */",
        "size_t",
        f"{component}_{name}_max_out({component}_state_t *state)",
        "{",
        "    (void)state;",
        "    return 0; /* placeholder */",
        "}",
        "",
        f"/* <<IMPLEMENT: process{' input and' if has_arg else ''} write results"
        f" into out[0..n_out-1]; return actual output count >> */",
        "size_t",
        f"{component}_{name}({component}_state_t *state"
        f"{step_param}, {ret_disp} *out{extra_out_params})",
        "{",
        "    (void)state;",
        suppress_in,
        "    (void)out;",
        "    return 0; /* placeholder */",
        "}",
    ]
    return "\n".join(lines) + "\n"


def _methods_c_stub_fixed(
    component: str,
    name: str,
    arg_type: str,
    return_type: str,
    multi_output: list[str] | None = None,
    params: list[tuple[str, str]] | None = None,
    out_type: str | None = None,
) -> str:
    """Generate a _core-level C stub for a fixed-output method."""
    ret_disp = T._ctype_display(return_type)
    has_arg = arg_type != "void"
    multi_output = multi_output or []
    params = params or []

    extra_params = "".join(
        f", {T._ctype_display(rt)} *out{i + 1}" for i, rt in enumerate(multi_output)
    )
    extra_suppress = "".join(f" (void)out{i + 1};" for i in range(len(multi_output)))
    out_param = f", {T._ctype_display(out_type)} *out" if out_type else ""
    out_suppress = " (void)out;" if out_type else ""

    if params:
        param_parts: list[str] = []
        suppress_parts: list[str] = []
        if has_arg:
            if T.is_array_param_type(arg_type):
                elem_disp = T._ctype_display(T.array_elem_ctype(arg_type))
                param_parts.append(f"const {elem_disp} *x")
                param_parts.append(f"size_t x_len")
                suppress_parts.append(f"(void)x;")
                suppress_parts.append(f"(void)x_len;")
            else:
                param_parts.append(f"{T._ctype_display(arg_type)} x")
                suppress_parts.append(f"(void)x;")
        for n, t in params:
            if T.is_array_param_type(t):
                elem_disp = T._ctype_display(T.array_elem_ctype(t))
                param_parts.append(f"const {elem_disp} *{n}")
                param_parts.append(f"size_t {n}_len")
                suppress_parts.append(f"(void){n};")
                suppress_parts.append(f"(void){n}_len;")
            else:
                param_parts.append(f"{T._ctype_display(t)} {n}")
                suppress_parts.append(f"(void){n};")
        param_str = ", ".join(param_parts)
        c_params = f"{component}_state_t *state, {param_str}{extra_params}{out_param}"
        suppress_names = " ".join(suppress_parts)
        suppress = f"    (void)state; {suppress_names}{extra_suppress}{out_suppress}"
    elif has_arg:
        if T.is_array_param_type(arg_type):
            elem_disp = T._ctype_display(T.array_elem_ctype(arg_type))
            c_params = (
                f"{component}_state_t *state, "
                f"const {elem_disp} *x, size_t x_len{extra_params}{out_param}"
            )
            suppress = f"    (void)state; (void)x; (void)x_len;{extra_suppress}{out_suppress}"
        else:
            arg_disp = T._ctype_display(arg_type)
            c_params = f"{component}_state_t *state, {arg_disp} x{extra_params}{out_param}"
            suppress = f"    (void)state; (void)x;{extra_suppress}{out_suppress}"
    else:
        c_params = f"{component}_state_t *state{extra_params}{out_param}"
        suppress = f"    (void)state;{extra_suppress}{out_suppress}"

    zero = T._CTYPE_META[return_type]["zero"] if return_type in T._CTYPE_META else None
    ret_line = f"    return ({ret_disp}){zero};" if zero is not None else ""
    lines = [
        f"/* <<IMPLEMENT: {name} >> */",
        f"{ret_disp}",
        f"{component}_{name}({c_params})",
        "{",
        suppress,
    ]
    if ret_line:
        lines.append(ret_line)
    lines.append("}")
    return "\n".join(lines) + "\n"


def _append_to_core_c(path: Path, stub: str) -> None:
    """Append a method stub to native/src/{comp}/{comp}_core.c."""
    existing = path.read_text(encoding="utf-8")
    path.write_text(existing + "\n" + stub, encoding="utf-8")
    print(f"  update  {path}")


def _build_method_prototype(
    component: str,
    name: str,
    arg_type: str,
    return_type: str,
    variable_output: bool,
    multi_output: list[str],
    params: list[tuple[str, str]],
    out_type: str | None = None,
) -> str:
    """Return C prototype declaration(s) for a method (no trailing newline)."""
    ret_disp = T._ctype_display(return_type)
    has_arg = arg_type != "void"
    multi_output = multi_output or []
    params = params or []

    extra_params = "".join(
        f", {T._ctype_display(rt)} *out{i + 1}" for i, rt in enumerate(multi_output)
    )
    out_param = f", {T._ctype_display(out_type)} *out" if out_type else ""

    if variable_output:
        if has_arg:
            step_param = f", const {T._ctype_display(arg_type)} *in, size_t n_in"
        elif params:
            p_parts: list[str] = []
            for pn, pt in params:
                if T.is_array_param_type(pt):
                    elem_disp = T._ctype_display(T.array_elem_ctype(pt))
                    p_parts.append(f"const {elem_disp} *{pn}")
                    p_parts.append(f"size_t {pn}_len")
                else:
                    p_parts.append(f"{T._ctype_display(pt)} {pn}")
            step_param = ", " + ", ".join(p_parts)
        else:
            step_param = ", size_t n"
        return "\n".join(
            [
                f"size_t {component}_{name}_max_out({component}_state_t *state);",
                f"size_t {component}_{name}({component}_state_t *state"
                f"{step_param}, {ret_disp} *out{extra_params});",
            ]
        )

    if params:
        parts: list[str] = []
        if has_arg:
            if T.is_array_param_type(arg_type):
                elem_disp = T._ctype_display(T.array_elem_ctype(arg_type))
                parts.append(f"const {elem_disp} *x")
                parts.append(f"size_t x_len")
            else:
                parts.append(f"{T._ctype_display(arg_type)} x")
        for n, t in params:
            if T.is_array_param_type(t):
                elem_disp = T._ctype_display(T.array_elem_ctype(t))
                parts.append(f"const {elem_disp} *{n}")
                parts.append(f"size_t {n}_len")
            else:
                parts.append(f"{T._ctype_display(t)} {n}")
        c_params = (
            f"{component}_state_t *state, {', '.join(parts)}{extra_params}{out_param}"
        )
    elif has_arg:
        if T.is_array_param_type(arg_type):
            elem_disp = T._ctype_display(T.array_elem_ctype(arg_type))
            c_params = (
                f"{component}_state_t *state, "
                f"const {elem_disp} *x, size_t x_len{extra_params}{out_param}"
            )
        else:
            c_params = (
                f"{component}_state_t *state, "
                f"{T._ctype_display(arg_type)} x{extra_params}{out_param}"
            )
    else:
        c_params = f"{component}_state_t *state{extra_params}{out_param}"

    return f"{ret_disp} {component}_{name}({c_params});"


def run(
    root: Path,
    object_name: str,
    method_name: str,
    module: str | None,
    arg_type: str,
    return_type: str,
    variable_output: bool,
    multi_output: list[str],
    params: list[tuple[str, str]] | None = None,
    out_type: str | None = None,
    out_divisor: int = 1,
    impl_body: str | None = None,
    batch: bool = False,
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

    params = params or []

    # 1. Append C stub to _core.c
    core_c = root / "native" / "src" / object_name / f"{object_name}_core.c"
    if variable_output:
        stub = _methods_c_stub_variable(
            object_name, method_name, arg_type, return_type, multi_output,
            params=[(p[0], p[1]) for p in params],
        )
    else:
        stub = _methods_c_stub_fixed(
            object_name,
            method_name,
            arg_type,
            return_type,
            multi_output,
            params,
            out_type,
        )
    if impl_body is not None:
        import re as _re

        from . import _impl as I

        body = impl_body
        if variable_output and not _re.search(r"\breturn\b", body):
            body = body.rstrip("\n") + "\nreturn n;"
        stub = I.inject_body_into_stub(stub, body)
    _append_to_core_c(core_c, stub)

    # 2. Update config  (was step 3)
    method_entry: dict = {
        "name": method_name,
        "arg_type": arg_type,
        "return_type": return_type,
    }
    if params:
        method_entry["params"] = [{"name": n, "type": t} for n, t in params]
    if variable_output:
        method_entry["variable_output"] = True
    if batch:
        method_entry["batch"] = True
    if multi_output:
        method_entry["multi_output"] = multi_output
    if out_type:
        method_entry["out_type"] = out_type
    if out_divisor != 1:
        method_entry["out_divisor"] = out_divisor

    C.add_method(cfg, object_name, method_entry)
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    # 3. Regenerate ext.c (with updated method wrappers)
    if module:
        _regenerate_module(root, cfg, module, pkg)
        # Also regenerate the per-object _core.h so that the new method
        # declaration is present for the module ext.c's #include.
        state_vars_list = C.state_vars(cfg, object_name)
        arg_type_ = C.arg_type(cfg, object_name)
        return_type_ = C.return_type(cfg, object_name)
        perf_ = C.is_perf(cfg)
        version_ = C.project_version(cfg)
        ctx_ = _make_component_ctx(object_name)
        ctx_.update(
            {
                "module": module,
                "Module": _to_title(module),
                "package": pkg,
                "PACKAGE": pkg.upper(),
                "project": pkg.replace("_", "-"),
                "project_underscore": pkg,
                "version": version_,
            }
        )
        ctx_.update(T.make_sample_ctx(arg_type_, return_type_))
        ctx_.update(
            T.make_state_ctx(
                object_name,
                _to_title(object_name),
                state_vars_list,
                array_args=C.array_args(cfg, object_name),
                no_state=C.is_no_state(cfg, object_name),
            )
        )
        ctx_.update(T.make_perf_ctx(perf_))
        ctx_.update(
            T.make_step_ctx(
                ctx_,
                arg_type_,
                return_type_,
                no_step=C.is_no_step(cfg, object_name),
                mutable=C.is_mutable(cfg, object_name),
            )
        )
        ctx_.update(
            T.make_methods_ctx(
                object_name,
                _to_title(object_name),
                C.methods(cfg, object_name),
                pkg=pkg,
                py_create_args=ctx_.get("py_create_args", ""),
            )
        )
        ctx_.update(
            T.make_properties_ctx(
                object_name,
                _to_title(object_name),
                C.properties(cfg, object_name),
                frozenset(n for n, _, _ in state_vars_list),
            )
        )
        core_h_ = (
            root / "native" / "inc" / object_name / f"{object_name}_core.h"
        )
        if core_h_.exists():
            core_h_.write_text(
                T.render(T.COMPONENT_CORE_H, ctx_), encoding="utf-8"
            )
            print(f"  update  {core_h_}")
    else:
        # Standalone: regenerate _core.h (adds method_decls) + _ext.c

        state_vars_list = C.state_vars(cfg, object_name)
        arg_type_ = C.arg_type(cfg, object_name)
        return_type_ = C.return_type(cfg, object_name)
        perf = C.is_perf(cfg)
        version = C.project_version(cfg)

        ctx = _make_component_ctx(object_name)
        ctx.update(
            {
                "package": pkg,
                "PACKAGE": pkg.upper(),
                "project": pkg.replace("_", "-"),
                "project_underscore": pkg,
                "version": version,
            }
        )
        ctx.update(T.make_sample_ctx(arg_type_, return_type_))
        ctx.update(
            T.make_state_ctx(
                object_name,
                Component,
                state_vars_list,
                array_args=C.array_args(cfg, object_name),
                no_state=C.is_no_state(cfg, object_name),
            )
        )
        ctx.update(T.make_perf_ctx(perf))
        ctx.update(
            T.make_step_ctx(
                ctx, arg_type_, return_type_, no_step=C.is_no_step(cfg, object_name)
            )
        )
        ctx.update(
            T.make_methods_ctx(
                object_name,
                Component,
                C.methods(cfg, object_name),
                pkg=pkg,
                py_create_args=ctx.get("py_create_args", ""),
            )
        )
        ctx.update(
            T.make_properties_ctx(
                object_name,
                Component,
                C.properties(cfg, object_name),
                frozenset(n for n, _, _ in state_vars_list),
            )
        )

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
        pyi_path = root / "src" / pkg / f"{object_name}.pyi"
        if pyi_path.exists():
            pyi_path.write_text(r(T.COMPONENT_PYI), encoding="utf-8")
            print(f"  update  {pyi_path}")

    print()
    print(f"Done!  Implement {object_name}_{method_name}() in {core_c.name}")
