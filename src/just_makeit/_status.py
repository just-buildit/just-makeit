"""
_status.py — `just-makeit status` command.

Read-only diagnostic that shows exactly what `jm apply` would change,
before you run it. Under the v0.14 sacred/glue model that is the only
honest framing: a file is "out of sync" if and only if `apply` would
rewrite it.

Rather than byte-diffing a fresh render against disk — which over-reports
the files `apply` *merges* rather than overwrites (the package
`__init__.py`, the hybrid `_core.h`) — `status` runs `apply` on a throwaway
*copy* of the project and diffs the result against the real tree. Whatever
`apply` would do to the copy is exactly what it would do to the project:

  - MISSING — `apply` would create it (absent today).
  - STALE   — `apply` would rewrite it from the manifest: glue
              (`_ext.c`, `.pyi`, CMake) regenerated, `_core.h` declarations
              merged. Run `jm apply` to sync.
  - OK      — `apply` would leave it untouched.

Your `_core.c` is sacred: `apply` never changes it, so hand-edited
algorithm code never shows up as STALE. To rebuild a component from the
manifest (discarding its `_core.c`), use `jm regenerate <component>`.

The verb is purely read-only — it only ever writes to the throwaway copy,
never the project. Safe from CI or before a sensitive change.
"""

import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

from . import _apply
from . import _config as C
from ._apply import _SKIP_DIRS, _SKIP_FILES, _SKIP_SUFFIXES

# Directories/files never copied into the scratch tree (build artefacts,
# VCS, caches) — not manifest-owned, and copying them only slows status.
_COPY_IGNORE = shutil.ignore_patterns(
    *_SKIP_DIRS, "*.so", "*.pyd", "*.pyc", "*.pyo", "compile_commands.json"
)


def _walk_managed(base: Path) -> list[Path]:
    """Return manifest-owned files under *base*, as relative paths."""
    out: list[Path] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base)
        if set(rel.parts) & _SKIP_DIRS or rel.name in _SKIP_FILES:
            continue
        if rel.suffix in _SKIP_SUFFIXES:
            continue
        out.append(rel)
    return out


def run(root: Path) -> int:
    """Print a status report; return the count of files `apply` would change."""
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)
    if not C.components(cfg) and not C.modules(cfg):
        print(
            "just-makeit: manifest declares no objects or modules; "
            "nothing to status."
        )
        return 0

    with tempfile.TemporaryDirectory(prefix="jm-status-") as tmp:
        scratch = Path(tmp) / root.name
        shutil.copytree(root, scratch, ignore=_COPY_IGNORE)
        # Run apply on the copy so we observe its real reconciliation
        # (glue regenerated, _core.h merged, _core.c preserved). Suppress
        # its progress output — status prints its own report.
        with contextlib.redirect_stdout(io.StringIO()):
            _apply.run(scratch)

        missing: list[Path] = []
        stale: list[Path] = []
        ok_count = 0
        for rel in _walk_managed(scratch):
            real = root / rel
            after = (scratch / rel).read_bytes()
            if not real.exists():
                missing.append(rel)
            elif real.read_bytes() != after:
                stale.append(rel)
            else:
                ok_count += 1

    if missing:
        print(f"MISSING ({len(missing)}) — `jm apply` will create:")
        for r in missing:
            print(f"  + {r}")
        print()
    if stale:
        print(
            f"STALE ({len(stale)}) — `jm apply` will rewrite from the manifest:"
        )
        for r in stale:
            print(f"  ~ {r}")
        print(
            "  Run `jm apply` to sync (glue regenerated; your _core.c is kept)."
        )
        print()

    total = len(missing) + len(stale)
    if total == 0:
        print(f"OK — up to date; {ok_count} manifest-owned file(s) match.")
    else:
        print(
            f"summary: {ok_count} OK, {len(missing)} missing, "
            f"{len(stale)} stale.\n"
            "Your `_core.c` is sacred — apply never changes it; use "
            "`jm regenerate <component>` to rebuild one from the manifest."
        )

    return total
