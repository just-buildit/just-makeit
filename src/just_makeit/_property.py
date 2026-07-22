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
from . import _glue
from . import _types as T
from ._init import (
    _inject_decls_into_core_h,
    _inject_struct_field,
)


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
    view: str = "",
    enum: str = "",
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

    # gh-519: an `enum` property presents its C int as the [[enum]] string on
    # the Python side. Validate the name *before* the manifest is written, so
    # a typo is a jm diagnostic rather than an undeclared `_enum_<typo>`
    # identifier surfacing in the user's compiler with the TOML already dirty.
    if enum:
        known = C.enums(cfg)
        if enum not in known:
            names = ", ".join(sorted(known)) or "(none declared)"
            print(
                f"error: unknown enum '{enum}'. Declare it as a top-level "
                f"[[enum]] with that name.\nKnown enums: {names}",
                file=sys.stderr,
            )
            sys.exit(1)
        if buf_field:
            print(
                "error: --enum and --buf-field are mutually exclusive — an "
                "array of enum strings has no decoded form.",
                file=sys.stderr,
            )
            sys.exit(1)

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

    # gh-504: --view retargets the property onto a VIEW of the object (a view
    # can ADD a property the parent lacks, or OVERRIDE a parent property of the
    # same name). Views are module-only.
    view_entry = None
    if view:
        if not module:
            print(
                "error: --view requires --module (views are a module-object "
                "feature).",
                file=sys.stderr,
            )
            sys.exit(1)
        view_entry = C._find_view(cfg, object_name, view)
        if view_entry is None:
            print(
                f"error: no view '{view}' on object '{object_name}'. "
                f"Create it first with 'just-makeit view {object_name} {view} "
                f"--module {module} --create-fn <fn>'.",
                file=sys.stderr,
            )
            sys.exit(1)
        if prop_name in C.view_exclude_properties(view_entry):
            print(
                f"error: property '{prop_name}' is both excluded and added on "
                f"view '{view}' — that is contradictory. Drop it from "
                f"--exclude-property, or don't add it.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Check for duplicate property name. For a view, dup-check against the
    # VIEW's own properties: an override intentionally shares a parent name, so
    # only a collision with the view's own list is the error.
    if view_entry is not None:
        existing = [p["name"] for p in C.view_properties(view_entry)]
        target = f"view '{view}'"
    else:
        existing = [p["name"] for p in C.properties(cfg, object_name)]
        target = f"'{object_name}'"
    if prop_name in existing:
        print(
            f"error: property '{prop_name}' already exists on {target}.",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = C.project_name(cfg)

    print(
        f"just-makeit: adding property '{prop_name}' to {target}"
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
    if enum:
        prop_entry["enum"] = enum
    if view_entry is not None:
        C.add_view_property(cfg, object_name, view, prop_entry)
    else:
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
    # their _ext.c. The assembly chain lives in _glue so `jm warning` and any
    # future manifest-driven command render from the identical context (gh-446
    # was a divergence between two copies of it).
    _glue.regenerate(root, cfg, object_name, module, pkg)

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
