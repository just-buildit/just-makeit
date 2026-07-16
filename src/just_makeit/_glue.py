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
from . import _context as Ctx
from . import _render as R
from . import _stubs as S
from . import _types as T
from ._init import _make_component_ctx, _to_title


def component_ctx(cfg: dict, object_name: str, pkg: str) -> dict:
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
            serializable=C.is_serializable(cfg, object_name),
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
    # gh-481: declared warnings. Re-rendered from the manifest on every pass,
    # which is the whole point — a hand-patched PyErr_WarnEx in this file was
    # silently lost the moment anything regenerated it.
    ctx.update(
        Ctx.make_warnings_ctx(
            object_name, Component, C.warnings(cfg, object_name)
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
        )
        if scalar_state and not Ctx._unseedable_required(init_params)
        else ""
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
    ctx = component_ctx(cfg, object_name, pkg)

    ext_c = root / "native" / "src" / object_name / f"{object_name}_ext.c"
    if ext_c.exists():
        ext_c.write_text(R.render(R.COMPONENT_EXT_C, ctx), encoding="utf-8")
        print(f"  update  {ext_c}")

    pyi_path = root / "src" / pkg / f"{object_name}.pyi"
    if pyi_path.exists():
        old_pyi = pyi_path.read_text(encoding="utf-8")
        new_pyi = R.render(R.COMPONENT_PYI, ctx)
        # gh-428: preserve any manual_stub method's hand-written text across
        # the otherwise-blind regen above.
        pyi_path.write_text(
            S._splice_manual_stub_bodies(cfg, old_pyi, new_pyi),
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
