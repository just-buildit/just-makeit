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
  - OUTDATED— (gh-949) a create-only file whose content is jm's own — the
              Makefile, `.clang-tidy`, `jm_test.h`, the common headers — and
              which differs from what this jm renders today. `apply` never
              rewrites a create-only file, so the copy/diff above is blind to
              it by construction; this is computed against the replay tree
              instead. Adopting the new render is the author's call, so it is
              reported and never counted. Files whose *starting* content jm
              renders but whose content is the author's (`_core.c`, your C
              tests, README) are excluded — they differ from their scaffold
              the moment the project is real.
  - NOTE    — (gh-921) a method sets `pass_capacity` while its header still
              declares `max_out(state)`, so the exact allocation the opt-in
              asks for is not the one generated. Not a file `apply` would
              write and not a defect — gh-920 keeps the clamp, so the
              allocation is correct — which is why it is never counted and
              never printed under `--check`.

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
from . import _createonly
from . import _docsync
from . import _fmtprobe
from . import _pyfmt
from . import _stubs
from ._apply import _SKIP_DIRS

# Directories/files never copied into the scratch tree (build artefacts,
# VCS, caches) — not manifest-owned, and copying them only slows status.
_COPY_IGNORE = shutil.ignore_patterns(
    *_SKIP_DIRS,
    "*.so",
    "*.pyd",
    "*.pyc",
    "*.pyo",
    "compile_commands.json",
    # Not merely ignorable but pointless to copy: coverage databases can be
    # large, and the scratch tree exists only to be re-applied and diffed.
    ".coverage",
    ".coverage.*",
)


def _walk_managed(base: Path) -> list[Path]:
    """Return manifest-owned files under *base*, as relative paths."""
    out: list[Path] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base)
        if _apply.is_skipped(rel):
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

    gh-785: that degradation is a **fail-open** on the input where content
    loss is largest — an unparseable stub yields no symbols, so nothing is
    ever reported as dropped from it. Not fixed here (a tolerant re-parse
    would be a second, weaker member map); covered instead by the
    UNPARSEABLE section, which reports the same file from the other side.
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

    # gh-784: names that built yesterday and do not today. `valid_identifier`
    # rejects non-ASCII since the term landed, and `apply` replays a manifest
    # through the same declaration commands — so a tree that already carries
    # one is refused by the scratch replay below, which ends the report before
    # it starts. Printed HERE, ahead of that replay, for exactly that reason:
    # the trees this section exists for are precisely the trees that no longer
    # reach the rest of the function, and one `error:` naming one name is a
    # rename-recompile-repeat loop where this names all of them at once.
    #
    # Never counted as drift — nothing here is a file apply would change — so
    # it stays out of `--check`, whose whole output is that count.
    if not as_json and not check:
        _non_ascii = C.non_ascii_names(cfg)
        if _non_ascii:
            print(
                f"NON-ASCII NAMES ({len(_non_ascii)}) — already declared, "
                "portability risk:"
            )
            for _kind, _name in _non_ascii:
                print(f"  ? {_kind} {_name!r}")
            print(
                "  GCC accepts UTF-8 identifiers as an extension and MSVC "
                "differs, so these can\n"
                "  compile on one toolchain and not another. Rename them to "
                "ASCII; jm no longer\n"
                "  accepts one, so `apply` refuses the manifest until you "
                "do. Not counted as drift."
            )
            print()

    allow_patterns = list(allow) + C.status_allow(cfg)
    # gh-830: collected during the walk below.
    managed_paths: list[str] = []

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

    # gh-921: `pass_capacity` methods whose header keeps the pre-gh-607
    # `max_out(state)`. Same static manifest-vs-header shape as gh-442 above,
    # and computed the same way — nothing here is a file `apply` would write.
    #
    # It lives in `status` rather than on the mutating commands deliberately.
    # The condition is per method, and a tree like doppler's carries dozens of
    # variable-output methods, so an `apply`-time note is a wall of lines
    # arriving at the moment the reader is watching for what changed. `status`
    # is the surface read on purpose, and this is a standing property of the
    # manifest, not an event.
    inert_cap_entries: list[tuple[str, str, str]] = [
        (obj, name, c_fn)
        for obj in C.components(cfg)
        for name, c_fn in _obj_mod.inert_pass_capacity(cfg, root, obj)
    ]

    # (rel_posix, state, allowed, diff_text, dropped_symbols)
    entries: list[tuple[str, str, bool, str, frozenset]] = []
    # gh-612: (path, detail) per fragment whose constructor kwlist has
    # drifted from what the manifest generates.
    # (path, detail, allowed) — gh-823. `allowed` entries are reported
    # and not counted, exactly like every other allowed deviation.
    kwargs_entries: list[tuple[str, str, bool]] = []
    # gh-848: why each unreconciled fragment differs, keyed by path.
    # Kept beside `entries` rather than widened into its tuple, which
    # several call sites unpack positionally.
    unreconciled_reasons: dict[str, dict] = {}
    # gh-785: (path, lineno, message, [at-risk member names]) per `.pyi` on
    # disk that does not parse *and* holds hand-owned members. Only that
    # intersection: a broken stub with nothing hand-written in it is
    # repaired by the next render at no cost, and reporting it would train
    # the reader past the one that costs everything.
    unparseable_entries: list[tuple[str, int, str, list[str]]] = []
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
        # gh-886: the buffers are named, because a *fatal* apply is not
        # progress. When the replay refuses — an old pin, a key this jm no
        # longer accepts, a half-finished migration — its `error:` line went
        # into a StringIO that was thrown away with the frame, and its
        # `sys.exit(1)` propagated out through `status`. The user got two
        # warnings, no report, exit 1, and no reason: the diagnostic is
        # unavailable in exactly the situation it exists for, and fails in
        # the least informative way available. Suppressing progress and
        # suppressing errors were one decision sharing one redirect.
        _replay_out, _replay_err = io.StringIO(), io.StringIO()
        # gh-949: keep the replay. Everything below compares the scratch copy
        # before `apply` against the scratch copy after it, which is blind to
        # a create-only file by construction — `apply` does not rewrite one, so
        # both sides carry whatever the project already had, however many
        # versions behind that is. The replay tree is the only place jm's
        # *current* render of those files exists.
        replay_root = Path(tmp) / "jm-replay"
        try:
            with (
                contextlib.redirect_stdout(_replay_out),
                contextlib.redirect_stderr(_replay_err),
            ):
                _apply.run(
                    scratch,
                    honor_status_allow=False,
                    replay_out=replay_root,
                )
        except SystemExit as _exc:
            # stderr first: that is where apply puts `error:`. stdout is the
            # fallback for the few exits that print there, and is not shown
            # otherwise — it is the progress log this block exists to hide.
            _why = (
                _replay_err.getvalue().strip()
                or _replay_out.getvalue().strip()
            )
            print(
                "error: the internal `apply` replay refused this manifest, "
                "so no status report can be produced.\n"
                "It ran on a scratch copy — your project was NOT modified.",
                file=sys.stderr,
            )
            for _ln in (
                _why or "(apply exited without a message)"
            ).splitlines():
                print(f"  {_ln}", file=sys.stderr)
            print(
                "\nResolve the above, then re-run `just-makeit status`. "
                "Running `just-makeit apply` directly\n"
                "shows the same failure with its full output.",
                file=sys.stderr,
            )
            raise SystemExit(
                _exc.code if isinstance(_exc.code, int) else 1
            ) from None

        # gh-949: computed inside the temp directory's lifetime, against the
        # *real* root rather than the scratch copy. For a versioned file the
        # two are identical (that is the whole point — `apply` never touched
        # it), so this is the same answer with the honest operand.
        # `status_allow`/`--allow` suppress it like any other deviation: a
        # project that has deliberately taken its Makefile over says so once
        # and stops hearing about it. Suppressible because adopting jm's
        # render is the author's call — unlike the gh-426 dropped symbol
        # beside it, nothing is being lost here.
        outdated_entries = [
            (p, _is_allowed(p, allow_patterns))
            for p in _createonly.outdated(root, replay_root)
        ]
        # gh-975: the other half of what the replay knows about a create-only
        # file — not "is it behind" but "can jm still write into it". Same
        # operands as `outdated` above and for the same reason: the real root,
        # inside the temp tree's lifetime.
        unanchored_entries = [
            (p, a, _is_allowed(p, allow_patterns))
            for (p, a) in _createonly.missing_anchors(root, replay_root)
        ]

        for rel in _walk_managed(scratch):
            real = root / rel
            after = (scratch / rel).read_bytes()
            rel_posix = rel.as_posix()
            # gh-830: every managed path, so an allow entry that
            # matches none of them can be named below.
            managed_paths.append(rel_posix)
            dropped: frozenset = frozenset()
            if not real.exists():
                state, diff = "missing", ""
            else:
                before = real.read_bytes()
                # gh-785: asked before the equality shortcut below, and
                # before anything classifies the file. An unparseable `.pyi`
                # is the one finding here that `jm apply` does not fix but
                # *consumes* — the render it writes parses, so after the
                # apply there is nothing left to detect and nothing left to
                # recover. This is the only place it can still be said while
                # the members are still on disk.
                if rel_posix.endswith(".pyi"):
                    _lost = _stubs.hand_owned_at_risk(
                        cfg, before.decode("utf-8", "replace")
                    )
                    _exc = _stubs.parse_error(
                        before.decode("utf-8", "replace")
                    )
                    if _exc is not None and _lost:
                        unparseable_entries.append(
                            (rel_posix, _exc.lineno or 0, _exc.msg, _lost)
                        )
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
                # gh-848: UNRECONCILED was a bare list of paths, and its
                # entries are two different things — a body the author wrote
                # (permanent, nothing to do) and a fragment predating a
                # codegen change (a fix sitting undelivered). doppler carried
                # 75 of these unchanged across many pins, so the two that had
                # become actionable — a feature released that day sat in
                # their delta — were indistinguishable from the 73 that never
                # will be. `before`/`after` are already in hand, and the
                # comparison is the one `warn_signature_drift` already makes.
                if state == "unreconciled":
                    _why = _docsync.signature_drift_details(
                        before.decode("utf-8", "replace"),
                        after.decode("utf-8", "replace"),
                    )
                    if _why:
                        unreconciled_reasons[rel_posix] = _why
                if state in ("unreconciled", "stale"):
                    *_, _detail = _docsync.init_kwargs_drift(
                        before.decode("utf-8", "replace"),
                        after.decode("utf-8", "replace"),
                    )
                    if _detail:
                        # gh-823: `status_allow` is the escape hatch for the
                        # gate this now feeds, so it has to reach here — an
                        # allowlist a project cannot use is an opt-out that
                        # does not exist. Through `_is_allowed`, the same
                        # matcher every other allowed path goes through, so
                        # one entry means the same thing everywhere.
                        #
                        # Recorded WITH that verdict rather than dropped on
                        # it. Dropping made an exempted constructor vanish
                        # from the report entirely: nothing listed it, the
                        # summary went back to a bare "OK — up to date" over
                        # a constructor the generator disagrees with, and
                        # there was no way to audit which exemptions existed
                        # or to notice one that had stopped diverging. Every
                        # other allowed deviation stays visible and uncounted;
                        # so does this.
                        kwargs_entries.append(
                            (
                                rel_posix,
                                _detail,
                                _is_allowed(rel_posix, allow_patterns),
                            )
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
    # gh-806: a `test_*_core.c` / `bench_*_core.c` that no build file compiles.
    # Computed here, above the JSON return, for the same reason `_wide` is:
    # the gate must fire identically for a human and for a CI consumer.
    from . import _hollow

    _orphans = _hollow.orphans(root, cfg)
    _orphan_allowed = {
        o.rel: _is_allowed(o.rel, allow_patterns) for o in _orphans
    }
    _silent = _hollow.silent_benches(root, cfg)
    # gh-984: components whose OBJECT library reaches no combined C library,
    # and wiring lines naming a core that is gone. Computed here, above the
    # JSON return, for the reason `_wide` and `_orphans` are: a gate that
    # fires for a human and not for `--json` is found the hard way.
    #
    # Alone among the findings here this needs no replay — it compares the
    # project against itself — so it also answers on a tree jm could not
    # re-render, and costs two file reads.
    from . import _libwiring

    _unwired = _libwiring.unwired(root, cfg)
    _dangling = _libwiring.dangling(root, cfg)
    # Suppressed by the file, like the UNANCHORED entry beside it, and ALSO
    # per component via `CMakeLists.txt:<core>`.
    #
    # Both, not one. Allowing the file is a project saying it owns this
    # wiring — gh-975 established that, and it stays true here: jm looks for
    # one exact `target_sources` spelling, so an author who links their cores
    # some other way would otherwise get a finding they cannot clear.
    #
    # The per-component key exists because that is a heavy hammer for the
    # common case. A root CMakeLists is `partial`, so it drifts as soon as the
    # author adds anything of their own, and `CMakeLists.txt` in status_allow
    # is the obvious way to quiet that — which would then silence every future
    # component too. Naming the one core kept out of the library on purpose
    # says only what is meant.
    _wiring_allowed = {
        u.core: _is_allowed("CMakeLists.txt", allow_patterns)
        or _is_allowed(f"CMakeLists.txt:{u.core}", allow_patterns)
        for u in _unwired
    }
    _n_unwired = sum(1 for u in _unwired if not _wiring_allowed[u.core])
    drift_count = (
        len(drift)
        + sum(1 for e in allowed if e[4])
        + len(drift_entries)
        # gh-823: unconditional, not opt-in. `strict_examples` above is a
        # completeness ratchet projects legitimately differ on; this is "the
        # published constructor raises when called as documented", which is
        # not a matter of taste. The precedent is gh-777, which moved an
        # absent PyMethodDef row from `unreconciled` to `stale` so `--check`
        # fails on it — the reclassification only bites where something was
        # already wrong, and that holds here too.
        #
        # An opt-in key would mean a project that never enables it never
        # learns its signature is broken, and silence is the entire failure
        # mode this addresses. `status_allow` is the escape hatch: the check
        # stays on, the finding is visible, and an instance is exempted by
        # name with a reason.
        + sum(1 for e in kwargs_entries if not e[2])
        + (_wide if _strict else 0)
        # gh-806: gates, unconditionally. The failure mode is a **green** CI
        # run — a scaffold that compiles, passes and is counted while the real
        # suite sits unbuilt beside it — so a finding that did not fail the
        # gate would reproduce the exact silence it exists to break. doppler
        # carried one for weeks with CI green. `status_allow` remains the
        # escape hatch for a file a project keeps unbuilt on purpose.
        + sum(1 for o in _orphans if not _orphan_allowed[o.rel])
        # gh-785: gates, and never suppressed by `status_allow` — the same
        # rule as the gh-426 dropped symbol it sits beside, for the same
        # reason. This is content loss, and it is the *last* moment anything
        # can say so: `jm apply` does not fix this finding, it consumes it.
        # The stub it writes parses, so the next status run is clean over a
        # tree that has lost members no manifest can put back.
        + len(unparseable_entries)
        # gh-975: gates, unlike the OUTDATED beside it, and the difference is
        # whether the reader can do anything. An outdated create-only file is
        # jm's opinion about a file that works; a missing anchor is a write
        # that did not happen — the module has no `add_subdirectory`, so the
        # extension is not built at all — and putting the line back clears it.
        # Suppressible by name, for a project that wires its own targets and
        # means to.
        + sum(1 for e in unanchored_entries if not e[2])
        # gh-984: gates, on gh-975's rule — the reader can do something about
        # it. A component in no library is a write that did not happen, and
        # `jm apply` puts the line back; that is the difference from the
        # OUTDATED beside it, which is jm's opinion about a file that works.
        # Suppressible for a project that wires its own library targets.
        #
        # The dangling half is never suppressed, and is the sharper case: it
        # names a target cmake will not resolve, so the project does not
        # configure. There is no reading of `status_allow` under which a
        # tree that cannot build is what the author meant.
        + _n_unwired
        + len(_dangling)
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
                        {"path": p, "detail": d, "allowed": a}
                        for (p, d, a) in kwargs_entries
                    ],
                    # gh-806: the file, why it is jm-shaped, and how much
                    # content is going unbuilt — the last of those is what
                    # tells a reader at a glance whether a scaffold displaced
                    # a real suite or an empty one.
                    "unbuilt_sources": [
                        {
                            "path": o.rel,
                            "kind": o.kind,
                            "component": o.stem,
                            "declared": o.declared,
                            "lines": o.lines,
                            "allowed": _orphan_allowed[o.rel],
                        }
                        for o in _orphans
                    ],
                    "unparseable_stubs": [
                        {
                            "path": p,
                            "line": ln,
                            "error": msg,
                            "hand_owned_at_risk": names,
                        }
                        for (p, ln, msg, names) in unparseable_entries
                    ],
                    "silent_benchmarks": [
                        {
                            "path": s.rel,
                            "component": s.component,
                            "methods": s.methods,
                        }
                        for s in _silent
                    ],
                    # gh-949: create-only files whose content is jm's and is
                    # behind. Reported, never counted — `jm apply` will not
                    # rewrite a create-only file, so gating on this would
                    # fail a CI run with no command that clears it.
                    "outdated": [
                        {"path": p, "allowed": a}
                        for (p, a) in outdated_entries
                    ],
                    # gh-975: a splice anchor jm renders and the file lacks,
                    # so the wiring it carries was never written. Counted,
                    # unlike `outdated` above — see `drift_count`.
                    "unanchored": [
                        {"path": p, "anchor": a, "allowed": al}
                        for (p, a, al) in unanchored_entries
                    ],
                    # gh-984: a component whose `_core` is folded into no
                    # combined C library, so its public header ships and its
                    # symbols do not. `targets` names the libraries it is
                    # missing from — a core in the shared one and not the
                    # static archive is the half-wired state gh-981 left.
                    "unwired_cores": [
                        {
                            "core": u.core,
                            "component": u.component,
                            "targets": list(u.targets),
                            "allowed": _wiring_allowed[u.core],
                        }
                        for u in _unwired
                    ],
                    # gh-984: the mirror — wiring naming a core no component
                    # declares. Never `allowed`: cmake will not resolve it, so
                    # the project does not configure.
                    "dangling_wiring": [
                        {"core": d.core, "targets": list(d.targets)}
                        for d in _dangling
                    ],
                    # gh-921: a note, so it appears here and in no count.
                    "inert_pass_capacity": [
                        {"object": o, "method": n, "c_function": f}
                        for (o, n, f) in inert_cap_entries
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
            # gh-848: split by WHY, because the two halves want opposite
            # actions and a single list made the actionable ones invisible.
            _actionable = [
                e for e in unreconciled_entries if e[0] in unreconciled_reasons
            ]
            _authored = [
                e
                for e in unreconciled_entries
                if e[0] not in unreconciled_reasons
            ]
            if _actionable:
                print(
                    f"  ACTIONABLE ({len(_actionable)}) — the manifest moved "
                    "and these did not, so a fix is\n"
                    "  sitting undelivered. Delete the file and re-run `jm "
                    "apply` to receive it\n"
                    "  (any hand-written body in it is lost)."
                )
                for p_, _, _, diff, _ in _actionable:
                    print(f"    ! {p_}")
                    for _member in sorted(unreconciled_reasons[p_]):
                        print(f"        {unreconciled_reasons[p_][_member]}")
                    if diff and show_diff:
                        print(
                            "".join(
                                f"      {ln}" for ln in diff.splitlines(True)
                            )
                        )
            if _authored:
                print(
                    f"  AUTHOR-OWNED ({len(_authored)}) — these differ "
                    "because you wrote them that way.\n"
                    "  Nothing to do; they stay unreconciled permanently."
                )
                # Paths stay listed. Suppressing them was the first cut, and
                # `test_an_edited_fragment_is_reported` (gh-767) caught it:
                # a project with ONE unreconciled fragment needs to know
                # which. What made the old report wallpaper was the absence
                # of a reason, not the presence of a path — so the split and
                # the reasons are the fix, and hiding evidence is not.
                for p_, _, _, diff, _ in _authored:
                    print(f"    ! {p_}")
                    if diff and show_diff:
                        print(
                            "".join(
                                f"      {ln}" for ln in diff.splitlines(True)
                            )
                        )
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

        # gh-830: an entry that matches no managed file at all. Unambiguous
        # and safe to report on its own — a rename or a deleted component
        # leaves the pattern behind, and nothing ever said so, which is the
        # failure gh-823 is about one level up: an exemption outliving its
        # cause, keeping a check switched off for a file nobody is thinking
        # about any more.
        #
        # Deliberately NOT the other shape — a pattern matching files, none
        # of which currently deviate. That one *looks* stale and often is
        # not: a glob covering a directory is doing its job by matching clean
        # files, and a pattern kept ahead of a known-coming change is
        # legitimate. Reporting it needs an answer to what `status_allow` is
        # FOR — a burn-down list you are expected to empty, or a standing
        # statement about files the gate does not govern — and those want
        # different reports. That question is still open on gh-830.
        #
        # Manifest entries only. A `--allow` on the command line was typed by
        # the person reading this output, and a CI job passing a defensive
        # one for a file that does not exist yet is not carrying a stale
        # exemption.
        _manifest_allow = C.status_allow(cfg)
        # gh-991: a `CMakeLists.txt:<core>` entry is a FINDING key, not a path.
        # It can never match a managed file, so every working per-component
        # wiring exemption was reported as suppressing nothing — in the same
        # run that printed `[status_allow]` beside the finding it was
        # suppressing. Both cannot be true, and the advice was wrong in the
        # destructive direction: the message says a leftover pattern "keeps
        # every check off", so the reader deletes it, and the one spelling
        # that DOES match a managed file is the blanket `CMakeLists.txt`
        # gh-984 exists to avoid. Following the advice would have re-opened
        # gh-981 on the project that reported this.
        #
        # Validated rather than merely skipped: an entry naming a core no
        # component declares is the genuinely stale case, and still reported.
        _wiring_keys = {
            f"CMakeLists.txt:{core}"
            for core in _libwiring.declared_cores(root)
        }
        _unmatched = [
            pat
            for pat in _manifest_allow
            if pat not in _wiring_keys
            and not any(_is_allowed(f, [pat]) for f in managed_paths)
        ]
        if _unmatched:
            print(
                f"STALE ALLOW ({len(_unmatched)}) — `status_allow` "
                "entries matching no managed file:"
            )
            for pat in _unmatched:
                print(f"  ? {pat}")
            print(
                "  Nothing in the project matches these, so they suppress "
                "nothing. A renamed or\n"
                "  deleted component leaves the pattern behind, and it then "
                "keeps every check off\n"
                "  for whatever path later happens to match it. Not counted "
                "as drift."
            )
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

    # gh-949: printed regardless of --check, and that is the entire feature.
    # The reported scenario is a reader running `status --check` *before*
    # migrating, seeing OK, and concluding there is nothing to do; collapsing
    # this into the summary line would reproduce it with extra steps.
    if outdated_entries:
        print(
            f"OUTDATED ({len(outdated_entries)}) — create-only file(s) "
            "behind jm's current version:"
        )
        for p, al in outdated_entries:
            tag = " [status_allow]" if al else ""
            print(f"  ↑ {p}{tag}")
        print(
            "  jm owns the content of these and ships a newer one, but"
            " `apply` will NOT\n"
            "  rewrite a create-only file — adopting it is your call, and"
            " your own edits\n"
            "  to it are here too. Diff before replacing; see"
            " docs/upgrading.md. Not counted."
        )
        print()

    # gh-975: printed on both paths for OUTDATED's reason, and counted for the
    # opposite one — this is a write that did not happen, not an opinion about
    # a file that works.
    if unanchored_entries:
        print(
            f"UNANCHORED ({len(unanchored_entries)}) — jm cannot write into "
            "these; the wiring is missing:"
        )
        for p, anchor, al in unanchored_entries:
            tag = " [status_allow]" if al else ""
            print(f"  ⚓ {p}: no `{anchor}` line{tag}")
        print(
            "  jm splices in after that line and treats its absence as"
            " nothing to do, so\n"
            "  what belongs there was never written — a module with no"
            " add_subdirectory()\n"
            "  is not built at all. Put the line back (jm's scaffold has it"
            " after\n"
            "  `enable_testing()`), or keep that wiring yourself and name the"
            " file in\n"
            "  [project] status_allow."
        )
        print()

    # gh-984: printed on both paths, like UNANCHORED and UNBUILT above, and
    # for the same reason sharpened one more turn — there is nothing wrong
    # anywhere a reader would think to look. The component compiles, the
    # extension imports, `ctest` passes, and the header installs; the symbol
    # is simply in no library, and the first report comes from a C consumer
    # weeks later.
    if _unwired:
        print(
            f"UNWIRED ({len(_unwired)}) — component core(s) folded into no C "
            "library:"
        )
        for u in _unwired:
            tag = " [status_allow]" if _wiring_allowed[u.core] else ""
            print(
                f"  ⊘ {u.core} (native/src/{u.component}) — missing from "
                f"{', '.join(u.targets)}{tag}"
            )
        print(
            "  These build, and their symbols ship in neither lib<pkg>.so nor"
            " lib<pkg>.a,\n"
            "  so the installed header declares functions a C consumer cannot"
            " link. Python\n"
            "  is unaffected — the extension links each core directly — which"
            " is why this\n"
            "  goes unnoticed. `jm apply` writes the missing"
            " target_sources() line. Keep a\n"
            "  core out of the library on purpose by naming"
            " `CMakeLists.txt:<core>` in\n"
            "  [project] status_allow; naming `CMakeLists.txt` itself exempts"
            " every core,\n"
            "  for a project that links its cores its own way."
        )
        print()

    # gh-984: the mirror, and the one finding here that stops the build
    # outright rather than quietly shipping less than it claims.
    if _dangling:
        print(
            f"DANGLING ({len(_dangling)}) — C library wiring naming a core "
            "that is gone:"
        )
        for d in _dangling:
            print(f"  ⊗ {d.core} — named by {', '.join(d.targets)}")
        print(
            "  cmake resolves $<TARGET_OBJECTS:> at CONFIGURE time, so this"
            " project does\n"
            "  not build at all. `jm apply` drops the line. Not suppressible"
            " — there is no\n"
            "  reading of status_allow under which an unconfigurable tree is"
            " intended."
        )
        print()

    # gh-806: same "impossible to miss" treatment, and for the sharpest
    # version of the same reason — every other listing here describes
    # something that is visibly wrong somewhere. This one describes a tree
    # where `ctest` prints "100% tests passed" and `make bench` exits 0.
    if _orphans:
        print(
            f"UNBUILT ({len(_orphans)}) — test/bench source(s) no build "
            "file compiles:"
        )
        for o in _orphans:
            tag = " [status_allow]" if _orphan_allowed[o.rel] else ""
            note = (
                "not a declared component"
                if not o.declared
                else "declared, but its target is gone"
            )
            print(
                f"  {'~' if _orphan_allowed[o.rel] else '!'} {o.rel} "
                f"({o.lines} lines, {note}){tag}"
            )
        print(
            "  A renamed component leaves its old test/bench behind and a "
            "fresh scaffold\n  takes over the target — which passes, so "
            "nothing goes red. See gh-806."
        )
        print()

    # gh-806: advisory, and printed beside the orphans because they are the
    # same discovery from opposite ends — one target covers nothing because
    # its content moved, the other because it never had any.
    if _silent:
        print(
            f"SILENT ({len(_silent)}) — benchmark(s) that record no "
            "measurement:"
        )
        for s in _silent:
            # gh-1034: a module's benchmark never had a step() to miss, so
            # the object wording is a non sequitur about it. Same order as
            # `SilentBench.describe`, which is the peer of this string —
            # they are two renderings of one fact and drift if only one
            # learns a new case.
            if s.functions:
                detail = f"{s.functions} function(s), none timed yet"
            elif s.methods:
                detail = f"{s.methods} method(s), none benchable"
            else:
                detail = "no step(), no methods"
            print(f"  ~ {s.rel} ({detail})")
        print(
            '  These build, run, exit 0 and write an empty "benchmarks": []. '
            "Not drift."
        )
        print()

    # gh-785: printed on both paths and under --check. The strongest case
    # for that treatment of any section here — this is the only finding whose
    # window closes when you act on the report, because the command that
    # clears it is the command that destroys the evidence.
    if unparseable_entries:
        _n = sum(len(names) for *_x, names in unparseable_entries)
        print(
            f"UNPARSEABLE ({len(unparseable_entries)}) — .pyi file(s) that "
            f"do not parse, holding {_n} hand-written member(s):"
        )
        for path, lineno, msg, names in unparseable_entries:
            print(f"  ! {path}: line {lineno}: {msg}")
            for name in names:
                print(f"      - {name}")
        print(
            "  jm finds a stub's members with `ast`, so a stub it cannot "
            "parse has none to\n  find and the next `jm apply` renders over "
            "them. Fix the syntax error first\n  and every `# jm:hand` / "
            "`manual_stub` member survives; run `jm apply` first and\n  they "
            "are gone. Not suppressed by status_allow. See gh-785."
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

    # gh-921: deliberately NOT the "impossible to miss" treatment the three
    # sections around it get. Nothing here is broken — gh-920 made this seam
    # safe, and the allocation is the historical clamped one, which is correct
    # for a bound that cannot see the call. What is wrong is only that the
    # author asked for something else and was not told they did not get it.
    #
    # So it prints under a plain `jm status` and nowhere else: not counted in
    # `drift_count`, not gating `--check`, and not among the conditions that
    # take the summary out of its "OK — up to date" branch. A note that failed
    # a gate would be a warning, and this one cannot be wrong in the direction
    # that would earn that — see `_object.inert_pass_capacity`.
    if inert_cap_entries and not check:
        print(
            f"NOTE ({len(inert_cap_entries)}) — `pass_capacity` is set but "
            "cannot take effect:"
        )
        for obj, name, c_fn in inert_cap_entries:
            print(
                f"  . {obj}.{name}: {c_fn}_max_out(state) cannot see the "
                f"call, so the\n"
                f"      allocation stays clamped to max(max_out, n)."
            )
        print(
            "  These are safe — a bound that cannot see the call is not "
            "trusted as a per-call\n"
            "  one (gh-920), so nothing truncates. But the exact allocation "
            "the opt-in asks for\n"
            "  is not what you get. Give max_out the count (gh-607) for the "
            "exact allocation,\n"
            "  or declare `exact_max_out` if the bound really is "
            "call-independent. Not drift."
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
        for path, detail, _allowed in kwargs_entries:
            # gh-823: an exempted entry is listed, not hidden — marked the
            # way the ALLOWED section marks its own, so the exemptions are
            # auditable and one that has stopped diverging can be spotted
            # and removed. `!` gates, `~` does not.
            print(f"  {'~' if _allowed else '!'} {path}")
            print(f"      {detail}")
            if _allowed:
                print("      (allowed by status_allow — not counted)")
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

    # gh-823: a drifted constructor is no longer merely *qualified* in the
    # "OK" line — it leaves that branch entirely. jm detected this condition,
    # named the file and named the reordering on every single apply, and
    # doppler still shipped a public constructor that raises when called as
    # documented, because the finding arrived inside a block of a dozen
    # warnings about fragments that were fine. A summary that opens "OK — up
    # to date" is what a reader takes away, and the exit code is what CI
    # takes away; both said fine.
    if (
        not drift
        and not dropped_entries
        and not drift_entries
        and not any(not e[2] for e in kwargs_entries)
        # gh-806: same treatment as the gating kwargs drift above. Saying
        # "OK — up to date" over a tree where a real test suite sits unbuilt
        # is the precise sentence this issue is about; the exit code alone
        # would be right and unread.
        and not any(not _orphan_allowed[o.rel] for o in _orphans)
        and not unparseable_entries
        # gh-975: and for the same reason again. "OK — up to date" over a
        # project whose module has no add_subdirectory() is the exact sentence
        # that issue is about — it was said, twice, over a tree where cmake
        # had zero targets for the module.
        and not any(not e[2] for e in unanchored_entries)
        # gh-984: and once more. This is the case with the strongest claim to
        # the branch on every signal a reader has — nothing is missing, stale,
        # dropped or unbuilt — and the weakest claim in fact: the tree ships a
        # header whose symbols are in no library. Saying "OK — up to date" is
        # how the condition survived undetected long enough to be found from
        # the other side, by a consumer who could not link.
        and not _n_unwired
        and not _dangling
    ):
        suffix = f" ({len(allowed)} allowed)" if allowed else ""
        # gh-767: "up to date" must not be said over files the generator no
        # longer agrees with, even though they are not drift and `apply`
        # cannot fix them. Qualify rather than stay silent.
        _unrec = (
            f"; {len(unreconciled_entries)} unreconciled"
            if unreconciled_entries
            else ""
        )
        # gh-612 qualified this line with a kwargs-drift count. gh-823 takes
        # a *gating* drift out of the branch entirely, so there is nothing to
        # qualify for that case — but an ALLOWED one still lands here, and an
        # unqualified "up to date" over a constructor the generator disagrees
        # with is the exact claim gh-767 established jm must not make. Being
        # exempt from the gate is not the same as being in sync.
        _kw_allowed = sum(1 for e in kwargs_entries if e[2])
        _kw = f"; {_kw_allowed} kwargs-drift (allowed)" if _kw_allowed else ""
        # gh-806: an exempted orphan is still a file nothing compiles, and
        # gh-767's rule applies unchanged — exempt from the gate is not the
        # same as in sync. A silent benchmark qualifies the line too: the
        # project is in sync and one of its bench targets measures nothing.
        _orph = f"; {len(_orphans)} unbuilt (allowed)" if _orphans else ""
        _sil = f"; {len(_silent)} silent bench" if _silent else ""
        # gh-949: a file jm ships a newer version of is not "up to date",
        # even though `apply` cannot fix it. Same qualification as
        # `unreconciled` beside it, for the same gh-767 reason.
        _out = (
            f"; {len(outdated_entries)} outdated" if outdated_entries else ""
        )
        # gh-975: only an allowed one reaches this branch — an unsuppressed
        # anchor gap gates and never gets here. Named anyway, on gh-767's
        # rule: exempt from the gate is not the same as in sync, and this
        # exemption means jm is not maintaining wiring it believes it owns.
        _anch = (
            f"; {len(unanchored_entries)} unanchored (allowed)"
            if unanchored_entries
            else ""
        )
        # gh-984: same rule again — only an allowed one reaches here, and it
        # is named anyway. This exemption means the project's own targets are
        # what put its components in a library, which is worth saying out loud
        # beside a line that otherwise reads as jm vouching for the wiring.
        _wire = f"; {len(_unwired)} unwired (allowed)" if _unwired else ""
        # gh-949: name what was NOT compared.
        #
        # Deliberately not the word "NOTE", and deliberately not "stale":
        # gh-921's report already owns "NOTE" in this output and asserts it is
        # absent under --check, and gh-542 asserts "stale" is absent over a
        # clean tree. Reusing either token here made two unrelated tests fail
        # and would have made the output ambiguous for a reader too. `status` re-applies the manifest
        # to a scratch copy and diffs, and `apply` does not rewrite a
        # create-only file -- so those files are byte-identical in both trees
        # however many versions behind they are, and the diff is empty by
        # construction rather than by being current. Measured: a Makefile with
        # none of v0.58's targets, `jm apply`, then this line saying OK.
        #
        # gh-767's rule, one step further out. That established jm must not say
        # "up to date" over files the generator no longer agrees with; this is
        # files the generator never looked at. Qualifying is the same answer,
        # and the same reason -- the exit code alone is right and unread.
        #
        # The detection half then took half of this note's job away, and
        # widened the other half. jm's own create-only files are compared
        # now, against the replay tree, and reported as OUTDATED above. What
        # is still uncompared is the *author-owned* kind -- and that set is
        # much larger than this note first claimed. Measured on a plain
        # one-object project: 27 of 32 manifest-owned files are invisible to
        # the copy/diff, not the five this note used to name. Every file
        # `jm new` writes once, and everything scaffolded per object bar the
        # four glue files, is in it.
        print(
            f"OK — up to date; {ok_count} manifest-owned file(s) match"
            f"{suffix}{_unrec}{_kw}{_orph}{_sil}{_out}{_anch}{_wire}."
        )
        print(
            "  create-only files: jm's own — the Makefile, .clang-tidy,"
            " jm_test.h,\n"
            "        jm_bench.h, jm_perf.h, the common headers — are"
            " compared against\n"
            "        this jm's render and reported as OUTDATED. NOT"
            " compared: the ones\n"
            "        whose content is yours — _core.c, your C and Python"
            " tests, README,\n"
            "        pyproject.toml — which differ from their scaffold as"
            " soon as the\n"
            "        project is real. `apply` rewrites neither kind; see"
            " docs/upgrading.md."
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
                f", {sum(1 for e in kwargs_entries if not e[2])} kwargs-drift (!)"
                if any(not e[2] for e in kwargs_entries)
                else ""
            )
            # gh-806: in the one-line summary as well, because that line is
            # what a reader skims when --check has already told them the
            # exit code is 1.
            + (
                f", {sum(1 for o in _orphans if not _orphan_allowed[o.rel])}"
                " unbuilt (!)"
                if any(not _orphan_allowed[o.rel] for o in _orphans)
                else ""
            )
            + (f", {len(_silent)} silent bench" if _silent else "")
            + (
                f", {len(unparseable_entries)} unparseable (!)"
                if unparseable_entries
                else ""
            )
            # gh-949: on this line too. A tree can be drifting *and* behind,
            # and `jm apply` clears only the first — a reader who runs it and
            # re-checks would otherwise meet the OK line's version of this
            # for the first time on the second run.
            + (
                f", {len(outdated_entries)} outdated"
                if outdated_entries
                else ""
            )
            # gh-975: with the gating mark, unlike `outdated` above it — this
            # one fails `--check` and `jm apply` does not clear it.
            + (
                f", {sum(1 for e in unanchored_entries if not e[2])}"
                " unanchored (!)"
                if any(not e[2] for e in unanchored_entries)
                else ""
            )
            # gh-984: on the skim line too, marked as gating. `jm apply` does
            # clear this one, unlike the anchor gap above it — which is why
            # the sentence below about apply is the right thing to read next.
            + (f", {_n_unwired} unwired (!)" if _n_unwired else "")
            + (f", {len(_dangling)} dangling (!)" if _dangling else "")
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
