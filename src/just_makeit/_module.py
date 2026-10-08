"""
_module.py — `just-makeit module` command.

Scaffolds a new empty Python extension module (a .so subpackage that will
host one or more object types added via `just-makeit object`).

After: just-makeit module filter
  native/src/filter/filter_ext.c        — empty module (no types yet)
  native/src/filter/CMakeLists.txt      — Python module target
  src/<pkg>/filter/__init__.py          — subpackage init (empty exports)
  just-makeit.toml                      — [module.filter] objects = []
  CMakeLists.txt                        — add_subdirectory appended

Everything but the sacred ``filter_core.h`` / ``filter_core.c`` and the
manifest is the module's glue, written by ``_object._regenerate_module`` --
the one module render, whatever the module holds (gh-2070).
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import _config as C
from . import _render as T
from . import _incpath as INC
from . import _csym as CSYM
from ._init import _to_title, _write
from ._object import _regenerate_module


def run(
    root: Path,
    module: str,
    extra_include_dirs: list[str] | None = None,
    extra_link_libs: list[str] | None = None,
    extra_types: list[str] | None = None,
    functions_in_core: bool = False,
    package: str = "",
    doc: str = "",
    platforms: list[str] | None = None,
) -> None:
    """Scaffold module *module* under *root*.

    *package* (gh-523) is the ``[module.X] package`` override: the package
    directory this module's Python artifacts (``.so``, ``.pyi``, re-export
    ``__init__.py``, tests and benchmarks) land in, instead of one named
    after the module. Empty means "a package of my own" — today's behaviour.

    *platforms* (gh-1463) is ``[module.X] platforms``: the platforms the
    module's extension is built on. It has no CLI flag; ``apply`` passes it so
    the replayed scaffold renders the guard from its first file.
    """
    err = C.validate_module_id(module)
    if err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)

    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)
    mp = C.module_paths(module)
    if module in C.modules(cfg):
        print(f"error: module '{module}' already exists.", file=sys.stderr)
        sys.exit(1)
    # A dotted module's cname must not collide with another module's cname or
    # a standalone component (they share native dirs / CMake targets).
    if mp.cname in C.module_cnames(cfg) or mp.cname in C.components(cfg):
        print(
            f"error: module '{module}' collides with existing "
            f"'{mp.cname}'. Modules, components, and nested-module cnames "
            "share one namespace.",
            file=sys.stderr,
        )
        sys.exit(1)
    if module in C.components(cfg):
        print(
            f"error: '{module}' is already a standalone component. "
            "Modules and components share the same namespace.",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = C.project_name(cfg)
    Module = _to_title(mp.cname)

    print(f"just-makeit: scaffolding module '{module}' in project '{pkg}'")
    print()

    # cname (dots→underscores) drives every C identifier / native dir / file
    # prefix; for a flat module it equals `module`, so nothing below changes
    # for existing projects.
    cname = mp.cname
    mod_ctx = {
        "module": cname,
        "Module": Module,
        "MODULE": cname.upper(),
        # gh-1583: the header layout of the project the module goes in.
        **INC.ctx_slots(cfg),
        # gh-1591: the stem the module's C symbols and guard derive from.
        **CSYM.slots(cfg, cname),
    }
    # C header and implementation for module-level functions
    _write(
        INC.core_h(root, cname),
        T.render(T.MODULE_CORE_H, mod_ctx),
    )
    _write(
        root / "native" / "src" / cname / f"{cname}_core.c",
        T.render(T.MODULE_CORE_C, mod_ctx),
    )

    # Ensure C test and benchmark directories exist (even before any objects
    # or functions are added — users may want to write their own C tests).
    for subdir in ("native/tests", "native/benchmarks"):
        d = root / subdir
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)

    # Config
    C.scaffold_module(cfg, module)
    if package:
        cfg["module"][module]["package"] = package
    # gh-645: a module has no header to derive from, so the manifest is the
    # only place its documentation can live. Saved before the render below,
    # since both generated faces read it from there.
    if doc:
        cfg["module"][module]["doc"] = doc
    if platforms:
        cfg["module"][module]["platforms"] = list(platforms)
    # Optional Phase-2 metadata persisted into the [module.X] section so
    # jm apply's renderer picks them up. The renderer + dump already
    # handle these keys (gh-66 / v0.13.22 for include_dirs and link_libs;
    # extra_types from the earlier gh-28 extras work).
    if extra_include_dirs:
        cfg.setdefault("module", {}).setdefault(module, {})[
            "extra_include_dirs"
        ] = list(extra_include_dirs)
    if extra_link_libs:
        cfg.setdefault("module", {}).setdefault(module, {})[
            "extra_link_libs"
        ] = list(extra_link_libs)
    if extra_types:
        cfg.setdefault("module", {}).setdefault(module, {})["extra_types"] = (
            list(extra_types)
        )
    if functions_in_core:
        # gh-247: keep this module's free functions in <module>_core.c (one TU)
        # rather than one .c per function.
        cfg.setdefault("module", {}).setdefault(module, {})[
            "functions_in_core"
        ] = "true"
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    # gh-2070: the module's glue -- `<cname>_ext.c`, its CMakeLists, the
    # re-export `__init__.py`, the `.pyi` and the root CMakeLists wiring --
    # is the ONE module render every member verb and `jm remove` end in,
    # whatever the module holds. This path had a render of its own for a
    # module with nothing in it, and `apply` replays an empty module through
    # here: so removing a module's last object (or function) left a tree the
    # next `apply` rewrote, and this render dropped the gh-1351
    # `<cname>_extra.cmake` hook, which an empty module's regenerated
    # CMakeLists needs exactly as much as a full one's does. It also never
    # read the `--extra-*` keys saved above.
    # After the save: the render reads the manifest, and gh-1985 places the
    # root wiring by the manifest's order on disk.
    _regenerate_module(root, cfg, module, pkg)

    print()
    print(
        f"Done!  Add types with: just-makeit object <name> --module {module}"
    )
