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

import sys
from pathlib import Path

from . import _config as C
from . import _templates as T
from ._init import _make_component_ctx, _to_title
from ._object import _make_object_ctx, _regenerate_module


def run(
    root: Path,
    object_name: str,
    prop_name: str,
    module: str | None,
    ctype: str,
    writable: bool,
    field: bool = False,
) -> None:
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    if ctype not in T._CTYPE_META:
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
    if writable:
        prop_entry["writable"] = True
    if field:
        prop_entry["field"] = True
    C.add_property(cfg, object_name, prop_entry)
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    # Regenerate ext.c (and core.h for field-backed properties)
    if module:
        _regenerate_module(root, cfg, module, pkg)
        # _regenerate_module only writes ext.c; field-backed properties also
        # add a struct field to _core.h, so regenerate that too.
        if field:
            state_vars_list = C.state_vars(cfg, object_name)
            arg_type_ = C.arg_type(cfg, object_name)
            return_type_ = C.return_type(cfg, object_name)
            perf = C.is_perf(cfg)
            Component = _to_title(object_name)
            ctx = _make_object_ctx(
                object_name, module, pkg,
                C.project_version(cfg),
                state_vars_list, arg_type_, return_type_,
                perf=perf,
                array_args=C.array_args(cfg, object_name),
                no_state=C.is_no_state(cfg, object_name),
                no_step=C.is_no_step(cfg, object_name),
            )
            ctx.update(T.make_methods_ctx(object_name, Component,
                                          C.methods(cfg, object_name),
                                          pkg=pkg,
                                          py_create_args=ctx.get("py_create_args", "")))
            ctx.update(T.make_properties_ctx(object_name, Component,
                                             C.properties(cfg, object_name),
                                             frozenset(n for n, _, _ in state_vars_list)))
            core_h = root / "native" / "inc" / object_name / f"{object_name}_core.h"
            if core_h.exists():
                core_h.write_text(T.render(T.COMPONENT_CORE_H, ctx), encoding="utf-8")
                print(f"  update  {core_h}")
    else:
        state_vars_list = C.state_vars(cfg, object_name)
        arg_type_ = C.arg_type(cfg, object_name)
        return_type_ = C.return_type(cfg, object_name)
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
        ctx.update(T.make_state_ctx(object_name, Component, state_vars_list,
                                    array_args=C.array_args(cfg, object_name),
                                    no_state=C.is_no_state(cfg, object_name)))
        ctx.update(T.make_perf_ctx(perf))
        ctx.update(T.make_step_ctx(ctx, arg_type_, return_type_,
                                   no_step=C.is_no_step(cfg, object_name)))
        ctx.update(T.make_methods_ctx(object_name, Component, C.methods(cfg, object_name),
                                      pkg=pkg,
                                      py_create_args=ctx.get("py_create_args", "")))
        ctx.update(T.make_properties_ctx(object_name, Component, C.properties(cfg, object_name),
                                         frozenset(n for n, _, _ in state_vars_list)))

        def r(tmpl):
            return T.render(tmpl, ctx)

        core_h = root / "native" / "inc" / object_name / f"{object_name}_core.h"
        ext_c = root / "native" / "src" / object_name / f"{object_name}_ext.c"
        if core_h.exists():
            core_h.write_text(r(T.COMPONENT_CORE_H), encoding="utf-8")
            print(f"  update  {core_h}")
        if ext_c.exists():
            ext_c.write_text(r(T.COMPONENT_EXT_C), encoding="utf-8")
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
