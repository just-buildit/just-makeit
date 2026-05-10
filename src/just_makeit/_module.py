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
from . import _templates as T
from ._init import _to_title, _write


def run(root: Path, module: str) -> None:
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

    # Empty module ext.c (no types yet — populated by `just-makeit object`)
    ext_c = T.render_module_ext_c(module, [])
    _write(root / "native" / "src" / module / f"{module}_ext.c", ext_c)

    # CMakeLists for the module (no object libs yet)
    cmake_ctx = {
        "module": module,
        "Module": Module,
        "object_list": "",
        "object_core_libs": "",
    }
    _write(
        root / "native" / "src" / module / "CMakeLists.txt",
        T.render(T.CMAKE_LISTS_MODULE, cmake_ctx),
    )

    # Python subpackage __init__.py (empty exports)
    init_ctx = {
        "module": module,
        "Module": Module,
        "object_imports": "",
        "object_all": "",
    }
    pkg_module_dir = root / "src" / pkg / module
    _write(pkg_module_dir / "__init__.py", T.render(T.MODULE_INIT_PY, init_ctx))

    # Root CMakeLists.txt — append add_subdirectory
    cmake_path = root / "CMakeLists.txt"
    if cmake_path.exists():
        cmake_text = cmake_path.read_text(encoding="utf-8")
        cmake_text += f"add_subdirectory(native/src/{module})\n"
        cmake_path.write_text(cmake_text, encoding="utf-8")
        print(f"  update  {cmake_path}")

    # Config
    C.scaffold_module(cfg, module)
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    print()
    print(f"Done!  Add types with: just-makeit object <name> --module {module}")
