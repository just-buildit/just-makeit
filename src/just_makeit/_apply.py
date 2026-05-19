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
    return _PLACEHOLDER_RE.sub(
        lambda m: str(ctx.get(m.group(1), m.group(0))), body
    )


def _resolve_impl(
    section: dict, ctx: dict, root: Path, label: str
) -> str | None:
    """Honour `impl` / `impl_file` / `replace` on a TOML section. Returns
    the resolved body (interpolated + substituted) or None when neither
    key is set. Raises ValueError on mutual-exclusion violations."""
    inline = section.get("impl")
    file_ref = section.get("impl_file")
    if inline and file_ref:
        raise ValueError(
            f"{label}: `impl` and `impl_file` are mutually exclusive — "
            f"set one or the other."
        )
    if not inline and not file_ref:
        return None

    if file_ref:
        from . import _impl as I

        path_part, _, func = file_ref.partition("::")
        if not func:
            raise ValueError(
                f"{label}: impl_file must be 'path::funcname', "
                f"got {file_ref!r}."
            )
        body = I.extract_body(root / path_part, func)
    else:
        body = inline

    body = _interpolate(body, ctx)

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
        "class_name": C.class_name(cfg, comp),
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
        _module.run(temp_root, mod)

    for comp in standalone:
        octx = _object_ctx(cfg, comp, None)
        impl = _resolve_impl(
            cfg.get(comp, {}), octx, project_root, f"object {comp}"
        )
        _object.run(
            temp_root,
            comp,
            None,
            impl_body=impl,
            **_object_kwargs(cfg, comp),
        )
    for mod in mods:
        for comp in C.module_objects(cfg, mod):
            octx = _object_ctx(cfg, comp, mod)
            impl = _resolve_impl(
                cfg.get(comp, {}), octx, project_root, f"object {comp}"
            )
            _object.run(
                temp_root,
                comp,
                mod,
                impl_body=impl,
                **_object_kwargs(cfg, comp),
            )

    all_comps = standalone + [
        o for m in mods for o in C.module_objects(cfg, m)
    ]
    for comp in all_comps:
        mod = C.component_module(cfg, comp)
        for m in C.methods(cfg, comp):
            mctx = _object_ctx(cfg, comp, mod) | {"method": m["name"]}
            m_impl = _resolve_impl(
                m, mctx, project_root, f"{comp}.{m['name']}"
            )
            _method.run(
                temp_root,
                comp,
                m["name"],
                mod,
                m.get("arg_type", "float _Complex"),
                m.get("return_type", "float _Complex"),
                bool(m.get("variable_output")),
                list(m.get("multi_output", [])),
                params=[(p["name"], p["type"]) for p in m.get("params", [])],
                out_type=m.get("out_type"),
                out_divisor=int(m.get("out_divisor", 1)),
                batch=bool(m.get("batch")),
                impl_body=m_impl,
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
            )

    for mod in mods:
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
                params=[(p["name"], p["type"]) for p in fn.get("params", [])],
                return_type=fn.get("return_type", "void"),
                impl_body=f_impl,
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


_INCLUDE_LINE = 'include = ["objects/*.toml"]\n'


def _validate_fragment_impl_keys(fragment: dict, label: str) -> None:
    """Check `impl` / `impl_file` mutual-exclusion on every section in
    *fragment* before any side-effects happen."""
    for key, value in fragment.items():
        if key in ("project", "module", "include"):
            continue
        if not isinstance(value, dict):
            continue
        if value.get("impl") and value.get("impl_file"):
            raise ValueError(
                f"{label}: object {key}: `impl` and `impl_file` are "
                f"mutually exclusive — set one or the other."
            )
        for m in value.get("methods", []):
            if m.get("impl") and m.get("impl_file"):
                raise ValueError(
                    f"{label}: {key}.{m.get('name', '?')}: `impl` and "
                    f"`impl_file` are mutually exclusive."
                )


def _compose_fragment(root: Path, fragment_path: Path) -> Path:
    """Validate *fragment_path*, copy it into `objects/`, and ensure the
    manifest's `include` glob covers it. Returns the destination path.

    Errors with the design-specified remedy if the fragment declares an
    object that the project already has."""
    if not fragment_path.exists():
        raise FileNotFoundError(f"fragment not found: {fragment_path}")

    with fragment_path.open("rb") as f:
        fragment = tomllib.load(f)
    # Test-merge against the current resolved cfg to surface conflicts
    # before we touch any files. _merge_fragment raises ValueError naming
    # the conflicting object and the recommended remedy.
    C._merge_fragment(C.load(root), fragment, fragment_path)
    _validate_fragment_impl_keys(fragment, str(fragment_path))

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
    return dest


def run(root: Path, fragment: Path | None = None) -> None:
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
            "error: manifest declares no objects or modules — "
            "nothing to materialize.",
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

    for rel in created:
        print(f"  create  {root / rel}")

    print()
    if created:
        print(f"Done!  Materialized {len(created)} file(s) from {C.FILENAME}.")
    else:
        print(f"Done!  Project already matches {C.FILENAME} — nothing to do.")
