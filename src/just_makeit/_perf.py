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

from . import _textio
import re
import sys
from pathlib import Path

from . import _config as C
from . import _render as T
from . import _incpath as INC
from . import _csym as CSYM


def _patch_core_h(header: Path, comp: str) -> bool:
    """Upgrade _core.h: add jm_perf.h include and replace step() qualifier."""
    text = header.read_text(encoding="utf-8")
    original = text
    common = f'#include "{INC.include("clib_common.h", header)}"'
    perf = f'#include "{INC.include("jm_perf.h", header)}"'
    # Any mention of the header counts as present, however it is spaced.
    if f'"{INC.include("jm_perf.h", header)}"' not in text:
        text = text.replace(common, f"{common}\n{perf}")
    # gh-1591: step() is spelled with the SYMBOL stem -- under c_prefix it is
    # `<p>_<comp>_step`, and matching the bare name retrofitted nothing.
    qualifier_re = re.compile(
        r"\bstatic inline\b(\s+\S.*?\n"
        + re.escape(CSYM.stem(header, comp))
        + r"_step\b)"
    )
    text = qualifier_re.sub(r"JM_FORCEINLINE JM_HOT\1", text)
    if text != original:
        _textio.write_text(header, text)
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

    inc = INC.header_root(root)
    perf_h = inc / "jm_perf.h"
    if not perf_h.exists():
        perf_h.parent.mkdir(parents=True, exist_ok=True)
        _textio.write_text(
            perf_h,
            T.render(T.JM_PERF_H, {"package": pkg, **INC.ctx_slots(cfg)}),
        )
        print(f"  create  {perf_h}")

    simd_h = inc / "jm_simd.h"
    if not simd_h.exists():
        _textio.write_text(simd_h, T.JM_SIMD_H)
        print(f"  create  {simd_h}")

    for comp in comps:
        core_h = INC.core_h(root, comp)
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
