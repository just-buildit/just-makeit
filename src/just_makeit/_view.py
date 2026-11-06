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

The view's class may live in another object module than its parent's
(gh-2516): ``--module`` names where the class lives, recorded as the entry's
``module`` key when it differs from the parent's. That module's aggregator
includes the view's fragment, its stub declares the class, its
``__init__.py`` re-exports it and its extension links the parent's core;
the C core, and the ``create_fn`` stub, stay the parent's.
``_config.view_module`` is the one answer to where a view lives.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import _config as C
from . import _context as Ctx
from . import _incpath as INC
from . import _csym as CSYM
from . import _ctorsig
from ._init import _inject_decls_into_core_h
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
    create_error: str | None = None,
    create_error_message: str | None = None,
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
    # registers the extra class; a standalone object's single-type .so does
    # not. The parent is found by name -- an object belongs to at most one
    # module -- and `--module` names where the VIEW's class lives (gh-2516):
    # the parent's module, or another object module. Every invocation
    # written before gh-2516 named the parent's, so it means what it always
    # did.
    if not module:
        _fail("'view' requires --module (views are a module-object feature).")
    parent_module = C.module_of(cfg, object_name)
    if not parent_module:
        _fail(f"object '{object_name}' not found in module '{module}'.")
    if module not in C.modules(cfg):
        _fail(
            f"module '{module}' not found (the view's class lives in a "
            f"declared module). Known: {sorted(C.modules(cfg))}"
        )
    if module != parent_module and (
        C.module_kind(cfg, module) or C.is_no_generate_module(cfg, module)
    ):
        _fail(
            f"module '{module}' is a `kind` or `no_generate` module; a view "
            "registers its class in an object module's generated aggregator."
        )
    mod_objs = C.module_objects(cfg, module)

    # The view's class name must be unique among every class the module
    # already exposes (each object's class_name and every view it registers,
    # its own objects' and other modules' alike, gh-2516) -- they collide on
    # the PyModule_AddObject key and the generated C symbols otherwise.
    taken: set[str] = set()
    for o in mod_objs:
        taken.add(C.resolved_class_name(cfg, o))
    for _parent, v in C.module_views(cfg, module):
        taken.add(v["class_name"])
    if class_name in taken:
        _fail(
            f"class name '{class_name}' is already used in module "
            f"'{module}'. Pick a distinct name."
        )
    # gh-2516: and among the parent's own views, wherever they live. A
    # fragment is tied back to its view by the lowercased class name among
    # the parent's views (`_view_frag_id`), so two of one parent's views in
    # different modules must not share it.
    for v in C.views(cfg, object_name):
        if v["class_name"].lower() == class_name.lower():
            _fail(
                f"'{object_name}' already has a view '{v['class_name']}' "
                f"(in module '{C.view_module(cfg, object_name, v)}'). Pick "
                "a distinct name."
            )

    if not create_fn:
        _fail("--create-fn is required (the view's C constructor).")
    # The parent's ACTUAL constructor, not the default-derived name. gh-509
    # lets an object back its `tp_init` with a differently named C function
    # (`acq_create_continuous`, where `acq_create` does not exist), and this
    # check never learned about it: it compared against `<obj>_create` while
    # its own message claimed to be comparing against the parent's. Under an
    # override that made `<obj>_create` the one name a view could not use,
    # even though it was no longer the parent's -- which blocks the shape
    # where the general constructor is the base and the specialised one is
    # the flavor (gh-1277).
    parent_create = C.object_create_name(cfg, object_name)
    if create_fn == parent_create:
        _fail(
            f"--create-fn must differ from the parent's "
            f"'{parent_create}' — a view exists to build from a "
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
    view_entry: dict = {"class_name": class_name}
    # gh-2516: recorded only when the class lives away from its parent, so a
    # same-module view's manifest entry is exactly what it always was.
    if module != parent_module:
        view_entry["module"] = module
    view_entry["create_fn"] = create_fn
    if doc:
        view_entry["doc"] = doc
    if ip_dicts:
        view_entry["init_params"] = ip_dicts
    if exclude_properties:
        view_entry["exclude_properties"] = exclude_properties
    if exclude_methods:
        view_entry["exclude_methods"] = exclude_methods
    # gh-1017: the view's OWN create()-failure translation (gh-580). `None`
    # means undeclared, and an undeclared view INHERITS the parent's at render
    # time — so an explicit "" (the deliberate opt-out back to MemoryError)
    # must stay distinguishable from absence, which a falsy check would lose.
    # Only `jm apply`'s replay passes these; the CLI verb is `jm error --view`.
    if create_error is not None:
        view_entry["create_error"] = create_error
    if create_error_message is not None:
        view_entry["create_error_message"] = create_error_message

    state_vars = C.state_vars(cfg, object_name)
    view_ip = C.view_init_params(cfg, object_name, view_entry)
    # gh-1591: the view constructs the PARENT's state, named by its stem.
    csym = CSYM.stem(cfg, object_name)
    vctx = Ctx.make_state_ctx(
        object_name,
        class_name,
        state_vars,
        init_params=view_ip,
        create_fn=create_fn,
        csym=csym,
        # gh-1882: the parent's reset(); only `create_params` is read here.
        reset_impl=bool(Ctx.declared_reset_impl(cfg, object_name)),
    )
    create_params = vctx["create_params"]

    core_h = INC.core_h(root, object_name, cfg)
    proto = f"{csym}_state_t *{create_fn}({create_params});"
    if _inject_decls_into_core_h(
        core_h, object_name, [proto], family=C.core_family(cfg, object_name)
    ):
        print(f"  update  {core_h}")

    core_c = INC.src_path(root, object_name, cfg) / f"{object_name}_core.c"
    if core_c.exists():
        core_text = core_c.read_text(encoding="utf-8")
        # Idempotent: _append_to_core_c blindly appends, so only add the stub
        # when this constructor is not already defined (re-run / apply replay).
        if not re.search(r"\b" + re.escape(create_fn) + r"\s*\(", core_text):
            # gh-1857: every parameter waits for the author, and says so.
            unread = _ctorsig.suppress_unread(create_params)
            stub = (
                f"{csym}_state_t *\n"
                f"{create_fn}({create_params})\n"
                f"{{\n"
                f"    /* <<IMPLEMENT>>: build the state for the "
                f"{class_name} view. */\n"
                f"{unread + chr(10) if unread else ''}"
                f"    return NULL;\n"
                f"}}\n"
            )
            _append_to_core_c(core_c, stub)

    C.add_view(cfg, object_name, view_entry)
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    # The parent's module: it regenerates every module registering one of
    # its objects' views, this one included (gh-2516).
    _regenerate_module(root, cfg, parent_module, pkg)
