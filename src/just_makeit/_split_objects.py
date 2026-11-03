"""
_split_objects.py — `just-makeit split-objects` migration.

Move every top-level `[obj]` section out of the manifest into its own
`objects/<obj>.toml` fragment, and add `include = ["objects/*.toml"]`
to the manifest. `[project]` and `[module.X]` stay in the manifest.

Idempotent: a project that already has the split layout is a no-op.

The fragments load sorted by path, so the root CMakeLists wiring and the
umbrella header's includes are re-sorted to the new order (gh-1985).
"""

from . import _textio
import sys
from pathlib import Path

from . import _config as C


def run(root: Path) -> None:
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    manifest = C.load_manifest(root)
    if "include" in manifest:
        print(
            "Project already uses the split layout "
            "(`include` is set); nothing to do."
        )
        return

    # gh-2434: components only; the project's own sections stay put.
    components = C.components(manifest)
    if not components:
        print("No object sections in the manifest — nothing to split.")
        return

    print(
        f"just-makeit: splitting {len(components)} object(s) into "
        f"objects/ fragments"
    )
    print()

    objects_dir = root / "objects"
    objects_dir.mkdir(exist_ok=True)

    # gh-2437: cut, not rebuilt, as in `migrate-to-fragments` -- each object
    # leaves with its comments and the manifest keeps every other byte.
    #
    # What stays is still subtractive (gh-763): the root file is the
    # document minus what was moved, so a section nobody thought about
    # survives by default instead of vanishing by default -- now with its
    # comments, too.
    manifest_text, obj_texts, _ = C.split_manifest(
        cfg_path.read_text(encoding="utf-8"), components, False
    )
    for comp in components:
        frag_path = objects_dir / f"{comp}.toml"
        _textio.write_text(frag_path, obj_texts[comp])
        print(f"  create  {frag_path}")

    _textio.write_text(
        cfg_path, C.set_include(manifest_text, ["objects/*.toml"])
    )
    print(f'  update  {cfg_path}  (include = ["objects/*.toml"])')
    # gh-1985: as in `migrate-to-fragments` -- the fragments load sorted, so
    # the order `apply` lists components in changed with the layout.
    from ._init import settle_aggregate_order

    for path in settle_aggregate_order(root):
        print(f"  update  {path}  (order)")

    print()
    print(f"Done!  {len(components)} object section(s) moved to objects/.")
    # gh-2440: the same move out of the root, so the same reader note.
    from ._migrate import READER_NOTE

    print()
    print(READER_NOTE)
