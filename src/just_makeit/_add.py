"""
_add.py — `just-makeit add` command.

Adds state variables to an existing component:
  1. Read just-makeit.toml
  2. Resolve which component to modify
  3. Validate no duplicate names
  4. Back up the six state-sensitive generated files
  5. Regenerate them from the merged state list
  6. Update just-makeit.toml
  On any error during steps 5-6, restore from backup before re-raising.
"""

import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import _config as C
from . import _init
from . import _templates as T

_STATEFUL_TEMPLATES = [
    ("native/inc/{c}/{c}_core.h", T.COMPONENT_CORE_H),
    ("native/src/{c}/{c}_core.c", T.COMPONENT_CORE_C),
    ("native/src/{c}/{c}_ext.c", T.COMPONENT_EXT_C),
    ("native/tests/test_{c}_core.c", T.COMPONENT_TEST_C),
    ("native/benchmarks/bench_{c}_core.c", T.COMPONENT_BENCH_C),
    ("src/{p}/{c}.pyi", T.COMPONENT_PYI),
    ("src/{p}/tests/test_{c}.py", T.PYTEST_TEST),
    ("src/{p}/benchmarks/bench_{c}.py", T.COMPONENT_BENCH_PY),
]

_PURE_SCALAR_TEMPLATES = [
    ("native/inc/{c}/{c}_core.h", T.PURE_SCALAR_CORE_H),
    ("native/src/{c}/{c}_core.c", T.PURE_SCALAR_CORE_C),
    ("native/src/{c}/{c}_ext.c", T.PURE_SCALAR_EXT_C),
    ("native/tests/test_{c}_core.c", T.PURE_SCALAR_TEST_C),
    ("native/benchmarks/bench_{c}_core.c", T.PURE_SCALAR_BENCH_C),
    ("src/{p}/{c}.pyi", T.PURE_SCALAR_PYI),
    ("src/{p}/tests/test_{c}.py", T.PYTEST_PURE_SCALAR_TEST),
    ("src/{p}/benchmarks/bench_{c}.py", T.PURE_SCALAR_BENCH_PY),
]

_PURE_STRUCT_TEMPLATES = [
    ("native/inc/{c}/{c}_core.h", T.PURE_STRUCT_CORE_H),
    ("native/src/{c}/{c}_core.c", T.PURE_STRUCT_CORE_C),
    ("native/src/{c}/{c}_ext.c", T.PURE_STRUCT_EXT_C),
    ("native/tests/test_{c}_core.c", T.PURE_STRUCT_TEST_C),
    ("native/benchmarks/bench_{c}_core.c", T.PURE_STRUCT_BENCH_C),
    ("src/{p}/{c}.pyi", T.PURE_STRUCT_PYI),
    ("src/{p}/tests/test_{c}.py", T.PYTEST_PURE_STRUCT_TEST),
    ("src/{p}/benchmarks/bench_{c}.py", T.PURE_STRUCT_BENCH_PY),
]


def _expand(pattern: str, comp: str, pkg: str) -> str:
    return pattern.replace("{c}", comp).replace("{p}", pkg)


@contextmanager
def _backup(files: list[Path]):
    backed: dict[Path, Path] = {}
    try:
        for p in files:
            if p.exists():
                fd, tmp = tempfile.mkstemp(suffix=".bak")
                os.close(fd)
                shutil.copy2(p, tmp)
                backed[p] = Path(tmp)
        yield
    except Exception:
        for p, tmp in backed.items():
            shutil.copy2(tmp, p)
            print(f"  restored  {p}", file=sys.stderr)
        raise
    finally:
        for tmp in backed.values():
            tmp.unlink(missing_ok=True)


def run(
    root: Path,
    component: str | None,
    new_vars: list[tuple[str, str, str]],
) -> None:
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)
    comps = C.components(cfg)

    if not comps:
        print(
            "error: project has no components yet. Run 'just-makeit init <component>' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    if component is None:
        if len(comps) == 1:
            component = comps[0]
        else:
            print(
                f"error: project has multiple components {comps}. Use --component to specify one.",
                file=sys.stderr,
            )
            sys.exit(1)
    elif component not in comps:
        print(
            f"error: component '{component}' not found. Available: {comps}",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = C.project_name(cfg)
    version = C.project_version(cfg)
    existing = C.state_vars(cfg, component)

    existing_names = {n for n, _, __ in existing}
    for name, _, __ in new_vars:
        if name in existing_names:
            print(
                f"error: state variable '{name}' already exists.",
                file=sys.stderr,
            )
            sys.exit(1)

    all_vars = existing + new_vars
    style = C.pure_style(cfg, component)

    ctx = _init._make_component_ctx(component)
    ctx.update(
        {
            "package": pkg,
            "project": pkg.replace("_", "-"),
            "project_underscore": pkg,
            "version": version,
        }
    )

    if style:
        pure_ctx = T.make_pure_ctx(ctx["component"], ctx["Component"], all_vars)
        ctx.update(pure_ctx)
        # pure_style may change if state type mix changes (scalar→struct on array add)
        new_style = pure_ctx["pure_style"]
        templates = _PURE_SCALAR_TEMPLATES if new_style == "scalar" else _PURE_STRUCT_TEMPLATES
    else:
        ctx.update(T.make_state_ctx(ctx["component"], ctx["Component"], all_vars))
        templates = _STATEFUL_TEMPLATES
        new_style = None

    ctx.update(T.make_perf_ctx(C.is_perf(cfg)))

    def r(tmpl):
        return T.render(tmpl, ctx)

    paths = [root / _expand(pat, component, pkg) for pat, _ in templates]

    print(f"just-makeit: adding {len(new_vars)} state variable(s) to '{component}'")
    print()

    with _backup(paths):
        for pat, tmpl in templates:
            path = root / _expand(pat, component, pkg)
            path.write_text(r(tmpl), encoding="utf-8")
            print(f"  update  {path}")

    cfg[component]["state"] = [
        {"name": n, "type": t, "default": d} for n, t, d in all_vars
    ]
    if new_style and new_style != style:
        cfg[component]["pure"] = new_style
    C.save(root, cfg)
    print(f"  update  {cfg_path}")
    print()
    print(f"Done!  {len(new_vars)} variable(s) added.")
