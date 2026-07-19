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

from __future__ import annotations

import contextlib
import fnmatch
import io
import os
import re
import shutil
import sys
import tempfile
import time

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib
from pathlib import Path

from . import _config as C
from . import _stubs as S
from ._init import _to_title


# `{identifier}` placeholders only — anything else passes through untouched.
# In particular, bare C braces (`{ … }`, `{0}`) are NOT consumed.
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate(body: str, ctx: dict) -> str:
    """Replace `{name}` placeholders with ctx[name]; unknown names are
    left in place so literal `{0}` / `{ static int x; }` C code survives."""
    return _PLACEHOLDER_RE.sub(
        lambda m: str(ctx.get(m.group(1), m.group(0))), body
    )


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

        path_part, _, ref = file_ref.partition("::")
        if not ref:
            raise ValueError(
                f"{label}: {impl_file_key} must be 'path::funcname' or "
                f"'path::N:M', got {file_ref!r}."
            )
        body = I.extract(root / path_part, ref)
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
        "serializable": C.is_serializable(cfg, comp),
        "streamable": C.is_streamable(cfg, comp),
        "async_stream": C.is_async_stream(cfg, comp),
        "stream_block_default": (
            C.stream_block_default(cfg, comp)
            if C.is_streamable(cfg, comp)
            else None
        ),
        "init_params": C.init_params(cfg, comp),
        "init_post_parse_impl": C.init_post_parse(cfg, comp),
        "class_name": C.class_name(cfg, comp),
        # gh-225: pass the RAW depends_on (preserving `{name, link}` tables) so
        # the replayed scaffold re-persists the link flag and the consuming
        # target's link line is regenerated; render paths flatten to names.
        "depends_on": C.depends_on_raw(cfg, comp),
        "opaque_fields": C.opaque_fields(cfg, comp),
        "no_ctor_names": C.no_ctor_names(cfg, comp),
        "controllable_names": C.controllable_names(cfg, comp),
        "extra_link_libs": C.component_extra_link_libs(cfg, comp),
        "extra_include_dirs": C.component_extra_include_dirs(cfg, comp),
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
    from . import (
        _capsule,
        _composer,
        _function,
        _handle,
        _method,
        _module,
        _new,
        _object,
        _property,
        _view,
        _warning,
        _error,
    )

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
        # gh-213: the temp project must target the same platforms so the
        # per-component Windows CMake blocks match (apply/status diff cleanly).
        platforms=cfg.get("project", {}).get("platforms"),
    )
    # Stamp the real project's version so generated files (pyproject, .pyi)
    # carry it rather than the `new` default.
    tcfg = C.load(temp_root)
    tcfg["project"]["version"] = C.project_version(cfg)
    # gh-353: carry the top-level [[enum]] SSOT into the temp manifest so a
    # replayed `jm function` with an enum param validates the name (and renders
    # the enum tables) against the same declared enums as the real project.
    if cfg.get("enum"):
        tcfg["enum"] = cfg["enum"]
    # gh-393: carry [project.bench] so the replayed scaffold honours the
    # project's configured benchmark block_sizes (#390) — otherwise a new
    # object materialised by `jm apply` reintroduces the default _1k suite.
    bench = cfg.get("project", {}).get("bench")
    if bench:
        tcfg["project"]["bench"] = bench
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
        # gh-286: a capsule module has no object-group scaffold — generate its
        # binding / CMake / .pyi directly from the manifest instead.
        if C.is_capsule_module(cfg, mod):
            _capsule.materialize(cfg, temp_root, mod)
            continue
        # gh-287: a composer module emits its OO-type binding / CMake / .pyi
        # directly from the manifest (no object-group scaffold).
        if C.is_composer_module(cfg, mod):
            _composer.materialize(cfg, temp_root, mod)
            continue
        # gh-306: a handle module emits its typed-class binding / CMake / .pyi
        # directly from the manifest (no object-group scaffold).
        if C.is_handle_module(cfg, mod):
            _handle.materialize(cfg, temp_root, mod)
            continue
        _module.run(temp_root, mod)

    # After module scaffolding, copy module-level metadata (e.g.
    # extra_link_libs) from the real project TOML into the temp TOML so
    # _regenerate_module() inside object.run() picks it up.
    _mods_need_update = [
        m
        for m in mods
        if not C.is_no_generate_module(cfg, m)
        and not C.is_capsule_module(cfg, m)
        and not C.is_composer_module(cfg, m)
        and not C.is_handle_module(cfg, m)
        and (
            cfg.get("module", {}).get(m, {}).get("extra_link_libs")
            or cfg.get("module", {}).get(m, {}).get("extra_types")
            or cfg.get("module", {}).get(m, {}).get("extra_include_dirs")
            # gh-247: functions_in_core must reach the temp TOML *before* the
            # function replay below, else _function.run() falls back to
            # per-function .c files and the build double-defines the symbols.
            or cfg.get("module", {}).get(m, {}).get("functions_in_core")
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
            if mod_data.get("extra_include_dirs"):
                tmod["extra_include_dirs"] = mod_data["extra_include_dirs"]
            if mod_data.get("functions_in_core"):
                tmod["functions_in_core"] = mod_data["functions_in_core"]
        C.save(temp_root, tcfg2)

    # Seed _extra.c files from the real project so _regenerate_module()
    # detects and re-includes them in the temp aggregator.
    for mod in mods:
        cname = C.module_paths(mod).cname
        src_dir = project_root / "native" / "src" / cname
        dst_dir = temp_root / "native" / "src" / cname
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

    all_comps = standalone + [
        o for m in mods for o in C.module_objects(cfg, m)
    ]

    # gh-504: one replay per member, reused for both an object's members and a
    # view's own (via view=). `view` routes _property.run/_method.run onto the
    # named view; "" is the object itself.
    def _replay_method(comp, mod, m, view=""):
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
            # gh-432: pass params through as full dicts — the old
            # (name, type, default) tuple flattening silently dropped every
            # other per-param key (capsule, header, out) on the replay path.
            params=[
                dict(p) for p in (m.get("extra_args") or m.get("params", []))
            ],
            out_type=m.get("out_type"),
            out_divisor=int(m.get("out_divisor", 1)),
            batch=bool(m.get("batch")),
            impl_body=m_impl,
            none_on_empty=bool(m.get("none_on_empty")),
            result_fields=list(m.get("result_fields", [])),
            max_results=int(m.get("max_results", 64)),
            single=bool(m.get("single")),
            record_name=m.get("record_name", ""),
            record_module=m.get("record_module", ""),
            py_return_type=m.get("py_return_type", ""),
            max_out=int(m.get("max_out", 0)),
            varargs=bool(m.get("varargs")),
            manual_stub=bool(m.get("manual_stub")),
            pass_capacity=bool(m.get("pass_capacity")),
            nogil=bool(m.get("nogil")),
            status_return=bool(m.get("status_return")),
            doc=m.get("doc", ""),
            from_apply=True,
            view=view,
        )

    def _replay_property(comp, mod, p, view=""):
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
            doc=p.get("doc", ""),
            view=view,
        )

    for comp in all_comps:
        mod = C.component_module(cfg, comp)
        for m in C.methods(cfg, comp):
            _replay_method(comp, mod, m)
        for p in C.properties(cfg, comp):
            _replay_property(comp, mod, p)
        # gh-481. Without this replay a declared warning never reaches a fresh
        # checkout: the object is scaffolded with no warnings and nothing puts
        # them back. That is the exact failure this feature exists to fix —
        # delete-the-fragment-and-apply is jm's own sanctioned migration
        # mechanic, so the manifest has to be able to rebuild the glue alone.
        for w in C.warnings(cfg, comp):
            _warning.run(
                temp_root,
                comp,
                w["condition"],
                w["message"],
                module=mod,
                category=w.get("category", "UserWarning"),
                after=w.get("after", "__init__"),
                stacklevel=int(w.get("stacklevel", 1) or 1),
            )
        # gh-482: same reasoning as the warnings replay above — a declared
        # create_error must reach a fresh checkout from the manifest alone.
        if C.create_error(cfg, comp):
            _error.run(
                temp_root,
                comp,
                C.create_error(cfg, comp),
                C.create_error_message(cfg, comp),
                module=mod,
            )
        # gh-504: same reasoning — a declared view (a second class over this
        # object's core) must rebuild from the manifest alone, or a fresh
        # checkout (and every `jm status`/`jm apply`) would drop it.
        for v in C.views(cfg, comp):
            _view.run(
                temp_root,
                comp,
                v["class_name"],
                mod,
                v["create_fn"],
                init_params=[dict(p) for p in v.get("init_params", [])],
                exclude_properties=list(v.get("exclude_properties", [])),
                exclude_methods=list(v.get("exclude_methods", [])),
                doc=v.get("doc", ""),
                from_apply=True,
            )
            # gh-504: the view's OWN added/overriding members, materialized
            # after the view exists (methods before properties so an override
            # method's shared C symbol is present).
            cls = v["class_name"]
            for m in C.view_methods(v):
                _replay_method(comp, mod, m, view=cls)
            for p in C.view_properties(v):
                _replay_property(comp, mod, p, view=cls)

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
            f_impl = _resolve_impl(
                fn, fctx, project_root, f"function {fn['name']}"
            )
            _function.run(
                temp_root,
                fn["name"],
                mod,
                doc=fn.get("doc", ""),
                params=[
                    # gh-170: `mutable` is accepted as a synonym for `out` —
                    # both drop the `const` on a writable array param.
                    # gh-353: replay the 5-tuple including `default` and `enum`
                    # so path/enum/defaulted params survive `jm apply`
                    # regeneration (the rendered _ext.c keeps its enum/path
                    # handling, otherwise these would silently drop).
                    (
                        p["name"],
                        p["type"],
                        bool(p.get("out") or p.get("mutable")),
                        p.get("default", ""),
                        p.get("enum", ""),
                    )
                    for p in fn.get("params", [])
                ],
                return_type=fn.get("return_type", "void"),
                impl_body=f_impl,
                out_type=fn.get("out_type", ""),
                result_fields=fn.get("result_fields", []),
                max_results_param=fn.get("max_results_param", ""),
                max_results=int(fn.get("max_results", 64)),
                # gh-335: self-sizing output for module functions. Without
                # these the replayed temp manifest loses them and the rendered
                # _ext.c under-allocates (out first, _dim = 1 / first array
                # length) → heap overrun in the C kernel.
                variable_output=bool(fn.get("variable_output")),
                out_size=fn.get("out_size", ""),
                check_return=bool(fn.get("check_return")),
            )


def _patch_step_impls(root: Path, cfg: dict) -> list[Path]:
    """Inject ``impl``/``impl_file`` bodies from the manifest into headers.

    ``_sync_missing`` only creates files that are absent in the project tree;
    existing ``_core.h`` files are left untouched even when the user has since
    added an ``impl`` key to the TOML.  This function runs afterwards and
    patches every component that carries an ``impl`` or ``impl_file`` key,
    using ``patch_function_body()`` which is safe to run on both newly-created
    and pre-existing headers (it replaces only the matching function body)."""
    from . import _impl as I

    mods = C.modules(cfg)
    module_owned = {o for m in mods for o in C.module_objects(cfg, m)}
    all_comps = [c for c in C.components(cfg) if c not in module_owned]
    all_comps += [o for m in mods for o in C.module_objects(cfg, m)]

    patched: list[Path] = []
    for comp in all_comps:
        if C.is_no_step(cfg, comp):
            continue
        sec = cfg.get(comp, {})
        if not sec.get("impl") and not sec.get("impl_file"):
            continue
        octx = _object_ctx(cfg, comp, C.component_module(cfg, comp))
        impl_body = _resolve_impl(sec, octx, root, f"object {comp}")
        if impl_body is None:
            continue
        h_path = root / "native" / "inc" / comp / f"{comp}_core.h"
        if not h_path.exists():
            continue
        original = h_path.read_text(encoding="utf-8")
        updated = I.patch_function_body(original, f"{comp}_step", impl_body)
        if updated != original:
            h_path.write_text(updated, encoding="utf-8")
            patched.append(h_path)
    return patched


def _sync_missing(temp_root: Path, root: Path) -> list[Path]:
    """Copy every file present in *temp_root* but missing from *root*.

    Returns the created paths, relative to *root*."""
    # Stamp newly-created source files 2 s in the future so GNU Make
    # (1-second timestamp resolution on macOS/Windows) always considers them
    # newer than any pre-existing object files in the build directory.
    _future = time.time() + 2.0
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
        os.utime(dst, times=(_future, _future))
        created.append(rel)
    return created


_EXTDEPS_BEGIN = "# ── External deps"
_EXTDEPS_END = "# ── End external deps"


def _splice_cmake_external_deps(real_path: Path, cfg: dict) -> bool:
    """Insert or replace the managed external-deps block in the top CMakeLists.

    Reads ``[project] find_packages`` and ``[project] pkg_modules`` from
    *cfg*, generates the corresponding ``find_package()`` /
    ``pkg_check_modules()`` lines, and either:

    - Replaces the content between existing ``# ── External deps`` /
      ``# ── End external deps`` sentinel lines, or
    - Inserts the whole block (including sentinels) immediately before the
      ``# ── Components`` sentinel when the block is absent.

    Returns True if the file was modified.  If both sentinel lines are absent
    and there is nothing to write, the file is left untouched."""
    find_pkgs = C.find_packages(cfg)
    pkg_mods = C.pkg_modules(cfg)

    real = real_path.read_text(encoding="utf-8")

    lines: list[str] = []
    if find_pkgs:
        for pkg in find_pkgs:
            lines.append(f"find_package({pkg} REQUIRED)\n")
    if pkg_mods:
        lines.append("find_package(PkgConfig REQUIRED)\n")
        for mod in pkg_mods:
            lines.append(
                f"pkg_check_modules({mod.upper()} REQUIRED IMPORTED_TARGET {mod})\n"
            )

    has_begin = _EXTDEPS_BEGIN in real
    has_end = _EXTDEPS_END in real

    if not lines:
        return False

    content = "".join(lines)

    if has_begin and has_end:
        begin_idx = real.index(_EXTDEPS_BEGIN)
        begin_line_end = real.index("\n", begin_idx) + 1
        end_idx = real.index(_EXTDEPS_END)
        new_real = real[:begin_line_end] + content + real[end_idx:]
    elif not has_begin and not has_end:
        if "# ── Components" not in real:
            return False
        idx = real.index("# ── Components")
        block = f"{_EXTDEPS_BEGIN}\n{content}{_EXTDEPS_END}\n\n"
        new_real = real[:idx] + block + real[idx:]
    else:
        return False  # mismatched sentinels — leave the file alone

    if new_real != real:
        real_path.write_text(new_real, encoding="utf-8")
        return True
    return False


_SUBDIR_BLOCK = re.compile(
    r"^add_subdirectory\(native/src/(\w+)\)[ \t]*\n"
    r"(?:^target_sources\(\w+ PRIVATE \$<TARGET_OBJECTS:\w+_core>\)[ \t]*\n)*",
    re.MULTILINE,
)


def _splice_cmake_components(
    real_path: Path, temp_path: Path, cfg: dict
) -> bool:
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

    # The regex captures the native-dir token, which for a nested module is its
    # cname (dsp_filters), not the dotted id — so classify against cnames.
    module_names = C.module_cnames(cfg)
    component_blocks: list[str] = []
    module_blocks: list[str] = []
    for m in _SUBDIR_BLOCK.finditer(temp):
        (
            module_blocks if m.group(1) in module_names else component_blocks
        ).append(m.group(0))

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
            line = (
                f"add_subdirectory(native/src/{C.module_paths(mod).cname})\n"
            )
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
    imports = re.findall(
        r"^from \.(\w+) import (\w+)", temp_text, re.MULTILINE
    )
    changed = False
    for comp, Component in imports:
        cur = real_path.read_text(encoding="utf-8")
        if f"from .{comp} import {Component}" in cur:
            continue
        _splice_init_py(real_path, comp, Component)
        changed = True
    return changed


def _merge_module_init_file(
    real_path: Path,
    module: str,
    temp_path: Path,
    reexports: dict[str, list[str]] | None = None,
) -> bool:
    """Run _merge_module_init against *real_path*, using the export list
    parsed out of *temp_path*'s import line. Preserves any user wrapper
    classes already in the real file. *reexports* (from the manifest) are
    folded into the import block and __all__ so a no_generate sibling's
    re-exported names regenerate cleanly instead of being hand-edited glue."""
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
    merged = _merge_module_init(existing, module, exports, reexports)
    if merged != existing:
        real_path.write_text(merged, encoding="utf-8")
        return True
    return False


def _overwrite_if_changed(
    real: Path,
    temp: Path,
    cfg: dict | None = None,
    rel: str = "",
    honor_status_allow: bool = True,
) -> bool:
    """Overwrite *real* with *temp*'s bytes if they differ.

    For a ``.pyi`` target, *cfg* (if given) is used to splice any
    manual_stub method's hand-written text from *real* back over the
    freshly rendered placeholder in *temp* before comparing (gh-428) —
    without this, this reconcile step is exactly what silently clobbers a
    hand-written manual_stub stub on every plain `jm apply`.

    *rel* — the project-relative posix path of *real* — is checked against
    ``[project] status_allow`` when *honor_status_allow* is true (gh-441): a
    hand-maintained file `jm status --check` already treats as allowed
    drift must never be silently overwritten by apply's reconcile step, so
    a match skips the write entirely rather than only suppressing the
    status warning. `_status.py` sets *honor_status_allow* false for its
    internal throwaway replay, which must keep computing the real diff
    (allowed files still need genuine before/after content to classify as
    ALLOWED rather than OK, and gh-426 dropped-symbol detection must see
    them too) instead of silently matching by never having written it.
    """
    if not real.exists() or not temp.exists():
        return False
    if honor_status_allow and cfg is not None and rel:
        allow_patterns = C.status_allow(cfg)
        if any(
            rel == pat or fnmatch.fnmatch(rel, pat) for pat in allow_patterns
        ):
            return False
    new_bytes = temp.read_bytes()
    if cfg is not None and real.suffix == ".pyi":
        try:
            new_bytes = S._splice_manual_stub_bodies(
                cfg,
                real.read_text(encoding="utf-8"),
                new_bytes.decode("utf-8"),
            ).encode("utf-8")
        except UnicodeDecodeError:
            pass
    if real.read_bytes() == new_bytes:
        return False
    real.write_bytes(new_bytes)
    return True


def _is_hand_owned_object_cmake(text: str, comp: str) -> bool:
    """True when a per-object ``CMakeLists.txt`` carries bespoke build wiring.

    The gh-271 reconcile re-renders a module object's per-object CMakeLists from
    the manifest. That is safe only while the file stays within the shape jm
    emits; once it gains build rules the manifest cannot express — extra
    ``add_library`` sources (vendored ``.c`` compiled into ``<comp>_core``),
    ``set_source_files_properties``, or a custom build step — re-rendering would
    silently drop them (gh-275: doppler's ``fft_core`` compiles in pocketfft /
    PFFFT, breaking every FFT consumer). Such a file is *hand-owned*: jm leaves
    it untouched and ``status --check`` treats it as up to date.

    Detected signals, all of which jm never generates itself:

    - an ``add_library(<comp>_core OBJECT …)`` source list naming anything
      besides ``<comp>_core.c``;
    - a ``set_source_files_properties`` / ``add_custom_command`` /
      ``add_custom_target`` statement anywhere in the file.
    """
    m = re.search(
        rf"add_library\(\s*{re.escape(comp)}_core\s+OBJECT\s+([^)]*)\)", text
    )
    if m and any(s != f"{comp}_core.c" for s in m.group(1).split()):
        return True
    return any(
        kw in text
        for kw in (
            "set_source_files_properties",
            "add_custom_command",
            "add_custom_target",
        )
    )


def _reconcile_object_core_cmake(
    real: Path, temp: Path, comp: str, include_dirs: "list[str]"
) -> bool:
    """Reconcile a non-collocated module object's ``CMakeLists.txt`` (gh-271).

    A module object's per-object ``native/src/<obj>/CMakeLists.txt`` is glue,
    but ``jm apply`` historically only *added* missing link/include lines to it
    (``_inject_object_core_cmake``), so a *change* to the object's ``depends_on``
    never reached the file once its ``target_link_libraries(<obj>_core PUBLIC …)``
    block already existed — the new dep cores were silently dropped from the
    object's own ``_core`` / ``test`` / ``bench`` link lines and the C test
    failed to link. ``jm status --check`` missed the drift because it observes
    the same skipped reconcile.

    The fix overwrites the file from the freshly-replayed canonical render in
    *temp* (which carries the current ``extra_link_libs`` + ``depends_on`` cores
    on every link line), then restores the two things the manifest-driven render
    cannot reproduce:

    1. component-level ``extra_include_dirs`` — the per-object template has no
       slot for them, so they only ever reach the file via this injection
       (mirrors the standalone path's gh-174 behaviour);
    2. user ``if(VAR) … endif()`` external-library blocks (e.g.
       ``if(DOPPLER_C_LIB)``) — hand-added wiring jm cannot re-derive.

    A file that has gone *hand-owned* (bespoke ``add_library`` sources /
    ``set_source_files_properties`` the manifest can't express — gh-275) is left
    untouched, since re-rendering it would drop those rules.

    Returns True if the file changed."""
    if not real.exists() or not temp.exists():
        return False
    from ._object import _external_cmake_blocks

    original = real.read_text(encoding="utf-8")
    # gh-275: never re-render a hand-owned file (vendored sources, per-source
    # build properties) — the canonical render cannot reproduce them.
    if _is_hand_owned_object_cmake(original, comp):
        return False
    new = temp.read_text(encoding="utf-8")
    # (1) re-add component extra_include_dirs as a second PUBLIC include block,
    # just before the test executable (matches _inject_object_core_cmake).
    if include_dirs and include_dirs[0] not in new:
        anchor = f"add_executable(test_{comp}_core"
        if anchor in new:
            block = (
                f"target_include_directories({comp}_core PUBLIC\n    "
                + "\n    ".join(include_dirs)
                + ")\n"
            )
            new = new.replace(anchor, f"{block}{anchor}", 1)
    # (2) preserve user external-library blocks the canonical render omits.
    for block in _external_cmake_blocks(original):
        if block not in new:
            new = new.rstrip("\n") + "\n\n" + block + "\n"
    if new == original:
        return False
    real.write_text(new, encoding="utf-8")
    return True


def _refresh_core_h_decls(real: Path, temp: Path, comp: str) -> bool:
    """Bring the real ``_core.h`` up to date with the manifest, splice-free.

    The temp header is freshly rendered from the manifest and carries every
    declaration the spec implies.  We extract those prototypes and inject any
    the user's header is *missing* — the sacred state struct and inline
    ``step()`` body are never touched.  Apply only *adds* decls; a signature
    change is a structural edit reached through ``jm regenerate`` (so it never
    duplicates a prototype).  Returns True if the real header changed."""
    if not temp.exists():
        return False
    from ._init import _core_h_decl_lines, _inject_decls_into_core_h

    if not real.exists():
        # Nothing to merge into — _sync_missing copies the temp header.
        return False
    decls = _core_h_decl_lines(temp.read_text(encoding="utf-8"))
    return _inject_decls_into_core_h(real, comp, decls)


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
    honor_status_allow: bool = True,
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
    # Maintain the external-deps sentinel block regardless of --only.
    if real_cmake.exists():
        if _splice_cmake_external_deps(real_cmake, cfg):
            if real_cmake not in updated:
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
        # gh-286/gh-287/gh-306: a capsule OR composer OR handle module's three
        # glue files (binding, CMake, .pyi) regenerate from the manifest like
        # any other module aggregator. There is no _core.h / object loop /
        # module __init__.py to reconcile — the owning package re-exports the
        # public names via [module.X.reexports]. (Handle uses the same three
        # glue files; the composer's _cli.c is composer-only.)
        if (
            C.is_capsule_module(cfg, mod)
            or C.is_composer_module(cfg, mod)
            or C.is_handle_module(cfg, mod)
        ):
            mp = C.module_paths(mod)
            out_pkg = C.capsule_package(cfg, mod) or mp.pypath
            glue = [
                f"native/src/{mp.cname}/{mp.cname}_ext.c",
                f"native/src/{mp.cname}/CMakeLists.txt",
                f"src/{pkg}/{out_pkg}/{mp.leaf}.pyi",
            ]
            # gh-287: a composer with the optional c-face CLI also regenerates
            # its <cname>_cli.c (glue).
            from . import _composer

            if C.is_composer_module(cfg, mod) and _composer.composer_cli(
                cfg, mod
            ).get("enabled"):
                glue.append(f"native/src/{mp.cname}/{mp.cname}_cli.c")
            for rel in glue:
                if _overwrite_if_changed(
                    root / rel,
                    temp_root / rel,
                    cfg,
                    rel=rel,
                    honor_status_allow=honor_status_allow,
                ):
                    updated.append(root / rel)
            continue
        # Nested-module forms: cname (flat native dir), pypath (nested Python
        # dir), leaf (.so basename / import). Flat modules collapse all to mod.
        mp = C.module_paths(mod)
        # Re-create any intermediate package markers the user may have deleted
        # (create-only — never clobbers a hand-edited marker).
        from ._init import ensure_parent_packages

        for init in ensure_parent_packages(root, pkg, mp):
            updated.append(init)
        # Module subpackage __init__.py — merged so user wrapper classes
        # below the re-exports survive (the gh#1 contract). The import line is
        # `from .<leaf> import ...`, so merge against the leaf.
        mod_init = root / "src" / pkg / mp.pypath / "__init__.py"
        temp_mod_init = temp_root / "src" / pkg / mp.pypath / "__init__.py"
        if mod_init.exists() and temp_mod_init.exists():
            if _merge_module_init_file(
                mod_init, mp.leaf, temp_mod_init, C.module_reexports(cfg, mod)
            ):
                updated.append(mod_init)
        # The rest of the module wiring is pure-generated.
        for rel in (
            f"native/src/{mp.cname}/{mp.cname}_ext.c",
            f"native/src/{mp.cname}/CMakeLists.txt",
            f"src/{pkg}/{mp.pypath}/{mp.leaf}.pyi",
        ):
            if _overwrite_if_changed(
                root / rel,
                temp_root / rel,
                cfg,
                rel=rel,
                honor_status_allow=honor_status_allow,
            ):
                updated.append(root / rel)
        # Module function bodies live in their own sacred <fn>.c (create-only
        # via _sync_missing), so <mod>_core.c is just the include scaffold —
        # also create-only. The module header accumulates function
        # declarations: inject any the manifest implies that are missing,
        # splice-free.
        rel = f"native/inc/{mp.cname}/{mp.cname}_core.h"
        if _refresh_core_h_decls(root / rel, temp_root / rel, mp.cname):
            if root / rel not in updated:
                updated.append(root / rel)
        # gh-170: each module object's own _core.h gains its depends_on
        # includes (the per-object headers are otherwise sacred / never
        # refreshed on apply).
        from ._init import _inject_includes_into_core_h

        for obj in C.module_objects(cfg, mod):
            obj_h = root / "native" / "inc" / obj / f"{obj}_core.h"
            if _inject_includes_into_core_h(
                obj_h,
                obj,
                C.depends_on(cfg, obj),
                extra=C.param_headers(cfg, obj),
            ):
                updated.append(obj_h)
            # gh-271: a non-collocated module object's OBJECT-core CMakeLists is
            # glue, so reconcile it from the canonical replay render — this picks
            # up a *changed* depends_on / extra_link_libs on the object's own
            # _core / test / bench link lines (the old surgical-add path skipped
            # the link block once it already existed, dropping new deps). The
            # reconcile preserves component extra_include_dirs and user external
            # if(VAR) blocks. Collocated objects share the module CMakeLists
            # (handled above).
            if obj != mod:
                obj_cmake = root / "native" / "src" / obj / "CMakeLists.txt"
                temp_cmake = (
                    temp_root / "native" / "src" / obj / "CMakeLists.txt"
                )
                if _reconcile_object_core_cmake(
                    obj_cmake,
                    temp_cmake,
                    obj,
                    list(C.component_extra_include_dirs(cfg, obj)),
                ):
                    updated.append(obj_cmake)

    # Standalone components: the sacred/glue split. Glue files (binding,
    # CMake, type stub) regenerate from the manifest on every apply, so a
    # TOML edit — a new method, init param, extra_link_libs — propagates
    # without a re-scaffold. The sacred sources are never re-rendered: _core.c
    # is create-only (_sync_missing), and _core.h only gains missing
    # declarations (struct + step left alone). A new *state field* is
    # structural and reaches the struct via jm regenerate, not apply.
    module_owned = {
        o for m in C.modules(cfg) for o in C.module_objects(cfg, m)
    }
    for comp in C.components(cfg):
        if comp in module_owned:
            continue
        if only_comp is not None and comp != only_comp:
            continue
        # Glue — pure boilerplate, no user content. Overwrite from the
        # freshly-rendered scaffold so manifest edits reach the binding,
        # stub, and build wiring.
        for rel in (
            f"native/src/{comp}/{comp}_ext.c",
            f"native/src/{comp}/CMakeLists.txt",
            f"src/{pkg}/{comp}.pyi",
        ):
            if _overwrite_if_changed(
                root / rel,
                temp_root / rel,
                cfg,
                rel=rel,
                honor_status_allow=honor_status_allow,
            ):
                updated.append(root / rel)
        # _core.h is a hybrid: the inline step() body and the state struct
        # are sacred; the function declarations are glue. Apply injects any
        # TOML-declared prototype the header is missing (a new method/property
        # reaches the public API) without ever re-rendering the struct/step.
        # The body is left to the user — a clean link error until written, or
        # `jm regenerate` for a structural change. _core.c is fully sacred:
        # never in any merge loop, created once by _sync_missing.
        rel = f"native/inc/{comp}/{comp}_core.h"
        changed = _refresh_core_h_decls(root / rel, temp_root / rel, comp)
        # gh-170: also inject `#include "<dep>/<dep>_core.h"` for each
        # depends_on entry, so opaque fields of a dependency's types compile.
        from ._init import _inject_includes_into_core_h

        if _inject_includes_into_core_h(
            root / rel,
            comp,
            C.depends_on(cfg, comp),
            extra=C.param_headers(cfg, comp),
        ):
            changed = True
        if changed:
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
        s.strip().strip('"')
        for s in m.group(2).split(",")
        if s.strip().strip('"')
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
        p.resolve() == fragment_resolved
        for p in C._resolve_includes(root, includes)
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
        if key in ("project", "module", "include") or not isinstance(
            value, dict
        ):
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


def _dangling_object_fragments(root: Path, cfg: dict) -> list[str]:
    """Objects defined in the manifest, referenced by no module, whose native
    sources are still in *module-object* shape (gh-327).

    An object section listed in no ``[module.X].objects`` is normally a
    standalone object — apply gives it its own ``.so``. But when an object is
    *removed* from its module and its ``objects/<obj>.toml`` fragment is left
    behind, it looks identical to a standalone object, so apply silently
    promotes it: it scaffolds a standalone module *over* the object's existing
    native dir, clobbering any hand-written ``<obj>_core`` lib that lived there
    (doppler's ``ddcr_core`` composed vendored sources — apply overwrote its
    CMakeLists).

    The two are distinguishable on disk: a real standalone object's
    ``native/src/<obj>/CMakeLists.txt`` builds its own extension
    (``Python3_add_library(<obj> MODULE …)``); a module object's carries only
    the ``<obj>_core`` OBJECT lib (its ``.so`` is the module's). So an object
    referenced by no module whose existing CMakeLists lacks that extension
    target is a *dangling* former-module fragment, not a standalone object.

    A brand-new standalone object (no native dir yet) has nothing to clobber and
    is materialized normally — only an existing module-shaped dir trips this.
    """
    module_owned = {
        o for m in C.modules(cfg) for o in C.module_objects(cfg, m)
    }
    dangling: list[str] = []
    for comp in C.components(cfg):
        if comp in module_owned:
            continue
        cml = root / "native" / "src" / comp / "CMakeLists.txt"
        if not cml.exists():
            continue
        text = cml.read_text(encoding="utf-8")
        if not re.search(
            rf"Python3_add_library\(\s*{re.escape(comp)}\s+MODULE", text
        ):
            dangling.append(comp)
    return dangling


def run(
    root: Path,
    fragment: Path | None = None,
    only: str | None = None,
    *,
    honor_status_allow: bool = True,
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
    # gh-183: record the generating jm version (monotonic; surgical write).
    _stamped = C.stamp_jm_version(root, cfg)
    if _stamped:
        print(f"  stamp   {C.FILENAME}  [project] jm_version = {_stamped}\n")
    if not C.components(cfg) and not C.modules(cfg):
        print(
            "error: manifest declares no objects or modules — nothing to materialize.",
            file=sys.stderr,
        )
        sys.exit(1)

    # gh-327: refuse to silently promote a former module object — whose
    # fragment was left behind after `objects = [...]` dropped it — into a
    # standalone module over its existing (possibly hand-owned) native dir.
    # Scoped to a full apply; `--only X` never touches unrelated objects.
    if only is None:
        dangling = _dangling_object_fragments(root, cfg)
        if dangling:
            lines = [
                f"error: object '{o}' is defined but not listed in any "
                f"[module.X].objects, and native/src/{o}/ already holds a "
                f"module-style core lib.\n"
                f"  Promoting it to a standalone module would overwrite those "
                f"files. Resolve by either:\n"
                f"    - add '{o}' back to a module's objects list, or\n"
                f"    - remove the objects/{o}.toml fragment (and its "
                f"native/src/{o}/ dir) if the object is gone."
                for o in dangling
            ]
            print("\n".join(lines), file=sys.stderr)
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

    from . import _object as _obj_mod

    with tempfile.TemporaryDirectory(prefix="jm-apply-") as tmp:
        temp_root = Path(tmp) / C.project_name(cfg)
        # Docstring derivation must read the REAL project's sacred `_core.h`
        # (with its hand-written Doxygen), not the template headers scaffolded
        # into the throwaway temp tree by _replay.
        _obj_mod._DOC_ROOT_OVERRIDE = root
        # The generators print progress for the throwaway temp tree; that
        # output names temp paths and would only confuse the user.
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                _replay(cfg, temp_root, root)
        except (ValueError, FileNotFoundError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            _obj_mod._DOC_ROOT_OVERRIDE = None
        # gh-493: reformat the throwaway scaffold to the project's house style
        # *before* it is compared against the real tree, so a c_style project's
        # on-disk (formatted) *_ext.c glue matches the freshly rendered glue
        # instead of reading as perpetual drift on every `apply`/`status`. Only
        # *_ext.c is touched (see _cfmt._generated_c_files); sacred sources are
        # unformatted on both sides and already compare equal. No-op unless
        # c_style is set, and a soft no-op if clang-format is absent (both
        # sides then stay jm-style, still equal). The real project's
        # .clang-format must be seeded into the temp tree first — _replay
        # rebuilds via _object/_module, which do not re-emit it, so
        # `clang-format --style=file` would otherwise fall back to LLVM there
        # and the two sides would diverge on style instead of converging.
        from . import _cfmt

        real_cf = root / ".clang-format"
        if C.c_style(cfg) == "clang-format" and real_cf.is_file():
            shutil.copy2(real_cf, temp_root / ".clang-format")
        _cfmt.format_project(temp_root, cfg, quiet=True)
        created = _sync_missing(temp_root, root)
        impl_patched = _patch_step_impls(root, cfg)
        updated = _sync_aggregates(
            temp_root,
            root,
            cfg,
            only_mod=only_mod,
            only_comp=only_comp,
            honor_status_allow=honor_status_allow,
        )

    bench_updated = _reconcile_bench_cmake(root, cfg)

    # Refresh runtime __doc__ in per-object binding fragments
    # (<mod>_ext_<obj>.c). _sync_aggregates reconciles the module
    # aggregator/.pyi/CMake but not these sacred fragments, so a header Doxygen
    # edit reaches the .pyi while the runtime PyMethodDef / tp_doc / PyGetSetDef
    # docs keep the stale scaffold fallback. _docsync transplants only the
    # doc-string slots into the existing fragment — every function body and
    # every hand-written non-manifest binding is left byte-for-byte identical.
    from . import _docsync

    frag_doc_updated = _docsync.refresh_module_fragment_docs(
        root, cfg, only_mod=only_mod
    )

    for rel in created:
        print(f"  create  {root / rel}")
    for path in impl_patched:
        print(f"  update  {path}")
    for path in updated + bench_updated + frag_doc_updated:
        print(f"  update  {path}")

    # gh-442: non-fatal — jm has no way to know which side (manifest or
    # hand-written header doc) is the stale one, so it warns rather than
    # failing the apply. `jm status --check` promotes this to a CI-gating
    # DRIFT section for projects that want it enforced.
    for obj in C.components(cfg):
        for name, m_dflt, h_dflt in _obj_mod.init_param_drift(cfg, root, obj):
            print(
                f"  warning: {obj}.{name} default mismatch: "
                f"manifest={m_dflt!r} header={h_dflt!r} "
                f"(native/inc/{obj}/{obj}_core.h) — one of these is stale"
            )

    print()
    total = (
        len(created)
        + len(updated)
        + len(bench_updated)
        + len(frag_doc_updated)
        + len(impl_patched)
    )
    if total:
        _reconciled = len(updated) + len(bench_updated) + len(frag_doc_updated)
        print(
            f"Done!  Materialized {len(created)} new file(s), "
            f"patched {len(impl_patched)} impl(s), and "
            f"reconciled {_reconciled} wiring file(s)"
            f" from {C.FILENAME}."
        )
    else:
        print(f"Done!  Project already matches {C.FILENAME} — nothing to do.")

    if C.app_config(cfg):
        from . import _app

        # gh-184: re-materialise the recorded app, not a default one. Passing
        # the [app] record's target/name/object keeps `jm apply` from rewriting
        # it to <project>/<first object>.
        _app_rec = C.app_config(cfg)
        _app.run(
            root,
            cfg,
            target=_app_rec.get("target", "c"),
            name=_app_rec.get("name"),
            object_=_app_rec.get("object"),
            function_=_app_rec.get("function"),
            module=_app_rec.get("module"),
            flags=_app_rec.get("flags"),
            commands=_app_rec.get("commands"),
        )
