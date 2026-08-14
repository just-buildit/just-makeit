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
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import _config as C
from . import _context as Ctx
from . import _stubs as S
from . import _render as T
from ._init import (
    MODULES_SENTINEL,
    _to_title,
    _write,
    component_core_libs,
    ensure_parent_packages,
    splice_cmake_component,
)


def run(
    root: Path,
    module: str,
    extra_include_dirs: list[str] | None = None,
    extra_link_libs: list[str] | None = None,
    extra_types: list[str] | None = None,
    functions_in_core: bool = False,
    package: str = "",
    doc: str = "",
) -> None:
    """Scaffold module *module* under *root*.

    *package* (gh-523) is the ``[module.X] package`` override: the package
    directory this module's Python artifacts (``.so``, ``.pyi``, re-export
    ``__init__.py``, tests and benchmarks) land in, instead of one named
    after the module. Empty means "a package of my own" — today's behaviour.
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

    # gh-523: seed `package` into the in-memory cfg now that the "module
    # already exists" checks above have passed, so every path helper below
    # (and _stubs.make_module_pyi) reads the destination from the one place.
    if package:
        cfg.setdefault("module", {}).setdefault(module, {})["package"] = (
            package
        )

    pkg = C.project_name(cfg)
    Module = _to_title(mp.cname)

    print(f"just-makeit: scaffolding module '{module}' in project '{pkg}'")
    print()

    # cname (dots→underscores) drives every C identifier / native dir / file
    # prefix; for a flat module it equals `module`, so nothing below changes
    # for existing projects.
    cname = mp.cname
    mod_ctx = {"module": cname, "Module": Module, "MODULE": cname.upper()}
    # Render slots that split the module's roles (module=cname, module_leaf,
    # module_pypath, module_output_name, module_tp).
    # gh-523: `package` redirects the Python-side artifacts into a sibling
    # package; unset it resolves to the module's own pypath (zero churn).
    out_pkg = C.module_package(cfg, module) or mp.pypath
    # `doc` is the freshly-passed value; the manifest is written further down,
    # so reading it back from cfg here would always see "".
    mod_slots = Ctx.make_module_ctx(
        module, pkg, out_pkg, doc or C.module_doc(cfg, module)
    )

    # C header and implementation for module-level functions
    _write(
        root / "native" / "inc" / cname / f"{cname}_core.h",
        T.render(T.MODULE_CORE_H, mod_ctx),
    )
    _write(
        root / "native" / "src" / cname / f"{cname}_core.c",
        T.render(T.MODULE_CORE_C, mod_ctx),
    )

    # Empty module ext.c (no types yet — populated by `just-makeit object`)
    ext_c = T.render_module_ext_c(
        module, [], module_doc_c=mod_slots["module_doc_c"]
    )
    _write(root / "native" / "src" / cname / f"{cname}_ext.c", ext_c)

    # CMakeLists for the module (no object libs yet)
    cmake_ctx = {
        **mod_slots,
        "Module": Module,
        "object_list": "",
        "object_core_libs": f"{cname}_core",
        "module_core_lib_block": (
            f"add_library({cname}_core OBJECT {cname}_core.c)\n"
            f"target_include_directories({cname}_core PRIVATE"
            f" ${{CMAKE_SOURCE_DIR}}/native/inc)\n\n"
        ),
        "extra_link_libs_block": "",
        "extra_include_dirs_block": "",
        # gh-213: Windows runtime-DLL block, off unless the project targets it.
        **Ctx.make_platform_ctx(C.is_windows_target(cfg), module=cname),
    }
    _write(
        root / "native" / "src" / cname / "CMakeLists.txt",
        T.render(T.CMAKE_LISTS_MODULE, cmake_ctx),
    )

    # Python subpackage at src/<pkg>/<pypath>/ (nested for dotted ids); ensure
    # the intermediate packages exist so `pkg.<parent>...` is importable.
    ensure_parent_packages(root, pkg, mp, out_pkg)
    pkg_module_dir = root / "src" / pkg / out_pkg
    # No objects yet, so no import line (an empty `from .<leaf> import` would be
    # a SyntaxError). gh-523: create-only — when `package` aims the module at a
    # pre-existing package, its __init__.py must never be stamped over.
    mod_init = pkg_module_dir / "__init__.py"
    if not mod_init.exists():
        _write(mod_init, T.render(T.MODULE_INIT_PY_EMPTY, mod_slots))
    _write(
        pkg_module_dir / f"{mp.leaf}.pyi",
        S.make_module_pyi(cfg, module, root),
    )

    # Root CMakeLists.txt — insert add_subdirectory into the Modules sentinel
    # section, and fold the module's own OBJECT library into the combined C
    # library the same way a component's is (gh-981). Without the second half a
    # module whose C surface is module-level *functions* builds its core, links
    # it straight into the Python extension, and ships it in no library at all —
    # so `jm function` compiles, imports and tests clean while a C consumer gets
    # `undefined reference`. `component_core_libs` reads the file just written
    # above, so a module with no core of its own (capsule/handle/composer, or a
    # collocated object supplying the `add_library`) wires nothing.
    splice_cmake_component(
        root,
        pkg,
        cname,
        component_core_libs(root, cname),
        sentinel=MODULES_SENTINEL,
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
    # only place its documentation can live. Set before mod_slots is built,
    # since both generated faces read it from there.
    if doc:
        cfg["module"][module]["doc"] = doc
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

    print()
    print(
        f"Done!  Add types with: just-makeit object <name> --module {module}"
    )
