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
from . import _docsync
from . import _fmtprobe
from . import _pyfmt
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

    gh-775: a **view** owns a fragment too, and enumerating only
    ``module_objects`` missed every one of them. The consequence was not that
    views went unreported — it was worse and backwards: a view fragment stayed
    in the compared set while its object siblings were excluded, so formatting
    the whole directory to the project's house style left the views, and only
    the views, failing ``--check``. doppler's gate went green -> 1 stale on
    bytes that had not changed.

    A view's fragment id is its lowercased ``class_name``, not the parent
    component it shares (gh-504). That derivation has one owner,
    ``_object._view_frag_id``, and this calls it rather than repeating
    ``.lower()`` — the two would drift, and this function existing to enumerate
    fragments while a *different* rule decided their names is how the gap
    opened.
    """
    from ._object import _view_frag_id

    out: set[str] = set()
    for mod in C.modules(cfg):
        cname = C.module_paths(mod).cname
        for obj in C.module_objects(cfg, mod):
            frag_ids = [obj] + [_view_frag_id(v) for v in C.views(cfg, obj)]
            for frag_id in frag_ids:
                rel = (
                    Path("native") / "src" / cname / f"{cname}_ext_{frag_id}.c"
                )
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
    strict_examples: bool = False,
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
    # gh-612: (path, detail) per fragment whose constructor kwlist has
    # drifted from what the manifest generates.
    kwargs_entries: list[tuple[str, str]] = []
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
                # gh-777: except when the difference is a member jm generates
                # that is simply absent. `apply` *does* put that back
                # (`transplant_missing_bindings`), so it is reconcilable and
                # belongs in the drift count — the bucket is for a body that
                # differs, which is the author's. Without this split a project
                # could carry a member its `.pyi` advertises and its extension
                # does not define, indefinitely, with CI green.
                if state == "unreconciled" and _docsync.absent_members(
                    before.decode("utf-8", "replace"),
                    after.decode("utf-8", "replace"),
                ):
                    state = "stale"
                # gh-612: a fragment whose constructor kwlist no longer
                # agrees with what the manifest generates. jm has been able
                # to answer this since doppler#616, but only ever asked it
                # mid-refresh (`warn_init_kwargs_drift`), and `status`
                # redirects that stderr away — so for a fragment nobody
                # refreshes, which is exactly the hand-owned case this comes
                # from, the question was never put. Asking it here costs
                # nothing: `before` and `after` are already in hand.
                if state in ("unreconciled", "stale"):
                    *_, _detail = _docsync.init_kwargs_drift(
                        before.decode("utf-8", "replace"),
                        after.decode("utf-8", "replace"),
                    )
                    if _detail:
                        kwargs_entries.append((rel_posix, _detail))
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
    # gh-752: a burn-down number for authored @code too wide for its stub.
    # Reported on both the clean and the drifting path, because it is not
    # drift — the project can be perfectly in sync and still carry examples
    # that no downstream 79-col gate can pass. A count only; `jm apply` prints
    # the sites.
    from . import _codecheck

    _wide = len(_codecheck.scan(root, cfg))
    # gh-760: strict mode makes that count a gate. Computed here rather than
    # beside its report, so the JSON path — which returns before the text
    # rendering — gates identically. A gate that fires for a human and not
    # for `--json` is the shape a CI consumer discovers the hard way.
    _strict = strict_examples or C.strict_examples(cfg)
    drift_count = (
        len(drift)
        + sum(1 for e in allowed if e[4])
        + len(drift_entries)
        + (_wide if _strict else 0)
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
                    "kwargs_drift": [
                        {"path": p, "detail": d} for (p, d) in kwargs_entries
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
                f"UNRECONCILED ({len(unreconciled_entries)}) — `jm apply` "
                "reconciles these in place, but not wholesale:"
            )
            for p, _, _, diff, _ in unreconciled_entries:
                print(f"  ! {p}")
                if diff:
                    print("".join(f"    {ln}" for ln in diff.splitlines(True)))
            print(
                "  apply refreshes these member by member (gh-767): "
                "docstrings, bindings the\n"
                "  manifest gained, and `*_max_out` arity. What it will not "
                "do is re-render a\n"
                "  wrapper body — those are yours, and gh-770 is what keeps "
                "a hand-written one\n"
                "  through a regeneration. So a body that no longer matches "
                "the manifest is\n"
                "  reported (see the `warning:` lines above) and left "
                "alone.\n"
                "  Not counted as drift: the remaining difference is one "
                "only you can settle."
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

    # gh-612: same "impossible to miss" treatment as DROPPED and DRIFT, and
    # for the same reason — nothing downstream can see it. The `.pyi` is
    # rendered from the manifest, so it documents the constructor the
    # manifest describes while the compiled extension parses the one the
    # fragment has. Both spellings compile, both import, and a type checker
    # blesses the call that raises: doppler's `CorrDetector2D(ref, 1, 0, 3,
    # "median")` shipped in 0.38.1 as a `TypeError` its own stub endorsed.
    if kwargs_entries:
        print(
            f"KWARGS ({len(kwargs_entries)}) — the constructor's keyword "
            "arguments disagree with the manifest:"
        )
        for path, detail in kwargs_entries:
            print(f"  ! {path}")
            print(f"      {detail}")
        print(
            "  The .pyi is generated from the manifest and the extension is "
            "not, so the\n"
            "  stub advertises a signature the compiled object rejects — a "
            "keyword that\n"
            "  raises TypeError, or positionals that bind to the wrong "
            "fields and do not.\n"
            "  Not counted as drift: jm regenerates a kwlist only with the "
            "body it belongs\n"
            "  to, so there is nothing to run — reconcile the manifest with "
            "the binding, or\n"
            "  move the hand-written constructor into an _extra.c. See "
            "gh-612."
        )
        print()

    n_missing = sum(1 for e in drift if e[1] == "missing")
    n_stale = sum(1 for e in drift if e[1] == "stale")

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
        # gh-612: and the same for a drifted constructor — "up to date" over
        # a stub whose signature the extension rejects is the exact claim
        # gh-767 established jm must not make.
        _kw = (
            f"; {len(kwargs_entries)} kwargs-drift (!)"
            if kwargs_entries
            else ""
        )
        print(
            f"OK — up to date; {ok_count} manifest-owned file(s) match"
            f"{suffix}{_unrec}{_kw}."
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
            + (
                f", {len(kwargs_entries)} kwargs-drift (!)"
                if kwargs_entries
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
        # gh-772: and the same question of `py_format_command`, which had the
        # identical exposure and no detection at all. One renderer for both,
        # so the two reports cannot drift into describing the same cause
        # differently — and it covers the *fails to spawn* case that gh-758's
        # check returned "fine" for, which is the shape doppler's Python
        # command actually has.
        for _key, _dep in (
            (
                "c_format_command",
                _fmtprobe.cwd_dependence(root, C.c_format_command(cfg))
                if C.c_formatting_on(cfg)
                else None,
            ),
            ("py_format_command", _pyfmt.cwd_dependent(root, cfg)),
        ):
            if _dep:
                print(_fmtprobe.describe(_dep, _key, root))

    # gh-767: printed on both paths, and under `--check`, for the same reason
    # the `@code` count above is — a project can be perfectly in sync and
    # still carry binding fragments the generator no longer agrees with, and
    # "OK — up to date" without this line is the exact sentence that hid 58 of
    # them on doppler.
    if unreconciled_entries and check:
        print(
            f"\n{len(unreconciled_entries)} generated binding fragment(s) "
            f"differ from a full render.\n  `jm apply` reconciles them member "
            f"by member (gh-767) but never re-renders a\n  wrapper body — "
            f"that part is yours. Not counted as drift. Run `jm status`\n"
            f"  without --check to list them."
        )

    # gh-760: strict mode makes the count a gate. Opt-in and off by default,
    # so a project mid-sweep keeps today's behaviour; once it reaches zero,
    # this is what stops the next line. Still not *drift* — the stub
    # faithfully reflects the header and no `jm apply` clears it — so it is
    # counted separately and says so.
    if _wide:
        print(
            f"\n{_wide} authored @code line(s) exceed 79 columns in the "
            f"generated stubs.\n  "
            + (
                "Fix them at the source — `jm apply` lists the sites and "
                "the per-line\n  budget. ([project] strict_examples is on, "
                "so this fails the gate.)"
                if _strict
                else "Not drift — `jm apply` lists the sites and "
                "the per-line budget."
            )
        )

    # gh-773: `c_style` is the legacy spelling and, alone, is the one that
    # leaves the formatter to PATH — which is how the same input produces
    # different bytes on two machines and the gate flips red on a project
    # nobody changed. Printed on both paths and under --check: a project can
    # be perfectly in sync today and still be one CI image away from that.
    _proj = cfg.get("project", {})
    if _proj.get("c_style") and _proj.get("c_format_command") is None:
        print(
            "\nnote: [project] c_style is the legacy opt-in and names no "
            "formatter, so the\n  version resolves from PATH and may differ "
            "between here and CI. Declaring\n  c_format_command is the opt-in "
            "on its own now — replace it with e.g.\n"
            '    c_format_command = ["uvx", "clang-format==22.1.8"]'
        )

    return drift_count
