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
import difflib
import fnmatch
import io
import json
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


def _is_allowed(rel_posix: str, patterns: "list[str]") -> bool:
    """True if *rel_posix* matches an allow entry (exact path or fnmatch glob)."""
    return any(
        rel_posix == pat or fnmatch.fnmatch(rel_posix, pat) for pat in patterns
    )


def _unified_diff(before: bytes, after: bytes, rel_posix: str) -> str:
    """Unified diff (disk → what apply would write) for a stale text file."""
    try:
        b = before.decode("utf-8").splitlines(keepends=True)
        a = after.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return f"    (binary file {rel_posix} differs)\n"
    return "".join(
        difflib.unified_diff(
            b, a, fromfile=f"a/{rel_posix}", tofile=f"b/{rel_posix}"
        )
    )


def run(
    root: Path,
    *,
    allow: "tuple[str, ...]" = (),
    as_json: bool = False,
    show_diff: bool = False,
    check: bool = False,
) -> int:
    """Print a status report; return the count of files `apply` would change.

    ``allow`` — path patterns (exact POSIX path or fnmatch glob) reported as
    ALLOWED and excluded from the returned count (combined with the
    ``[project] status_allow`` manifest list). ``as_json`` emits a structured
    report. ``show_diff`` prints a unified diff per stale file. ``check``
    suppresses the per-file listing (one-line summary only). The return value
    counts only non-allowed drift, so a CI gate fails exactly on real drift
    (gh-140)."""
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
        if as_json:
            print(json.dumps({"entries": [], "ok": 0}))
        else:
            print(
                "just-makeit: manifest declares no objects or modules; "
                "nothing to status."
            )
        return 0

    allow_patterns = list(allow) + C.status_allow(cfg)

    # (rel_posix, state, allowed, diff_text)
    entries: list[tuple[str, str, bool, str]] = []
    ok_count = 0
    with tempfile.TemporaryDirectory(prefix="jm-status-") as tmp:
        scratch = Path(tmp) / root.name
        shutil.copytree(root, scratch, ignore=_COPY_IGNORE)
        # Run apply on the copy so we observe its real reconciliation
        # (glue regenerated, _core.h merged, _core.c preserved). Suppress
        # its progress output — status prints its own report.
        with contextlib.redirect_stdout(io.StringIO()):
            _apply.run(scratch)

        for rel in _walk_managed(scratch):
            real = root / rel
            after = (scratch / rel).read_bytes()
            rel_posix = rel.as_posix()
            if not real.exists():
                state, diff = "missing", ""
            else:
                before = real.read_bytes()
                if before == after:
                    ok_count += 1
                    continue
                state = "stale"
                diff = (
                    _unified_diff(before, after, rel_posix)
                    if show_diff
                    else ""
                )
            entries.append(
                (
                    rel_posix,
                    state,
                    _is_allowed(rel_posix, allow_patterns),
                    diff,
                )
            )

    drift = [e for e in entries if not e[2]]
    allowed = [e for e in entries if e[2]]

    if as_json:
        print(
            json.dumps(
                {
                    "entries": [
                        {"path": p, "state": s, "allowed": al}
                        for (p, s, al, _) in entries
                    ],
                    "ok": ok_count,
                    "drift": len(drift),
                },
                indent=2,
            )
        )
        return len(drift)

    if not check:
        missing = [e for e in drift if e[1] == "missing"]
        stale = [e for e in drift if e[1] == "stale"]
        if missing:
            print(f"MISSING ({len(missing)}) — `jm apply` will create:")
            for p, _, _, _ in missing:
                print(f"  + {p}")
            print()
        if stale:
            print(
                f"STALE ({len(stale)}) — `jm apply` will rewrite "
                "from the manifest:"
            )
            for p, _, _, diff in stale:
                print(f"  ~ {p}")
                if diff:
                    print("".join(f"    {ln}" for ln in diff.splitlines(True)))
            print(
                "  Run `jm apply` to sync (glue regenerated; "
                "your _core.c is kept)."
            )
            print()
        if allowed:
            print(f"ALLOWED ({len(allowed)}) — known deviations, not counted:")
            for p, st, _, _ in allowed:
                print(f"  {'+' if st == 'missing' else '~'} {p}")
            print()

    n_missing = sum(1 for e in drift if e[1] == "missing")
    n_stale = sum(1 for e in drift if e[1] == "stale")
    if not drift:
        suffix = f" ({len(allowed)} allowed)" if allowed else ""
        print(
            f"OK — up to date; {ok_count} manifest-owned file(s) match"
            f"{suffix}."
        )
    else:
        print(
            f"summary: {ok_count} OK, {n_missing} missing, "
            f"{n_stale} stale"
            + (f", {len(allowed)} allowed" if allowed else "")
            + ".\n"
            "Your `_core.c` is sacred — apply never changes it; use "
            "`jm regenerate <component>` to rebuild one from the manifest."
        )

    return len(drift)
