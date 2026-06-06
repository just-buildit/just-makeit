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

from __future__ import annotations

import sys
from pathlib import Path

from . import _config as C
from . import _context as Ctx
from . import _render as R
from . import _types as T
from ._init import (
    _inject_decls_into_core_h,
    _make_component_ctx,
    _to_title,
)
from ._object import _regenerate_module


def _block_in_elem_disp(arg_type: str) -> str:
    """Display ctype of a block-input element for the ``const T *in`` lowering.

    A block method's input is always ``const <elem> *in, size_t n_in``. When
    ``arg_type`` is given as an array (``"float _Complex[]"``) the ``[]`` must
    be stripped to the element type, or the rendered prototype/cast becomes the
    invalid ``const float complex[] *in`` (gh-139). A scalar ``arg_type`` is
    already its own element type.
    """
    if T.is_array_param_type(arg_type):
        return T._ctype_display(T.array_elem_ctype(arg_type))
    return T._ctype_display(arg_type)


def _methods_c_stub_variable(
    component: str,
    name: str,
    arg_type: str,
    return_type: str,
    multi_output: list[str],
    params: list[tuple[str, str]] | None = None,
    out_type: str | None = None,
    max_out: int = 0,
    pass_capacity: bool = False,
) -> str:
    """Generate _core-level C stubs for a variable-output method.

    ``max_out`` (when > 0) makes the generated ``<comp>_<name>_max_out``
    return that integer literal instead of the ``return 0; /* placeholder */``
    stub.  Saves the user from hand-writing the obvious upper bound for
    detector / event-emitter shapes (gh-65 follow-up; Phase 2 row).

    ``pass_capacity`` (gh-138) appends a trailing ``size_t max_out`` output
    capacity parameter, for a C API that bounds-checks the caller's buffer.
    """
    buf_type = out_type if out_type else return_type
    ret_disp = T._ctype_display(buf_type)
    has_arg = arg_type != "void"
    params = params or []

    if has_arg:
        arg_disp = _block_in_elem_disp(arg_type)
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
        f", {T._ctype_display(rt)} *out{i + 1}"
        for i, rt in enumerate(all_extra)
    )
    cap_param = ", size_t max_out" if pass_capacity else ""
    cap_suppress = " (void)max_out;" if pass_capacity else ""

    if max_out > 0:
        _max_out_head = f"/* Worst-case output count for {name}() — set via --max-out {max_out}. */"
        _max_out_body = f"    return {max_out};"
    else:
        _max_out_head = (
            f"/* <<IMPLEMENT: return maximum possible output samples for {name}"
            f" given current state >> */"
        )
        _max_out_body = "    return 0; /* placeholder */"
    lines = [
        _max_out_head,
        "size_t",
        f"{component}_{name}_max_out({component}_state_t *state)",
        "{",
        "    (void)state;",
        _max_out_body,
        "}",
        "",
        f"/* <<IMPLEMENT: process{' input and' if has_arg else ''} write results"
        f" into out[0..n_out-1]; return actual output count >> */",
        "size_t",
        f"{component}_{name}({component}_state_t *state"
        f"{step_param}, {ret_disp} *out{extra_out_params}{cap_param})",
        "{",
        "    (void)state;",
        suppress_in,
        f"    (void)out;{cap_suppress}",
        "    return 0; /* placeholder */",
        "}",
    ]
    return "\n".join(lines) + "\n"


def _methods_c_stub_result_fields(
    component: str,
    name: str,
    arg_type: str,
    return_type: str,
    max_results: int = 64,
) -> str:
    """C stub for a method that returns a list of structs (result_fields)."""
    ret_disp = T._ctype_display(return_type)
    has_arg = arg_type != "void"
    if has_arg:
        arg_disp = _block_in_elem_disp(arg_type)
        step_param = f", const {arg_disp} *in, size_t n_in"
        suppress = "    (void)in; (void)n_in;"
    else:
        step_param = ""
        suppress = ""
    lines = [
        "/* <<IMPLEMENT: push input, fill result[], return count >> */",
        "size_t",
        f"{component}_{name}({component}_state_t *state"
        f"{step_param}, {ret_disp} *result, size_t max_results)",
        "{",
        "    (void)state;",
        suppress,
        "    (void)result; (void)max_results;",
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
    batch: bool = False,
) -> str:
    """Generate a _core-level C stub for a fixed-output method."""
    ret_disp = T._ctype_display(return_type)
    has_arg = arg_type != "void"
    multi_output = multi_output or []
    params = params or []

    # gh-179: a batch (1:1-rate) method writes n outputs into `out` and returns
    # void — its stub must match the (state, const in *in, size_t n, out *out)
    # prototype, not the scalar fall-through.
    if batch:
        if has_arg:
            in_part = f", const {_block_in_elem_disp(arg_type)} *in, size_t n"
            sup = "    (void)state; (void)in; (void)n; (void)out;"
        else:
            in_part = ", size_t n"
            sup = "    (void)state; (void)n; (void)out;"
        c_params = f"{component}_state_t *state{in_part}, {ret_disp} *out"
        return (
            f"/* <<IMPLEMENT: {name} (1:1-rate batch) >> */\n"
            f"void\n{component}_{name}({c_params})\n{{\n{sup}\n}}\n"
        )

    extra_params = "".join(
        f", {T._ctype_display(rt)} *out{i + 1}"
        for i, rt in enumerate(multi_output)
    )
    extra_suppress = "".join(
        f" (void)out{i + 1};" for i in range(len(multi_output))
    )
    out_param = f", {T._ctype_display(out_type)} *out" if out_type else ""
    out_suppress = " (void)out;" if out_type else ""

    if params:
        param_parts: list[str] = []
        suppress_parts: list[str] = []
        if has_arg:
            if T.is_array_param_type(arg_type):
                elem_disp = T._ctype_display(T.array_elem_ctype(arg_type))
                param_parts.append(f"const {elem_disp} *x")
                param_parts.append("size_t x_len")
                suppress_parts.append("(void)x;")
                suppress_parts.append("(void)x_len;")
            else:
                param_parts.append(f"{T._ctype_display(arg_type)} x")
                suppress_parts.append("(void)x;")
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
        c_params = (
            f"{component}_state_t *state, {param_str}{extra_params}{out_param}"
        )
        suppress_names = " ".join(suppress_parts)
        suppress = (
            f"    (void)state; {suppress_names}{extra_suppress}{out_suppress}"
        )
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
            suppress = (
                f"    (void)state; (void)x;{extra_suppress}{out_suppress}"
            )
    else:
        c_params = f"{component}_state_t *state{extra_params}{out_param}"
        suppress = f"    (void)state;{extra_suppress}{out_suppress}"

    zero = (
        T._CTYPE_META[return_type]["zero"]
        if return_type in T._CTYPE_META
        else None
    )
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


def _splice_varargs_source(
    cmake_path: Path,
    component: str,
    binding_file: str,
) -> None:
    """Add *binding_file* to the Python3_add_library line in CMakeLists.txt.

    Idempotent: does nothing if the file is already listed.  The splice
    targets the first occurrence of ``<component>_ext.c`` on the
    ``Python3_add_library`` line so it works whether the file was generated
    with the old template (no placeholder) or the new one.
    """
    import re

    text = cmake_path.read_text(encoding="utf-8")
    if binding_file in text:
        return  # already present
    # Match the Python3_add_library() call and append the new source before ')'
    pattern = re.compile(
        r"(Python3_add_library\("
        + re.escape(component)
        + r" MODULE WITH_SOABI"
        r"[^)]*?)(\))"
    )
    new_text = pattern.sub(
        lambda m: m.group(1) + f" {binding_file}" + m.group(2),
        text,
        count=1,
    )
    if new_text == text:
        return  # pattern not found, nothing to do
    cmake_path.write_text(new_text, encoding="utf-8")
    print(f"  update  {cmake_path}")


def _write_varargs_core_c(
    path: Path,
    component: str,
    method_name: str,
) -> None:
    """Write the sacred *args/**kwargs binding file for a varargs method.

    This file is compiled into the Python extension DSO (not the pure-C
    OBJECT library) so that it can use Python.h.  The user implements the
    body in the ``<<IMPLEMENT>>`` block.

    To access the component's C state inside the binding, cast ``self``:
      typedef struct { PyObject_HEAD; <comp>_state_t *handle; } CompObj;
      <comp>_state_t *state = ((CompObj *)self)->handle;
    """
    text = (
        f"/*\n"
        f" * {component}_{method_name}_core.c"
        f" — varargs Python binding for {component}.{method_name}().\n"
        f" *\n"
        f" * Compiled into the Python extension DSO, not the pure-C core.\n"
        f" * To access the C state inside this function:\n"
        f" *   typedef struct {{ PyObject_HEAD;"
        f" {component}_state_t *handle; }} Obj;\n"
        f" *   {component}_state_t *state = ((Obj *)self)->handle;\n"
        f" */\n"
        f"#define PY_SSIZE_T_CLEAN\n"
        f"#include <Python.h>\n"
        f'#include "{component}/{component}_core.h"\n'
        f"\n"
        f"/* <<IMPLEMENT: {method_name}(*args, **kwargs)\n"
        f" * Parse args/kwargs and return a PyObject *.\n"
        f" * Return NULL on error (exception must be set).\n"
        f" */\n"
        f"PyObject *\n"
        f"{component}_{method_name}"
        f"(PyObject *self, PyObject *args, PyObject *kwargs)\n"
        f"{{\n"
        f"    (void)self; (void)args; (void)kwargs;\n"
        f"    Py_RETURN_NONE;\n"
        f"}}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"  create  {path}")


def _build_method_prototype(
    component: str,
    name: str,
    arg_type: str,
    return_type: str,
    variable_output: bool,
    multi_output: list[str],
    params: list[tuple[str, str]],
    out_type: str | None = None,
    pass_capacity: bool = False,
    batch: bool = False,
) -> str:
    """Return C prototype declaration(s) for a method (no trailing newline)."""
    ret_disp = T._ctype_display(return_type)
    has_arg = arg_type != "void"
    multi_output = multi_output or []
    params = params or []

    # gh-179: a batch (1:1-rate) method is a block transform —
    # (state, const in *in, size_t n, out *out), or (state, size_t n, out *out)
    # for a void arg_type. The binding allocates `out` of length n and calls
    # this 4-arg (or 3-arg) form, so the prototype must match it, not the
    # scalar (state, T x) shape it would otherwise fall through to.
    if batch:
        in_part = (
            f", const {_block_in_elem_disp(arg_type)} *in, size_t n"
            if has_arg
            else ", size_t n"
        )
        return (
            f"void {component}_{name}({component}_state_t *state"
            f"{in_part}, {ret_disp} *out);"
        )

    extra_params = "".join(
        f", {T._ctype_display(rt)} *out{i + 1}"
        for i, rt in enumerate(multi_output)
    )
    out_param = f", {T._ctype_display(out_type)} *out" if out_type else ""
    cap_param = ", size_t max_out" if pass_capacity else ""

    if variable_output:
        if has_arg:
            step_param = (
                f", const {_block_in_elem_disp(arg_type)} *in, size_t n_in"
            )
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
        out_disp = T._ctype_display(out_type) if out_type else ret_disp
        return "\n".join(
            [
                f"size_t {component}_{name}_max_out({component}_state_t *state);",
                f"size_t {component}_{name}({component}_state_t *state"
                f"{step_param}, {out_disp} *out{extra_params}{cap_param});",
            ]
        )

    if params:
        parts: list[str] = []
        if has_arg:
            if T.is_array_param_type(arg_type):
                elem_disp = T._ctype_display(T.array_elem_ctype(arg_type))
                parts.append(f"const {elem_disp} *x")
                parts.append("size_t x_len")
            else:
                parts.append(f"{T._ctype_display(arg_type)} x")
        for n, t in params:
            if T.is_array_param_type(t):
                elem_disp = T._ctype_display(T.array_elem_ctype(t))
                parts.append(f"const {elem_disp} *{n}")
                parts.append(f"size_t {n}_len")
            else:
                parts.append(f"{T._ctype_display(t)} {n}")
        c_params = f"{component}_state_t *state, {', '.join(parts)}{extra_params}{out_param}"
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
    no_bench: bool = False,
    none_on_empty: bool = False,
    result_fields: list[dict] | None = None,
    max_results: int = 64,
    py_return_type: str = "",
    max_out: int = 0,
    varargs: bool = False,
    pass_capacity: bool = False,
    nogil: bool = False,
    doc: str = "",
    from_apply: bool = False,
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
    result_fields = result_fields or []

    # 1. Write C stub: either append to _core.c or write sacred binding file
    core_c = root / "native" / "src" / object_name / f"{object_name}_core.c"
    if varargs:
        # Varargs methods live in a sacred per-method file compiled into the
        # Python extension DSO (not the pure-C OBJECT lib) so they can use
        # Python.h.  No _core.c or _core.h changes needed.
        binding_c = (
            root
            / "native"
            / "src"
            / object_name
            / f"{object_name}_{method_name}_core.c"
        )
        _write_varargs_core_c(binding_c, object_name, method_name)
    else:
        if result_fields:
            stub = _methods_c_stub_result_fields(
                object_name,
                method_name,
                arg_type,
                return_type,
                max_results,
            )
        elif variable_output:
            stub = _methods_c_stub_variable(
                object_name,
                method_name,
                arg_type,
                return_type,
                multi_output,
                params=[(p[0], p[1]) for p in params],
                out_type=out_type,
                max_out=max_out,
                pass_capacity=pass_capacity,
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
                batch=batch,
            )
        if impl_body is not None:
            import re as _re

            from . import _impl as I

            body = impl_body
            if variable_output and not _re.search(r"\breturn\b", body):
                body = body.rstrip("\n") + "\nreturn n;"
            stub = I.inject_body_into_stub(stub, body)
        _append_to_core_c(core_c, stub)

    # The method's public prototype, injected surgically into _core.h below
    # (one or two lines; variable-output methods declare a sibling _max_out).
    # Varargs methods have no typed C prototype — their binding is Python-aware
    # and lives in the sacred binding .c file, not _core.h.
    proto_lines: list[str] = []
    if not varargs:
        proto_lines = _build_method_prototype(
            object_name,
            method_name,
            arg_type,
            return_type,
            variable_output,
            multi_output,
            [(p[0], p[1]) for p in params],
            out_type,
            pass_capacity=pass_capacity,
            batch=batch,
        ).split("\n")
    # For variable_output methods the generated 4-arg declaration would
    # clobber a user-written declaration with a different arity (e.g. a
    # 5-arg version that passes capacity).  Preserve the existing decl and
    # warn instead.
    # Preserving an existing declaration is an *interactive* safety net (the
    # user may have hand-edited the header). During `jm apply` replay the
    # manifest is authoritative and the object is rebuilt from scratch, so the
    # pre-existing decl is jm's own scaffolded default — never preserve it, or
    # a redefinition (e.g. a builtin steps() promoted to a variable_output
    # method) would be skipped instead of replaced (gh-137).
    _vo_skip: frozenset[str] = frozenset()
    if variable_output and not pass_capacity and not from_apply:
        _vo_fn = f"{object_name}_{method_name}"
        _core_h_check = (
            root / "native" / "inc" / object_name / f"{object_name}_core.h"
        )
        if _core_h_check.exists():
            _h_text = _core_h_check.read_text(encoding="utf-8")
            import re as _re

            _pat = _re.compile(
                r"\b" + _re.escape(_vo_fn) + r"\s*\(", _re.MULTILINE
            )
            if _pat.search(_h_text):
                import sys as _sys

                print(
                    f"WARNING: '{_vo_fn}' is already declared in "
                    f"{_core_h_check.relative_to(root)}.\n"
                    f"  The generated 4-arg declaration will be skipped "
                    f"to preserve your existing signature.\n"
                    f"  jm expects: {_vo_fn}(state, in, n_in, out) "
                    f"— remove the capacity param if present.",
                    file=_sys.stderr,
                )
                _vo_skip = frozenset({_vo_fn})

    # 2. Update config  (was step 3)
    method_entry: dict = {
        "name": method_name,
        "arg_type": arg_type,
        "return_type": return_type,
    }
    if doc:
        method_entry["doc"] = doc
    if varargs:
        method_entry["varargs"] = True
    if params:
        method_entry["params"] = [{"name": n, "type": t} for n, t in params]
    if variable_output:
        method_entry["variable_output"] = True
    if pass_capacity:
        method_entry["pass_capacity"] = True
    if nogil:
        method_entry["nogil"] = True
    if none_on_empty:
        method_entry["none_on_empty"] = True
    if batch:
        method_entry["batch"] = True
    if multi_output:
        method_entry["multi_output"] = multi_output
    if out_type:
        method_entry["out_type"] = out_type
    if out_divisor != 1:
        method_entry["out_divisor"] = out_divisor
    if no_bench:
        method_entry["bench"] = False
    if result_fields:
        method_entry["result_fields"] = result_fields
        method_entry["max_results"] = max_results
    if py_return_type:
        method_entry["py_return_type"] = py_return_type
    if max_out > 0:
        method_entry["max_out"] = max_out

    C.add_method(cfg, object_name, method_entry)
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    # 3. Regenerate ext.c (with updated method wrappers)
    if module:
        _regenerate_module(root, cfg, module, pkg)
        # Surgically add the new method's declaration to the per-object
        # _core.h (needed for the module ext.c's #include) — no re-render,
        # no body splice.
        core_h_ = (
            root / "native" / "inc" / object_name / f"{object_name}_core.h"
        )
        if _inject_decls_into_core_h(
            core_h_, object_name, proto_lines, skip_names=_vo_skip
        ):
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
        ctx.update(Ctx.make_sample_ctx(arg_type_, return_type_))
        ctx.update(
            Ctx.make_state_ctx(
                object_name,
                Component,
                state_vars_list,
                array_args=C.array_args(cfg, object_name),
                no_state=C.is_no_state(cfg, object_name),
                init_params=C.init_params(cfg, object_name),
            )
        )
        ctx.update(Ctx.make_perf_ctx(perf))
        ctx.update(
            Ctx.make_step_ctx(
                ctx,
                arg_type_,
                return_type_,
                no_step=C.is_no_step(cfg, object_name),
            )
        )
        methods_ctx = Ctx.make_methods_ctx(
            object_name,
            Component,
            C.methods(cfg, object_name),
            pkg=pkg,
            py_create_args=ctx.get("py_create_args", ""),
            no_state=C.is_no_state(cfg, object_name),
        )
        ctx.update(methods_ctx)
        # extra_ext_sources: space-prefixed list of varargs binding .c files
        # compiled into the Python DSO target (not the pure-C OBJECT lib).
        ctx["extra_ext_sources"] = "".join(
            f" {f}" for f in methods_ctx.get("varargs_binding_files", [])
        )
        ctx.update(
            Ctx.make_properties_ctx(
                object_name,
                Component,
                C.properties(cfg, object_name),
                frozenset(n for n, _, _ in state_vars_list),
            )
        )

        def r(tmpl):
            return R.render(tmpl, ctx)

        # Surgically inject the new method's declaration into _core.h (sacred
        # struct + inline step() untouched); regenerate the glue (_ext.c, the
        # benchmark, the stub, and the component CMakeLists) from the manifest.
        core_h = (
            root / "native" / "inc" / object_name / f"{object_name}_core.h"
        )
        ext_c = root / "native" / "src" / object_name / f"{object_name}_ext.c"
        obj_cmake = root / "native" / "src" / object_name / "CMakeLists.txt"
        no_step = C.is_no_step(cfg, object_name)
        bench_c_tmpl = R.NO_STEP_BENCH_C if no_step else R.COMPONENT_BENCH_C
        if _inject_decls_into_core_h(
            core_h, object_name, proto_lines, skip_names=_vo_skip
        ):
            print(f"  update  {core_h}")
        if ext_c.exists():
            ext_c.write_text(r(R.COMPONENT_EXT_C), encoding="utf-8")
            print(f"  update  {ext_c}")
        bench_c = (
            root / "native" / "benchmarks" / f"bench_{object_name}_core.c"
        )
        if bench_c.exists():
            bench_c.write_text(r(bench_c_tmpl), encoding="utf-8")
            print(f"  update  {bench_c}")
        pyi_path = root / "src" / pkg / f"{object_name}.pyi"
        if pyi_path.exists():
            pyi_path.write_text(r(R.COMPONENT_PYI), encoding="utf-8")
            print(f"  update  {pyi_path}")
        # Surgical splice: when a varargs binding file was just added,
        # insert it into the Python3_add_library line in CMakeLists.txt.
        # Only varargs methods change the build-system source list; normal
        # methods have no effect on CMakeLists.
        if varargs and obj_cmake.exists():
            _splice_varargs_source(
                obj_cmake,
                object_name,
                f"{object_name}_{method_name}_core.c",
            )

    print()
    if varargs:
        print(
            f"Done!  Implement {object_name}_{method_name}()"
            f" in {object_name}_{method_name}_core.c"
        )
    else:
        print(
            f"Done!  Implement {object_name}_{method_name}() in {core_c.name}"
        )
