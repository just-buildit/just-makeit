"""
_add.py — `just-makeit add` command.

Author one or more state variables into an existing object's manifest entry,
then rebuild the object from the manifest.

State is *structural*: a new field changes the sacred ``<obj>_state_t`` struct
and the ``create()`` / ``reset()`` lifecycle.  Under the sacred/glue contract
those are never spliced into your files — the object is rebuilt from the
manifest instead, exactly like ``jm regenerate``.  Keep your algorithm in the
TOML (``impl`` / ``create_impl``) or ``git stash`` first so the rebuild
re-asserts it.  ``--force`` skips the confirmation.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import _config as C
from . import _regenerate


def run(
    root: Path,
    component: str | None,
    new_vars: list[tuple[str, str, str]],
    force: bool = False,
) -> None:
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)
    comps = C.components(cfg)
    if not comps:
        print(
            "error: project has no standalone objects yet. "
            "Run 'just-makeit object <name>' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    if component is None:
        if len(comps) == 1:
            component = comps[0]
        else:
            print(
                f"error: project has multiple objects {comps}. "
                "Use --object to specify one.",
                file=sys.stderr,
            )
            sys.exit(1)
    elif component not in comps:
        print(
            f"error: object '{component}' not found. Available: {comps}",
            file=sys.stderr,
        )
        sys.exit(1)

    existing = C.state_vars(cfg, component)
    existing_names = {n for n, _, _ in existing}
    for name, _, _ in new_vars:
        if name in existing_names:
            print(
                f"error: state variable '{name}' already exists.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Author: append the new field(s) to the object's manifest entry.  The
    # struct/lifecycle change is materialized by the regenerate below — never
    # by splicing into the sacred source.
    all_vars = existing + new_vars
    cfg[component]["state"] = [
        {"name": n, "type": t, "default": d} for n, t, d in all_vars
    ]
    C.save(root, cfg)
    print(f"  update  {cfg_path}")
    print()
    names = ", ".join(n for n, _, _ in new_vars)
    print(
        f"just-makeit: added state ({names}) to '{component}'. State is "
        f"structural, so '{component}' is rebuilt from the manifest:"
    )
    print()
    # discard=True: the old body's signature is guaranteed stale (it
    # predates the new field(s)) — splicing it back in would either not
    # compile or silently skip initializing the new state.
    _regenerate.run(root, component, force=force, discard=True)
