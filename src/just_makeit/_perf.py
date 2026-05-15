"""
_perf.py — `just-makeit perf` command.

Upgrades an existing project to use performance annotations without
overwriting any user-written code.  Safe to run on a project with a
filled-in step() implementation.

What it does, per component:
  - Adds #include "jm_perf.h" to the component header (after clib_common.h)
  - Replaces `static inline` with `JM_FORCEINLINE JM_HOT` on step()
  - Writes native/inc/jm_perf.h if not already present
  - Records perf = "true" in just-makeit.toml

Idempotent: running twice changes nothing.
"""

import re
import sys
from pathlib import Path

from . import _config as C
from . import _templates as T


def _patch_core_h(header: Path, comp: str) -> bool:
    """Upgrade _core.h: add jm_perf.h include and replace step() qualifier."""
    text = header.read_text(encoding="utf-8")
    original = text
    if '"jm_perf.h"' not in text:
        text = text.replace(
            '#include "clib_common.h"',
            '#include "clib_common.h"\n#include "jm_perf.h"',
        )
    qualifier_re = re.compile(
        r"\bstatic inline\b(\s+\S.*?\n" + re.escape(comp) + r"_step\b)"
    )
    text = qualifier_re.sub(r"JM_FORCEINLINE JM_HOT\1", text)
    if text != original:
        header.write_text(text, encoding="utf-8")
        return True
    return False


def run(root: Path) -> None:
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)

    if C.is_perf(cfg):
        print("just-makeit: perf already enabled — nothing to do.")
        return

    comps = C.components(cfg)
    if not comps:
        print("error: project has no components.", file=sys.stderr)
        sys.exit(1)

    pkg = C.project_name(cfg)
    print("just-makeit: enabling perf annotations")
    print()

    inc = root / "native" / "inc"
    perf_h = inc / "jm_perf.h"
    if not perf_h.exists():
        perf_h.parent.mkdir(parents=True, exist_ok=True)
        perf_h.write_text(T.render(T.JM_PERF_H, {"package": pkg}), encoding="utf-8")
        print(f"  create  {perf_h}")

    simd_h = inc / "jm_simd.h"
    if not simd_h.exists():
        simd_h.write_text(T.JM_SIMD_H, encoding="utf-8")
        print(f"  create  {simd_h}")

    for comp in comps:
        core_h = root / "native" / "inc" / comp / f"{comp}_core.h"
        if core_h.exists():
            if _patch_core_h(core_h, comp):
                print(f"  update  {core_h}")
            else:
                print(f"  ok      {core_h}  (already up to date)")
        else:
            print(f"  skip    {comp}  (header not found)", file=sys.stderr)

    cfg.setdefault("project", {})["perf"] = "true"
    C.save(root, cfg)
    print(f"  update  {cfg_path}")
    print()
    print("Done!  Rebuild; use ENABLE_SIMD=ON for SIMD optimisations.")
