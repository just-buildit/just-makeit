"""
_property.py — `just-makeit property` command.

Adds a read-only (or read-write) Python property to an existing object:

    just-makeit property nco phase --module dsp --type uint32_t
    just-makeit property buffer dropped --type size_t              # standalone

Properties use PyGetSetDef (tp_getset) and do NOT add C state — they expose
computed or internally-managed values. The getter stub calls:

    <<component>>_get_<<prop>>(self->handle)

which the user implements however they like (read from state, compute, etc.).
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
    _inject_struct_field,
    _make_component_ctx,
    _to_title,
)
from ._object import _regenerate_module


def run(
    root: Path,
    object_name: str,
    prop_name: str,
    module: str | None,
    ctype: str,
    writable: bool,
    field: bool = False,
    buf_field: str = "",
    len_field: str = "n",
    valid_field: str = "",
    expr: str = "",
    doc: str = "",
) -> None:
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not buf_field and not expr and ctype not in T._CTYPE_META:
        supported = ", ".join(sorted(T._CTYPE_META))
        print(
            f"error: unsupported --type '{ctype}'.\nSupported: {supported}",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)

    # Resolve component
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

    # Check for duplicate property name
    existing = [p["name"] for p in C.properties(cfg, object_name)]
    if prop_name in existing:
        print(
            f"error: property '{prop_name}' already exists on '{object_name}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = C.project_name(cfg)
    Component = _to_title(object_name)

    print(
        f"just-makeit: adding property '{prop_name}' to '{object_name}'"
        + (f" in module '{module}'" if module else "")
    )
    print()

    # Update config
    prop_entry: dict = {"name": prop_name, "type": ctype}
    if doc:
        prop_entry["doc"] = doc
    if writable:
        prop_entry["writable"] = True
    if field:
        prop_entry["field"] = True
    if buf_field:
        prop_entry["buf_field"] = buf_field
        prop_entry["len_field"] = len_field
    if valid_field:
        prop_entry["valid_field"] = valid_field
    if expr:
        prop_entry["expr"] = expr
    C.add_property(cfg, object_name, prop_entry)
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    # Every property kind is purely additive to _core.h — no re-render, no
    # splice:
    #   field-backed  -> a new <obj>_state_t struct member (set via the setter,
    #                    so it needs no create/reset wiring; injected directly).
    #   computed      -> a getter/setter declaration the user implements in the
    #                    sacred _core.c.
    #   buf/expr      -> pure glue; their accessors are inlined into _ext.c, so
    #                    nothing is added to _core.h.
    core_h = root / "native" / "inc" / object_name / f"{object_name}_core.h"
    if field:
        disp = T._ctype_display(ctype)
        if _inject_struct_field(core_h, object_name, f"{disp} {prop_name};"):
            print(f"  update  {core_h}")
    elif not buf_field and not expr:
        disp = T._ctype_display(ctype)
        decls = [
            f"{disp} {object_name}_get_{prop_name}"
            f"(const {object_name}_state_t *state);"
        ]
        if writable:
            decls.append(
                f"void {object_name}_set_{prop_name}"
                f"({object_name}_state_t *state, {disp} val);"
            )
        if _inject_decls_into_core_h(core_h, object_name, decls):
            print(f"  update  {core_h}")

    # Regenerate the glue (Python getset descriptor + binding).  Module objects
    # share one _ext.c (rebuilt by _regenerate_module); standalone objects own
    # their _ext.c.
    if module:
        _regenerate_module(root, cfg, module, pkg)
    else:
        state_vars_list = C.state_vars(cfg, object_name)
        arg_type_ = C.arg_type(cfg, object_name)
        return_type_ = C.return_type(cfg, object_name)
        ctx = _make_component_ctx(object_name)
        ctx.update(
            {
                "package": pkg,
                "PACKAGE": pkg.upper(),
                "project": pkg.replace("_", "-"),
                "project_underscore": pkg,
                "version": C.project_version(cfg),
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
        ctx.update(Ctx.make_perf_ctx(C.is_perf(cfg)))
        ctx.update(
            Ctx.make_step_ctx(
                ctx,
                arg_type_,
                return_type_,
                no_step=C.is_no_step(cfg, object_name),
            )
        )
        ctx.update(
            Ctx.make_methods_ctx(
                object_name,
                Component,
                C.methods(cfg, object_name),
                pkg=pkg,
                py_create_args=ctx.get("py_create_args", ""),
                no_state=C.is_no_state(cfg, object_name),
            )
        )
        ctx.update(
            Ctx.make_properties_ctx(
                object_name,
                Component,
                C.properties(cfg, object_name),
                frozenset(n for n, _, _ in state_vars_list),
            )
        )
        # Preserve the stream generator (gh-201) across `jm property`.
        ctx.update(
            Ctx.make_stream_ctx(
                object_name,
                Component,
                ctx["ComponentW"],
                streamable=C.is_streamable(cfg, object_name),
                methods=C.methods(cfg, object_name),
                arg_type=arg_type_,
                return_type=return_type_,
                default_block=C.stream_block_default(cfg, object_name),
            )
        )
        ext_c = root / "native" / "src" / object_name / f"{object_name}_ext.c"
        if ext_c.exists():
            ext_c.write_text(
                R.render(R.COMPONENT_EXT_C, ctx), encoding="utf-8"
            )
            print(f"  update  {ext_c}")

    print()
    rw = "read/write" if writable else "read-only"
    if field:
        print(
            f"Done!  Struct field '{prop_name}' added to"
            f" {object_name}_state_t; getter/setter auto-implemented.  [{rw}]"
        )
    else:
        print(
            f"Done!  Implement {object_name}_get_{prop_name}() in"
            f" native/src/{object_name}/{object_name}_core.c"
            f"  [{rw}]"
        )
