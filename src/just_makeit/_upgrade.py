"""
_upgrade.py — schema migration for existing just-makeit projects.

Each migration entry in MIGRATIONS maps a schema version N to a list of
steps that advance the project from schema N to schema N+1.  Steps are
applied in order; each is idempotent (safe to run repeatedly).

Step types
----------
AddFile(path, template_attr)
    Write the named template to `path` (relative to project root) if and
    only if the file does not already exist.  `path` supports the same
    ``<<package>>`` / ``<<project>>`` placeholders used by template strings.
    `template_attr` is the attribute name on the ``_templates`` module.

AddTomlKey(section, key, default)
    Add `key = default` under `[section]` in the TOML if the key is absent.
    `section` is a list of strings, e.g. ``["project"]``.

Usage
-----
    just-makeit upgrade          # advance to CURRENT_SCHEMA
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from . import _config as C
from . import _context as Ctx
from . import _render as R


@dataclass
class AddFile:
    """Write a rendered template file if it does not already exist."""

    path: str  # relative path; may contain <<package>>/<<project>>
    template_attr: str  # attribute name on the _templates module


@dataclass
class AddTomlKey:
    """Insert a key with a default value into a top-level TOML section if absent."""

    section: str  # top-level section name, e.g. "project"
    key: str
    default: str


@dataclass
class RegenBench:
    """Re-render bench_<comp>_core.c for every standalone component.

    Unlike AddFile, this overwrites existing files so that new template
    features (e.g. method timing blocks) land in projects that were
    scaffolded before the feature was added.
    """


@dataclass
class MigrateBenchHistory:
    """Schema 5: dated, trimmed benchmark snapshots.

    Ensures ``benchmarks/history/.gitkeep`` exists and rewrites the
    Makefile so ``make bench`` delegates to ``just-makeit bench`` — which
    drops raw per-iteration arrays before writing a snapshot, so a single
    run no longer bloats the JSON to 100+ MB.  Any older ``bench-python``
    / ``bench-c`` targets and ``BENCH_*`` variables are removed.
    """


# `make bench` after migration: a one-line delegation to the jm CLI,
# which owns building, running, trimming, and snapshotting.
_BENCH_TARGET = "bench:\n\tjust-makeit bench\n"


def _rewrite_makefile_bench(text: str) -> str:
    """Return *text* with the bench target(s) collapsed to _BENCH_TARGET.

    Drops the ``bench`` / ``bench-python`` / ``bench-c`` target blocks and
    every ``BENCH_*`` variable line, then re-emits a single ``bench``
    target where the old ``bench`` target stood.  Idempotent: a Makefile
    already in the target form is returned unchanged.
    """
    lines = text.splitlines(keepends=True)
    drop_targets = {"bench", "bench-python", "bench-c"}
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        body = lines[i].rstrip("\n")
        # Orphan BENCH_* variable assignment → drop it and any
        # backslash-continued lines that belong to it.
        if re.match(r"BENCH_[A-Z_]+\s*[:?]?=", body):
            while True:
                cont = body.endswith("\\")
                i += 1
                if not cont or i >= n:
                    break
                body = lines[i].rstrip("\n")
            continue
        m = re.match(r"([A-Za-z0-9_.-]+)\s*:(?!=)", body)
        if m and m.group(1) in drop_targets:
            is_bench = m.group(1) == "bench"
            i += 1
            # A recipe is the run of tab-indented lines that follows.
            while i < n and lines[i].startswith("\t"):
                i += 1
            # Absorb one blank line that trailed the dropped block.
            if i < n and lines[i].strip() == "":
                i += 1
            if is_bench:
                out.append(_BENCH_TARGET + "\n")
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


# Migration table: schema N → N+1.
# Keep migrations append-only; never modify an existing entry.
MIGRATIONS: dict[int, list] = {
    1: [
        # Schema 2 adds docs/coverage scaffolding (zensical.toml + docs/).
        AddFile("zensical.toml", "ZENSICAL_TOML"),
        AddFile("docs/index.md", "DOCS_INDEX_MD"),
        AddFile("docs/api.md", "DOCS_API_MD"),
    ],
    2: [
        # Schema 3 regenerates bench files so method timing blocks appear
        # in projects that were scaffolded before this feature was added.
        RegenBench(),
    ],
    3: [
        # Schema 4 adds jm_bench.h (per-round stats + pytest-benchmark JSON)
        # and regenerates bench C files to use the new timing structure.
        AddFile("native/benchmarks/jm_bench.h", "JM_BENCH_H"),
        RegenBench(),
    ],
    4: [
        # Schema 5 moves benchmarking under `just-makeit bench`, which
        # trims raw per-iteration arrays and writes dated snapshots to
        # benchmarks/history/.
        MigrateBenchHistory(),
    ],
    5: [
        # Schema 6 gates the `include = [...]` key for split per-object
        # TOMLs. Projects stay single-file unless they opt in by adding
        # `include`; the bump alone is the migration.
    ],
    6: [
        # Schema 7 gates the top-level `[[enum]]` single-source-of-truth and
        # the `type = "enum:<name>"` parameter reference (gh-285). Projects
        # keep inlining `string_enum:a,b,c` unless they opt in by declaring an
        # `[[enum]]`; the bump alone is the migration. (Older jm cannot read a
        # manifest that declares `[[enum]]`, so the bump also signals the
        # minimum tool version.)
    ],
}


def _build_ctx(cfg: dict) -> dict[str, str]:
    """Build a minimal render context from project config."""
    name = C.project_name(cfg)
    return {
        "package": name,
        "project": name.replace("_", "-"),
        "project_underscore": name,
        "version": C.project_version(cfg),
    }


def _apply_step(root: Path, step, ctx: dict[str, str]) -> None:
    if isinstance(step, AddFile):
        dest = root / R.render(step.path, ctx)
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        template = getattr(R, step.template_attr)
        dest.write_text(R.render(template, ctx), encoding="utf-8")
        print(f"  create  {dest.relative_to(root)}")

    elif isinstance(step, AddTomlKey):
        target = C.load(root)
        section = target.setdefault(step.section, {})
        if step.key not in section:
            section[step.key] = step.default
            C.save(root, target)
            print(f"  update  just-makeit.toml  [{step.section}] {step.key}")

    elif isinstance(step, MigrateBenchHistory):
        gitkeep = root / "benchmarks" / "history" / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.parent.mkdir(parents=True, exist_ok=True)
            gitkeep.write_text("", encoding="utf-8")
            print(f"  create  {gitkeep.relative_to(root)}")
        makefile = root / "Makefile"
        if makefile.exists():
            old = makefile.read_text(encoding="utf-8")
            new = _rewrite_makefile_bench(old)
            if new != old:
                makefile.write_text(new, encoding="utf-8")
                print("  update  Makefile  (bench → just-makeit bench)")

    elif isinstance(step, RegenBench):
        cfg = C.load(root)
        pkg = C.project_name(cfg)
        version = C.project_version(cfg)
        perf = C.is_perf(cfg)
        for comp in C.components(cfg):
            bench_c = root / "native" / "benchmarks" / f"bench_{comp}_core.c"
            if not bench_c.exists():
                continue
            no_step = C.is_no_step(cfg, comp)
            tmpl = R.NO_STEP_BENCH_C if no_step else R.COMPONENT_BENCH_C
            comp_ctx: dict = {"component": comp, "Component": comp.title()}
            comp_ctx.update(
                {
                    "package": pkg,
                    "PACKAGE": pkg.upper(),
                    "project": pkg.replace("_", "-"),
                    "project_underscore": pkg,
                    "version": version,
                }
            )
            arg_type = C.arg_type(cfg, comp)
            return_type = C.return_type(cfg, comp)
            comp_ctx.update(Ctx.make_sample_ctx(arg_type, return_type))
            comp_ctx.update(
                Ctx.make_state_ctx(
                    comp,
                    comp_ctx["Component"],
                    C.state_vars(cfg, comp),
                    array_args=C.array_args(cfg, comp),
                    no_state=C.is_no_state(cfg, comp),
                    init_params=C.init_params(cfg, comp),
                )
            )
            comp_ctx.update(Ctx.make_perf_ctx(perf))
            comp_ctx.update(
                Ctx.make_step_ctx(
                    comp_ctx,
                    arg_type,
                    return_type,
                    no_step=no_step,
                    mutable=C.is_mutable(cfg, comp),
                )
            )
            comp_ctx.update(
                Ctx.make_methods_ctx(
                    comp,
                    comp_ctx["Component"],
                    C.methods(cfg, comp),
                    pkg=pkg,
                    py_create_args=comp_ctx.get("py_create_args", ""),
                    no_state=C.is_no_state(cfg, comp),
                    serializable=C.is_serializable(cfg, comp),
                )
            )
            # NO_STEP components with no init_params have an empty
            # c_create_args — the _create() signature is user-managed.
            # In that case suppress method blocks (obj is unavailable)
            # and emit a TODO comment instead of a broken _create() call.
            if no_step:
                c_args = comp_ctx.get("c_create_args", "")
                if c_args:
                    comp_ctx["bench_create_stmt"] = (
                        f"    {comp}_state_t *obj = {comp}_create({c_args});"
                    )
                    comp_ctx["bench_destroy_stmt"] = (
                        f"    {comp}_destroy(obj);"
                    )
                else:
                    comp_ctx["bench_create_stmt"] = (
                        f"    /* TODO: {comp}_state_t *obj"
                        f" = {comp}_create(...); */"
                    )
                    comp_ctx["bench_destroy_stmt"] = ""
                    comp_ctx["bench_methods_timing_block"] = ""
            bench_c.write_text(R.render(tmpl, comp_ctx), encoding="utf-8")
            print(f"  update  {bench_c.relative_to(root)}")


def run(root: Path) -> None:
    """Advance the project at *root* to CURRENT_SCHEMA."""
    cfg = C.load(root)
    if not cfg:
        print("error: no just-makeit.toml found.", file=sys.stderr)
        sys.exit(1)

    current = C.schema_version(cfg)
    target = C.CURRENT_SCHEMA

    if current >= target:
        print(f"already up to date (schema {current})")
        return

    ctx = _build_ctx(cfg)

    for version in range(current, target):
        steps = MIGRATIONS.get(version, [])
        print(f"migrating schema {version} → {version + 1}")
        for step in steps:
            # Reload cfg each step so AddTomlKey changes are cumulative.
            cfg = C.load(root)
            _apply_step(root, step, ctx)

        cfg = C.load(root)
        C.set_schema_version(cfg, version + 1)
        C.save(root, cfg)

    print(f"project is now at schema {target}")
