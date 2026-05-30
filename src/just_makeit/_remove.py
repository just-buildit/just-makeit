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
from . import _context as Ctx
from . import _render as R
from ._init import (
    _make_component_ctx,
    _preserve_core_bodies,
    _to_title,
    _write_compile_commands,
)
from ._object import _regenerate_module


_STUB_MARKER = "/* TODO: implement"
_CORE_C_STUB_MARKER = "/* <<IMPLEMENT:"


def _is_implemented(path: Path) -> bool:
    """Return True if path exists and its TODO stub placeholder has been replaced.

    The inline ``step()`` function in a freshly scaffolded ``_core.h``
    contains either ``/* TODO: implement */`` (stateless objects) or
    ``/* TODO: implement using state variables */`` (stateful objects).
    Both share the prefix ``/* TODO: implement``.  Once the user writes their
    algorithm that prefix disappears, making its absence a reliable signal
    that the file holds hand-written code that ``jm remove`` would
    permanently destroy.
    """
    return path.exists() and _STUB_MARKER not in path.read_text(
        encoding="utf-8"
    )


def _core_c_is_implemented(
    path: Path, is_no_state: bool, has_methods: bool
) -> bool:
    """Return True if a ``no_step`` object's ``_core.c`` likely holds user code.

    A freshly scaffolded ``_core.c`` for a ``no_step`` object contains
    ``/* <<IMPLEMENT: ... >> */`` placeholders in:

    - Lifecycle stubs (``create``/``destroy``/``reset``) for ``no_state``
      objects — the struct and its lifecycle are left for the user to fill in.
    - Method bodies added via ``jm method`` — each method stub carries an
      ``/* <<IMPLEMENT: name >> */`` comment.

    Once the user replaces every placeholder, the marker disappears.  Its
    absence is therefore a reliable signal that the file contains hand-written
    code that ``jm remove`` would permanently destroy.

    The exception is a ``no_step + has_state + no methods`` object: its
    generated ``_core.c`` never contains the marker even on a fresh scaffold
    (the lifecycle is fully emitted from the TOML state variables), so the
    heuristic cannot distinguish "untouched" from "modified".  In that case
    the function returns ``False`` to avoid spurious warnings.

    Parameters
    ----------
    path : Path
        Absolute path to ``{obj}_core.c``.
    is_no_state : bool
        True when the object was scaffolded with ``--no-state``.
    has_methods : bool
        True when the TOML lists at least one named extra method.
    """
    if not path.exists():
        return False
    # For has_state + no methods: the generated file never contains the
    # <<IMPLEMENT marker, so we cannot tell fresh from modified.  Skip.
    if not is_no_state and not has_methods:
        return False
    return _CORE_C_STUB_MARKER not in path.read_text(encoding="utf-8")


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


def _warn_if_implemented(core_h: Path) -> bool:
    """Print a stderr warning when *core_h* has hand-written code; return True
    if implemented."""
    if _is_implemented(core_h):
        print(
            f"  warning: {core_h} contains hand-written code"
            " that will be permanently deleted (no recovery without git).",
            file=sys.stderr,
        )
        return True
    return False


def _core_c_warn_if_implemented(
    core_c: Path, is_no_state: bool, has_methods: bool
) -> bool:
    """Print a stderr warning when a ``no_step`` *core_c* has user code; return
    True if implemented."""
    if _core_c_is_implemented(core_c, is_no_state, has_methods):
        print(
            f"  warning: {core_c} contains hand-written code"
            " that will be permanently deleted (no recovery without git).",
            file=sys.stderr,
        )
        return True
    return False


def _remove_object(root: Path, cfg: dict, obj: str, force: bool) -> None:
    pkg = C.project_name(cfg)
    if obj not in C.components(cfg):
        print(f"error: object '{obj}' not found.", file=sys.stderr)
        sys.exit(1)
    module = C.component_module(cfg, obj)

    core_h = root / "native" / "inc" / obj / f"{obj}_core.h"
    if C.is_no_step(cfg, obj):
        core_c = root / "native" / "src" / obj / f"{obj}_core.c"
        implemented = _core_c_warn_if_implemented(
            core_c,
            C.is_no_state(cfg, obj),
            bool(C.methods(cfg, obj)),
        )
        impl_path = core_c
    else:
        implemented = _warn_if_implemented(core_h)
        impl_path = core_h

    where = f" from module '{module}'" if module else ""
    if implemented:
        prompt_note = "\n  This cannot be recovered without git."
    elif impl_path.exists():
        prompt_note = (
            f"\n  note: {impl_path} may hold your hand-written implementation."
        )
    else:
        prompt_note = ""
    if not _confirm(
        f"Remove object '{obj}'{where} and all its generated files?{prompt_note}",
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

    def _obj_implemented(obj: str) -> bool:
        if C.is_no_step(cfg, obj):
            core_c = root / "native" / "src" / obj / f"{obj}_core.c"
            return _core_c_warn_if_implemented(
                core_c,
                C.is_no_state(cfg, obj),
                bool(C.methods(cfg, obj)),
            )
        return _warn_if_implemented(
            root / "native" / "inc" / obj / f"{obj}_core.h"
        )

    any_implemented = any(_obj_implemented(obj) for obj in objects)
    prompt_note = (
        "\n  This cannot be recovered without git." if any_implemented else ""
    )
    if not _confirm(
        f"Remove module '{module}'{detail} and all generated files?{prompt_note}",
        force,
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


def _warn_if_state_ref(core_h: Path, core_c: Path, name: str) -> None:
    """Print a stderr warning if state-><name> appears in any core file."""
    for path in (core_h, core_c):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if f"state->{name}" in text or f"obj->{name}" in text:
            print(
                f"  warning: '{name}' is referenced in {path}."
                " Update any hand-written code that uses it.",
                file=sys.stderr,
            )


def _regenerate_after_state_change(
    root: Path, cfg: dict, obj: str, pkg: str
) -> None:
    """Re-render all generated files after the state list changes.

    Preserves the user's step() body in core.h and non-lifecycle functions
    in core.c, but regenerates create() and reset() so they reflect the
    updated state fields — same pattern used by `jm add`."""
    module = C.component_module(cfg, obj)
    ctx = _object_ctx(cfg, obj, pkg, module)
    excl = (f"{obj}_create", f"{obj}_reset")

    core_h = root / "native" / "inc" / obj / f"{obj}_core.h"
    if core_h.exists():
        core_h.write_text(
            _preserve_core_bodies(
                core_h,
                R.render(R.COMPONENT_CORE_H, ctx),
                obj,
                exclude=excl,
                skip_struct_merge=True,
            ),
            encoding="utf-8",
        )
        print(f"  update  {core_h}")

    core_c = root / "native" / "src" / obj / f"{obj}_core.c"
    if core_c.exists():
        core_c.write_text(
            _preserve_core_bodies(
                core_c, R.render(R.COMPONENT_CORE_C, ctx), obj, exclude=excl
            ),
            encoding="utf-8",
        )
        print(f"  update  {core_c}")

    if module:
        _regenerate_module(root, cfg, module, pkg)
        return

    for path, tmpl in [
        (root / "native" / "src" / obj / f"{obj}_ext.c", R.COMPONENT_EXT_C),
        (root / "src" / pkg / f"{obj}.pyi", R.COMPONENT_PYI),
    ]:
        if path.exists():
            path.write_text(R.render(tmpl, ctx), encoding="utf-8")
            print(f"  update  {path}")

    bench_c = root / "native" / "benchmarks" / f"bench_{obj}_core.c"
    if bench_c.exists():
        tmpl = (
            R.NO_STEP_BENCH_C
            if C.is_no_step(cfg, obj)
            else R.COMPONENT_BENCH_C
        )
        bench_c.write_text(R.render(tmpl, ctx), encoding="utf-8")
        print(f"  update  {bench_c}")


def _remove_state(
    root: Path, cfg: dict, obj: str, name: str, force: bool
) -> None:
    pkg = C.project_name(cfg)
    if obj not in C.components(cfg):
        print(f"error: object '{obj}' not found.", file=sys.stderr)
        sys.exit(1)
    state = cfg.get(obj, {}).get("state", [])
    if not any(s.get("name") == name for s in state):
        print(
            f"error: state field '{name}' not found on object '{obj}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    core_h = root / "native" / "inc" / obj / f"{obj}_core.h"
    core_c = root / "native" / "src" / obj / f"{obj}_core.c"
    _warn_if_state_ref(core_h, core_c, name)

    if not _confirm(
        f"Remove state field '{name}' from object '{obj}'?", force
    ):
        print("Aborted.")
        return

    print(f"just-makeit: removing state field '{name}' from '{obj}'")
    print()
    _drop_named_entry(state, name)
    if not state:
        cfg[obj].pop("state", None)
    C.save(root, cfg)
    print(f"  update  {root / C.FILENAME}")

    _regenerate_after_state_change(root, cfg, obj, pkg)
    print()
    print(f"Done!  State field '{name}' removed.")


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
    ctx.update(Ctx.make_sample_ctx(arg_t, ret_t))
    ctx.update(
        Ctx.make_state_ctx(
            obj,
            Component,
            state_vars,
            array_args=C.array_args(cfg, obj),
            no_state=C.is_no_state(cfg, obj),
            init_params=C.init_params(cfg, obj),
        )
    )
    ctx.update(Ctx.make_perf_ctx(C.is_perf(cfg)))
    ctx.update(
        Ctx.make_step_ctx(
            ctx,
            arg_t,
            ret_t,
            no_step=C.is_no_step(cfg, obj),
            mutable=C.is_mutable(cfg, obj),
        )
    )
    ctx.update(
        Ctx.make_methods_ctx(
            obj,
            Component,
            C.methods(cfg, obj),
            pkg=pkg,
            py_create_args=ctx.get("py_create_args", ""),
            no_state=C.is_no_state(cfg, obj),
        )
    )
    ctx.update(
        Ctx.make_properties_ctx(
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
                core_h, R.render(R.COMPONENT_CORE_H, ctx), obj
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
        ext_c.write_text(R.render(R.COMPONENT_EXT_C, ctx), encoding="utf-8")
        print(f"  update  {ext_c}")
    pyi = root / "src" / pkg / f"{obj}.pyi"
    if pyi.exists():
        pyi.write_text(R.render(R.COMPONENT_PYI, ctx), encoding="utf-8")
        print(f"  update  {pyi}")
    bench_c = root / "native" / "benchmarks" / f"bench_{obj}_core.c"
    if bench_c.exists():
        tmpl = (
            R.NO_STEP_BENCH_C
            if C.is_no_step(cfg, obj)
            else R.COMPONENT_BENCH_C
        )
        bench_c.write_text(R.render(tmpl, ctx), encoding="utf-8")
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
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)
    cfg = C.load(root)

    if kind == "state":
        if not object_name:
            print(
                "error: 'remove state' requires --object <obj>.",
                file=sys.stderr,
            )
            sys.exit(1)
        _remove_state(root, cfg, object_name, name, force)
    elif kind == "object":
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
