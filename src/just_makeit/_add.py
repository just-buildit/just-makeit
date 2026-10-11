"""
_add.py — `just-makeit add` command.

Author one or more state variables into an existing object's manifest entry,
then rebuild the object from the manifest.

State is *structural*: a new field changes the sacred ``<obj>_state_t`` struct
and the ``create()`` / ``reset()`` lifecycle.  Under the sacred/glue contract
those are never spliced into your files — the object is rebuilt from the
manifest instead, exactly like ``jm regenerate``.  Keep your algorithm in the
TOML (``impl`` / ``create_impl``) or ``git stash`` first so the rebuild
re-asserts it.  The confirmation comes before anything is written, and
declining exits 1 with every file as it was; ``--force`` skips it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import _config as C
from . import _regenerate
from ._remove import _confirm


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

    # gh-1890: the rows as the manifest holds them, every key on each. This
    # was `C.state_vars`, whose (name, type, default) triples skip an opaque
    # row by design, and the list was rebuilt from them -- so one `jm add`
    # deleted every opaque field and every `doc`, `no_ctor`, `controllable`
    # and `str_hint` on the rows it kept (the gh-1760 / gh-838 class). The
    # existing rows are never rebuilt now, only appended to, and the name
    # check reads every row: an opaque field's name is taken too.
    rows = cfg[component].get("state", [])
    taken = {row.get("name") for row in rows}
    for name, _, _ in new_vars:
        if name in taken:
            print(
                f"error: state variable '{name}' already exists.",
                file=sys.stderr,
            )
            sys.exit(1)
        taken.add(name)

    names = ", ".join(n for n, _, _ in new_vars)
    # gh-2087: the rebuild below deletes the object's files for `apply` to
    # write again, and `apply` writes nothing of a `no_generate` module.
    # Refused here, before anything is asked or saved, so the tree is left
    # as it was.
    _regenerate.refuse_hand_written(
        cfg, component, f"add state ({names}) to '{component}'"
    )
    print(
        f"just-makeit: add state ({names}) to '{component}'. State is "
        f"structural, so '{component}' is rebuilt from the manifest."
    )
    print()
    # gh-1889: ask BEFORE the manifest is written. The prompt used to be
    # regenerate's own, reached after `C.save`, so answering N left the new
    # row on disk with no rebuild behind it: exit 0, `jm status` STALE, and
    # the `jm apply` it advises rewrote create() against a `_core.c` that
    # was never regenerated. The confirmation is the caller's, as for `jm
    # remove` of a state field, and the rebuild below runs forced.
    if not _confirm(
        f"Add state ({names}) to '{component}'? This rebuilds "
        f"'{component}' from the manifest and discards hand-written "
        "_core.c bodies (git stash or keep them in impl/create_impl "
        "first).",
        force,
    ):
        print(
            f"error: aborted; nothing was added to '{component}' and no "
            "file was written.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Author: append the new field(s) to the object's manifest entry.  The
    # struct/lifecycle change is materialized by the regenerate below — never
    # by splicing into the sacred source.
    cfg[component]["state"] = rows + [
        {"name": n, "type": t, "default": d} for n, t, d in new_vars
    ]
    C.save(root, cfg)
    print(f"  update  {cfg_path}")
    print()
    # discard=True: the old body's signature is guaranteed stale (it
    # predates the new field(s)) — splicing it back in would either not
    # compile or silently skip initializing the new state.
    _regenerate.run(root, component, force=True, discard=True)
