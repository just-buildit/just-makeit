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
) -> None:
    """Declare a post-construction warning on `object_name`.

    Parameters
    ----------
    root : Path
        Project root (the directory holding ``just-makeit.toml``).
    object_name : str
        Component to attach the warning to.
    condition : str
        Name of a bool-ish field on the component's state struct. Emitted as
        ``self->handle-><condition>``, so it must be an identifier.
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

    if not _IDENT.match(condition):
        print(
            f"error: --condition '{condition}' is not a C identifier.\n"
            "It names a bool field on the state struct, e.g. 'underpowered'.",
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
    known = {n for n, _, _ in C.state_vars(cfg, object_name)}
    known |= {p["name"] for p in C.properties(cfg, object_name)}
    if known and condition not in known:
        print(
            f"warning: '{condition}' is not a declared state field or property"
            f" on '{object_name}'. If it is hand-added to the sacred struct"
            " this is fine; otherwise the generated glue will not compile.",
            file=sys.stderr,
        )

    # Idempotent for `jm apply` replay: the same condition re-declared is the
    # same warning, so update it in place rather than emitting a duplicate
    # PyErr_WarnEx guarded by the identical `if`.
    existing = C.warnings(cfg, object_name)
    entry: dict = {
        "after": after,
        "condition": condition,
        "category": category,
        "message": message,
    }
    if stacklevel != 1:
        entry["stacklevel"] = stacklevel

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
            f" when {condition}" + (f" in module '{module}'" if module else "")
        )
        print()

    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    _glue.regenerate(root, cfg, object_name, module, C.project_name(cfg))
