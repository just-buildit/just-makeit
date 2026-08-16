"""
_method.py — `just-makeit method` command.

Adds a named execute method to an existing object:

    just-makeit method nco execute_cf32 --module dsp \\
        --arg-type void --return-type "float _Complex" --variable-output

For --variable-output methods:
  - NumPy owns each call's output arrays (gh-600 multi-output, gh-604 single)
    — no instance buffer, no freelist, no liveness tracking. See
    docs/memory-ownership.md for the policy and the measurements behind it.
  - Appends <<component>>_<name>_max_out() + <<component>>_<name>() stubs to _core.c
  - Declarations go into _core.h via <<method_decls>> placeholder (regenerated)

For fixed-output methods:
  - Appends a simple stub for <<component>>_<name>() to _core.c
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import _config as C
from . import _glue
from . import _render as R
from . import _stubs as S
from . import _types as T

from . import _report
from ._builtins import (
    builtin_method_names,
    is_builtin_symbol,
    reserved_python_members,
    withdraw_overridden_builtin,
)
from ._init import (
    _inject_decls_into_core_h,
    standalone_extra_include,
)
from ._object import _regenerate_module

# gh-805 §B: the return types on which `_rc < 0` is a meaningful test.
# Enumerated rather than derived from `_CTYPE_META[...]["kind"] == "int"`,
# because that predicate is true of `size_t` and every `uint*_t` — the exact
# set where the generated comparison is always false and therefore silent.
# `bool` and `ptrdiff_t` are int-kind too and equally wrong here.
SIGNED_INT_RETURNS = frozenset(
    {"int", "int8_t", "int16_t", "int32_t", "int64_t"}
)

# gh-805 §A2: what a C function name may be.
_C_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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


def _out_elem_disp(return_type: str, out_type: str | None = None) -> str:
    """Display ctype of a variable-output buffer's *element*.

    A variable-output method writes into a ``<elem> *out`` buffer, so a ``T[]``
    return type (or ``out_type``) must be reduced to its element ``T`` — else
    the prototype/stub render the invalid ``T[] *out`` (the array-input
    ``const T[] *in`` counterpart fixed in gh-139). A scalar type is already
    its own element type, so this is a no-op for the common case.
    """
    src = out_type if out_type else return_type
    if src.endswith("[]"):
        src = src[:-2]
    return T._ctype_display(src)


def _max_out_count_param(
    arg_type: str, params: list[tuple[str, str]] | None
) -> "tuple[str, str | None]":
    """gh-607: the count parameter ``*_max_out()`` takes, mirroring the
    shape's own kernel count argument — the same value the binding is about
    to pass to the kernel — rather than inventing a fourth name for the same
    concept.

    Returns ``(decl, name)``: ``decl`` is the C parameter text to append
    (``", size_t n_in"``); ``name`` is the identifier the stub body
    suppresses/uses, or ``None`` only for the all-scalar-params shape, whose
    kernel has no size-bearing argument to mirror — ``max_out`` stays
    zero-arg for that one shape (there is nothing to pass).
    """
    params = params or []
    if arg_type != "void":
        return ", size_t n_in", "n_in"
    for pn, pt in params:
        if T.is_array_param_type(pt):
            return f", size_t {pn}_len", f"{pn}_len"
    if not params:
        return ", size_t n", "n"
    return "", None


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
    count_default: str = "",
    c_fn: str = "",
) -> str:
    """Generate _core-level C stubs for a variable-output method.

    ``max_out`` (when > 0) makes the generated ``<comp>_<name>_max_out``
    return that integer literal instead of the ``return 0; /* placeholder */``
    stub.  Saves the user from hand-writing the obvious upper bound for
    detector / event-emitter shapes (gh-65 follow-up; Phase 2 row).

    ``pass_capacity`` (gh-138) appends a trailing ``size_t max_out`` output
    capacity parameter, for a C API that bounds-checks the caller's buffer.

    gh-607: ``<comp>_<name>_max_out`` takes the same count parameter as the
    method's own kernel call (see :func:`_max_out_count_param`) — the
    binding calls it with exactly the value it is about to pass to the
    kernel, so ``0`` is an ordinary answer (e.g. "this call produces
    nothing"), not a "no information" sentinel.
    """
    c_fn = c_fn or f"{component}_{name}"
    ret_disp = _out_elem_disp(return_type, out_type)
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

    moc_decl, moc_name = _max_out_count_param(arg_type, params)
    moc_suppress = f" (void){moc_name};" if moc_name else ""

    if max_out > 0:
        _max_out_head = f"/* Worst-case output count for {name}() — set via --max-out {max_out}. */"
        _max_out_body = f"    return {max_out};"
    else:
        _max_out_head = (
            "/* <<IMPLEMENT: return maximum possible output samples for"
            f" {name} given current state"
            + (f" and {moc_name}" if moc_name else "")
            + " >> */"
        )
        _max_out_body = "    return 0; /* placeholder */"
    lines = [
        _max_out_head,
        "size_t",
        f"{c_fn}_max_out({component}_state_t *state{moc_decl})",
        "{",
        f"    (void)state;{moc_suppress}",
        _max_out_body,
        "}",
        "",
        f"/* <<IMPLEMENT: process{' input and' if has_arg else ''} write results"
        f" into out[0..n_out-1]; return actual output count >> */",
        "size_t",
        f"{c_fn}({component}_state_t *state"
        f"{step_param}, {ret_disp} *out{extra_out_params}{cap_param})",
        "{",
        "    (void)state;",
        suppress_in,
        # gh-600: the extra multi_output buffers are parameters too — omitting
        # them left a freshly scaffolded stub warning about unused `out1`
        # (an error under -Werror) before a line of user code is written.
        "    (void)out;"
        + "".join(f" (void)out{i + 1};" for i in range(len(all_extra)))
        + cap_suppress,
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
    params: list | None = None,
    c_fn: str = "",
) -> str:
    """C stub for a method that returns a list of structs (result_fields).

    ``params`` expand into the signature exactly as they do in
    :func:`_build_method_prototype` (gh-594) — the stub *is* the definition
    that prototype declares, so the two must not drift.
    """
    c_fn = c_fn or f"{component}_{name}"
    ret_disp = T._ctype_display(return_type)
    has_arg = arg_type != "void"
    params = params or []
    if has_arg:
        arg_disp = _block_in_elem_disp(arg_type)
        step_param = f", const {arg_disp} *in, size_t n_in"
        suppress = "    (void)in; (void)n_in;"
    else:
        step_param = ""
        suppress = ""
    _p_parts = T.c_param_parts(params)
    if _p_parts:
        step_param += ", " + ", ".join(_p_parts)
        suppress += (
            ("" if not suppress else "\n")
            + "    "
            + " ".join(T.c_param_suppress(params))
        )
    lines = [
        "/* <<IMPLEMENT: push input, fill result[], return count >> */",
        "size_t",
        f"{c_fn}({component}_state_t *state"
        f"{step_param}, {ret_disp} *result, size_t max_results)",
        "{",
        "    (void)state;",
        suppress,
        "    (void)result; (void)max_results;",
        "    return 0; /* placeholder */",
        "}",
    ]
    return "\n".join(lines) + "\n"


def _methods_c_stub_result_single(
    component: str,
    name: str,
    arg_type: str,
    return_type: str,
    params: list | None = None,
    c_fn: str = "",
) -> str:
    """C stub for a method that returns one record struct by value (gh-244).

    The single-record sibling of :func:`_methods_c_stub_result_fields`: no
    ``results[]`` buffer / ``max_results`` — the kernel computes and returns one
    ``<return_type>`` value, which the binding unpacks into a named
    PyStructSequence.

    ``params`` expand into the signature as everywhere else (gh-594); before
    that fix this stub took only ``state``, while the generated binding called
    it with every declared param — a guaranteed "too many arguments".
    """
    c_fn = c_fn or f"{component}_{name}"
    ret_disp = T._ctype_display(return_type)
    has_arg = arg_type != "void"
    params = params or []
    if has_arg:
        arg_disp = _block_in_elem_disp(arg_type)
        step_param = f", const {arg_disp} *in, size_t n_in"
        suppress = "    (void)in; (void)n_in;"
    else:
        step_param = ""
        suppress = ""
    _p_parts = T.c_param_parts(params)
    if _p_parts:
        step_param += ", " + ", ".join(_p_parts)
        suppress += (
            ("" if not suppress else "\n")
            + "    "
            + " ".join(T.c_param_suppress(params))
        )
    lines = [
        "/* <<IMPLEMENT: compute and return the record >> */",
        ret_disp,
        f"{c_fn}({component}_state_t *state{step_param})",
        "{",
        "    (void)state;",
        suppress,
        f"    {ret_disp} _r = {{0}};",
        "    return _r; /* placeholder */",
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
    c_fn: str = "",
) -> str:
    """Generate a _core-level C stub for a fixed-output method."""
    c_fn = c_fn or f"{component}_{name}"
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
            f"void\n{c_fn}({c_params})\n{{\n{sup}\n}}\n"
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
        f"{c_fn}({c_params})",
        "{",
        suppress,
    ]
    if ret_line:
        lines.append(ret_line)
    lines.append("}")
    return "\n".join(lines) + "\n"


#: The comment jm stamps above every method stub it writes, and above no
#: built-in body.
#:
#: Anchored to column 0, which is the whole discriminator. The same marker
#: appears *indented* inside two built-in bodies — `create_assignments` and
#: `reset_assignments` fall back to `/* <<IMPLEMENT: initialise state >> */`
#: on a `--no-state` object — so an unanchored match would read those as
#: method stubs and hand the built-in's symbol to the method.
#:
#: Deliberately not matched on the text after the colon: only the fixed-shape
#: stub spells the method's name there. The variable-output shape writes
#: `process input and write results into out[...]` and the record shape
#: `compute and return the record`, so a name comparison recognises one stub
#: kind out of three and quietly misclassifies the other two.
_IMPLEMENT_MARKER = re.compile(r"^/\* <<IMPLEMENT:", re.M)


def already_provides(
    root: Path,
    component: str,
    c_fn: str,
    builtins: "frozenset[str]",
) -> str:
    """Where a *built-in* already provides *c_fn*, or ``""``.

    gh-994. A method may be named after something jm emits itself —
    ``reset``, ``destroy``, ``create``, ``step``, ``steps``, or the
    ``get_``/``set_`` accessor of a state field — and doppler declares
    ``reset`` that way in 28 objects, because a method entry is how a
    component's Python surface is written down. ``_core.c`` is create-only, so
    by the time ``jm method`` runs the built-in body is already in a file jm
    must not rewrite; appending the stub anyway puts two definitions of one
    symbol in it and the tree jm just wrote does not compile.

    Two questions have to agree before this returns anything, and getting
    either alone wrong is a shipped bug:

    - **Is the symbol one jm's own generator owns?** Answered by
      :mod:`._builtins` from the manifest, not by a reserved-word list —
      ``step`` is not a collision on a ``--no-step`` object and ``get_gain``
      is one only where ``gain`` is a scalar field.
    - **Is the definition in the tree the built-in's, or a method stub?** By
      the time this is asked a second time — a re-run, or an ``apply`` replay
      over a tree jm already materialized — the ``<comp>_reset`` in
      ``_core.c`` may be the *method's* stub. Reading only the symbol name
      there makes the method conclude it is already provided and skip its own
      prototype, and the tree fails to build with `implicit declaration of
      <comp>_<method>`. The ``/* <<IMPLEMENT: ... >> */`` comment jm stamps
      above a method stub, and above no built-in body, is what tells them
      apart — see :func:`_is_method_stub`.

    The header is consulted as well as the source: ``step`` is a ``static
    inline`` in ``_core.h`` and never appears in ``_core.c`` at all, so a
    source-only check misses exactly the case that fails as *conflicting
    types* rather than as a redefinition.
    """
    if not is_builtin_symbol(component, c_fn, builtins):
        return ""

    # `rx_reset(` sits at column 0 in a definition (the return type is on the
    # line above); a header declaration is `void rx_steps(...)`, and `step` is
    # `static inline float rx_step(...)`. An optional type prefix covers both.
    # The `^` anchor is what keeps an indented CALL from matching.
    pat = re.compile(
        rf"^(?:[A-Za-z_][A-Za-z0-9_ *]*\s+)?{re.escape(c_fn)}\s*\(", re.M
    )
    # `_core.c` is asked first and, when it has an answer, is the *only*
    # answer. A definition there settles ownership outright — the marker says
    # whose it is — and falling through to the header on a match that turned
    # out to be this method's own stub would then read the built-in's
    # *declaration*, which carries no marker and so reads as the built-in's no
    # matter who actually defines the symbol. The header is the fallback for
    # the one built-in that has no `_core.c` body at all: `step`, a `static
    # inline` in the header.
    src = Path("native") / "src" / component / f"{component}_core.c"
    inc = Path("native") / "inc" / component / f"{component}_core.h"
    for rel, what in ((src, "defines"), (inc, "declares")):
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        m = pat.search(text)
        if m is None:
            continue
        if _is_method_stub(text, m.start()):
            return ""
        return f"{rel.as_posix()} already {what} it"
    return ""


def _is_method_stub(text: str, at: int) -> bool:
    """Whether the definition beginning at *at* is a generated method stub.

    True when the nearest ``/* <<IMPLEMENT: ... >> */`` marker above it is
    *this* definition's — nothing but the return type sits between them, so a
    closing brace in the gap means the marker belongs to an earlier stub and
    this definition has none of its own.

    Only the nearest preceding marker is considered, for the same reason:
    every stub in the file carries one, so the first match found by an
    unanchored search would make every definition below the first stub look
    like a stub.
    """
    prev = None
    for m in _IMPLEMENT_MARKER.finditer(text, 0, at):
        prev = m
    if prev is None:
        return False
    return "}" not in text[prev.end() : at]


def _append_to_core_c(
    path: Path, stub: str, c_fn: str = "", provided_by: str = ""
) -> None:
    """Append a method stub to native/src/{comp}/{comp}_core.c.

    Skips the append when a built-in already provides *c_fn* (gh-994) — jm
    must never write two definitions of one symbol into a file it cannot
    rewrite afterwards. *provided_by* comes from :func:`already_provides`,
    which reads the header as well as the source: `step` is a `static inline`
    in the header and never appears in `_core.c` at all, so a source-only
    check misses it.
    """
    existing = path.read_text(encoding="utf-8")
    if c_fn and provided_by:
        print(f"  skip    {c_fn}() — {provided_by}")
        return
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
    count_default: str = "",
    batch: bool = False,
    result_fields: list[dict] | None = None,
    single: bool = False,
    record_dtype: str = "",
    c_fn: str = "",
) -> str:
    """Return C prototype declaration(s) for a method (no trailing newline).

    gh-805 §A2: *c_fn* is the C symbol to declare, defaulting to the derived
    ``<component>_<name>``. This is the declaration that lands in the SACRED
    ``_core.h``, so it is the half of the override that must be right — the
    definition in ``_core.c`` is written against it.

    gh-788: *record_dtype* names the POD struct a variable-output method
    writes rows of. It reaches the ``*out`` parameter through the same
    ``out_type`` slot every other element type uses, and it also suppresses
    the ``result_fields`` branch below — a record-dtype method carries
    ``result_fields`` for the dtype's columns, not for a list-of-records
    out-param pair, and this is the third of three places that distinction
    has to be drawn (the other two are ``make_methods_ctx``'s declaration
    chain and :func:`run`'s stub dispatch).
    """
    c_fn = c_fn or f"{component}_{name}"
    ret_disp = T._ctype_display(return_type)
    has_arg = arg_type != "void"
    multi_output = multi_output or []
    params = params or []
    result_fields = result_fields or []

    # gh-244: a result_fields method returns a list of structs — the header
    # declaration must match _methods_c_stub_result_fields/_result_single's
    # shape (size_t count + results[]/max_results out-params, or one record
    # by value with `single`), not the generic scalar/array fallback below.
    if result_fields and not (variable_output and record_dtype):
        # gh-594: the record shapes used to build their signature from
        # `arg_type` alone, silently dropping every declared `param`. The
        # binding passed them anyway, so a `single` method with params gave
        # "too many arguments to function" and a non-single one quietly
        # ignored params on both sides. Params expand here exactly as they do
        # for every other shape (T.c_param_parts) -- array -> ptr + `_len`.
        step_param = (
            f", const {_block_in_elem_disp(arg_type)} *in, size_t n_in"
            if has_arg
            else ""
        )
        _p_parts = T.c_param_parts(params)
        if _p_parts:
            step_param += ", " + ", ".join(_p_parts)
        if single:
            return (
                f"{ret_disp} {c_fn}({component}_state_t *state{step_param});"
            )
        return (
            f"size_t {c_fn}({component}_state_t *state"
            f"{step_param}, {ret_disp} *result, size_t max_results);"
        )

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
            f"void {c_fn}({component}_state_t *state"
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
        out_disp = _out_elem_disp(return_type, record_dtype or out_type)
        moc_decl, _ = _max_out_count_param(arg_type, params)
        return "\n".join(
            [
                f"size_t {c_fn}_max_out({component}_state_t"
                f" *state{moc_decl});",
                f"size_t {c_fn}({component}_state_t *state"
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

    return f"{ret_disp} {c_fn}({c_params});"


#: How a manifest method entry becomes the corresponding `run()` argument.
#:
#: Every entry is `key -> (coercion, default-when-absent)`, and the pair is the
#: point: a manifest that omits `nogil` and a caller that passes `nogil=False`
#: describe the same method, so comparing the raw `entry.get(key)` against the
#: argument would report a difference that is not one. That false positive is
#: the failure mode a gh-1011 fix is most likely to introduce — refusing a
#: doc-only override because the parent's TOML simply did not mention a key.
#:
#: `tests/test_gh1012_view_signature_override.py` holds this to
#: `_keys.METHOD_SIGNATURE_KEYS`, so a new signature key cannot be added
#: without deciding how it compares.
_SIGNATURE_COERCIONS: dict = {
    "arg_type": (str, "void"),
    "return_type": (str, "float _Complex"),
    "variable_output": (bool, False),
    "multi_output": (list, []),
    "params": (list, []),
    "out_type": (lambda v: v or "", None),
    "out_divisor": (int, 1),
    "batch": (bool, False),
    "none_on_empty": (bool, False),
    "result_fields": (list, []),
    "max_results": (int, 64),
    "single": (bool, False),
    "record_name": (str, ""),
    "record_module": (str, ""),
    "record_dtype": (str, ""),
    "py_return_type": (str, ""),
    "max_out": (int, 0),
    "varargs": (bool, False),
    "manual_stub": (bool, False),
    "pass_capacity": (bool, False),
    "exact_max_out": (bool, False),
    "count_default": (str, ""),
    "nogil": (bool, False),
    "status_return": (bool, False),
    "error_negative": (bool, False),
    "error": (str, ""),
    "error_message": (str, ""),
    "codec": (str, ""),
    "sink_fn": (str, ""),
}


def _signature_differences(
    parent: dict, declared: "frozenset[str] | None" = None, **incoming
) -> "list[str]":
    """Signature keys where `incoming` disagrees with the `parent` entry.

    *declared* names the keys the caller actually stated. `None` means "all of
    them", which is right for a direct call where every argument is explicit.
    It matters because **only the caller knows what it left out**: by the time
    values reach here, `_apply._replay_method` has already substituted this
    function's own defaults for keys the manifest never had, so an entry
    declaring nothing but `doc` arrives indistinguishable from one declaring
    `arg_type = "void"`. Comparing those manufactured values is what refused a
    hand-written doc-only override — the same absence-is-not-a-difference trap
    as below, one layer out, on the path both issues were filed from.

    Both sides go through the SAME coercion, which is why this compares
    reliably: the parent arrives as raw TOML (where a bool may be `true` or
    `"true"`, and an absent key means "the default"), the caller's values
    arrive already typed, and normalising only one of them is how a comparison
    like this reports differences that do not exist.

    `extra_args` has no row of its own because `_apply._replay_method` already
    funnels it into `params` — comparing both would ask the same question
    twice and answer it differently when only one is set.
    """

    def _norm(coerce, raw):
        if raw is None:
            return None
        try:
            return coerce(raw)
        except (TypeError, ValueError):
            return raw

    differing = []
    for key, value in incoming.items():
        if declared is not None and key not in declared:
            continue  # never stated, so it inherits — not a difference
        coerce, default = _SIGNATURE_COERCIONS[key]
        # Absence is spelled two different ways and BOTH mean "the default":
        # the parent's TOML simply omits the key, while `run`'s own signature
        # defaults several of these to None (`params`, `result_fields`,
        # `out_type`). Collapsing only the first is not enough — it reported
        # `params` as differing on every doc-only override, because the
        # manifest had no `params` key and the caller passed None.
        theirs = _norm(coerce, parent.get(key, default))
        mine = _norm(coerce, default if value is None else value)
        if theirs != mine:
            differing.append(key)
    return sorted(differing)


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
    single: bool = False,
    record_name: str = "",
    record_module: str = "",
    record_doc: str = "",
    record_dtype: str = "",
    py_return_type: str = "",
    max_out: int = 0,
    varargs: bool = False,
    manual_stub: bool = False,
    pass_capacity: bool = False,
    exact_max_out: bool = False,
    count_default: str = "",
    nogil: bool = False,
    status_return: bool = False,
    fn: str = "",
    error_negative: bool = False,
    error: str = "",
    error_message: str = "",
    doc: str = "",
    from_apply: bool = False,
    view: str = "",
    codec: str = "",
    sink_fn: str = "",
    #: Signature keys the caller actually stated, for a view override.
    #: `None` means every argument below is explicit — correct for a direct
    #: call, and wrong for a replay, where absent manifest keys have already
    #: become this function's defaults. Only the caller can tell them apart.
    declared: "frozenset[str] | None" = None,
) -> None:
    C.require_name(method_name, "method")  # gh-625
    # gh-910: the method's parameters and result fields, before this command
    # writes any C. `C.save`'s gate catches them either way, but this command
    # renders the binding and the stub before it saves, so without this a
    # refused `--param gaïn:double` still reached the generated source.
    C.require_declared_names(
        {
            object_name: {
                "methods": [
                    {
                        "name": method_name,
                        "params": C.as_named_tables(params),
                        "result_fields": C.as_named_tables(result_fields),
                        **(
                            {"record_name": record_name} if record_name else {}
                        ),
                    }
                ]
            }
        }
    )
    # gh-788: `record_dtype` is only meaningful as the element type of a
    # variable-output result, and the dtype cannot be built without the
    # member list. Both are checked here rather than left to fail later as a
    # C compile error in the user's tree, where the cause is several
    # generated files away from the symptom.
    if record_dtype:
        if not variable_output:
            print(
                "error: --record-dtype describes the ELEMENT of a "
                "variable-output result;\n"
                "it needs --variable-output as well.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not result_fields:
            print(
                f"error: --record-dtype {record_dtype} needs at least one "
                "--result-field\n"
                "to build the numpy dtype from -- the struct's members are "
                "what become\n"
                "the dtype's columns.",
                file=sys.stderr,
            )
            sys.exit(1)
        if single:
            print(
                "error: --record-dtype and --single are different results.\n"
                "--single returns ONE record as a named tuple; "
                "--record-dtype returns\n"
                "an ARRAY of records as a structured ndarray. Pick one.",
                file=sys.stderr,
            )
            sys.exit(1)
    # gh-805 §B: value-or-negative-error. Checked here rather than left to
    # render, because each of these produces C that COMPILES and is wrong —
    # the failure mode this key exists to remove.
    if error_negative:
        if status_return:
            print(
                "error: --error-negative and --status-return make opposite "
                "claims about\nthe return value. --status-return says the int "
                "is ONLY a status (non-zero\nraises); --error-negative says it "
                "is a VALUE unless negative. Pick one.",
                file=sys.stderr,
            )
            sys.exit(1)
        if return_type not in SIGNED_INT_RETURNS:
            # An unsigned or non-integer return makes `_rc < 0` either
            # always-false or meaningless, and always-false is the version
            # that compiles, runs, and silently restores the bug. `kind ==
            # "int"` is NOT the test: it is true of `size_t` and every
            # `uint*_t` too, which is exactly the set that must be rejected.
            print(
                f"error: --error-negative needs a SIGNED integer return type; "
                f"{return_type!r}\ncannot be negative, so the error test would "
                f"never fire.\nUse one of: "
                f"{', '.join(sorted(SIGNED_INT_RETURNS))}.",
                file=sys.stderr,
            )
            sys.exit(1)
        # The fourth member of this set, and the one review caught: every
        # non-scalar result shape builds its return value somewhere else, so
        # `error_negative` was accepted, written to the manifest, and then
        # silently emitted nothing. That is the failure this feature's own
        # rejections exist to prevent, and the one gh-805 §G is about — a
        # declaration that lands where jm does not look for it, read back as
        # correct because reading the manifest is what reviewing it consists
        # of. Rejection rather than a warning, to match the three siblings.
        _shape = (
            "--variable-output"
            if variable_output
            else "--single"
            if single
            else "--record-dtype"
            if record_dtype
            else "--multi-output"
            if multi_output
            else "--out-type"
            if out_type
            else ""
        )
        if _shape:
            print(
                f"error: --error-negative needs a plain scalar int return, "
                f"but {_shape}\nbuilds an array or a record instead — there "
                "is no single int for the\nnegative test to read. Drop one "
                "of the two.",
                file=sys.stderr,
            )
            sys.exit(1)
    # gh-823 Ask D: `status_return` raises too, so it may name the exception
    # and carry the message. The key was never the problem — both were already
    # read from the manifest; only `error_negative`'s emitter looked at them.
    # This gate is what turned that into a refusal rather than a silent no-op.
    if error and not (error_negative or status_return):
        print(
            "error: --error names the exception a failing return raises, so "
            "it needs\n--error-negative or status_return as well.",
            file=sys.stderr,
        )
        sys.exit(1)
    if error_message and not (error_negative or status_return):
        print(
            "error: --error-message is the text a failing return raises with, "
            "so it\nneeds --error-negative or status_return as well.",
            file=sys.stderr,
        )
        sys.exit(1)
    # gh-805 §A2: `fn` is spliced verbatim into the generated C, so a
    # non-identifier produces a file that does not compile. Deliberately not
    # the gh-625 name predicate, though gh-784's `isascii()` term has since
    # narrowed the two to within one case of each other (`valid_identifier`
    # rejects a name of only underscores; C does not). They answer different
    # questions and only look alike: `valid_identifier` governs names *jm*
    # writes into its own artifacts, while `--fn` names a symbol the AUTHOR
    # already has, which must match C's rule exactly because jm is not
    # generating it — it is calling it. The earlier note here justified the
    # split by claiming jm names are lowercase; they never were, which is the
    # whole of gh-784's first half.
    if fn and not _C_IDENTIFIER.fullmatch(fn):
        print(
            f"error: --fn {fn!r} is not a C identifier. It is emitted "
            "verbatim as the\nfunction jm calls, so it must match "
            "[A-Za-z_][A-Za-z0-9_]*.",
            file=sys.stderr,
        )
        sys.exit(1)
    if error and error not in C.ERROR_CATEGORIES:
        print(
            f"error: --error {error!r} is not a known exception category.\n"
            f"Choose one of: {', '.join(sorted(C.ERROR_CATEGORIES))}.",
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

    # Resolve which component this belongs to
    # gh-963: the manifest already records which module owns this object,
    # so `--module` is a confirmation rather than the only way jm can know.
    # Without this the branch below fell through to the standalone path on a
    # module-owned object: the verb wrote the C stub, never touched the
    # module's binding fragment, printed `Done!`, and left a project that
    # compiles and imports with the member missing from the class.
    module = C.resolve_module(cfg, object_name, module)

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

    pkg = C.project_name(cfg)

    # gh-504: --view retargets the method onto a VIEW of the object. Views are
    # module-only.
    view_entry = None
    if view:
        if not module:
            print(
                f"error: object '{object_name}' is standalone, so it cannot have a\n"
                "  view — views are a module-object feature (gh-504). "
                "Move the object into a\n  module, or drop --view.",
                file=sys.stderr,
            )
            sys.exit(1)
        view_entry = C._find_view(cfg, object_name, view)
        if view_entry is None:
            print(
                f"error: no view '{view}' on object '{object_name}'. Create it "
                f"first with 'just-makeit view {object_name} {view} --module "
                f"{module} --create-fn <fn>'.",
                file=sys.stderr,
            )
            sys.exit(1)
        if method_name in C.view_exclude_methods(view_entry):
            print(
                f"error: method '{method_name}' is both excluded and added on "
                f"view '{view}' — that is contradictory. Drop it from "
                f"--exclude-method, or don't add it.",
                file=sys.stderr,
            )
            sys.exit(1)
        # A view method whose name matches a PARENT method overrides it, and
        # there are two kinds (gh-504, then gh-1012):
        #
        #   doc-only  — the view's wrapper calls the shared <comp>_<method>
        #               symbol, so the signature MUST stay the parent's. Copy
        #               the parent entry and swap the doc; no C to scaffold.
        #   signature — the view binds a DIFFERENT C symbol under the same
        #               Python name, so it may take a different `arg_type`.
        #               That is the ordinary ADD path with a colliding name,
        #               and it falls through to it below.
        #
        # `fn` is what separates them, and not as a convenience: the parent's
        # symbol has the parent's prototype, so a different signature is only
        # callable through a different symbol. Declaring one IS declaring the
        # other. Until gh-1012 the second kind was accepted and silently
        # discarded — `entry = dict(parent)` kept every signature key of the
        # parent's, so a declared `arg_type` reached neither face (gh-1011).
        parent_names = {m["name"] for m in C.methods(cfg, object_name)}
        if method_name in parent_names:
            parent = next(
                m
                for m in C.methods(cfg, object_name)
                if m["name"] == method_name
            )
            if method_name in {m["name"] for m in C.view_methods(view_entry)}:
                print(
                    f"error: view '{view}' already overrides method "
                    f"'{method_name}'.",
                    file=sys.stderr,
                )
                sys.exit(1)
            parent_symbol = C.method_c_symbol(object_name, parent)
            if fn:
                # gh-1012: a signature override. Refuse a symbol that is the
                # parent's — it would compile as a redefinition with a
                # conflicting prototype, which is the failure the doc-only
                # rule existed to prevent and is worth naming here rather
                # than letting the C compiler report it.
                if fn == parent_symbol:
                    print(
                        f"error: view '{view}' overrides method "
                        f"'{method_name}' with fn '{fn}', which is the "
                        f"symbol the parent already binds.\n"
                        f"  A signature override needs its OWN C function — "
                        f"give --fn a different name, or drop it for a "
                        f"doc-only override.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                # NB the view's fragment already carries a wrapper for this
                # name — the one it inherited from the parent. Regenerating it
                # is handled where every path meets (`_regenerate_module_now`
                # derives it from the manifest), not threaded from here: this
                # command is only one of the ways the override arrives, and
                # `apply` needs the same answer.
                #
                # fall through to the ADD path: it scaffolds the stub into the
                # shared core and binds `fn` under this Python name.
            else:
                differing = _signature_differences(
                    parent,
                    declared,
                    arg_type=arg_type,
                    return_type=return_type,
                    variable_output=variable_output,
                    multi_output=multi_output,
                    params=params,
                    out_type=out_type,
                    out_divisor=out_divisor,
                    batch=batch,
                    none_on_empty=none_on_empty,
                    result_fields=result_fields,
                    max_results=max_results,
                    single=single,
                    record_name=record_name,
                    record_module=record_module,
                    record_dtype=record_dtype,
                    py_return_type=py_return_type,
                    max_out=max_out,
                    varargs=varargs,
                    manual_stub=manual_stub,
                    pass_capacity=pass_capacity,
                    exact_max_out=exact_max_out,
                    count_default=count_default,
                    nogil=nogil,
                    status_return=status_return,
                    error_negative=error_negative,
                    error=error,
                    error_message=error_message,
                    codec=codec,
                    sink_fn=sink_fn,
                )
                if differing:
                    # gh-1011: this used to be accepted and ignored.
                    print(
                        f"error: view '{view}' redeclares parent method "
                        f"'{method_name}' with a different "
                        f"{', '.join(differing)}.\n"
                        f"  Without --fn the view calls the parent's "
                        f"{parent_symbol}, which has the parent's signature, "
                        f"so this could only be ignored.\n"
                        f"  Pass --fn <symbol> to bind its own C function "
                        f"(gh-1012), or drop the differing key(s) for a "
                        f"doc-only override.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                entry = dict(parent)
                entry["doc"] = doc
                C.add_view_method(cfg, object_name, view, entry)
                C.save(root, cfg)
                print(f"  update  {cfg_path}")
                _regenerate_module(root, cfg, module, pkg)
                print()
                print(
                    f"Done!  View '{view}' overrides the doc of "
                    f"'{method_name}' (shares the parent's {parent_symbol})."
                )
                return

    # Check for duplicate method name. For a view ADD, dup-check against the
    # view's own methods (the object's shared method of the same name would be
    # a doc-override, handled above).
    if view_entry is not None:
        existing = [m["name"] for m in C.view_methods(view_entry)]
        target = f"view '{view}'"
    else:
        existing = [m["name"] for m in C.methods(cfg, object_name)]
        target = f"'{object_name}'"
    if method_name in existing:
        print(
            f"error: method '{method_name}' already exists on {target}.",
            file=sys.stderr,
        )
        sys.exit(1)

    # gh-996: the same question one step wider. The check above knows about
    # other *declared* methods; these are the names the object's own generated
    # code already puts on the class, where a method entry has no second
    # reading. Accepting one bound something other than what the manifest
    # asked for — a shadowed `__enter__`, a `_ext.c` that would not compile,
    # or in `create`'s case nothing at all — and said nothing.
    #
    # Refusal, not absorption, and here rather than at save time: this runs
    # before the command writes anything, so a refused name leaves no
    # half-made tree (the gh-910 reasoning, one command over). The six
    # built-ins gh-994 absorbs are not in this set — an entry naming `reset`
    # describes a member jm really does emit, which is the supported pattern.
    _reserved = reserved_python_members(cfg, object_name)
    if view_entry is None and method_name in _reserved:
        _holder, _hint = _reserved[method_name]
        print(
            f"error: method '{method_name}' is already provided by "
            f"{_holder} on {target}.\n"
            f"  Drop the entry or rename the method. {_hint}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"just-makeit: adding method '{method_name}' to {target}"
        + (f" in module '{module}'" if module else "")
    )
    print()

    params = params or []
    result_fields = result_fields or []
    # gh-432: accept dict params (the apply replay's full-fidelity form,
    # preserving per-param keys like capsule/header/out) alongside the CLI's
    # (name, type[, default]) tuples; normalise to dicts once so every
    # downstream use is uniform.
    _norm_params: list[dict] = []
    for _p in params:
        if isinstance(_p, dict):
            _norm_params.append(dict(_p))
        else:
            _entry = {"name": _p[0], "type": _p[1]}
            if len(_p) > 2 and _p[2]:
                _entry["default"] = _p[2]
            _norm_params.append(_entry)
    params = _norm_params

    # gh-994: asked ONCE, BEFORE this command writes anything.
    #
    # The ordering is the whole correctness argument. Computed lazily at each
    # use, the header check runs *after* `_append_to_core_c` has written this
    # method's own stub — so every method sees its own definition, concludes
    # it is already provided, and skips its prototype. Measured: 78 tests red
    # with `implicit declaration of nco_steps_ovf`, which is jm emitting a
    # call to a function it just decided not to declare.
    _c_fn = fn or f"{object_name}_{method_name}"
    _provided_by = already_provides(
        root,
        object_name,
        _c_fn,
        builtin_method_names(
            C.state_vars(cfg, object_name),
            no_state=C.is_no_state(cfg, object_name),
            no_step=C.is_no_step(cfg, object_name),
            no_reset=C.is_no_reset(cfg, object_name),
        ),
    )
    # A method that *replaces* a built-in — a `reset(start)`, or the
    # variable-output `steps` doppler declares on most of its objects — takes
    # the symbol, so the built-in's now-orphaned body comes out of `_core.c`
    # first. Only while that body is still jm's untouched scaffold; otherwise
    # this warns and the built-in keeps the symbol.
    _withdrew_builtin = False
    if _provided_by and not (manual_stub or codec or varargs):
        _withdrawn, _warn = withdraw_overridden_builtin(
            root,
            cfg,
            pkg,
            object_name,
            {
                "name": method_name,
                "arg_type": arg_type,
                "return_type": return_type,
                "variable_output": variable_output,
                "multi_output": multi_output,
                "params": params,
                "out_type": out_type,
                "pass_capacity": pass_capacity,
                "batch": batch,
                "result_fields": result_fields,
                "single": single,
                "record_dtype": record_dtype,
                "fn": fn,
            },
        )
        if _withdrawn:
            _provided_by = ""
            _withdrew_builtin = True
        elif _warn:
            _report.warn(_warn)

    # 1. Write C stub: either append to _core.c or write sacred binding file
    core_c = root / "native" / "src" / object_name / f"{object_name}_core.c"
    if manual_stub or codec:
        # manual_stub (gh-428): the C binding already exists, hand-written,
        # inside the user's already-sacred _ext_<obj>_extra.c fragment -- jm
        # never created it and must declare nothing for it (unlike varargs,
        # which owns and creates a fresh <comp>_<name>_core.c stub file).
        # codec (gh-554): a codec-pack method has no core fn at all — jm
        # generates the whole binding in the ext and calls an external sink_fn,
        # so there is likewise no _core.c stub / _core.h prototype to write.
        pass
    elif varargs:
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
        if result_fields and single:
            # gh-244: return one record by value (no results[] buffer).
            stub = _methods_c_stub_result_single(
                object_name,
                method_name,
                arg_type,
                return_type,
                params=[(p["name"], p["type"]) for p in params],
                c_fn=fn,
            )
        elif result_fields and not (variable_output and record_dtype):
            stub = _methods_c_stub_result_fields(
                object_name,
                method_name,
                arg_type,
                return_type,
                max_results,
                params=[(p["name"], p["type"]) for p in params],
                c_fn=fn,
            )
        elif variable_output:
            stub = _methods_c_stub_variable(
                object_name,
                method_name,
                arg_type,
                return_type,
                multi_output,
                params=[(p["name"], p["type"]) for p in params],
                # gh-788: the record struct IS the output element, so it
                # reaches the stub the same way `out_type` does — one
                # substitution, and the `dp_tlm_rec_t *out` parameter, the
                # `(void)out;` suppression and the binding's cast all agree
                # because they are all derived from it.
                out_type=record_dtype or out_type,
                max_out=max_out,
                pass_capacity=pass_capacity,
                c_fn=fn,
            )
        else:
            stub = _methods_c_stub_fixed(
                object_name,
                method_name,
                arg_type,
                return_type,
                multi_output,
                # The C stub signature ignores the optional `default` (a
                # binding concern); project to (name, type) (gh-240).
                [(p["name"], p["type"]) for p in params],
                out_type,
                batch=batch,
                c_fn=fn,
            )
        if impl_body is not None:
            import re as _re

            from . import _impl as I

            body = impl_body
            if variable_output and not _re.search(r"\breturn\b", body):
                body = body.rstrip("\n") + "\nreturn n;"
            stub = I.inject_body_into_stub(stub, body)
        # gh-994: `fn` overrides the symbol; unset it derives the same way
        # every stub emitter above does.
        _append_to_core_c(core_c, stub, _c_fn, _provided_by)

    # The method's public prototype, injected surgically into _core.h below
    # (one or two lines; variable-output methods declare a sibling _max_out).
    # Varargs methods have no typed C prototype — their binding is Python-aware
    # and lives in the sacred binding .c file, not _core.h.
    proto_lines: list[str] = []
    if not varargs and not manual_stub and not codec:
        proto_lines = _build_method_prototype(
            object_name,
            method_name,
            arg_type,
            return_type,
            variable_output,
            multi_output,
            [(p["name"], p["type"]) for p in params],
            out_type,
            pass_capacity=pass_capacity,
            batch=batch,
            result_fields=result_fields,
            single=single,
            record_dtype=record_dtype,
            c_fn=fn,
        ).split("\n")

    # gh-666: a newly injected prototype gets jm's prose-free doc skeleton, so
    # the author writes prose and never structure. The sibling `_max_out`
    # declaration a variable_output method emits is jm's own bookkeeping, not
    # a surface anyone documents, so only the method itself is mapped.
    # gh-805 §A2: the skeleton is stamped above the symbol actually
    # declared, so an `fn`-overridden method gets one too.
    _doc_members = {(fn or f"{object_name}_{method_name}"): method_name}
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
    #
    # gh-994 adds the third case the net must stand down for: a built-in whose
    # body jm has just WITHDRAWN from `_core.c` above. Its declaration is no
    # longer a signature to preserve — it describes a function that no longer
    # exists, and leaving it would be the header half of the duplicate-symbol
    # bug rather than a safeguard against it.
    _vo_skip: frozenset[str] = frozenset()
    if (
        variable_output
        and not pass_capacity
        and not from_apply
        and not _withdrew_builtin
    ):
        # gh-1012: the symbol this method actually BINDS, which is `fn` when
        # declared. Deriving it from the name instead was invisible while
        # every `fn` was a name nothing else used — a signature override makes
        # `<obj>_<name>` collide by construction (that is what it is for), so
        # the net fired on the parent's declaration every time and advised
        # removing a capacity param that was never there.
        _vo_fn = C.method_c_symbol(
            object_name, {"name": method_name, "fn": fn}
        )
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
    # gh-994, the header half of the same rule: jm must not declare a symbol a
    # built-in already declares, for the same reason it must not define one
    # twice. Skipping only the `_core.c` definition fixes `reset` and
    # `destroy` — whose built-in declaration already matches — and leaves
    # `step`, `steps`, `create` and the `get_`/`set_` accessors failing as
    # *conflicting types*, because `_inject_decls_into_core_h` would REPLACE
    # the built-in's prototype with the method's while the built-in's
    # definition, which jm just declined to overwrite, keeps the old one.
    #
    # Per SYMBOL, not per command: a variable-output method also declares a
    # sibling `<fn>_max_out`, which no built-in provides and which the
    # binding calls on every invocation. Suppressing the whole prototype
    # block took that with it and the extension stopped compiling (gh-607's
    # `steps` case, which is a collision and a variable-output method at
    # once).
    if _provided_by:
        _vo_skip = _vo_skip | {_c_fn}

    # 2. Update config  (was step 3)
    method_entry: dict = {
        "name": method_name,
        "arg_type": arg_type,
        "return_type": return_type,
    }
    if doc:
        method_entry["doc"] = doc
    if codec:
        # gh-554: a codec-pack method — jm generates the whole binding from the
        # [codec.X] table and calls the external sink_fn; arg_type/return_type
        # above are inert placeholders (a codec method has neither).
        method_entry["codec"] = codec
        method_entry["sink_fn"] = sink_fn
    if varargs:
        method_entry["varargs"] = True
    if manual_stub:
        method_entry["manual_stub"] = True
    if params:
        # Params were normalised to dicts on entry (gh-240 default carried
        # as a key; gh-432 capsule/header/out keys preserved verbatim).
        method_entry["params"] = [dict(p) for p in params]
    if variable_output:
        method_entry["variable_output"] = True
    if pass_capacity:
        method_entry["pass_capacity"] = True
    if exact_max_out:
        method_entry["exact_max_out"] = True
    if count_default:
        method_entry["count_default"] = count_default
    if nogil:
        method_entry["nogil"] = True
    if status_return:
        # gh-432: int return is a status code — binds -> None, ValueError
        # on non-zero.
        method_entry["status_return"] = True
    # gh-805 §A2/§B. Written only when set, so an existing manifest is
    # byte-identical and `jm status --check` stays quiet on every project
    # that does not use them.
    if fn:
        method_entry["fn"] = fn
    if error_negative:
        method_entry["error_negative"] = True
    if error:
        method_entry["error"] = error
    if error_message:
        method_entry["error_message"] = error_message
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
    if single:
        method_entry["single"] = True
    if record_name:
        # gh-257: a chosen public name for the single-record structseq,
        # independent of the C return-type derivation.
        method_entry["record_name"] = record_name
    if record_module:
        # gh-261: module qualifier for the structseq's __module__, so a record's
        # repr matches the project's import path rather than the C component.
        method_entry["record_module"] = record_module
    if record_doc:
        # gh-646: the record type's own documentation — reaches PyStructSequence
        # _Desc.doc (so `help(ToneMetrics)` is not empty) and the record class's
        # .pyi docstring. Without it both faces fall back to the CPython-style
        # `ToneMetrics(enob, sfdr)` synopsis, never to nothing.
        method_entry["record_doc"] = record_doc
    if record_dtype:
        # gh-788: the POD C struct whose layout becomes the returned numpy
        # array's dtype. Paired with `result_fields`, which names its members.
        method_entry["record_dtype"] = record_dtype
    if py_return_type:
        method_entry["py_return_type"] = py_return_type
    if max_out > 0:
        method_entry["max_out"] = max_out

    # gh-504: a view ADD stores the method on the view (its C stub still lands
    # in the shared core above, so the view's wrapper can call it); a normal
    # method stores on the object.
    if view_entry is not None:
        C.add_view_method(cfg, object_name, view, method_entry)
    else:
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
            core_h_,
            object_name,
            proto_lines,
            skip_names=_vo_skip,
            doc_members=_doc_members,
        ):
            print(f"  update  {core_h_}")
    else:
        # Standalone: regenerate _core.h (adds method_decls) + _ext.c

        # gh-486: one assembly chain. This was an inline copy of _glue's and
        # drifted the same way _remove's did — it never learned to rebuild
        # pyi_examples with the real package, so every `jm method` regenerated
        # the stub's doctest as `>>> from <<package>> import <<Component>>`
        # (the gh-481 bug, which only the _glue-backed paths got fixed).
        # Seed the header's create() Doxygen so the shared chain enriches the
        # class docstring from a hand-written @brief/@param instead of reverting
        # it to the generic stub on every `jm method` (see regenerate_standalone).
        from ._object import _load_doc_blocks

        cfg.setdefault(object_name, {})["_doc_blocks"] = _load_doc_blocks(
            root, object_name
        )
        ctx = _glue.component_ctx(cfg, object_name, pkg, root)

        # The only slot this command needs beyond the shared base:
        # extra_ext_sources is the space-prefixed list of varargs binding .c
        # files compiled into the Python DSO target (not the pure-C OBJECT
        # lib). make_methods_ctx already put varargs_binding_files in the ctx
        # — it is a list, so render() skips it as a non-str value.
        ctx["extra_ext_sources"] = "".join(
            f" {f}" for f in ctx.get("varargs_binding_files", [])
        )

        # gh-543: keep a hand-written extra wired through a method add.
        ctx["extra_include"] = standalone_extra_include(root, object_name)

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
            core_h,
            object_name,
            proto_lines,
            skip_names=_vo_skip,
            doc_members=_doc_members,
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
            old_pyi = pyi_path.read_text(encoding="utf-8")
            new_pyi = R.render_component_pyi(ctx)
            # gh-428: preserve any manual_stub method's hand-written text
            # across the otherwise-blind regen above.
            pyi_path.write_text(
                S._splice_manual_stub_bodies(
                    cfg, old_pyi, new_pyi, path=pyi_path
                ),
                encoding="utf-8",
            )
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
    if manual_stub:
        print(
            f"Done!  Hand-write {method_name}()'s signature/docstring "
            f"in the .pyi placeholder — jm emits no C-side code for it."
        )
    elif varargs:
        print(
            f"Done!  Implement {object_name}_{method_name}()"
            f" in {object_name}_{method_name}_core.c"
        )
    else:
        # gh-805 §A2: name the symbol actually written. Pointing the author at
        # a function the file does not contain is a small lie that costs a
        # grep on the one path where the two names differ.
        print(
            f"Done!  Implement {fn or f'{object_name}_{method_name}'}()"
            f" in {core_c.name}"
        )
