"""_glue.py — regenerate a component's Python glue from the manifest.

The ``_ext.c`` binding and the ``.pyi`` stub are *derived*: every fact needed
to rebuild them lives in ``just-makeit.toml``. Any command that changes one of
those facts has to re-render both, and before this module each such command
carried its own copy of the assembly chain.

That duplication is the known failure mode in this codebase, not a
hypothetical one: gh-446 was a real bug where one renderer of the property
stubs learned something the other never did, and the two silently diverged.
Adding a third copy for ``jm warning`` (gh-481) would have been the same trap,
so the chain lives here once and its callers pass a manifest.

Sacred files are never touched here — ``_core.h``/``_core.c`` hold
hand-written algorithm code and are only ever spliced, never re-rendered.
"""

from __future__ import annotations

from pathlib import Path

from . import _config as C
from ._context._parse import _build_ml_doc
from ._docstring import authored_class_brief
from . import _context as Ctx
from . import _render as R
from . import _stubs as S
from . import _types as T
from ._builtins import builtin_owned_members, overridden_builtin_slots
from ._init import (
    _make_component_ctx,
    _to_title,
    standalone_extra_include,
)


def component_ctx(
    cfg: dict, object_name: str, pkg: str, root: "Path | None" = None
) -> dict:
    """Assemble the full render context for a component, from the manifest.

    Every ``make_*_ctx`` builder that feeds ``COMPONENT_EXT_C`` /
    ``COMPONENT_PYI`` is chained here in dependency order — ``make_step_ctx``
    and the diagnostics builders read slots the earlier ones produced, so the
    order matters.

    Parameters
    ----------
    cfg : dict
        The loaded manifest. Sole source of truth; nothing is read from disk.
    object_name : str
        Component id, e.g. ``acq``.
    pkg : str
        Python package name, from ``[project] name``.
    root : Path or None
        Project root, when the caller has one. The single exception to the
        manifest-only contract above, and it is here because gh-994's question
        — did the built-in or the declared method end up owning
        ``<comp>_reset`` — is settled by what was *written* into a create-only
        file, not by anything the manifest records. Omitted, no member is
        treated as built-in-owned, which is the behaviour every project
        without a colliding method name has anyway.

    Returns
    -------
    dict
        Context ready for ``R.render()``. Every slot the templates reference
        is present, including the ones that resolve empty.
    """
    Component = _to_title(object_name)
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
            # gh-1066: everything below reaches the SACRED HEADER through
            # `_object.run` and reached this render through nothing, so a
            # re-render dropped it. The header kept `create(a)` while the
            # binding it is compiled against went back to `create(a, derived)`
            # -- two files jm owns, disagreeing, and `jm status --check`
            # reporting `OK` because each matches its own re-render.
            #
            # Only reachable via a command that re-renders glue without
            # re-running the object scaffold: `jm error`, `jm warning`,
            # `jm property`, `jm view`. That is why a plain
            # scaffold-then-edit-then-apply looked fine and the bug needed a
            # second declaration to show up.
            #
            # Kept as an explicit list rather than a **kwargs splat so that a
            # key added to `make_state_ctx` fails loudly here instead of
            # silently defaulting -- which is the failure this comment is
            # about.
            init_post_parse_impl=C.init_post_parse(cfg, object_name),
            opaque_fields=C.opaque_fields(cfg, object_name),
            opaque_state=C.is_opaque_state(cfg, object_name),
            no_ctor_names=C.no_ctor_names(cfg, object_name),
            create_fn=C.object_create_fn(cfg, object_name),
            # gh-542: the glue render is exactly the pass that used to
            # silently reinstate a hand-removed reset() binding.
            no_reset=C.is_no_reset(cfg, object_name),
            # gh-676/gh-644: the built-ins derive from the header too. Without
            # this the standalone generators never saw doc_blocks at all, so a
            # hand-written @brief on <obj>_reset/_step/_steps reached the
            # module .pyi and nothing else.
            doc_blocks=cfg.get(object_name, {}).get("_doc_blocks", {}),
        )
    )
    ctx.update(Ctx.make_perf_ctx(C.is_perf(cfg)))
    ctx.update(
        Ctx.make_step_ctx(
            ctx,
            arg_type_,
            return_type_,
            no_step=C.is_no_step(cfg, object_name),
            doc_blocks=cfg.get(object_name, {}).get("_doc_blocks", {}),
        )
    )
    # gh-994: computed BEFORE make_methods_ctx, which blanks some of the very
    # slots the comparison reads (gh-131's `builtin_reset_decl`). Applied
    # after, so the built-in's glue goes only once the method's is in place.
    _override_slots = overridden_builtin_slots(
        object_name, C.methods(cfg, object_name), ctx
    )
    ctx.update(
        Ctx.make_methods_ctx(
            object_name,
            Component,
            C.methods(cfg, object_name),
            pkg=pkg,
            py_create_args=ctx.get("py_create_args", ""),
            no_state=C.is_no_state(cfg, object_name),
            serializable=C.is_serializable(cfg, object_name),
            # Enrich standalone method docstrings from the header's Doxygen just
            # like the module path (_object.build_component_ctxs) — seeded on cfg
            # by the root-having callers (regenerate_standalone / jm method /
            # apply); absent -> {} -> the generic name-based stub, unchanged.
            doc_blocks=cfg.get(object_name, {}).get("_doc_blocks", {}),
            codecs=C.codecs(cfg),
            builtin_members=(
                builtin_owned_members(root, cfg, object_name)
                if root is not None
                else frozenset()
            ),
            enums=C.enums(cfg),  # gh-1021
        )
    )
    for _slot in _override_slots:
        ctx[_slot] = ""
    # gh-1021: every [[enum]] this type indexes, emitted ONCE above the three
    # C slots that reference them (a method parameter's lookup lands in
    # `extra_methods_c`, a property getter's in `getset_def`).
    ctx.update(
        Ctx.make_enum_tables_ctx(
            object_name,
            Component,
            C.methods(cfg, object_name),
            C.properties(cfg, object_name),
            enums=C.enums(cfg),
        )
    )
    ctx.update(
        Ctx.make_properties_ctx(
            object_name,
            Component,
            C.properties(cfg, object_name),
            frozenset(n for n, _, _ in state_vars_list),
            # gh-519: the [[enum]] SSOT, so a property's `enum = "<name>"`
            # decodes to its string on the Python side instead of leaking the
            # raw int.
            enums=C.enums(cfg),
            # Enrich standalone property docstrings from the getter's @brief,
            # same as the module path (_object); absent -> generic, unchanged.
            doc_blocks=cfg.get(object_name, {}).get("_doc_blocks", {}),
            codecs=C.codecs(cfg),
        )
    )
    # gh-481: declared warnings. Re-rendered from the manifest on every pass,
    # which is the whole point — a hand-patched PyErr_WarnEx in this file was
    # silently lost the moment anything regenerated it.
    ctx.update(
        Ctx.make_warnings_ctx(
            object_name, Component, C.warnings(cfg, object_name)
        )
    )
    # gh-482: translate this component's create() failure. Undeclared yields
    # the historical MemoryError block, so nothing changes for existing
    # projects.
    ctx.update(
        Ctx.make_errors_ctx(
            object_name,
            C.create_error(cfg, object_name),
            C.create_error_message(cfg, object_name),
            create_fn=C.object_create_fn(cfg, object_name),
        )
    )
    # gh-541/gh-544: the declared destructor contract. Regenerated from the
    # manifest on every pass — the hand-written close()/__exit__ this replaces
    # was silently dropped by exactly this render.
    ctx.update(
        Ctx.make_destroy_ctx(
            object_name,
            ctx["ComponentW"],
            C.destroy_spec(cfg, object_name),
            C.methods(cfg, object_name),
            class_name=C.class_name(cfg, object_name) or "",
        )
    )
    ctx.update(
        Ctx.make_stream_ctx(
            object_name,
            Component,
            ctx["ComponentW"],
            streamable=C.is_streamable(cfg, object_name),
            async_stream=C.is_async_stream(cfg, object_name),
            methods=C.methods(cfg, object_name),
            arg_type=arg_type_,
            return_type=return_type_,
            default_block=C.stream_block_default(cfg, object_name),
        )
    )

    # Re-generate pyi_examples with the real package name. make_state_ctx seeds
    # this slot with <<package>>/<<Component>> placeholders that only _init.run
    # was resolving, so every regenerating command (jm property, and now jm
    # warning) rewrote the stub's doctest to a literal
    # `>>> from <<package>> import <<Component>>`. That went unnoticed because
    # the placeholder scan in tests covers .py/.c/.h/.toml/.txt but not .pyi —
    # the one file it corrupts. Doing it here fixes every caller at once, which
    # is the point of a single assembly chain.
    init_params = C.init_params(cfg, object_name)
    scalar_state = (
        [
            (n, ct, dflt)
            for n, ct, dflt in state_vars_list
            if not T.parse_array_type(ct)
        ]
        if not C.is_no_state(cfg, object_name)
        else []
    )
    # gh-273: suppress the construction doctest when a required init-param has
    # no default — there is no valid seed and a validating ctor would reject
    # the type's zero under `pytest --doctest-glob='*.pyi'`.
    ctx["pyi_examples"] = (
        Ctx._pyi_examples_block(
            scalar_state,
            bool(C.array_args(cfg, object_name)),
            f"from {pkg} import {Component}",
            ctx.get("py_create_args", ""),
            Component,
            no_reset=C.is_no_reset(cfg, object_name),
            init_params=init_params,
        )
        if scalar_state and not Ctx._unseedable_required(init_params)
        else ""
    )
    # Class docstring via the one shared builder (identical to the module .pyi
    # path), so the two generators never drift. doc_blocks carry the sacred
    # header's create() @brief/@param; they are seeded on cfg by callers that
    # have `root` (regenerate_standalone below, and jm method/apply) — absent,
    # this falls back to {} and the generic "<Component> component." summary,
    # byte-identical to the pre-unification template.
    from . import _stubs as _S

    # gh-805 §F: the declared failure surface, from the one derivation every
    # class-docstring producer shares. Both faces below take it.
    _cls_raises, _cls_warns = _S.class_diagnostics(cfg, object_name)
    # gh-901: per-choice enum docs, from the same header read.
    _cls_enums = C.enum_choice_docs(
        cfg, cfg.get(object_name, {}).get("_doc_blocks", {})
    )
    ctx["class_docstring"] = _S.class_docstring_block(
        object_name,
        Component,
        state_vars_list,
        C.is_no_state(cfg, object_name),
        init_params,
        f"from {pkg} import {Component}",
        ctx.get("py_create_args", ""),
        doc_blocks=cfg.get(object_name, {}).get("_doc_blocks", {}),
        manifest_doc=cfg.get(object_name, {}).get("doc", ""),
        custom_reset=bool(init_params) or C.is_no_reset(cfg, object_name),
        create_fn=C.object_create_fn(cfg, object_name),
        raises=_cls_raises,
        warns=_cls_warns,
        enum_choices=_cls_enums,
    )
    # gh-644/gh-676: the runtime class docstring. The module aggregator has
    # derived this from create()'s @brief since gh-602; the standalone template
    # carried a fixed literal, so `help(Obj)` showed "<Component> component.
    # Wraps <c>_state_t." however the author documented create() -- while the
    # .pyi class docstring beside it showed the real thing. Same precedence as
    # the module path: manifest doc= > header @brief > a generic fallback.
    _tp = authored_class_brief(
        cfg.get(object_name, {}).get("_doc_blocks", {}),
        C.object_create_fn(cfg, object_name) or f"{object_name}_create",
        cfg.get(object_name, {}).get("doc", ""),
    )
    # Only override when there is something authored to override WITH. An
    # unconditional fallback here would rewrite the seeded default, and
    # `jm object` renders this file without doc_blocks while `jm apply`
    # renders it with them -- so a freshly scaffolded project would report
    # STALE against itself the moment it was created.
    # gh-805 §F: a declared `create_error` or `[[obj.warnings]]` is also
    # "something to override with", and it does not carry the hazard the
    # comment above describes. That hazard is about *header* doc_blocks, which
    # `jm object` renders without and `jm apply` renders with; these two come
    # from the MANIFEST, which both paths read alike — so widening the gate
    # cannot make a fresh scaffold report STALE against itself. Without this
    # the object's own `.pyi` documented an exception that `help(Obj)` did
    # not, which is the two-faces-disagree bug gh-642 exists to prevent.
    if _tp or _cls_raises or _cls_warns or _cls_enums:
        # gh-642: the whole class block, not just the brief — the same text
        # the .pyi beside it carries.
        ctx["tp_doc"] = _build_ml_doc(
            _S.class_runtime_doc(
                object_name,
                Component,
                state_vars_list,
                C.is_no_state(cfg, object_name),
                init_params,
                f"from {pkg} import {Component}",
                ctx.get("py_create_args", ""),
                doc_blocks=cfg.get(object_name, {}).get("_doc_blocks", {}),
                manifest_doc=cfg.get(object_name, {}).get("doc", ""),
                custom_reset=bool(init_params)
                or C.is_no_reset(cfg, object_name),
                create_fn=C.object_create_fn(cfg, object_name),
                raises=_cls_raises,
                warns=_cls_warns,
                enum_choices=_cls_enums,
            )
        )
    return ctx


def regenerate_standalone(
    root: Path, cfg: dict, object_name: str, pkg: str
) -> None:
    """Re-render a standalone object's ``_ext.c`` and ``.pyi`` in place.

    Both writes are create-if-exists: a component whose files have not been
    materialised yet is left alone rather than half-written, so this is safe
    to call on a manifest-only component (``jm apply`` is what materialises).
    """
    # Seed the sacred header's create() Doxygen (filtered of jm's own scaffold
    # boilerplate) so component_ctx can enrich the class docstring — it is
    # manifest-only by contract and has no `root`. Absent header -> {} -> the
    # generic summary. This mirrors the module path (_object.build_component_ctxs
    # seeds the same key) so a header-authored @brief/@param survives every
    # standalone regen instead of reverting to the generic stub.
    from ._object import _load_doc_blocks

    cfg.setdefault(object_name, {})["_doc_blocks"] = _load_doc_blocks(
        root, object_name
    )
    ctx = component_ctx(cfg, object_name, pkg, root)
    # gh-543: component_ctx is manifest-only by contract, so the on-disk probe
    # for a hand-written extra belongs here, in the caller that has `root`.
    ctx["extra_include"] = standalone_extra_include(root, object_name)

    ext_c = root / "native" / "src" / object_name / f"{object_name}_ext.c"
    if ext_c.exists():
        ext_c.write_text(R.render(R.COMPONENT_EXT_C, ctx), encoding="utf-8")
        print(f"  update  {ext_c}")

    pyi_path = root / "src" / pkg / f"{object_name}.pyi"
    if pyi_path.exists():
        old_pyi = pyi_path.read_text(encoding="utf-8")
        new_pyi = R.render_component_pyi(ctx)
        # gh-428: preserve any manual_stub method's hand-written text across
        # the otherwise-blind regen above.
        pyi_path.write_text(
            S._splice_manual_stub_bodies(cfg, old_pyi, new_pyi, path=pyi_path),
            encoding="utf-8",
        )
        print(f"  update  {pyi_path}")


def regenerate(
    root: Path, cfg: dict, object_name: str, module: str | None, pkg: str
) -> None:
    """Re-render a component's glue, module-aware.

    A module object shares one ``_ext.c`` with its siblings, so the whole
    aggregate is rebuilt; a standalone object owns its own.
    """
    if module:
        from ._object import _regenerate_module

        _regenerate_module(root, cfg, module, pkg)
    else:
        regenerate_standalone(root, cfg, object_name, pkg)
