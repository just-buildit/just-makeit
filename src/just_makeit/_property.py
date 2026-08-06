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
from ._context._methods import (
    container_fn_names,
    validate_container_property,
)
from ._init import (
    _inject_decls_into_core_h,
    _inject_struct_field,
)


def plain_accessor_decls(
    object_name: str, prop_name: str, ctype: str, writable: bool
) -> list[str]:
    """Prototypes for a plain getter/setter-backed property.

    The declarations a ``[[<obj>.properties]]`` entry adds to the public C API
    when nothing else backs it — no ``field``, ``expr``, ``buf_field`` or
    container/codec, each of which is declared differently (or by the user).

    Shared by :func:`run` and ``_apply`` (gh-627): apply has to inject the
    same prototypes when a property arrives by manifest rather than by CLI, and
    two copies of a signature rule is how they drift.
    """
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
    return decls


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
    value_type: str = "",
    count_fn: str = "",
    key_fn: str = "",
    value_fn: str = "",
    codec: str = "",
    entry_fn: str = "",
    entry_type: str = "",
    type_field: str = "",
    count_field: str = "",
    value_field: str = "",
) -> None:
    # gh-625: `jm object` and `jm function` rejected this and these two did
    # not, so `jm property thing level:double` — the shape muscle memory
    # produces, since every --state/--param flag is colon-delimited — wrote
    # `level:double` into the sacred header and exited 0.
    C.require_name(prop_name, "property")
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # gh-543: a container property's `type` names a Python container, not a C
    # type, so it is deliberately absent from _CTYPE_META.
    container = T.is_container_type(ctype)
    if (
        not container
        and not buf_field
        and not expr
        and ctype not in T._CTYPE_META
    ):
        supported = ", ".join(sorted(T._CTYPE_META) + list(T.CONTAINER_KINDS))
        print(
            f"error: unsupported --type '{ctype}'.\nSupported: {supported}",
            file=sys.stderr,
        )
        sys.exit(1)

    # gh-543: the container accessors are only meaningful on a container, and
    # a silently-ignored flag is the foot-gun this project keeps paying for.
    if not container:
        for flag, val in (
            ("--value-type", value_type),
            ("--count-fn", count_fn),
            ("--key-fn", key_fn),
            ("--value-fn", value_fn),
        ):
            if val:
                kinds = ", ".join(T.CONTAINER_KINDS)
                print(
                    f"error: {flag} applies only to a container property. "
                    f"Pass --type with one of: {kinds}",
                    file=sys.stderr,
                )
                sys.exit(1)

    # gh-543: reject an incoherent container declaration before anything is
    # printed or written, for the same reason the enum check below runs early.
    if container:
        try:
            validate_container_property(
                object_name,
                {
                    "name": prop_name,
                    "type": ctype,
                    "value_type": value_type,
                    "key_fn": key_fn,
                    "writable": writable,
                    "field": field,
                    "buf_field": buf_field,
                    "expr": expr,
                    "enum": enum,
                },
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
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
    if container:
        # Only record what was actually asked for; the render layer supplies
        # the defaults, so an unspecified accessor stays unspecified in the
        # manifest rather than freezing today's naming into the project.
        if value_type:
            prop_entry["value_type"] = value_type
        for key, val in (
            ("count_fn", count_fn),
            ("key_fn", key_fn),
            ("value_fn", value_fn),
            # gh-554: a codec property decodes via a generated helper over an
            # entry_fn cursor — no hand value_fn. These carry the codec ref and
            # the entry struct's field names.
            ("codec", codec),
            ("entry_fn", entry_fn),
            ("entry_type", entry_type),
            ("type_field", type_field),
            ("count_field", count_field),
            ("value_field", value_field),
        ):
            if val:
                prop_entry[key] = val
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
    #   container     -> the plain-C accessors (count, and key for a dict, and
    #                    value unless it returns a PyObject *). A PyObject *
    #                    value_fn needs Python.h and so is forward-declared in
    #                    the glue instead -- see _methods._container_getter.
    core_h = root / "native" / "inc" / object_name / f"{object_name}_core.h"
    if container:
        fns = container_fn_names(object_name, prop_name, prop_entry)
        state_t = f"const {object_name}_state_t *"
        decls = [f"size_t {fns['count_fn']}({state_t}state);"]
        if ctype == "dict":
            decls.append(
                f"const char *{fns['key_fn']}({state_t}state, size_t i);"
            )
        if codec:
            # gh-554: a codec property decodes an entry_fn cursor. jm does NOT
            # declare entry_fn or its struct (the read mirror of the write
            # side's undeclared sink_fn) — the user declares both in _core.h (or
            # a `header` the property names), so jm injects only count_fn/key_fn.
            pass
        else:
            vtype = value_type or T.OBJECT_VALUE_TYPE
            if vtype != T.OBJECT_VALUE_TYPE:
                vdisp = T._ctype_display(vtype)
                if not vdisp.endswith("*"):
                    vdisp += " "
                decls.append(
                    f"{vdisp}{fns['value_fn']}({state_t}state, size_t i);"
                )
        if _inject_decls_into_core_h(core_h, object_name, decls):
            print(f"  update  {core_h}")
    elif field:
        disp = T._ctype_display(ctype)
        if _inject_struct_field(core_h, object_name, f"{disp} {prop_name};"):
            print(f"  update  {core_h}")
    elif not buf_field and not expr:
        decls = plain_accessor_decls(object_name, prop_name, ctype, writable)
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
    if container and codec:
        fns = container_fn_names(object_name, prop_name, prop_entry)
        e_fn = entry_fn or f"{object_name}_{prop_name}_entry"
        core_c = f"native/src/{object_name}/{object_name}_core.c"
        todo = [fns["count_fn"]]
        if ctype == "dict":
            todo.append(fns["key_fn"])
        todo.append(e_fn)
        print(
            f"Done!  Implement {', '.join(f'{n}()' for n in todo)} in"
            f" {core_c};\n"
            f"       jm generates the '{codec}' decode over {e_fn}()."
            f"  [{ctype}, {rw}]"
        )
    elif container:
        fns = container_fn_names(object_name, prop_name, prop_entry)
        todo = [fns["count_fn"]]
        if ctype == "dict":
            todo.append(fns["key_fn"])
        vtype = value_type or T.OBJECT_VALUE_TYPE
        core_c = f"native/src/{object_name}/{object_name}_core.c"
        if vtype == T.OBJECT_VALUE_TYPE:
            print(
                f"Done!  Implement {', '.join(f'{n}()' for n in todo)} in"
                f" {core_c},\n"
                f"       and {fns['value_fn']}() -- which returns a"
                f" PyObject * and so needs\n"
                f"       Python.h -- in a hand-written"
                f" {object_name}_ext_extra.c alongside the\n"
                f"       generated binding.  [{ctype}, {rw}]"
            )
        else:
            todo.append(fns["value_fn"])
            print(
                f"Done!  Implement {', '.join(f'{n}()' for n in todo)}\n"
                f"       in {core_c}  [{ctype}, {rw}]"
            )
    elif field:
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
