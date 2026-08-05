"""
_split_objects.py — `just-makeit split-objects` migration.

Move every top-level `[obj]` section out of the manifest into its own
`objects/<obj>.toml` fragment, and add `include = ["objects/*.toml"]`
to the manifest. `[project]` and `[module.X]` stay in the manifest.

Idempotent: a project that already has the split layout is a no-op.
"""

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

    components = [k for k in manifest if k not in ("project", "module")]
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

    for comp in components:
        frag_path = objects_dir / f"{comp}.toml"
        frag_text = C._dump({comp: manifest[comp]})
        frag_path.write_text(frag_text, encoding="utf-8")
        print(f"  create  {frag_path}")

    # The manifest keeps whatever was NOT relocated, gains the include glob.
    #
    # Subtractive on purpose (gh-763). Written as an additive list of names to
    # re-add, correctness depended on this list and `components`' exclusion
    # list agreeing — two lists in different functions that have to be edited
    # together, with nothing checking that they were. A top-level key added to
    # one and forgotten in the other does not error; it is written to a
    # fragment and then also dropped from root, or kept in root and never
    # moved. Deriving `keep` from what was actually relocated makes the
    # invariant local: a section nobody thought about survives by default
    # instead of vanishing by default.
    keep = {k: v for k, v in manifest.items() if k not in components}

    manifest_text = C._dump(keep)
    manifest_text = (
        f"include = {C._toml_string_array(['objects/*.toml'])}\n\n"
        + manifest_text
    )
    cfg_path.write_text(manifest_text, encoding="utf-8")
    print(f'  update  {cfg_path}  (include = ["objects/*.toml"])')

    print()
    print(f"Done!  {len(components)} object section(s) moved to objects/.")
