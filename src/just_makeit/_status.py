"""
_status.py — `just-makeit status` command.

Read-only diagnostic that reports drift between `just-makeit.toml` and
the materialised project on disk. Foundation for the v0.14 sacred-files
model: shows the user exactly what `jm apply` would change before they
run it.

The implementation replays the manifest into a throwaway temp directory
(same machinery `jm apply` uses) and walks the result, comparing each
generated file against the real project:

  - MISSING — file would be created by `jm apply` (it's missing today).
  - DRIFT   — file exists but its content differs from the manifest.
              Under today's apply semantics (add-only), `jm apply` will
              SKIP this file. Under the planned sacred-files rule
              (cli-redesign.md), the user must `rm` the file to refresh
              it.
  - OK      — file matches its manifest-driven content byte-for-byte.

Files the project owns but the manifest does NOT generate (hand-written
sources, tests the user authored) are not reported — only manifest-owned
files appear in the listing.

The verb is purely read-only; it never writes to the project. Safe to
run from CI or before a sensitive change.
"""

import contextlib
import io
import sys
import tempfile
from pathlib import Path

from . import _config as C
from ._apply import _SKIP_DIRS, _SKIP_FILES, _SKIP_SUFFIXES, _replay


def _walk_managed(temp_root: Path) -> list[Path]:
    """Return manifest-owned files under *temp_root*, relative paths."""
    out: list[Path] = []
    for p in sorted(temp_root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(temp_root)
        if set(rel.parts) & _SKIP_DIRS or rel.name in _SKIP_FILES:
            continue
        if rel.suffix in _SKIP_SUFFIXES:
            continue
        out.append(rel)
    return out


def _categorise(rel: Path, temp_root: Path, root: Path) -> str:
    """Return one of MISSING / DRIFT / OK for *rel*."""
    real = root / rel
    if not real.exists():
        return "MISSING"
    expected = (temp_root / rel).read_bytes()
    actual = real.read_bytes()
    return "OK" if expected == actual else "DRIFT"


def run(root: Path) -> int:
    """Print a status report; return the count of non-OK files."""
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)
    if not C.components(cfg) and not C.modules(cfg):
        print(
            "just-makeit: manifest declares no objects or modules; nothing to status."
        )
        return 0

    with tempfile.TemporaryDirectory(prefix="jm-status-") as tmp:
        temp_root = Path(tmp) / "replay"
        # _replay prints progress noisily; we want a quiet report.
        with contextlib.redirect_stdout(io.StringIO()):
            _replay(cfg, temp_root, root)

        managed = _walk_managed(temp_root)
        if not managed:
            print("just-makeit: replay produced no files.")
            return 0

        rows: list[tuple[str, Path]] = [
            (_categorise(rel, temp_root, root), rel) for rel in managed
        ]

    missing = [r for s, r in rows if s == "MISSING"]
    drift = [r for s, r in rows if s == "DRIFT"]
    ok = [r for s, r in rows if s == "OK"]

    if missing:
        print(f"MISSING ({len(missing)}) — `jm apply` will create:")
        for r in missing:
            print(f"  + {r}")
        print()
    if drift:
        print(f"DRIFT ({len(drift)}) — manifest disagrees with disk:")
        for r in drift:
            print(f"  ~ {r}")
        print(
            "  (today: `jm apply` SKIPS these — they exist already. "
            "Remove them and re-run apply to regenerate from the manifest.)"
        )
        print()
    if not missing and not drift:
        print(f"OK — {len(ok)} manifest-owned file(s) match the project.")
    else:
        print(
            f"summary: {len(ok)} OK, {len(missing)} missing, {len(drift)} drift "
            f"({len(rows)} total manifest-owned files)."
        )

    return len(missing) + len(drift)
