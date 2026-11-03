"""
_migrate.py — `just-makeit migrate-to-fragments`.

Convert a central-manifest project to the per-component fragment layout:

    just-makeit.toml      # [project] + include globs only
    objects/<obj>.toml    # one per standalone object
    modules/<cname>.toml  # one per module (full [module.X] config)

Each component's spec becomes a self-contained file, so copying a component
into another project is "copy its fragment + source dirs, run jm apply" —
no central-manifest surgery. Supersedes `split-objects`, which moved only
objects (modules stayed inline).

Idempotent: a fully migrated project is a no-op. Runs incrementally — a
project already split via `split-objects` (objects out, modules inline)
just gets its modules moved and the `modules/*.toml` glob added.

Fragments load sorted by path, so the manifest's component order can change;
the root CMakeLists wiring is spliced as `apply` writes it in the new order,
and the umbrella header's includes are re-sorted to it
(`_init.settle_aggregate_order`, gh-1985, gh-2434). No other file is touched.
The project's own sections -- `[[enum]]`, `[codec]`, `[[app]]`, `[[group]]`,
`[template]` -- stay in the manifest, where `save` keeps them.

Every comment survives (gh-2437): the manifest is cut with
`_config.split_manifest`, so a moved table takes its own comments -- the
ones inside it and the run directly above its header -- and the manifest
keeps every other byte.
"""

from . import _textio
import sys
from pathlib import Path

from . import _config as C

#: Printed once after a migration that moved anything (gh-2440). A project
#: script that parsed ``just-makeit.toml`` with ``tomllib`` read every table
#: from that one file; after the move it finds none and, unless it checks,
#: passes on nothing. `split-objects` prints it too.
READER_NOTE = (
    "Note: tables now live in the files `include` names. A script that\n"
    "parses just-makeit.toml directly sees none of them; read the merged\n"
    "manifest with just_makeit.load_manifest(root) or\n"
    "`just-makeit manifest --json` instead."
)


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
    existing_globs = list(manifest.get("include", []))

    # Inline components still living in the central manifest. The project's
    # own sections (`[[enum]]`, `[codec]`, `[[app]]`, ...) stay there, where
    # `save` keeps them (gh-2434).
    components = C.components(manifest)
    modules = dict(manifest.get("module", {}))

    if not components and not modules:
        if existing_globs:
            print("Project already uses the fragment layout; nothing to do.")
        else:
            print("No object or module sections to migrate.")
        return

    print("just-makeit: migrating to the per-component fragment layout")
    print()

    # gh-2437: the manifest is CUT, not rebuilt -- each moved table leaves
    # with its comments, and the root file keeps every other byte. Rebuilt
    # from plain dicts with `_dump`, every comment in it was deleted.
    # `module` moves wholesale.
    manifest_text, obj_texts, mod_texts = C.split_manifest(
        cfg_path.read_text(encoding="utf-8"), components, bool(modules)
    )

    # The fragments this run writes, keyed by the glob that reaches them.
    created: dict[str, list[Path]] = {}
    if components:
        objects_dir = root / "objects"
        objects_dir.mkdir(exist_ok=True)
        for comp in components:
            frag = objects_dir / f"{comp}.toml"
            _textio.write_text(frag, obj_texts[comp])
            created.setdefault("objects/*.toml", []).append(frag)
            print(f"  create  {frag}")

    if modules:
        (root / "modules").mkdir(exist_ok=True)
        for mod in modules:
            # gh-2306: where `save` writes a new module, so a migrated
            # project names its fragments as a split one does.
            frag = C.module_fragment(root, mod)
            _textio.write_text(frag, mod_texts[mod])
            created.setdefault("modules/*.toml", []).append(frag)
            print(f"  create  {frag}")

    # Rebuild the include list: the existing entries, plus a glob for each
    # directory holding a new fragment those entries do not already reach.
    # Reach is asked of `_resolve_includes`, the one reading of `include`
    # that `load` makes, so an entry spelled any other way that already
    # matches (`"*/*.toml"`, an explicit path) is not answered twice.
    globs = list(existing_globs)
    covered = set(C._resolve_includes(root, globs))
    for glob, frags in created.items():
        if any(frag not in covered for frag in frags):
            globs.append(glob)

    # Extended in place when it exists, written once either way (gh-2263:
    # carried over beside the merged list, it was written twice and the
    # manifest no longer parsed).
    if globs:
        manifest_text = C.set_include(manifest_text, globs)
    _textio.write_text(cfg_path, manifest_text)
    print(f"  update  {cfg_path}  (include = {C._toml_string_array(globs)})")
    # gh-1985: fragments load sorted by path, so the manifest's order -- the
    # order `apply` lists components in -- just changed; the two files listing
    # them follow it, or `status --check` reports them stale.
    from ._init import settle_aggregate_order

    for path in settle_aggregate_order(root):
        print(f"  update  {path}  (order)")

    print()
    print(
        f"Done!  Moved {len(components)} object(s) and {len(modules)} "
        f"module(s) into fragments."
    )
    print()
    print(READER_NOTE)
