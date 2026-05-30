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

import sys
from pathlib import Path

from . import _config as C
from . import _stubs as S
from . import _render as T
from ._init import _to_title, _write, _write_compile_commands


def run(
    root: Path,
    module: str,
    extra_include_dirs: list[str] | None = None,
    extra_link_libs: list[str] | None = None,
    extra_types: list[str] | None = None,
) -> None:
    if not module.replace("_", "").isalnum() or module[0].isdigit():
        print(
            f"error: '{module}' is not a valid module name.\n"
            "Use lowercase letters, digits, and underscores only; "
            "must not start with a digit.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)

    if module in C.modules(cfg):
        print(f"error: module '{module}' already exists.", file=sys.stderr)
        sys.exit(1)
    if module in C.components(cfg):
        print(
            f"error: '{module}' is already a standalone component. "
            "Modules and components share the same namespace.",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = C.project_name(cfg)
    Module = _to_title(module)

    print(f"just-makeit: scaffolding module '{module}' in project '{pkg}'")
    print()

    mod_ctx = {"module": module, "Module": Module, "MODULE": module.upper()}

    # C header and implementation for module-level functions
    _write(
        root / "native" / "inc" / module / f"{module}_core.h",
        T.render(T.MODULE_CORE_H, mod_ctx),
    )
    _write(
        root / "native" / "src" / module / f"{module}_core.c",
        T.render(T.MODULE_CORE_C, mod_ctx),
    )

    # Empty module ext.c (no types yet — populated by `just-makeit object`)
    ext_c = T.render_module_ext_c(module, [])
    _write(root / "native" / "src" / module / f"{module}_ext.c", ext_c)

    # CMakeLists for the module (no object libs yet)
    cmake_ctx = {
        "module": module,
        "Module": Module,
        "object_list": "",
        "object_core_libs": f"{module}_core",
        "module_core_lib_block": (
            f"add_library({module}_core OBJECT {module}_core.c)\n"
            f"target_include_directories({module}_core PRIVATE"
            f" ${{CMAKE_SOURCE_DIR}}/native/inc)\n\n"
        ),
        "extra_link_libs_block": "",
        "extra_include_dirs_block": "",
    }
    _write(
        root / "native" / "src" / module / "CMakeLists.txt",
        T.render(T.CMAKE_LISTS_MODULE, cmake_ctx),
    )

    # Python subpackage __init__.py — no objects yet, so no import line
    # (an empty `from .<module> import` would be a SyntaxError).
    pkg_module_dir = root / "src" / pkg / module
    _write(
        pkg_module_dir / "__init__.py",
        T.render(T.MODULE_INIT_PY_EMPTY, {"module": module}),
    )
    _write(pkg_module_dir / f"{module}.pyi", S.make_module_pyi(cfg, module))

    # Root CMakeLists.txt — insert add_subdirectory into Modules sentinel section.
    cmake_path = root / "CMakeLists.txt"
    if cmake_path.exists():
        cmake_text = cmake_path.read_text(encoding="utf-8")
        sub = f"add_subdirectory(native/src/{module})\n"
        if sub not in cmake_text:
            sentinel = "# ── Modules"
            if sentinel in cmake_text:
                idx = cmake_text.index(sentinel)
                idx = cmake_text.index("\n", idx) + 1
                cmake_text = cmake_text[:idx] + sub + cmake_text[idx:]
            else:
                cmake_text += sub
            cmake_path.write_text(cmake_text, encoding="utf-8")
            print(f"  update  {cmake_path}")

    # Ensure C test and benchmark directories exist (even before any objects
    # or functions are added — users may want to write their own C tests).
    for subdir in ("native/tests", "native/benchmarks"):
        d = root / subdir
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)

    # Config
    C.scaffold_module(cfg, module)
    # Optional Phase-2 metadata persisted into the [module.X] section so
    # jm apply's renderer picks them up. The renderer + dump already
    # handle these keys (gh-66 / v0.13.22 for include_dirs and link_libs;
    # extra_types from the earlier gh-28 extras work).
    if extra_include_dirs:
        cfg.setdefault("module", {}).setdefault(module, {})["extra_include_dirs"] = (
            list(extra_include_dirs)
        )
    if extra_link_libs:
        cfg.setdefault("module", {}).setdefault(module, {})["extra_link_libs"] = list(
            extra_link_libs
        )
    if extra_types:
        cfg.setdefault("module", {}).setdefault(module, {})["extra_types"] = list(
            extra_types
        )
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    # compile_commands.json — include the new module's ext.c from the start
    _write_compile_commands(root, C.components(cfg), C.modules(cfg))

    print()
    print(f"Done!  Add types with: just-makeit object <name> --module {module}")
