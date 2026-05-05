"""
_add.py — `just-makeit add` command.

Adds state variables to an existing project:
  1. Read just-makeit.toml
  2. Validate no duplicate names
  3. Back up the six state-sensitive generated files
  4. Regenerate them from the merged state list
  5. Update just-makeit.toml
  On any error during steps 4-5, restore from backup before re-raising.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import _config as C
from . import _init
from . import _templates as T

_STATE_TEMPLATES = [
    ("native/inc/{c}/{c}_core.h",        T.COMPONENT_CORE_H),
    ("native/src/{c}/{c}_core.c",        T.COMPONENT_CORE_C),
    ("native/src/{c}/{c}_ext.c",         T.COMPONENT_EXT_C),
    ("native/tests/test_{c}_core.c",     T.COMPONENT_TEST_C),
    ("src/{c}/{c}.pyi",                  T.COMPONENT_PYI),
    ("src/{c}/tests/test_{c}.py",        T.PYTEST_TEST),
]


def _expand(pattern: str, comp: str) -> str:
    return pattern.replace("{c}", comp)


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


def run(root: Path, new_vars: list[tuple[str, str, str]]) -> None:
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit init' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)
    comp = cfg["component"]["name"]
    version = cfg["component"].get("version", "0.1.0")
    existing = C.state_vars(cfg)

    existing_names = {n for n, _, __ in existing}
    for name, _, __ in new_vars:
        if name in existing_names:
            print(
                f"error: state variable '{name}' already exists.",
                file=sys.stderr,
            )
            sys.exit(1)

    all_vars = existing + new_vars

    ctx = _init._make_ctx(comp, version)
    ctx.update(T.make_state_ctx(ctx["component"], ctx["Component"], all_vars))
    r = lambda tmpl: T.render(tmpl, ctx)

    paths = [root / _expand(pat, comp) for pat, _ in _STATE_TEMPLATES]

    print(
        f"just-makeit: adding {len(new_vars)} state variable(s) to '{comp}'"
    )
    print()

    with _backup(paths):
        for pat, tmpl in _STATE_TEMPLATES:
            path = root / _expand(pat, comp)
            path.write_text(r(tmpl), encoding="utf-8")
            print(f"  update  {path}")

    cfg["state"] = [
        {"name": n, "type": t, "default": d} for n, t, d in all_vars
    ]
    C.save(root, cfg)
    print(f"  update  {cfg_path}")
    print()
    print(f"Done!  {len(new_vars)} variable(s) added.")
