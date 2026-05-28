"""
_apply.py — `just-makeit apply` command.

Materialize a project from its `just-makeit.toml`: generate every file each
object / module / method / property / function in the manifest implies.

`apply` is **add only** — it creates files that are missing and never
overwrites or deletes anything (deletion is `jm remove`'s job). It is safe
to run repeatedly; on a fully materialized project it is a no-op.

Implementation: replay the project's full scaffold into a throwaway temp
directory — reusing every generator command — then copy across only the
files the real project does not already have. A project is thus
reproducible from `just-makeit.toml` (plus any hand-written `*_core.c` /
`*_core.h`) alone.
"""

import contextlib
import io
import re
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

from . import _config as C
from ._init import _to_title


# `{identifier}` placeholders only — anything else passes through untouched.
# In particular, bare C braces (`{ … }`, `{0}`) are NOT consumed.
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate(body: str, ctx: dict) -> str:
    """Replace `{name}` placeholders with ctx[name]; unknown names are
    left in place so literal `{0}` / `{ static int x; }` C code survives."""
    return _PLACEHOLDER_RE.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), body)


def _resolve_impl(
    section: dict,
    ctx: dict,
    root: Path,
    label: str,
    impl_key: str = "impl",
    impl_file_key: str = "impl_file",
) -> str | None:
    """Honour `<impl_key>` / `<impl_file_key>` / `replace` on a TOML section.

    Returns the resolved body (interpolated + substituted) or None when
    neither key is set.  Raises ValueError on mutual-exclusion violations.
    The *replace* dict is only honoured for the default impl/impl_file pair."""
    inline = section.get(impl_key)
    file_ref = section.get(impl_file_key)
    if inline and file_ref:
        raise ValueError(
            f"{label}: `{impl_key}` and `{impl_file_key}` are mutually "
            f"exclusive — set one or the other."
        )
    if not inline and not file_ref:
        return None

    if file_ref:
        from . import _impl as I

        path_part, _, func = file_ref.partition("::")
        if not func:
            raise ValueError(
                f"{label}: {impl_file_key} must be 'path::funcname', got {file_ref!r}."
            )
        body = I.extract_body(root / path_part, func)
    else:
        body = inline

    body = _interpolate(body, ctx)

    if impl_key == "impl":
        replace = section.get("replace") or {}
        if replace:
            from . import _impl as I

            body = I.apply_replacements(body, list(replace.items()))

    return body


# Directory names and filenames never copied from the replay.
_SKIP_DIRS = {"build", ".venv", ".git", "dist", "__pycache__"}
_SKIP_FILES = {C.FILENAME, "compile_commands.json"}
_SKIP_SUFFIXES = {".pyc", ".pyo", ".so", ".pyd"}


def _object_kwargs(cfg: dict, comp: str) -> dict:
    """CLI-equivalent kwargs for re-running `object` generation for *comp*."""
    return {
        "state_vars": C.state_vars(cfg, comp),
        "perf": C.is_perf(cfg),
        "arg_type": C.arg_type(cfg, comp),
        "return_type": C.return_type(cfg, comp),
        "array_args": C.array_args(cfg, comp),
        "no_state": C.is_no_state(cfg, comp),
        "no_step": C.is_no_step(cfg, comp),
        "mutable": C.is_mutable(cfg, comp),
        "init_params": C.init_params(cfg, comp),
        "init_post_parse_impl": C.init_post_parse(cfg, comp),
        "class_name": C.class_name(cfg, comp),
        "depends_on": C.depends_on(cfg, comp),
        "opaque_fields": C.opaque_fields(cfg, comp),
        "no_ctor_names": C.no_ctor_names(cfg, comp),
    }


def _object_ctx(cfg: dict, comp: str, module: str | None) -> dict:
    """Interpolation context for an object's impl body."""
    return {
        "component": comp,
        "Component": _to_title(comp),
        "module": module or "",
        "Module": _to_title(module) if module else "",
        "arg_type": C.arg_type(cfg, comp),
        "return_type": C.return_type(cfg, comp),
    }


def _replay(cfg: dict, temp_root: Path, project_root: Path) -> None:
    """Re-run the full scaffold for *cfg* into the pristine *temp_root*.

    *project_root* is the source project (not the temp) — `impl_file`
    paths in the TOML are resolved relative to it."""
    from . import _function, _method, _module, _new, _object, _property

    project = C.project_name(cfg)
    mods = C.modules(cfg)
    module_owned = {o for m in mods for o in C.module_objects(cfg, m)}
    standalone = [c for c in C.components(cfg) if c not in module_owned]

    _new.run(
        project,
        temp_root,
        [],
        [],
        build_system=C.build_system(cfg),
        perf=C.is_perf(cfg),
        pytest_=C.is_pytest(cfg),
        pytest_benchmark_=C.is_pytest_benchmark(cfg),
    )
    # Stamp the real project's version so generated files (pyproject, .pyi)
    # carry it rather than the `new` default.
    tcfg = C.load(temp_root)
    tcfg["project"]["version"] = C.project_version(cfg)
    C.save(temp_root, tcfg)

    # `new` with no objects writes the minimal package __init__.py; the
    # first standalone object is what generates the full one (DLL-dir
    # preamble included). Drop the placeholder so that object regenerates
    # it exactly as a normal scaffold would.
    if standalone:
        (temp_root / "src" / project / "__init__.py").unlink(missing_ok=True)

    for mod in mods:
        if C.is_no_generate_module(cfg, mod):
            continue
        _module.run(temp_root, mod)

    # After module scaffolding, copy module-level metadata (e.g.
    # extra_link_libs) from the real project TOML into the temp TOML so
    # _regenerate_module() inside object.run() picks it up.
    _mods_need_update = [
        m
        for m in mods
        if not C.is_no_generate_module(cfg, m)
        and (
            cfg.get("module", {}).get(m, {}).get("extra_link_libs")
            or cfg.get("module", {}).get(m, {}).get("extra_types")
        )
    ]
    if _mods_need_update:
        tcfg2 = C.load(temp_root)
        for mod in _mods_need_update:
            mod_data = cfg["module"][mod]
            tmod = tcfg2.setdefault("module", {}).setdefault(mod, {})
            if mod_data.get("extra_types"):
                tmod["extra_types"] = mod_data["extra_types"]
            if mod_data.get("extra_link_libs"):
                tmod["extra_link_libs"] = mod_data["extra_link_libs"]
        C.save(temp_root, tcfg2)

    # Seed _extra.c files from the real project so _regenerate_module()
    # detects and re-includes them in the temp aggregator.
    for mod in mods:
        src_dir = project_root / "native" / "src" / mod
        dst_dir = temp_root / "native" / "src" / mod
        if src_dir.is_dir():
            dst_dir.mkdir(parents=True, exist_ok=True)
            for extra in src_dir.glob("*_extra.c"):
                shutil.copy2(extra, dst_dir / extra.name)

    for comp in standalone:
        octx = _object_ctx(cfg, comp, None)
        sec = cfg.get(comp, {})
        impl = _resolve_impl(sec, octx, project_root, f"object {comp}")
        create_impl = _resolve_impl(
            sec,
            octx,
            project_root,
            f"object {comp} create",
            impl_key="create_impl",
            impl_file_key="create_impl_file",
        )
        reset_impl = _resolve_impl(
            sec,
            octx,
            project_root,
            f"object {comp} reset",
            impl_key="reset_impl",
            impl_file_key="reset_impl_file",
        )
        destroy_impl = _resolve_impl(
            sec,
            octx,
            project_root,
            f"object {comp} destroy",
            impl_key="destroy_impl",
            impl_file_key="destroy_impl_file",
        )
        _object.run(
            temp_root,
            comp,
            None,
            impl_body=impl,
            create_impl_body=create_impl,
            reset_impl_body=reset_impl,
            destroy_impl_body=destroy_impl,
            **_object_kwargs(cfg, comp),
        )
    for mod in mods:
        if C.is_no_generate_module(cfg, mod):
            continue
        for comp in C.module_objects(cfg, mod):
            octx = _object_ctx(cfg, comp, mod)
            sec = cfg.get(comp, {})
            impl = _resolve_impl(sec, octx, project_root, f"object {comp}")
            create_impl = _resolve_impl(
                sec,
                octx,
                project_root,
                f"object {comp} create",
                impl_key="create_impl",
                impl_file_key="create_impl_file",
            )
            reset_impl = _resolve_impl(
                sec,
                octx,
                project_root,
                f"object {comp} reset",
                impl_key="reset_impl",
                impl_file_key="reset_impl_file",
            )
            destroy_impl = _resolve_impl(
                sec,
                octx,
                project_root,
                f"object {comp} destroy",
                impl_key="destroy_impl",
                impl_file_key="destroy_impl_file",
            )
            _object.run(
                temp_root,
                comp,
                mod,
                impl_body=impl,
                create_impl_body=create_impl,
                reset_impl_body=reset_impl,
                destroy_impl_body=destroy_impl,
                **_object_kwargs(cfg, comp),
            )

    all_comps = standalone + [o for m in mods for o in C.module_objects(cfg, m)]
    for comp in all_comps:
        mod = C.component_module(cfg, comp)
        for m in C.methods(cfg, comp):
            mctx = _object_ctx(cfg, comp, mod) | {"method": m["name"]}
            m_impl = _resolve_impl(m, mctx, project_root, f"{comp}.{m['name']}")
            _method.run(
                temp_root,
                comp,
                m["name"],
                mod,
                m.get("arg_type", "void"),
                m.get("return_type", "float _Complex"),
                bool(m.get("variable_output")),
                list(m.get("multi_output", [])),
                params=[(p["name"], p["type"]) for p in m.get("params", [])],
                out_type=m.get("out_type"),
                out_divisor=int(m.get("out_divisor", 1)),
                batch=bool(m.get("batch")),
                impl_body=m_impl,
                none_on_empty=bool(m.get("none_on_empty")),
                result_fields=list(m.get("result_fields", [])),
                max_results=int(m.get("max_results", 64)),
                py_return_type=m.get("py_return_type", ""),
            )
        for p in C.properties(cfg, comp):
            _property.run(
                temp_root,
                comp,
                p["name"],
                mod,
                p.get("type") or p.get("ctype", "size_t"),
                bool(p.get("writable")),
                field=bool(p.get("field")),
                buf_field=p.get("buf_field", ""),
                len_field=p.get("len_field", "n"),
                valid_field=p.get("valid_field", ""),
                expr=p.get("expr", ""),
            )

    for mod in mods:
        if C.is_no_generate_module(cfg, mod):
            continue
        for fn in C.module_functions(cfg, mod):
            fctx = {
                "function": fn["name"],
                "module": mod,
                "Module": _to_title(mod),
                "return_type": fn.get("return_type", "void"),
            }
            f_impl = _resolve_impl(fn, fctx, project_root, f"function {fn['name']}")
            _function.run(
                temp_root,
                fn["name"],
                mod,
                doc=fn.get("doc", ""),
                params=[(p["name"], p["type"]) for p in fn.get("params", [])],
                return_type=fn.get("return_type", "void"),
                impl_body=f_impl,
                out_type=fn.get("out_type", ""),
                result_fields=fn.get("result_fields", []),
                max_results_param=fn.get("max_results_param", ""),
            )


def _sync_missing(temp_root: Path, root: Path) -> list[Path]:
    """Copy every file present in *temp_root* but missing from *root*.

    Returns the created paths, relative to *root*."""
    created: list[Path] = []
    for src in sorted(temp_root.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(temp_root)
        if set(rel.parts) & _SKIP_DIRS or rel.name in _SKIP_FILES:
            continue
        if rel.suffix in _SKIP_SUFFIXES:
            continue
        dst = root / rel
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        created.append(rel)
    return created


_SUBDIR_BLOCK = re.compile(
    r"^add_subdirectory\(native/src/(\w+)\)\s*\n"
    r"(?:^target_sources\(\w+ PRIVATE \$<TARGET_OBJECTS:\w+_core>\)\s*\n)*",
    re.MULTILINE,
)


def _splice_cmake_components(real_path: Path, temp_path: Path, cfg: dict) -> bool:
    """Reconcile the top CMakeLists's component / module wiring.

    Extracts every `add_subdirectory(native/src/X)` block (with adjacent
    `target_sources(... TARGET_OBJECTS:X_core)` lines) from *temp_path*
    wherever they appear, removes any existing blocks from *real_path*,
    and inserts them into the `# ── Components` / `# ── Modules` sentinel
    sections — components in one, modules in the other, so the layout
    matches a freshly-scaffolded project. Content outside those two
    sentinels (e.g. doppler's vendored-libzmq block) is preserved."""
    real = real_path.read_text(encoding="utf-8")
    temp = temp_path.read_text(encoding="utf-8")

    module_names = set(C.modules(cfg))
    component_blocks: list[str] = []
    module_blocks: list[str] = []
    for m in _SUBDIR_BLOCK.finditer(temp):
        (module_blocks if m.group(1) in module_names else component_blocks).append(
            m.group(0)
        )

    # c_deps: pure add_subdirectory, no Python scaffolding.
    # Prepended so their targets exist before any depending component emits
    # target_sources(...TARGET_OBJECTS:dep_core...).
    seen_blocks = {b.split("\n")[0] for b in component_blocks}
    dep_blocks: list[str] = []
    for dep in C.c_deps(cfg):
        line = f"add_subdirectory(native/src/{dep})\n"
        if line.rstrip("\n") not in seen_blocks:
            dep_blocks.append(line)
    component_blocks = dep_blocks + component_blocks

    # no_generate modules: add_subdirectory only; all source files are hand-written.
    seen_mod_blocks = {b.split("\n")[0] for b in module_blocks}
    for mod in C.modules(cfg):
        if C.is_no_generate_module(cfg, mod):
            line = f"add_subdirectory(native/src/{mod})\n"
            if line.rstrip("\n") not in seen_mod_blocks:
                module_blocks.append(line)

    new_real = _SUBDIR_BLOCK.sub("", real)

    def _insert(text: str, sentinel: str, content: str) -> str:
        if not content or sentinel not in text:
            return text
        idx = text.index(sentinel)
        idx = text.index("\n", idx) + 1
        return text[:idx] + content + text[idx:]

    new_real = _insert(new_real, "# ── Components", "".join(component_blocks))
    new_real = _insert(new_real, "# ── Modules", "".join(module_blocks))

    if new_real != real:
        real_path.write_text(new_real, encoding="utf-8")
        return True
    return False


def _merge_pkg_init(real_path: Path, temp_path: Path) -> bool:
    """Splice every missing `from .X import Y` import from *temp_path* into
    *real_path*, preserving user content. Returns True if modified."""
    from ._init import _splice_init_py

    temp_text = temp_path.read_text(encoding="utf-8")
    imports = re.findall(r"^from \.(\w+) import (\w+)", temp_text, re.MULTILINE)
    changed = False
    for comp, Component in imports:
        cur = real_path.read_text(encoding="utf-8")
        if f"from .{comp} import {Component}" in cur:
            continue
        _splice_init_py(real_path, comp, Component)
        changed = True
    return changed


def _merge_module_init_file(real_path: Path, module: str, temp_path: Path) -> bool:
    """Run _merge_module_init against *real_path*, using the export list
    parsed out of *temp_path*'s import line. Preserves any user wrapper
    classes already in the real file."""
    from ._object import _merge_module_init

    temp_text = temp_path.read_text(encoding="utf-8")
    m = re.search(
        rf"^from \.{re.escape(module)} import[ \t]*"
        r"(\([^)]*\)|[^\n]*)[^\n]*$",
        temp_text,
        re.MULTILINE,
    )
    if not m:
        return False
    raw = re.sub(r"#[^\n]*", "", m.group(1)).strip().strip("()")
    exports = [n.strip() for n in raw.split(",") if n.strip()]
    if not exports:
        return False

    existing = real_path.read_text(encoding="utf-8")
    merged = _merge_module_init(existing, module, exports)
    if merged != existing:
        real_path.write_text(merged, encoding="utf-8")
        return True
    return False


def _overwrite_if_changed(real: Path, temp: Path) -> bool:
    """Overwrite *real* with *temp*'s bytes if they differ."""
    if not real.exists() or not temp.exists():
        return False
    if real.read_bytes() == temp.read_bytes():
        return False
    real.write_bytes(temp.read_bytes())
    return True


def _add_cmake_block_for(
    real_path: Path, temp_path: Path, comp: str, cfg: dict
) -> bool:
    """Insert the `add_subdirectory` block for *comp* into *real_path*.

    Reads the generated cmake from *temp_path*, locates the block that
    matches *comp* via _SUBDIR_BLOCK, and inserts it immediately after the
    sentinel line `# ── Modules` (when *comp* is a module) or
    `# ── Components` (otherwise).  If the block is already present in
    *real_path*, or cannot be found in *temp_path*, returns False.

    This is the narrow-scope variant used by --only: it adds exactly one
    component's wiring instead of re-splicing all components.
    """
    real = real_path.read_text(encoding="utf-8")
    # Guard: block already wired in.
    if f"add_subdirectory(native/src/{comp})" in real:
        return False

    temp = temp_path.read_text(encoding="utf-8")
    block: str | None = None
    for m in _SUBDIR_BLOCK.finditer(temp):
        if m.group(1) == comp:
            block = m.group(0)
            break
    if block is None:
        return False

    module_names = set(C.modules(cfg))
    sentinel = "# ── Modules" if comp in module_names else "# ── Components"
    if sentinel not in real:
        return False

    idx = real.index(sentinel)
    idx = real.index("\n", idx) + 1
    new_real = real[:idx] + block + real[idx:]
    real_path.write_text(new_real, encoding="utf-8")
    return True


def _add_umbrella_include(real_path: Path, temp_path: Path, comp: str) -> bool:
    """Insert `#include "comp/comp_core.h"` into the umbrella header.

    Reads *temp_path* to confirm the include line is present in the
    generated output (module objects are NOT in the umbrella, so we skip
    them gracefully).  If the line already exists in *real_path*, or is
    absent from *temp_path*, returns False without touching anything.

    The line is inserted immediately before the final `#endif` so the
    header remains valid C.
    """
    include_line = f'#include "{comp}/{comp}_core.h"'
    real = real_path.read_text(encoding="utf-8")
    if include_line in real:
        return False
    temp = temp_path.read_text(encoding="utf-8")
    if include_line not in temp:
        return False

    # Insert before the last #endif
    last_endif = real.rfind("#endif")
    if last_endif == -1:
        return False
    new_real = real[:last_endif] + include_line + "\n" + real[last_endif:]
    real_path.write_text(new_real, encoding="utf-8")
    return True


def _sync_aggregates(
    temp_root: Path,
    root: Path,
    cfg: dict,
    *,
    only_mod: str | None = None,
    only_comp: str | None = None,
) -> list[Path]:
    """Reconcile wiring files that already exist on disk and so are
    skipped by _sync_missing but need to absorb newly-materialized
    components: top CMakeLists, umbrella header, package __init__.py,
    and each module's __init__.py / ext.c / CMakeLists / .pyi.

    When *only_comp* is set (e.g. ``--only fir`` where fir lives in the
    dsp module):

    - Root CMakeLists: only *comp*'s single block is inserted (additive);
      other components are left untouched.
    - Umbrella header: only *comp*'s include line is inserted (additive).
    - Module loop: only the module that owns *comp* is processed.

    When *only_mod* is set but *only_comp* is None (e.g. ``--only dsp``):

    - Root CMakeLists: full splice for all spectral-owned components.
    - Umbrella header: full overwrite.
    - Module loop: only the named module is processed.

    Package __init__.py is always merged (it is already additive and safe
    to run unconditionally).
    """
    pkg = C.project_name(cfg)
    updated: list[Path] = []

    real_cmake = root / "CMakeLists.txt"
    temp_cmake = temp_root / "CMakeLists.txt"
    if real_cmake.exists() and temp_cmake.exists():
        if only_comp is not None:
            if _add_cmake_block_for(real_cmake, temp_cmake, only_comp, cfg):
                updated.append(real_cmake)
        else:
            if _splice_cmake_components(real_cmake, temp_cmake, cfg):
                updated.append(real_cmake)

    umbrella = root / "native" / "inc" / f"{pkg}.h"
    temp_umbrella = temp_root / "native" / "inc" / f"{pkg}.h"
    if only_comp is not None:
        if umbrella.exists() and temp_umbrella.exists():
            if _add_umbrella_include(umbrella, temp_umbrella, only_comp):
                updated.append(umbrella)
    else:
        if _overwrite_if_changed(umbrella, temp_umbrella):
            updated.append(umbrella)

    pkg_init = root / "src" / pkg / "__init__.py"
    temp_pkg_init = temp_root / "src" / pkg / "__init__.py"
    if pkg_init.exists() and temp_pkg_init.exists():
        if _merge_pkg_init(pkg_init, temp_pkg_init):
            updated.append(pkg_init)

    for mod in C.modules(cfg):
        if C.is_no_generate_module(cfg, mod):
            continue
        if only_mod is not None and mod != only_mod:
            continue
        # Module subpackage __init__.py — merged so user wrapper classes
        # below the re-exports survive (the gh#1 contract).
        mod_init = root / "src" / pkg / mod / "__init__.py"
        temp_mod_init = temp_root / "src" / pkg / mod / "__init__.py"
        if mod_init.exists() and temp_mod_init.exists():
            if _merge_module_init_file(mod_init, mod, temp_mod_init):
                updated.append(mod_init)
        # The rest of the module wiring is pure-generated.
        for rel in (
            f"native/src/{mod}/{mod}_ext.c",
            f"native/src/{mod}/CMakeLists.txt",
            f"src/{pkg}/{mod}/{mod}.pyi",
        ):
            if _overwrite_if_changed(root / rel, temp_root / rel):
                updated.append(root / rel)

    return updated


def _reconcile_bench_cmake(root: Path, cfg: dict) -> list[Path]:
    """Append a missing bench_*_core CMake target to each component CMakeLists.

    Existing projects that were scaffolded before the bench target was added
    to the template will have the bench source file but no CMake target.  This
    is idempotent: if the target is already present the file is not touched."""
    updated: list[Path] = []
    for comp in C.components(cfg):
        cmake_path = root / "native" / "src" / comp / "CMakeLists.txt"
        if not cmake_path.exists():
            continue
        text = cmake_path.read_text(encoding="utf-8")
        if f"bench_{comp}_core" in text:
            continue
        bench_block = (
            f"\nadd_executable(bench_{comp}_core\n"
            f"    ${{CMAKE_SOURCE_DIR}}/native/benchmarks/"
            f"bench_{comp}_core.c)\n"
            f"target_link_libraries(bench_{comp}_core"
            f" PRIVATE {comp}_core m)\n"
            f"target_include_directories(bench_{comp}_core\n"
            f"    PRIVATE ${{CMAKE_SOURCE_DIR}}/native/inc\n"
            f"            ${{CMAKE_SOURCE_DIR}}/native/benchmarks)\n"
        )
        cmake_path.write_text(text.rstrip() + bench_block, encoding="utf-8")
        updated.append(cmake_path)
    return updated


_INCLUDE_LINE = 'include = ["objects/*.toml"]\n'


def _wire_module_object(manifest: Path, mod_name: str, comp: str) -> bool:
    """Append *comp* to the `objects = [...]` line of [module.mod_name] in
    *manifest*. Returns True if the file was modified.

    Uses a targeted in-place text edit so fragment files (which may contain
    `impl` bodies not tracked by `_dump`) are never touched."""
    text = manifest.read_text(encoding="utf-8")
    pat = re.compile(
        rf"(\[module\.{re.escape(mod_name)}\][^\[]*?"
        rf"objects\s*=\s*\[)([^\]]*)\]",
        re.DOTALL,
    )
    m = pat.search(text)
    if not m:
        return False
    existing = [
        s.strip().strip('"') for s in m.group(2).split(",") if s.strip().strip('"')
    ]
    if comp in existing:
        return False
    items = existing + [comp]
    new_list = ", ".join(f'"{x}"' for x in items)
    manifest.write_text(
        text[: m.start(2)] + new_list + text[m.end(2) :], encoding="utf-8"
    )
    return True


def _validate_fragment_impl_keys(fragment: dict, label: str) -> None:
    """Check impl/impl_file mutual-exclusion on every section in *fragment*
    before any side-effects happen.  Covers impl, create_impl, reset_impl,
    and destroy_impl pairs."""
    _impl_pairs = [
        ("impl", "impl_file"),
        ("create_impl", "create_impl_file"),
        ("reset_impl", "reset_impl_file"),
        ("destroy_impl", "destroy_impl_file"),
    ]
    for key, value in fragment.items():
        if key in ("project", "module", "include"):
            continue
        if not isinstance(value, dict):
            continue
        for ik, ifk in _impl_pairs:
            if value.get(ik) and value.get(ifk):
                raise ValueError(
                    f"{label}: object {key}: `{ik}` and `{ifk}` are "
                    f"mutually exclusive — set one or the other."
                )
        opaque_state = [s for s in value.get("state", []) if s.get("opaque")]
        if opaque_state and not (
            value.get("create_impl") or value.get("create_impl_file")
        ):
            names = ", ".join(s.get("name", "?") for s in opaque_state)
            raise ValueError(
                f"{label}: object {key}: opaque state field(s) [{names}] "
                f"require `create_impl` or `create_impl_file` to initialize "
                f"them — the auto-generated create() would leave them "
                f"uninitialized."
            )
        for m in value.get("methods", []):
            if m.get("impl") and m.get("impl_file"):
                raise ValueError(
                    f"{label}: {key}.{m.get('name', '?')}: `impl` and "
                    f"`impl_file` are mutually exclusive."
                )


def _fragment_already_included(root: Path, fragment_path: Path) -> bool:
    """Return True when *fragment_path* is already covered by the manifest's
    ``include`` glob — i.e. the file is in place and the loader already sees
    its objects in the merged config.  This is the normal state after a user
    manually copies a fragment into ``objects/`` before running ``jm apply``."""
    manifest = C.load_manifest(root)
    includes = manifest.get("include")
    if not includes:
        return False
    fragment_resolved = fragment_path.resolve()
    return any(
        p.resolve() == fragment_resolved for p in C._resolve_includes(root, includes)
    )


def _compose_fragment(root: Path, fragment_path: Path) -> Path:
    """Validate *fragment_path*, copy it into `objects/`, and ensure the
    manifest's `include` glob covers it. Returns the destination path.

    Errors with the design-specified remedy if the fragment declares an
    object that the project already has.

    If the fragment is already on disk under the existing include glob (e.g.
    the user placed it in ``objects/`` manually before running ``jm apply``),
    the copy and conflict-check steps are skipped and the command proceeds
    directly to materialization — identical to running bare ``jm apply``.

    If a component section carries `module = "X"`, the component is wired
    into `[module.X].objects` in the manifest so `_replay` routes it to the
    module directory instead of generating standalone files."""
    if not fragment_path.exists():
        raise FileNotFoundError(f"fragment not found: {fragment_path}")

    with fragment_path.open("rb") as f:
        fragment = tomllib.load(f)

    already_included = _fragment_already_included(root, fragment_path)

    if not already_included:
        # Test-merge against the current resolved cfg to surface conflicts
        # before we touch any files. _merge_fragment raises ValueError naming
        # the conflicting object and the recommended remedy.
        # Skip when already_included: the "conflict" is the glob loading the
        # fragment itself, which is the expected state.
        C._merge_fragment(C.load(root), fragment, fragment_path)
    _validate_fragment_impl_keys(fragment, str(fragment_path))

    # Collect module-routing directives and validate before side-effects.
    module_directives: list[tuple[str, str]] = []
    for key, value in fragment.items():
        if key in ("project", "module", "include") or not isinstance(value, dict):
            continue
        mod_name = value.get("module")
        if isinstance(mod_name, str) and mod_name:
            module_directives.append((key, mod_name))
    if module_directives:
        known_mods = C.modules(C.load_manifest(root))
        for comp, mod_name in module_directives:
            if mod_name not in known_mods:
                raise ValueError(
                    f"object '{comp}' declares module='{mod_name}' but "
                    f"[module.{mod_name}] is not in {C.FILENAME}. "
                    f"Defined modules: {known_mods or ['(none)']}."
                )

    objects_dir = root / "objects"
    objects_dir.mkdir(exist_ok=True)
    dest = objects_dir / fragment_path.name
    src_resolved = fragment_path.resolve()
    if dest.resolve() != src_resolved:
        if dest.exists():
            raise FileExistsError(
                f"{dest} already exists. Move or rename the incoming "
                f"fragment, or `jm remove` the object first."
            )
        shutil.copy2(fragment_path, dest)
        print(f"  copy    {fragment_path} -> {dest}")

    # Ensure `include = ["objects/*.toml"]` is present at the top of the
    # manifest. Phase 1 does a minimal targeted text edit; the
    # format-preserving multi-file writer arrives with the provenance
    # work in Phase 2.
    manifest = root / C.FILENAME
    text = manifest.read_text(encoding="utf-8")
    if "include" not in C.load_manifest(root):
        manifest.write_text(_INCLUDE_LINE + "\n" + text, encoding="utf-8")
        print(f'  update  {manifest}  (include = ["objects/*.toml"])')

    for comp, mod_name in module_directives:
        if _wire_module_object(manifest, mod_name, comp):
            print(f"  update  {manifest}  ([module.{mod_name}])")

    return dest


def run(
    root: Path,
    fragment: Path | None = None,
    only: str | None = None,
) -> None:
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first, or author a manifest to apply.",
            file=sys.stderr,
        )
        sys.exit(1)

    if fragment is not None:
        print(f"just-makeit: composing fragment {fragment}")
        try:
            _compose_fragment(root, fragment)
        except (FileNotFoundError, FileExistsError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        print()

    cfg = C.load(root)
    if not C.components(cfg) and not C.modules(cfg):
        print(
            "error: manifest declares no objects or modules — nothing to materialize.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Resolve --only to (only_mod, only_comp).  A module name produces
    # only_mod with only_comp=None (full splice for that module).  A
    # component name produces only_comp plus the owning module (or None for
    # standalone components).
    only_mod: str | None = None
    only_comp: str | None = None
    if only is not None:
        mods = C.modules(cfg)
        comps = C.components(cfg)
        if only in mods:
            only_mod = only
        elif only in comps:
            only_comp = only
            only_mod = C.component_module(cfg, only)
        else:
            print(
                f"error: --only: '{only}' is not a known module or "
                f"component in {C.FILENAME}",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"just-makeit: applying {C.FILENAME}")
    print()

    with tempfile.TemporaryDirectory(prefix="jm-apply-") as tmp:
        temp_root = Path(tmp) / C.project_name(cfg)
        # The generators print progress for the throwaway temp tree; that
        # output names temp paths and would only confuse the user.
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                _replay(cfg, temp_root, root)
        except (ValueError, FileNotFoundError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        created = _sync_missing(temp_root, root)
        updated = _sync_aggregates(
            temp_root,
            root,
            cfg,
            only_mod=only_mod,
            only_comp=only_comp,
        )

    bench_updated = _reconcile_bench_cmake(root, cfg)

    for rel in created:
        print(f"  create  {root / rel}")
    for path in updated + bench_updated:
        print(f"  update  {path}")

    print()
    total = len(created) + len(updated) + len(bench_updated)
    if total:
        print(
            f"Done!  Materialized {len(created)} new file(s) and "
            f"reconciled {len(updated) + len(bench_updated)} wiring file(s)"
            f" from {C.FILENAME}."
        )
    else:
        print(f"Done!  Project already matches {C.FILENAME} — nothing to do.")
