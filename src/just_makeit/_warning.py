"""_warning.py — ``just-makeit warning`` command (gh-481).

Declares a Python warning raised after a successful construction, when a bool
field on the state struct says the result was best-effort rather than what the
caller asked for.

The motivating case: an object auto-sizes something from physics and can end up
with a result that doesn't meet the caller's target. Construction genuinely
succeeded — it's a "here's the best I could do" signal, not an error — so the
right Python surface is a warning. C can't raise one (``create()`` returns
non-NULL or it doesn't; there's no channel for "succeeded, but"), so this was
a hand-patch in the ``_ext.c`` glue. That glue is regenerated wholesale from
the manifest, and jm's own documented way to pick up a new declarative field on
an existing object is delete-the-fragment-and-``jm apply`` — which dropped the
patch silently. Declaring it here makes it survive like any other generated
boilerplate.

Purely additive to the glue: no sacred file is touched, and the condition field
must already exist on the state struct (the component computes it; jm only
surfaces it).
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import _config as C
from . import _glue
from ._context._diagnostics import _IDENT


def run(
    root: Path,
    object_name: str,
    condition: str,
    message: str,
    *,
    module: str | None = None,
    category: str = "UserWarning",
    after: str = "__init__",
    stacklevel: int = 1,
    view: str = "",
) -> None:
    """Declare a post-construction warning on `object_name`.

    Parameters
    ----------
    root : Path
        Project root (the directory holding ``just-makeit.toml``).
    object_name : str
        Component to attach the warning to.
    condition : str
        A bare field name on the component's state struct — emitted as
        ``self->handle-><condition>`` — or a complete C expression, used
        verbatim (gh-601). The second form is what a **forwarder** object
        needs: its state struct is a handle onto a shared engine and has no
        bool field to name, so its condition reaches through
        (``self->handle->engine->underpowered``) exactly as every one of its
        properties already does via ``expr``.
    message : str
        Warning text shown to the Python caller.
    module : str, optional
        Owning module, for a module object.
    category : str, default "UserWarning"
        A name from `C.WARNING_CATEGORIES`.
    after : str, default "__init__"
        Where the warning fires. Only ``__init__`` is supported today.
    stacklevel : int, default 1
        Passed to ``PyErr_WarnEx``. 1 points at the caller's construction site.
    """
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    if after != "__init__":
        # Method-site warnings are a real want, but method glue has many
        # shapes (variable_output, nogil, streams) and each would need its own
        # injection point. Rejecting is honest; half-generating is not.
        print(
            f"error: --after '{after}' is not supported yet; only '__init__'."
            "\nTrack method-site warnings in gh-481 before relying on this.",
            file=sys.stderr,
        )
        sys.exit(1)

    # gh-601: a bare identifier is sugar for `self->handle-><name>`; anything
    # else is a complete C expression, exactly as a property's `expr` is. What
    # is still rejected is only what cannot be an *expression* at all — a
    # statement spliced into `if (...)` produces broken C in generated code the
    # author did not write, which is the gh-625 failure mode.
    if not condition.strip():
        print(
            "error: --condition is required — a warning with no condition "
            "would fire unconditionally.",
            file=sys.stderr,
        )
        sys.exit(1)
    _bad = {c for c in ";{}" if c in condition}
    if _bad:
        print(
            f"error: --condition '{condition}' contains {sorted(_bad)!r} and "
            "is not a C expression.\n"
            "It is spliced into `if (<condition>)`. Use a bare field name "
            "('underpowered',\nreached through the handle for you) or a full "
            "expression\n('self->handle->engine->underpowered').",
            file=sys.stderr,
        )
        sys.exit(1)

    if category not in C.WARNING_CATEGORIES:
        supported = ", ".join(sorted(C.WARNING_CATEGORIES))
        print(
            f"error: unsupported --category '{category}'.\n"
            f"Supported: {supported}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not message:
        print(
            "error: --message is required — a warning with no text tells the "
            "caller nothing.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)

    all_comps = C.components(cfg)
    if module:
        if object_name not in C.module_objects(cfg, module):
            print(
                f"error: object '{object_name}' not found in module"
                f" '{module}'.",
                file=sys.stderr,
            )
            sys.exit(1)
    elif object_name not in all_comps:
        print(
            f"error: object '{object_name}' not found. Available: {all_comps}",
            file=sys.stderr,
        )
        sys.exit(1)

    # The condition must name something the component actually computes. jm
    # can only check what it declared itself — a field the user hand-added to
    # the sacred struct is invisible here — so this warns rather than errors.
    # gh-601: only meaningful for the bare-identifier form, which is the one
    # jm resolves against the struct. A full expression names its own reach —
    # typically through a forwarder's `engine->`, which is by definition not a
    # field jm declared — so cross-checking it would warn on every correct use.
    known = {n for n, _, _ in C.state_vars(cfg, object_name)}
    known |= {p["name"] for p in C.properties(cfg, object_name)}
    if _IDENT.match(condition) and known and condition not in known:
        print(
            f"warning: '{condition}' is not a declared state field or property"
            f" on '{object_name}'. If it is hand-added to the sacred struct"
            " this is fine; otherwise the generated glue will not compile.",
            file=sys.stderr,
        )

    entry: dict = {
        "after": after,
        "condition": condition,
        "category": category,
        "message": message,
    }
    if stacklevel != 1:
        entry["stacklevel"] = stacklevel

    # gh-509: --view targets a view's OWN warnings ([[<obj>.views.warnings]])
    # rather than the object's. A view is module-only, so --module is required;
    # add_view_warning is idempotent on (condition, after) exactly like the
    # object path below, so `jm apply` replay stays stable.
    if view:
        if not module:
            print(
                "error: --view requires --module (views are module-only).",
                file=sys.stderr,
            )
            sys.exit(1)
        if C._find_view(cfg, object_name, view) is None:
            print(
                f"error: no view '{view}' on object '{object_name}'.",
                file=sys.stderr,
            )
            sys.exit(1)
        C.add_view_warning(cfg, object_name, view, entry)
        print(
            f"just-makeit: adding {category} on view '{view}'"
            f" of '{object_name}' when {condition}"
        )
        print()
    else:
        # Idempotent for `jm apply` replay: the same condition re-declared is
        # the same warning, so update it in place rather than emitting a
        # duplicate PyErr_WarnEx guarded by the identical `if`.
        existing = C.warnings(cfg, object_name)
        for i, w in enumerate(existing):
            if (
                w.get("condition") == condition
                and w.get("after", "__init__") == after
            ):
                cfg[object_name]["warnings"][i] = entry
                break
        else:
            C.add_warning(cfg, object_name, entry)
            print(
                f"just-makeit: adding {category} on '{object_name}'"
                f" when {condition}"
                + (f" in module '{module}'" if module else "")
            )
            print()

    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    _glue.regenerate(root, cfg, object_name, module, C.project_name(cfg))
