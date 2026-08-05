"""
_migrate.py — `just-makeit migrate-to-fragments`.

Convert a central-manifest project to the per-component fragment layout:

    just-makeit.toml      # [project] + include globs only
    objects/<obj>.toml    # one per standalone object
    modules/<mod>.toml     # one per module (full [module.X] config)

Each component's spec becomes a self-contained file, so copying a component
into another project is "copy its fragment + source dirs, run jm apply" —
no central-manifest surgery. Supersedes `split-objects`, which moved only
objects (modules stayed inline).

Idempotent: a fully migrated project is a no-op. Runs incrementally — a
project already split via `split-objects` (objects out, modules inline)
just gets its modules moved and the `modules/*.toml` glob added.
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
    existing_globs = list(manifest.get("include", []))

    # Inline sections still living in the central manifest.
    components = [
        k for k in manifest if k not in ("project", "module", "include")
    ]
    modules = dict(manifest.get("module", {}))

    if not components and not modules:
        if existing_globs:
            print("Project already uses the fragment layout; nothing to do.")
        else:
            print("No object or module sections to migrate.")
        return

    print("just-makeit: migrating to the per-component fragment layout")
    print()

    if components:
        objects_dir = root / "objects"
        objects_dir.mkdir(exist_ok=True)
        for comp in components:
            frag = objects_dir / f"{comp}.toml"
            frag.write_text(C._dump({comp: manifest[comp]}), encoding="utf-8")
            print(f"  create  {frag}")

    if modules:
        modules_dir = root / "modules"
        modules_dir.mkdir(exist_ok=True)
        for mod, data in modules.items():
            frag = modules_dir / f"{mod}.toml"
            frag.write_text(C._dump({"module": {mod: data}}), encoding="utf-8")
            print(f"  create  {frag}")

    # Rebuild the include list: keep any existing globs, add objects/ and
    # modules/ globs for the directories that now hold fragments.
    globs = list(existing_globs)
    if (root / "objects").is_dir() and "objects/*.toml" not in globs:
        globs.append("objects/*.toml")
    if (root / "modules").is_dir() and "modules/*.toml" not in globs:
        globs.append("modules/*.toml")

    # Subtractive, for the reason in `_split_objects` (gh-763): an additive
    # list here has to stay in step with `components`' exclusion list above,
    # and nothing enforces that. `module` is relocated wholesale into
    # modules/, so it is named explicitly rather than left to the derivation.
    _relocated = set(components) | ({"module"} if modules else set())
    keep = {k: v for k, v in manifest.items() if k not in _relocated}
    manifest_text = C._dump(keep)
    if globs:
        manifest_text = (
            f"include = {C._toml_string_array(globs)}\n\n" + manifest_text
        )
    cfg_path.write_text(manifest_text, encoding="utf-8")
    print(f"  update  {cfg_path}  (include = {C._toml_string_array(globs)})")

    print()
    print(
        f"Done!  Moved {len(components)} object(s) and {len(modules)} "
        f"module(s) into fragments."
    )
