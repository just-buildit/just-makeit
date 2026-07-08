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
  - DROPPED — (gh-426) a stale `.pyi` where a class/method/function present
              on disk has no manifest trace and vanishes in what `apply`
              would write. `.pyi` files have no sacred-fragment mechanism
              like `_core.c` does, so a hand-added stub with zero manifest
              declaration is silently deleted on regen — this is content
              loss, not routine drift, and is never suppressed by
              `status_allow`.

Your `_core.c` is sacred: `apply` never changes it, so hand-edited
algorithm code never shows up as STALE. To rebuild a component from the
manifest (discarding its `_core.c`), use `jm regenerate <component>`.

The verb is purely read-only — it only ever writes to the throwaway copy,
never the project. Safe from CI or before a sensitive change.
"""

from __future__ import annotations

import ast
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


def _pyi_symbols(text: str) -> "set[str]":
    """Return {ClassName, ClassName.method, function_name} for a .pyi source.

    Best-effort: an unparsable (e.g. mid-edit) file yields an empty set
    rather than raising, so a broken .pyi degrades to "no drop detected"
    instead of crashing status.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(f"{node.name}.{item.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def _dropped_pyi_symbols(
    before: bytes, after: bytes, rel_posix: str
) -> "frozenset[str]":
    """Symbol names present in *before* but absent from *after* (gh-426).

    `.pyi` files are jm's alone, full-stop — unlike `_core.c`, there is no
    sacred-fragment mechanism preserving hand-added Python-visible surface
    that has no manifest declaration at all. A regen then silently drops it
    with no other signal (a normal STALE diff looks identical to routine
    reformatting). Only meaningful for `.pyi` — valid Python syntax, so
    `ast.parse` gives an exact symbol set for free.
    """
    if not rel_posix.endswith(".pyi"):
        return frozenset()
    try:
        before_syms = _pyi_symbols(before.decode("utf-8"))
        after_syms = _pyi_symbols(after.decode("utf-8"))
    except UnicodeDecodeError:
        return frozenset()
    return frozenset(before_syms - after_syms)


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
    (gh-140).

    A stale ``.pyi`` is additionally checked for dropped symbols (gh-426): a
    class/method/function present in the on-disk file but absent from what
    `apply` would write — the signature of a hand-written stub with zero
    manifest declaration silently vanishing on regen. This is reported in a
    separate DROPPED section (printed even under ``check``) and always
    counted in the return value, ignoring ``allow``/``status_allow`` — a
    drop is real content loss, not routine drift, so it is never
    suppressible."""
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)
    # gh-183: surface jm-version skew on stderr (keeps stdout/JSON clean).
    _rec, _run = C.jm_version(cfg), C.jm_cli_version()
    if not as_json and _rec and _run != "unknown" and _rec != _run:
        print(
            f"warning: project generated with just-makeit {_rec}, running "
            f"{_run} (gh-183) — results may not reflect the pinned version.",
            file=sys.stderr,
        )
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

    # (rel_posix, state, allowed, diff_text, dropped_symbols)
    entries: list[tuple[str, str, bool, str, frozenset]] = []
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
            dropped: frozenset = frozenset()
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
                # gh-426: a hand-written .pyi symbol with no manifest trace
                # at all vanishes on regen looking like routine STALE drift.
                dropped = _dropped_pyi_symbols(before, after, rel_posix)
            entries.append(
                (
                    rel_posix,
                    state,
                    _is_allowed(rel_posix, allow_patterns),
                    diff,
                    dropped,
                )
            )

    drift = [e for e in entries if not e[2]]
    allowed = [e for e in entries if e[2]]
    dropped_entries = [e for e in entries if e[4]]
    n_dropped_files = len(dropped_entries)
    # gh-426: a symbol drop is never suppressed by status_allow/--allow —
    # it must count toward the CI-gating return value even for an allowed
    # file, or a status_allow entry silently hides real content loss behind
    # "this file always drifts, that's expected."
    drift_count = len(drift) + sum(1 for e in allowed if e[4])

    if as_json:
        print(
            json.dumps(
                {
                    "entries": [
                        {
                            "path": p,
                            "state": s,
                            "allowed": al,
                            "dropped_symbols": sorted(dr),
                        }
                        for (p, s, al, _, dr) in entries
                    ],
                    "ok": ok_count,
                    "drift": drift_count,
                    "dropped_files": n_dropped_files,
                },
                indent=2,
            )
        )
        return drift_count

    if not check:
        missing = [e for e in drift if e[1] == "missing"]
        stale = [e for e in drift if e[1] == "stale"]
        if missing:
            print(f"MISSING ({len(missing)}) — `jm apply` will create:")
            for p, _, _, _, _ in missing:
                print(f"  + {p}")
            print()
        if stale:
            print(
                f"STALE ({len(stale)}) — `jm apply` will rewrite "
                "from the manifest:"
            )
            for p, _, _, diff, _ in stale:
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
            for p, st, _, _, _ in allowed:
                print(f"  {'+' if st == 'missing' else '~'} {p}")
            print()

    # gh-426: printed regardless of --check — this is the one section meant
    # to be impossible to miss, unlike the listings above which --check
    # deliberately collapses to a one-line summary.
    if dropped_entries:
        print(
            f"DROPPED ({n_dropped_files}) — hand-written .pyi symbol(s) "
            "vanished with no manifest trace (not suppressed by "
            "status_allow):"
        )
        for p, _, al, _, dropped in dropped_entries:
            tag = " [status_allow]" if al else ""
            print(f"  ! {p}{tag}")
            for name in sorted(dropped):
                print(f"      - {name}")
        print(
            "  A method/class with zero manifest declaration silently"
            " disappears on regen — see gh-426."
        )
        print()

    n_missing = sum(1 for e in drift if e[1] == "missing")
    n_stale = sum(1 for e in drift if e[1] == "stale")
    if not drift and not dropped_entries:
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
            + (f", {n_dropped_files} dropped (!)" if dropped_entries else "")
            + ".\n"
            "Your `_core.c` is sacred — apply never changes it; use "
            "`jm regenerate <component>` to rebuild one from the manifest."
        )

    return drift_count
