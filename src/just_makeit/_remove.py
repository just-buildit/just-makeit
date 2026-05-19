"""
_remove.py — `just-makeit remove` command.

The explicit, destructive counterpart to the additive commands. Removes a
scaffolded object, module, method, property, or function: deletes the
generated files, strips the CMakeLists / umbrella-header / package
`__init__.py` wiring, and drops the entry from `just-makeit.toml`.

Kept separate from `apply` so deletion is always a deliberate act, never an
inferred side effect of a reconcile. Prompts for confirmation unless
`--force` is given.
"""

import shutil
import sys
from pathlib import Path

from . import _config as C
from . import _templates as T
from ._init import (
    _make_component_ctx,
    _preserve_core_bodies,
    _to_title,
    _write_compile_commands,
)
from ._object import _regenerate_module


def _confirm(prompt: str, force: bool) -> bool:
    """Ask the user to confirm a destructive action; --force skips the prompt."""
    if force:
        return True
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    return answer in ("y", "yes")


def _rm(path: Path) -> None:
    """Delete a file or directory tree if it exists, logging what was removed."""
    if path.is_dir():
        shutil.rmtree(path)
        print(f"  remove  {path}/")
    elif path.exists():
        path.unlink()
        print(f"  remove  {path}")


def _object_paths(
    root: Path, pkg: str, obj: str, module: str | None
) -> list[Path]:
    """Return every generated path that belongs to object *obj*."""
    paths = [
        root / "native" / "inc" / obj,
        root / "native" / "src" / obj,
        root / "native" / "tests" / f"test_{obj}_core.c",
        root / "native" / "benchmarks" / f"bench_{obj}_core.c",
    ]
    if module:
        sub = root / "src" / pkg / module
        paths += [
            sub / "tests" / f"test_{obj}.py",
            sub / "benchmarks" / f"bench_{obj}.py",
        ]
    else:
        paths += [
            root / "src" / pkg / f"{obj}.pyi",
            root / "src" / pkg / "tests" / f"test_{obj}.py",
            root / "src" / pkg / "benchmarks" / f"bench_{obj}.py",
        ]
    return paths


def _strip_cmake_object(root: Path, obj: str) -> None:
    """Drop the add_subdirectory + target_sources lines for *obj* from the top
    CMakeLists.txt."""
    cmake = root / "CMakeLists.txt"
    if not cmake.exists():
        return
    lines = cmake.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [
        ln
        for ln in lines
        if ln.strip() != f"add_subdirectory(native/src/{obj})"
        and f"$<TARGET_OBJECTS:{obj}_core>" not in ln
    ]
    if len(kept) != len(lines):
        cmake.write_text("".join(kept), encoding="utf-8")
        print(f"  update  {cmake}")


def _strip_cmake_module(root: Path, module: str) -> None:
    """Drop the module's add_subdirectory line from the top CMakeLists.txt."""
    cmake = root / "CMakeLists.txt"
    if not cmake.exists():
        return
    lines = cmake.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [
        ln
        for ln in lines
        if ln.strip() != f"add_subdirectory(native/src/{module})"
    ]
    if len(kept) != len(lines):
        cmake.write_text("".join(kept), encoding="utf-8")
        print(f"  update  {cmake}")


def _strip_umbrella(root: Path, pkg: str, obj: str) -> None:
    """Drop the object's #include line from the umbrella header."""
    umbrella = root / "native" / "inc" / f"{pkg}.h"
    if not umbrella.exists():
        return
    include = f'#include "{obj}/{obj}_core.h"\n'
    text = umbrella.read_text(encoding="utf-8")
    if include in text:
        umbrella.write_text(text.replace(include, "", 1), encoding="utf-8")
        print(f"  update  {umbrella}")


def _strip_pkg_init(root: Path, pkg: str, obj: str, Component: str) -> None:
    """Remove a standalone object's import + __all__ entry from the package
    __init__.py (the reverse of _splice_init_py)."""
    init = root / "src" / pkg / "__init__.py"
    if not init.exists():
        return
    lines = init.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [
        ln
        for ln in lines
        if not ln.startswith(f"from .{obj} import {Component}")
    ]
    text = "".join(kept)
    # Drop the name from __all__ = [...], handling both quote styles.
    for token in (f'"{Component}"', f"'{Component}'"):
        text = (
            text.replace(f"[{token}, ", "[")
            .replace(f", {token}]", "]")
            .replace(f"[{token}]", "[]")
        )
    if text != "".join(lines):
        init.write_text(text, encoding="utf-8")
        print(f"  update  {init}")


def _remove_object_files(
    root: Path, cfg: dict, pkg: str, obj: str, module: str | None
) -> None:
    """Delete every generated file/dir for *obj* and strip its build wiring."""
    for path in _object_paths(root, pkg, obj, module):
        _rm(path)
    _strip_cmake_object(root, obj)
    _strip_umbrella(root, pkg, obj)
    if module is None:
        _strip_pkg_init(root, pkg, obj, _to_title(obj))


def _remove_object(root: Path, cfg: dict, obj: str, force: bool) -> None:
    pkg = C.project_name(cfg)
    if obj not in C.components(cfg):
        print(f"error: object '{obj}' not found.", file=sys.stderr)
        sys.exit(1)
    module = C.component_module(cfg, obj)

    core_c = root / "native" / "src" / obj / f"{obj}_core.c"
    warn = (
        f"\n  note: {core_c} may hold your hand-written implementation."
        if core_c.exists()
        else ""
    )
    where = f" from module '{module}'" if module else ""
    if not _confirm(
        f"Remove object '{obj}'{where} and all its generated files?{warn}",
        force,
    ):
        print("Aborted.")
        return

    print(f"just-makeit: removing object '{obj}'")
    print()
    _remove_object_files(root, cfg, pkg, obj, module)

    # Drop the TOML section and module membership.
    cfg.pop(obj, None)
    if module:
        objs = cfg["module"][module].get("objects", [])
        if obj in objs:
            objs.remove(obj)
    C.save(root, cfg)
    print(f"  update  {root / C.FILENAME}")

    # Refresh the module's shared ext.c / CMakeLists / __init__ for the
    # objects that remain.
    if module:
        _regenerate_module(root, cfg, module, pkg)

    _write_compile_commands(root, C.components(cfg), C.modules(cfg))
    print()
    print(f"Done!  Object '{obj}' removed.")


def _remove_module(root: Path, cfg: dict, module: str, force: bool) -> None:
    pkg = C.project_name(cfg)
    if module not in C.modules(cfg):
        print(f"error: module '{module}' not found.", file=sys.stderr)
        sys.exit(1)

    objects = C.module_objects(cfg, module)
    detail = f" and its objects ({', '.join(objects)})" if objects else ""
    if not _confirm(
        f"Remove module '{module}'{detail} and all generated files?", force
    ):
        print("Aborted.")
        return

    print(f"just-makeit: removing module '{module}'")
    print()

    # Each object owns its own native/inc, native/src, and build wiring.
    for obj in objects:
        _remove_object_files(root, cfg, pkg, obj, module)
        cfg.pop(obj, None)

    # The module's own files.
    _rm(root / "native" / "inc" / module)
    _rm(root / "native" / "src" / module)
    _rm(root / "src" / pkg / module)
    _strip_cmake_module(root, module)

    cfg.get("module", {}).pop(module, None)
    if cfg.get("module") == {}:
        cfg.pop("module", None)
    C.save(root, cfg)
    print(f"  update  {root / C.FILENAME}")

    _write_compile_commands(root, C.components(cfg), C.modules(cfg))
    print()
    print(f"Done!  Module '{module}' removed.")


def _drop_named_entry(entries: list[dict], name: str) -> bool:
    """Remove the first entry whose 'name' matches; return True if found."""
    for i, entry in enumerate(entries):
        if entry.get("name") == name:
            del entries[i]
            return True
    return False


def _remove_method(
    root: Path, cfg: dict, obj: str, name: str, force: bool
) -> None:
    pkg = C.project_name(cfg)
    if obj not in C.components(cfg):
        print(f"error: object '{obj}' not found.", file=sys.stderr)
        sys.exit(1)
    methods = cfg.get(obj, {}).get("methods", [])
    if not any(m.get("name") == name for m in methods):
        print(
            f"error: method '{name}' not found on object '{obj}'.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not _confirm(f"Remove method '{name}' from object '{obj}'?", force):
        print("Aborted.")
        return

    print(f"just-makeit: removing method '{name}' from '{obj}'")
    print()
    _drop_named_entry(methods, name)
    if not methods:
        cfg[obj].pop("methods", None)
    C.save(root, cfg)
    print(f"  update  {root / C.FILENAME}")

    _regenerate_object_bindings(root, cfg, obj, pkg)
    print()
    print(
        f"Done!  Method '{name}' removed."
        f"\n  note: {obj}_{name}() remains in {obj}_core.c — delete it by hand."
    )


def _remove_property(
    root: Path, cfg: dict, obj: str, name: str, force: bool
) -> None:
    pkg = C.project_name(cfg)
    if obj not in C.components(cfg):
        print(f"error: object '{obj}' not found.", file=sys.stderr)
        sys.exit(1)
    props = cfg.get(obj, {}).get("properties", [])
    entry = next((p for p in props if p.get("name") == name), None)
    if entry is None:
        print(
            f"error: property '{name}' not found on object '{obj}'.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not _confirm(f"Remove property '{name}' from object '{obj}'?", force):
        print("Aborted.")
        return

    print(f"just-makeit: removing property '{name}' from '{obj}'")
    print()
    is_field = bool(entry.get("field"))
    _drop_named_entry(props, name)
    if not props:
        cfg[obj].pop("properties", None)
    C.save(root, cfg)
    print(f"  update  {root / C.FILENAME}")

    _regenerate_object_bindings(root, cfg, obj, pkg)
    print()
    note = (
        ""
        if is_field
        else f"\n  note: {obj}_get_{name}()/{obj}_set_{name}() remain in "
        f"{obj}_core.c — delete them by hand."
    )
    print(f"Done!  Property '{name}' removed.{note}")


def _remove_function(
    root: Path, cfg: dict, module: str, name: str, force: bool
) -> None:
    pkg = C.project_name(cfg)
    if module not in C.modules(cfg):
        print(f"error: module '{module}' not found.", file=sys.stderr)
        sys.exit(1)
    fns = cfg.get("module", {}).get(module, {}).get("functions", [])
    if not any(f.get("name") == name for f in fns):
        print(
            f"error: function '{name}' not found in module '{module}'.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not _confirm(
        f"Remove function '{name}' from module '{module}'?", force
    ):
        print("Aborted.")
        return

    print(f"just-makeit: removing function '{name}' from module '{module}'")
    print()
    _drop_named_entry(fns, name)
    if not fns:
        cfg["module"][module].pop("functions", None)
    C.save(root, cfg)
    print(f"  update  {root / C.FILENAME}")

    _regenerate_module(root, cfg, module, pkg)
    print()
    print(
        f"Done!  Function '{name}' removed."
        f"\n  note: {name}() remains in {module}_core.c — delete it by hand."
    )


def _object_ctx(cfg: dict, obj: str, pkg: str, module: str | None) -> dict:
    """Build the full render ctx for regenerating object *obj*."""
    state_vars = C.state_vars(cfg, obj)
    arg_t = C.arg_type(cfg, obj)
    ret_t = C.return_type(cfg, obj)
    Component = _to_title(obj)
    ctx = _make_component_ctx(obj)
    ctx.update(
        {
            "package": pkg,
            "PACKAGE": pkg.upper(),
            "project": pkg.replace("_", "-"),
            "project_underscore": pkg,
            "version": C.project_version(cfg),
        }
    )
    if module:
        ctx.update({"module": module, "Module": _to_title(module)})
    ctx.update(T.make_sample_ctx(arg_t, ret_t))
    ctx.update(
        T.make_state_ctx(
            obj,
            Component,
            state_vars,
            array_args=C.array_args(cfg, obj),
            no_state=C.is_no_state(cfg, obj),
            init_params=C.init_params(cfg, obj),
        )
    )
    ctx.update(T.make_perf_ctx(C.is_perf(cfg)))
    ctx.update(
        T.make_step_ctx(
            ctx,
            arg_t,
            ret_t,
            no_step=C.is_no_step(cfg, obj),
            mutable=C.is_mutable(cfg, obj),
        )
    )
    ctx.update(
        T.make_methods_ctx(
            obj,
            Component,
            C.methods(cfg, obj),
            pkg=pkg,
            py_create_args=ctx.get("py_create_args", ""),
        )
    )
    ctx.update(
        T.make_properties_ctx(
            obj,
            Component,
            C.properties(cfg, obj),
            frozenset(n for n, _, _ in state_vars),
        )
    )
    return ctx


def _regenerate_object_bindings(
    root: Path, cfg: dict, obj: str, pkg: str
) -> None:
    """Re-render core.h / ext.c / .pyi / bench after a method or property
    entry was dropped from the TOML."""
    module = C.component_module(cfg, obj)
    ctx = _object_ctx(cfg, obj, pkg, module)

    core_h = root / "native" / "inc" / obj / f"{obj}_core.h"
    if core_h.exists():
        core_h.write_text(
            _preserve_core_bodies(
                core_h, T.render(T.COMPONENT_CORE_H, ctx), obj
            ),
            encoding="utf-8",
        )
        print(f"  update  {core_h}")

    if module:
        # The module's shared ext.c / CMakeLists / __init__ / .pyi.
        _regenerate_module(root, cfg, module, pkg)
        return

    ext_c = root / "native" / "src" / obj / f"{obj}_ext.c"
    if ext_c.exists():
        ext_c.write_text(T.render(T.COMPONENT_EXT_C, ctx), encoding="utf-8")
        print(f"  update  {ext_c}")
    pyi = root / "src" / pkg / f"{obj}.pyi"
    if pyi.exists():
        pyi.write_text(T.render(T.COMPONENT_PYI, ctx), encoding="utf-8")
        print(f"  update  {pyi}")
    bench_c = root / "native" / "benchmarks" / f"bench_{obj}_core.c"
    if bench_c.exists():
        tmpl = (
            T.NO_STEP_BENCH_C
            if C.is_no_step(cfg, obj)
            else T.COMPONENT_BENCH_C
        )
        bench_c.write_text(T.render(tmpl, ctx), encoding="utf-8")
        print(f"  update  {bench_c}")


def run(
    root: Path,
    kind: str,
    name: str,
    module: str | None = None,
    object_name: str | None = None,
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

    if kind == "object":
        _remove_object(root, cfg, name, force)
    elif kind == "module":
        _remove_module(root, cfg, name, force)
    elif kind == "method":
        if not object_name:
            print(
                "error: 'remove method' requires --object <obj>.",
                file=sys.stderr,
            )
            sys.exit(1)
        _remove_method(root, cfg, object_name, name, force)
    elif kind == "property":
        if not object_name:
            print(
                "error: 'remove property' requires --object <obj>.",
                file=sys.stderr,
            )
            sys.exit(1)
        _remove_property(root, cfg, object_name, name, force)
    elif kind == "function":
        if not module:
            print(
                "error: 'remove function' requires --module <mod>.",
                file=sys.stderr,
            )
            sys.exit(1)
        _remove_function(root, cfg, module, name, force)
    else:
        print(f"error: cannot remove '{kind}'.", file=sys.stderr)
        sys.exit(1)
