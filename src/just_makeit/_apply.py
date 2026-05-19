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
import sys
import tempfile
from pathlib import Path

from . import _config as C

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


def _replay(cfg: dict, temp_root: Path) -> None:
    """Re-run the full scaffold for *cfg* into the pristine *temp_root*."""
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
        _object.run(temp_root, comp, None, **_object_kwargs(cfg, comp))
    for mod in mods:
        for comp in C.module_objects(cfg, mod):
            _object.run(temp_root, comp, mod, **_object_kwargs(cfg, comp))

    all_comps = standalone + [
        o for m in mods for o in C.module_objects(cfg, m)
    ]
    for comp in all_comps:
        mod = C.component_module(cfg, comp)
        for m in C.methods(cfg, comp):
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
            _function.run(
                temp_root,
                fn["name"],
                mod,
                doc=fn.get("doc", ""),
                params=[(p["name"], p["type"]) for p in fn.get("params", [])],
                return_type=fn.get("return_type", "void"),
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


def run(root: Path) -> None:
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first, or author a manifest to apply.",
            file=sys.stderr,
        )
        sys.exit(1)

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
        with contextlib.redirect_stdout(io.StringIO()):
            _replay(cfg, temp_root)
        created = _sync_missing(temp_root, root)

    for rel in created:
        print(f"  create  {root / rel}")

    print()
    if created:
        print(f"Done!  Materialized {len(created)} file(s) from {C.FILENAME}.")
    else:
        print(f"Done!  Project already matches {C.FILENAME} — nothing to do.")
