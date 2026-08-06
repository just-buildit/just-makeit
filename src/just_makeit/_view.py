"""_view.py — `just-makeit view`: a second Python class over one C core (gh-504).

A *view* exposes a second Python class (its ``class_name``) over an existing
module object's generated C core: it shares ``<component>_state_t`` and the
parent's ``_core.c``, differing only in its C constructor (``create_fn``), its
own ``init_params``, and an optionally-trimmed property surface
(``exclude_properties``). The parent's methods are shared as-is (v1).

This generator persists a ``[[<obj>.views]]`` entry, scaffolds a stub for the
view's ``create_fn`` into the sacred core so the module still compiles out of
the box (the user fills in the alternate constructor's body), and regenerates
the module glue. Views are a module-object feature only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import _config as C
from . import _context as Ctx
from ._init import _inject_decls_into_core_h, _to_title
from ._method import _append_to_core_c
from ._object import _regenerate_module


def _fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(
    root: Path,
    object_name: str,
    class_name: str,
    module: str | None,
    create_fn: str,
    init_params: list | None = None,
    exclude_properties: list[str] | None = None,
    exclude_methods: list[str] | None = None,
    doc: str = "",
    from_apply: bool = False,
) -> None:
    # gh-625's audit: a view's class name is not just a Python identifier —
    # `_view_frag_id` lowercases it into the fragment's filename (gh-504), so
    # an invalid one lands in a path as well as in the header and the stub.
    _msg = C.validate_name(class_name, "view class")
    if _msg:
        _fail(_msg)
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        _fail(
            f"no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first."
        )

    cfg = C.load(root)

    # Views are a module-object feature (v1): the multi-type module machinery
    # registers the extra class; a standalone object's single-type .so does not.
    if not module:
        _fail("'view' requires --module (views are a module-object feature).")
    mod_objs = C.module_objects(cfg, module)
    if object_name not in mod_objs:
        _fail(f"object '{object_name}' not found in module '{module}'.")

    # The view's class name must be unique among every class the module already
    # exposes (each object's class_name and every existing view) — they collide
    # on the PyModule_AddObject key and the generated C symbols otherwise.
    taken: set[str] = set()
    for o in mod_objs:
        taken.add(C.class_name(cfg, o) or _to_title(o))
        for v in C.views(cfg, o):
            taken.add(v["class_name"])
    if class_name in taken:
        _fail(
            f"class name '{class_name}' is already used in module "
            f"'{module}'. Pick a distinct name."
        )

    if not create_fn:
        _fail("--create-fn is required (the view's C constructor).")
    if create_fn == f"{object_name}_create":
        _fail(
            f"--create-fn must differ from the parent's "
            f"'{object_name}_create' — a view exists to build from a "
            f"different constructor."
        )

    # v1 does not support a view over a parent that uses per-array-arg
    # dtype-dispatch or optional-array init-params: those paths embed
    # `<component>_create` directly in the arg-parse block and blank
    # create_line, so the view's create_fn would be silently ignored.
    for p in C.init_params(cfg, object_name):
        # (name, type, default, default_raw, real_type, real_create_fn,
        #  optional, create_fn, required, doc)
        if p[4] or p[5] or p[6] or p[7]:
            _fail(
                f"object '{object_name}' uses dtype-dispatch / optional-array "
                f"init-params; views over such objects are not supported yet."
            )

    prop_names = {p["name"] for p in C.properties(cfg, object_name)}
    exclude_properties = list(exclude_properties or [])
    for ep in exclude_properties:
        if ep not in prop_names:
            _fail(
                f"--exclude-property '{ep}' is not a property of "
                f"'{object_name}'. Known: {sorted(prop_names)}"
            )

    method_names = {m["name"] for m in C.methods(cfg, object_name)}
    exclude_methods = list(exclude_methods or [])
    for em in exclude_methods:
        if em not in method_names:
            _fail(
                f"--exclude-method '{em}' is not a method of "
                f"'{object_name}'. Known: {sorted(method_names)}"
            )

    # Normalise init_params (CLI tuples or apply-replay dicts) to stored dicts.
    ip_dicts: list[dict] = []
    for p in init_params or []:
        if isinstance(p, dict):
            ip_dicts.append(dict(p))
        else:
            ip_dicts.append(C.init_param_tuple_to_dict(p))

    pkg = C.project_name(cfg)
    print(
        f"just-makeit: adding view '{class_name}' over '{object_name}' "
        f"in module '{module}'"
    )
    print()

    # Scaffold the view's constructor into the sacred core so the module still
    # compiles out of the box. The signature is derived from the view's own
    # init_params (falling back to the parent's), exactly as the parent's
    # <comp>_create() signature is; the body is an IMPLEMENT stub the user
    # fills in with the alternate construction.
    view_entry: dict = {"class_name": class_name, "create_fn": create_fn}
    if doc:
        view_entry["doc"] = doc
    if ip_dicts:
        view_entry["init_params"] = ip_dicts
    if exclude_properties:
        view_entry["exclude_properties"] = exclude_properties
    if exclude_methods:
        view_entry["exclude_methods"] = exclude_methods

    state_vars = C.state_vars(cfg, object_name)
    view_ip = C.view_init_params(cfg, object_name, view_entry)
    vctx = Ctx.make_state_ctx(
        object_name,
        class_name,
        state_vars,
        init_params=view_ip,
        create_fn=create_fn,
    )
    create_params = vctx["create_params"]

    core_h = root / "native" / "inc" / object_name / f"{object_name}_core.h"
    proto = f"{object_name}_state_t *{create_fn}({create_params});"
    if _inject_decls_into_core_h(core_h, object_name, [proto]):
        print(f"  update  {core_h}")

    core_c = root / "native" / "src" / object_name / f"{object_name}_core.c"
    if core_c.exists():
        core_text = core_c.read_text(encoding="utf-8")
        # Idempotent: _append_to_core_c blindly appends, so only add the stub
        # when this constructor is not already defined (re-run / apply replay).
        if not re.search(r"\b" + re.escape(create_fn) + r"\s*\(", core_text):
            stub = (
                f"{object_name}_state_t *\n"
                f"{create_fn}({create_params})\n"
                f"{{\n"
                f"    /* <<IMPLEMENT>>: build the state for the "
                f"{class_name} view. */\n"
                f"    return NULL;\n"
                f"}}\n"
            )
            _append_to_core_c(core_c, stub)

    C.add_view(cfg, object_name, view_entry)
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    _regenerate_module(root, cfg, module, pkg)
