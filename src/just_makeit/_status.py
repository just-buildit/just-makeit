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
from . import _cfmt
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


def _unreconciled_glue(root: Path, cfg: dict) -> "set[str]":
    """Generated glue `apply` renders but never writes over an existing file.

    gh-767. `_apply._sync_missing` copies a rendered file back only when the
    real tree *lacks* it; everything else is reconciled by
    `_apply._sync_aggregates`, whose set is hand-enumerated — root CMakeLists,
    umbrella header, package ``__init__.py``, and each module's
    ``__init__.py`` / ``ext.c`` / ``CMakeLists`` / ``.pyi``. The per-object
    binding fragments were simply never added to it.

    That made them invisible twice over: `apply` leaves them frozen at
    whatever jm emitted when they were first created, and `status` cannot see
    it either — it copies the real tree into its scratch, `apply` rewrites
    neither side, and identical stale bytes compare equal. doppler carried 58
    bindings whose Python-level arity no longer matched the generator, and 45
    doctest lines divergent from the ``.pyi`` beside them, with
    ``status --check`` reporting clean throughout.

    Deleting them from the scratch is what makes them visible: `_sync_missing`
    then materializes jm's current output, and the existing comparison does
    the rest. Nothing in the real tree is touched.

    Derived from the manifest rather than globbed, so a module or object added
    later is covered without editing this. ``*_ext_*_extra.c`` is deliberately
    *not* here — that file is hand-written by contract (gh-543).
    """
    out: set[str] = set()
    for mod in C.modules(cfg):
        cname = C.module_paths(mod).cname
        for obj in C.module_objects(cfg, mod):
            rel = Path("native") / "src" / cname / f"{cname}_ext_{obj}.c"
            if (root / rel).is_file():
                out.add(rel.as_posix())
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

    # gh-442: init-param default mismatch between the manifest and the
    # sacred header's own @param (default: X) doc — a static comparison,
    # unrelated to what `apply` would rewrite, so it's computed directly
    # against the real project rather than via the scratch-copy diff below.
    from . import _object as _obj_mod

    drift_entries: list[tuple[str, str, str, str]] = [
        (obj, name, m_dflt, h_dflt)
        for obj in C.components(cfg)
        for name, m_dflt, h_dflt in _obj_mod.init_param_drift(cfg, root, obj)
    ]

    # (rel_posix, state, allowed, diff_text, dropped_symbols)
    entries: list[tuple[str, str, bool, str, frozenset]] = []
    ok_count = 0
    with tempfile.TemporaryDirectory(prefix="jm-status-") as tmp:
        # gh-764: `root.name` is "" for a relative root — `jm status` run as
        # `_status.run(Path("."))` collapsed the scratch path onto the temp
        # directory itself and died in `copytree` with FileExistsError.
        # Resolve for the *name* only; `root` itself stays as the caller gave
        # it, so reported paths are unchanged.
        scratch = Path(tmp) / (root.resolve().name or "project")
        shutil.copytree(root, scratch, ignore=_COPY_IGNORE)
        # gh-767: drop the glue `apply` renders but never writes back, so the
        # replay below materializes jm's current output for it and the
        # comparison can see it at all. Scratch only — the real tree is never
        # touched, and these are reported in their own section because
        # `jm apply` will *not* fix them.
        unreconciled = _unreconciled_glue(scratch, cfg)
        for _rel in unreconciled:
            (scratch / _rel).unlink()
        # Run apply on the copy so we observe its real reconciliation
        # (glue regenerated, _core.h merged, _core.c preserved). Suppress
        # its progress output — status prints its own report.
        # honor_status_allow=False (gh-441): apply itself now skips
        # status_allow-matched files so a real `jm apply` never clobbers
        # hand-maintained content, but this throwaway replay must still
        # regenerate them to compute the genuine before/after diff below —
        # otherwise every allowed file would look up-to-date (never
        # written) instead of classified ALLOWED, and the gh-426
        # dropped-symbol check would go blind for exactly the files it
        # exists to guard.
        # gh-609: also suppress stderr — the same replay triggers
        # `_patch_step_impls`'s impl-overwrite warning against the *scratch*
        # copy, which would otherwise leak a misleading "overwriting the
        # header" message about a throwaway temp path even though `status`
        # never touches the real project. The resulting STALE entry below
        # already reports the real divergence.
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            _apply.run(scratch, honor_status_allow=False)

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
                # gh-767: "stale" means `jm apply` will rewrite this. For the
                # unreconciled glue it will not — saying so would send the
                # reader to a command that changes nothing.
                state = (
                    "unreconciled" if rel_posix in unreconciled else "stale"
                )
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

    # gh-767: reported, but not part of the gate. These files predate the
    # current generator and `jm apply` will not fix them, so failing
    # `--check` on them would turn every existing project's CI red for
    # something no jm command can clear — the gh-752 precedent (report the
    # count, let a project opt into strictness separately) is the project's
    # own answer to exactly this tension.
    unreconciled_entries = [e for e in entries if e[1] == "unreconciled"]
    entries = [e for e in entries if e[1] != "unreconciled"]
    drift = [e for e in entries if not e[2]]
    allowed = [e for e in entries if e[2]]
    dropped_entries = [e for e in entries if e[4]]
    n_dropped_files = len(dropped_entries)
    # gh-426: a symbol drop is never suppressed by status_allow/--allow —
    # it must count toward the CI-gating return value even for an allowed
    # file, or a status_allow entry silently hides real content loss behind
    # "this file always drifts, that's expected."
    # gh-442: same treatment for a default-doc mismatch — not suppressible,
    # always counted, so `jm status --check` actually gates on it.
    drift_count = (
        len(drift) + sum(1 for e in allowed if e[4]) + len(drift_entries)
    )

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
                    "param_default_drift": [
                        {
                            "object": o,
                            "param": n,
                            "manifest_default": m,
                            "header_default": h,
                        }
                        for (o, n, m, h) in drift_entries
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
        if unreconciled_entries:
            print(
                f"UNRECONCILED ({len(unreconciled_entries)}) — generated by "
                "an older jm; `jm apply` will NOT rewrite these:"
            )
            for p, _, _, diff, _ in unreconciled_entries:
                print(f"  ! {p}")
                if diff:
                    print("".join(f"    {ln}" for ln in diff.splitlines(True)))
            print(
                "  jm renders these but only ever writes them when absent "
                "(gh-767), so they\n"
                "  stay frozen at whatever it emitted when they were first "
                "created — and the\n"
                "  `.pyi` beside them does not. Delete one and run "
                "`jm apply` to regenerate it,\n"
                "  but read it first: these files may carry hand-written "
                "members that exist\n"
                "  nowhere else, and regenerating discards them.\n"
                "  Not counted as drift — no jm command clears it yet."
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

    # gh-442: same "impossible to miss" treatment as DROPPED — this is a
    # static doc-consistency check, not a diff of what apply would write,
    # so it prints even when every managed file is otherwise OK.
    if drift_entries:
        print(
            f"DRIFT ({len(drift_entries)}) — init-param default disagrees "
            "between the manifest and the header's own doc:"
        )
        for obj, name, m_dflt, h_dflt in drift_entries:
            print(
                f"  ! {obj}.{name}: manifest={m_dflt!r} header={h_dflt!r} "
                f"(native/inc/{obj}/{obj}_core.h)"
            )
        print(
            "  One of these is stale — jm can't tell which; update the "
            "manifest default or the header doc to match. See gh-442."
        )
        print()

    n_missing = sum(1 for e in drift if e[1] == "missing")
    n_stale = sum(1 for e in drift if e[1] == "stale")
    # gh-752: a burn-down number for authored @code too wide for its stub.
    # Reported on both the clean and the drifting path, because it is not
    # drift — the project can be perfectly in sync and still carry examples
    # that no downstream 79-col gate can pass. A count only; `jm apply` prints
    # the sites.
    from . import _codecheck

    _wide = len(_codecheck.scan(root, cfg))

    if not drift and not dropped_entries and not drift_entries:
        suffix = f" ({len(allowed)} allowed)" if allowed else ""
        # gh-767: "up to date" must not be said over files the generator no
        # longer agrees with, even though they are not drift and `apply`
        # cannot fix them. Qualify rather than stay silent.
        _unrec = (
            f"; {len(unreconciled_entries)} unreconciled"
            if unreconciled_entries
            else ""
        )
        print(
            f"OK — up to date; {ok_count} manifest-owned file(s) match"
            f"{suffix}{_unrec}."
        )
    else:
        print(
            f"summary: {ok_count} OK, {n_missing} missing, "
            f"{n_stale} stale"
            + (
                f", {len(unreconciled_entries)} unreconciled"
                if unreconciled_entries
                else ""
            )
            + (f", {len(allowed)} allowed" if allowed else "")
            + (f", {n_dropped_files} dropped (!)" if dropped_entries else "")
            + (
                f", {len(drift_entries)} default-drift (!)"
                if drift_entries
                else ""
            )
            + ".\n"
            "Your `_core.c` is sacred — apply never changes it; use "
            "`jm regenerate <component>` to rebuild one from the manifest."
        )
        # gh-745: name the formatter *when there is drift to explain*. A
        # `c_style` project's most confusing failure is "stale in CI, clean
        # locally" on identical input, whose cause is two clang-format
        # versions — invisible unless something prints it. Only on the drift
        # path, so the clean summary keeps its exact wording.
        _fmt_note = _cfmt.format_version(cfg)
        if _fmt_note:
            print(
                f"\nGenerated C was formatted by: {_fmt_note.splitlines()[0]}"
                f"\n  ([project] c_format_command — pin it if this differs "
                f"between machines.)"
            )
        # gh-758: and the same failure one level meaner — not two machines,
        # two *directories* on one machine. `apply` formats its temp scaffold
        # from outside the project, so a CWD-dependent command formats the
        # two compared sides with two binaries and no amount of `apply`
        # clears the drift. Reported only alongside drift, like the version.
        _cwd_dep = _cfmt.cwd_dependent_version(root, cfg)
        if _cwd_dep:
            _here, _there = _cwd_dep
            print(
                f"\nWARNING: [project] c_format_command resolves a different "
                f"formatter depending on\n  the working directory, which is "
                f"very likely this drift:\n"
                f"    in {root}: {_here.splitlines()[0]}\n"
                f"    outside a project:  {_there.splitlines()[0]}\n"
                f"  `apply` formats a temp scaffold outside the project, so "
                f"the two sides being\n  compared were formatted by different "
                f"binaries. `uv run --group <g> <tool>` is\n  the usual cause "
                f"— it no-ops outside a project and falls back to PATH. Use a "
                f"\n  CWD-independent command (`uvx <tool>==<version>` or an "
                f"absolute path)."
            )

    # gh-767: printed on both paths, and under `--check`, for the same reason
    # the `@code` count above is — a project can be perfectly in sync and
    # still carry binding fragments the generator no longer agrees with, and
    # "OK — up to date" without this line is the exact sentence that hid 58 of
    # them on doppler.
    if unreconciled_entries and check:
        print(
            f"\n{len(unreconciled_entries)} generated binding fragment(s) "
            f"predate the current jm and\n  `jm apply` will not rewrite them "
            f"(gh-767). Not counted as drift — no jm\n  command clears it "
            f"yet. Run `jm status` without --check to list them."
        )

    if _wide:
        print(
            f"\n{_wide} authored @code line(s) exceed 79 columns in the "
            f"generated stubs.\n  Not drift — `jm apply` lists the sites and "
            f"the per-line budget."
        )

    return drift_count
