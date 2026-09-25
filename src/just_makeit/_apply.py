"""
_apply.py — `just-makeit apply` command.

Materialize a project from its `just-makeit.toml`: generate every file each
object / module / method / property / function in the manifest implies.

`apply` is **add only** — it creates files that are missing and never
overwrites or deletes anything (deletion is `jm remove`'s job). It is safe
to run repeatedly; on a fully materialized project it is a no-op.

Implementation: replay the project's full scaffold into a throwaway temp
directory — reusing every generator command — then copy across only the
files the real project does not already have. A project is thus
reproducible from `just-makeit.toml` (plus any hand-written `*_core.c` /
`*_core.h`) alone.
"""

from __future__ import annotations

from . import _textio

import contextlib
import fnmatch
import hashlib
import io
import os
import re
import textwrap
import shutil
import sys
import tempfile
import time

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib
from pathlib import Path

from . import _config as C
from . import _modplatforms
from . import _procglobal
from . import _createonly
from . import _libwiring
from . import _report
from . import _stubs as S
from . import _incpath as INC
from . import _csym as CSYM
from ._init import _to_title


# `{identifier}` placeholders only — anything else passes through untouched.
# In particular, bare C braces (`{ … }`, `{0}`) are NOT consumed.
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate(body: str, ctx: dict) -> str:
    """Replace `{name}` placeholders with ctx[name]; unknown names are
    left in place so literal `{0}` / `{ static int x; }` C code survives."""
    return _PLACEHOLDER_RE.sub(
        lambda m: str(ctx.get(m.group(1), m.group(0))), body
    )


def _resolve_impl(
    section: dict,
    ctx: dict,
    root: Path,
    label: str,
    impl_key: str = "impl",
    impl_file_key: str = "impl_file",
) -> str | None:
    """Honour `<impl_key>` / `<impl_file_key>` / `replace` on a TOML section.

    Returns the resolved body (interpolated + substituted) or None when
    neither key is set.  Raises ValueError on mutual-exclusion violations.
    The *replace* dict is only honoured for the default impl/impl_file pair."""
    inline = section.get(impl_key)
    file_ref = section.get(impl_file_key)
    if inline and file_ref:
        raise ValueError(
            f"{label}: `{impl_key}` and `{impl_file_key}` are mutually "
            f"exclusive — set one or the other."
        )
    if not inline and not file_ref:
        return None

    if file_ref:
        from . import _impl as I

        path_part, _, ref = file_ref.partition("::")
        if not ref:
            raise ValueError(
                f"{label}: {impl_file_key} must be 'path::funcname' or "
                f"'path::N:M', got {file_ref!r}."
            )
        body = I.extract(root / path_part, ref)
    else:
        body = inline

    body = _interpolate(body, ctx)

    if impl_key == "impl":
        replace = section.get("replace") or {}
        if replace:
            from . import _impl as I

            body = I.apply_replacements(body, list(replace.items()))

    return body


# Directory names and filenames never copied from the replay.
#
# `.coverage` (and coverage's per-process `.coverage.<host>.<pid>.<rand>`
# siblings) are tool output landing in the project root, exactly like the
# `__pycache__` and `.so` entries beside them. Without this, running a
# generated project's own suite under coverage leaves a file `apply` never
# writes, and `jm status` reports it as STALE — drift a user cannot act on,
# in the count whose whole value is that everything in it is actionable.
#
# Found via the c_style convergence tests, which had never run in CI because
# clang-format was not installed there: `status --check` returned 1 with
# `~ .coverage` as the sole finding.
_SKIP_DIRS = {"build", ".venv", ".git", "dist", "__pycache__"}
_SKIP_FILES = {C.FILENAME, "compile_commands.json", ".coverage"}
_SKIP_SUFFIXES = {".pyc", ".pyo", ".so", ".pyd"}
# Prefixes, for tool output whose suffix is a random per-process token rather
# than a fixed extension — `.coverage.runner.4711.981234`.
_SKIP_PREFIXES = (".coverage.",)


def is_build_tree(path: Path) -> bool:
    """True when *path* is a CMake binary directory, whatever its name.

    gh-1473. ``_SKIP_DIRS`` knew the name ``build`` and nothing else, so
    every other build tree inside a project was walked, copied into the
    status scratch and counted as manifest-owned -- 35 files became 116 with
    one ``cmake -B build-rel`` -- and CLion's default ``cmake-build-debug``
    is the same -- by a count that was the toolchain's rather than jm's.
    (jm's own presets build into ``out/build/<preset>``, which was covered
    only because one path component happens to be spelled ``build``:
    coverage by coincidence, not by rule.)

    Recognised by what it IS rather than what it is called: CMake writes
    ``CMakeCache.txt`` at the top of every binary directory it configures,
    and nothing else does. No list of names to keep current, and a name a
    project picks tomorrow is covered today.

    >>> import tempfile
    >>> d = Path(tempfile.mkdtemp())
    >>> is_build_tree(d)
    False
    >>> _ = (d / "CMakeCache.txt").write_text("")
    >>> is_build_tree(d)
    True
    """
    return (path / "CMakeCache.txt").is_file()


def _tree_digests(root: Path) -> dict:
    """Digest of every project file, keyed by POSIX path relative to *root*.

    gh-1474: what `apply` reports is decided by comparing these with the
    same files after it has finished -- formatter included -- rather than by
    each write site's own idea of whether it changed something. Those ideas
    were right about their write and wrong about the file: the gh-917
    formatter pass rewrites a render back to the project's style, so on a
    ``c_format_command`` project a second `apply` announced nine `update`s
    and changed no bytes; and two modules sharing one package merged its
    ``__init__.py`` in turn, so it was announced twice.

    Taken once, before anything is written, from the same walk rules
    `status` uses: skipped names and build trees are never descended into.
    """
    out: dict = {}
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _SKIP_DIRS and not is_build_tree(base / d)
        ]
        for name in filenames:
            p = base / name
            rel = p.relative_to(root)
            if is_skipped(rel):
                continue
            try:
                out[rel.as_posix()] = hashlib.sha1(p.read_bytes()).digest()
            except OSError:
                continue
    return out


def _project_rel(root: Path, path) -> str:
    """*path* as a POSIX path relative to *root*.

    The write sites hand back both forms: `_sync_missing` its relative
    paths, the rest ``root / rel``.
    """
    p = Path(path)
    try:
        p = p.relative_to(root)
    except ValueError:
        pass  # already project-relative
    return p.as_posix()


def _changed_report(
    root: Path, before: dict, candidates: list
) -> "list[tuple[str, str]]":
    """``(verb, rel)`` for each candidate whose bytes this run changed.

    *candidates* are the paths the write sites say they touched, relative or
    under *root*, in the order they were written. Each is reported at most
    once, project-relative: ``create`` if it did not exist before the run,
    ``update`` if its bytes differ from *before*, and nothing if the run
    ended where it started -- which is what a second `apply` must print.

    A candidate :func:`is_skipped` excludes is never reported: *before* has no
    digest for it, so it would read as ``create`` on every run. The manifest
    is the one a write site hands back (gh-1477: the app replay saves it).
    """
    out: list = []
    seen: set = set()
    for cand in candidates:
        rel = _project_rel(root, cand)
        if rel in seen or is_skipped(Path(rel)):
            continue
        seen.add(rel)
        real = root / rel
        if not real.is_file():
            continue
        now = hashlib.sha1(real.read_bytes()).digest()
        if rel not in before:
            out.append(("create", rel))
        elif before[rel] != now:
            out.append(("update", rel))
    return out


def superseded_advice(root: Path, old: str, new: str) -> str:
    """What to do about a file jm has renamed (gh-1472), for apply and status.

    One sentence for both commands: `upgrade` does the rename while only the
    old file exists; once both do, jm cannot tell which holds the author's
    edits, so merging them is the author's.
    """
    if (root / new).is_file():
        return (
            f"{old} is superseded by {new}, which is the file jm maintains."
            f" Merge anything you added to {old} into {new}, then delete"
            f" {old}."
        )
    return (
        f"{old} is now called {new}; jm will not create {new} beside it."
        " Run `just-makeit upgrade` to rename it -- your edits come along."
    )


def is_skipped(rel: Path) -> bool:
    """True when *rel* is not manifest-owned and must never be compared.

    One predicate rather than the condition written out at each walk. It was
    duplicated in :func:`_sync_missing` and :func:`_status._walk_managed`, and
    a rule expressed twice is the peer pair this codebase keeps rediscovering
    — the `.coverage` case had to be fixed in both to fix it at all.

    *rel* is a path relative to the project root.
    """
    if set(rel.parts) & _SKIP_DIRS or rel.name in _SKIP_FILES:
        return True
    if rel.suffix in _SKIP_SUFFIXES:
        return True
    return rel.name.startswith(_SKIP_PREFIXES)


def _object_kwargs(cfg: dict, comp: str) -> dict:
    """CLI-equivalent kwargs for re-running `object` generation for *comp*."""
    return {
        "state_vars": C.state_vars(cfg, comp),
        "perf": C.is_perf(cfg),
        "arg_type": C.arg_type(cfg, comp),
        "return_type": C.return_type(cfg, comp),
        "array_args": C.array_args(cfg, comp),
        "no_state": C.is_no_state(cfg, comp),
        "no_step": C.is_no_step(cfg, comp),
        # gh-1311: replayed for the sharpest version of gh-542's reason --
        # dropping it here puts back both the `_core.c` and the OBJECT core
        # library, and an OBJECT library with no sources fails CONFIGURE, so
        # the project does not build at all rather than merely regenerating
        # something the manifest asked to remove.
        "header_only": C.is_header_only(cfg, comp),
        # gh-1310: and a family member's header without it renders jm's own
        # `static inline` bodies, each a second definition of a function the
        # family macro defines.
        "core_family": C.core_family(cfg, comp),
        # gh-542: replayed like every other shape key — a manifest key that
        # apply drops regenerates the very method it asked to remove.
        "no_reset": C.is_no_reset(cfg, comp),
        # gh-1117: manifest-only (no CLI flag), so the replay has to carry it
        # explicitly. Without this the temp scaffold's manifest has no
        # `process_global` at all, every module's rendezvous renders empty,
        # and `jm apply` writes a binding with none of it -- while every unit
        # test passes, because they call the emitters with a cfg that HAS the
        # key. Same shape as `destroy` below and gh-542's `no_reset`.
        "process_global": _procglobal.is_process_global(cfg, comp),
        # gh-1448: manifest-only, for the reason every key above is. Dropped
        # here, the replayed tree renders every owned fragment SACRED -- the
        # "Hand-patches are preserved" banner and no ownership token -- and
        # `_sync_missing` copies that over the owned file. Both sides of a
        # `status` diff replay the same way, so nothing reported it; the
        # first version of the flip shipped exactly that, under a test that
        # checked the body and not the banner.
        "fragment": C.fragment_kind(cfg, comp),
        "mutable": C.is_mutable(cfg, comp),
        "serializable": C.is_serializable(cfg, comp),
        "streamable": C.is_streamable(cfg, comp),
        "async_stream": C.is_async_stream(cfg, comp),
        "stream_block_default": (
            C.stream_block_default(cfg, comp)
            if C.is_streamable(cfg, comp)
            else None
        ),
        "init_params": C.init_params(cfg, comp),
        "init_post_parse_impl": C.init_post_parse(cfg, comp),
        "class_name": C.class_name(cfg, comp),
        # gh-509: replay the object-level C constructor override so the
        # regenerated tp_init calls it, not the default <comp>_create.
        "create_fn": C.object_create_fn(cfg, comp),
        # gh-225: pass the RAW depends_on (preserving `{name, link}` tables) so
        # the replayed scaffold re-persists the link flag and the consuming
        # target's link line is regenerated; render paths flatten to names.
        "depends_on": C.depends_on_raw(cfg, comp),
        "opaque_fields": C.opaque_fields(cfg, comp),
        "no_ctor_names": C.no_ctor_names(cfg, comp),
        "controllable_names": C.controllable_names(cfg, comp),
        "extra_link_libs": C.component_extra_link_libs(cfg, comp),
        "extra_include_dirs": C.component_extra_include_dirs(cfg, comp),
        # gh-541/gh-544: the destructor contract is manifest-only (no CLI
        # flag), so the replay has to carry it explicitly — without this the
        # temp scaffold renders a `void` destructor and the declared
        # name/aliases/error vanish on a fresh checkout.
        "destroy": C.destroy_spec(cfg, comp),
        # gh-1172: the authored class docstring, manifest-only for the same
        # reason `process_global` and `destroy` above are — `jm object` has no
        # `--doc`, so nothing in the reconstructed CLI history carries it and
        # the temp manifest had none. Every module artefact renders from THAT
        # manifest, so the module `.pyi` kept the generic seed and
        # `_sync_aggregates` copied the seed over the real file.
        #
        # And the half that made it survive: `status` copies the project,
        # runs this same replay on the copy, and diffs. A key the replay
        # drops is dropped identically on both sides, so the two agree and
        # `status --check` exits 0 — the checker cannot see a difference it
        # also makes. That is why the fix belongs HERE, in the replay, and
        # not in a post-replay re-render of the one file that showed it.
        "doc": (cfg.get(comp) or {}).get("doc", ""),
    }


def _object_ctx(cfg: dict, comp: str, module: str | None) -> dict:
    """Interpolation context for an object's impl body."""
    return {
        "component": comp,
        "Component": _to_title(comp),
        "module": module or "",
        "Module": _to_title(module) if module else "",
        "arg_type": C.arg_type(cfg, comp),
        "return_type": C.return_type(cfg, comp),
    }


def replay_project(cfg: dict, temp_root: Path, project_root: Path) -> None:
    """Replay *cfg* into *temp_root* the one way `apply` does -- for anyone.

    The replay is not :func:`_replay` alone: it runs inside the scopes that
    make it correct and affordable, and a caller that skips one gets a
    different tree, or none. #1596's `adopt --packaging` called `_replay`
    bare and could not replay doppler at all: without
    `deferred_module_regen` a module regenerates mid-replay, before a
    component it references has had its capsule property replayed, and
    `resolve_object_ref` refuses ("publishes no capsule"). So every caller
    replays through here, and a test refuses a direct `_replay` call
    anywhere else.

    The scopes, each with its reason at its definition:

    - ``_DOC_ROOT_OVERRIDE``: docstrings derive from the REAL project's
      sacred ``_core.h`` (its hand-written Doxygen), not the template
      headers scaffolded into the temp tree.
    - ``scratch_writes`` / ``deferred_save`` (gh-698, gh-764): the plain
      dumper and one deferred save for a manifest nobody reads back.
    - the progress output, which names temp paths, is swallowed.
    - ``deferred_module_regen``: one regeneration per module, after every
      member of it -- and everything it references -- is replayed.
    - ``replay_dependency_graph`` (gh-1549): link closures follow the
      complete ``depends_on`` graph, not the part replayed so far.
    """
    from . import _object as _obj_mod

    _obj_mod._DOC_ROOT_OVERRIDE = project_root
    try:
        # gh-698: the replay runs one mutating command per method, and each
        # rewrites the whole manifest through tomlkit's comment-preserving
        # path — O(methods x manifest size), which is why doppler's 67 KB
        # manifest never finished. The temp tree has no authored comments
        # to preserve (the replay just dumped it) and its manifest is never
        # copied back (`_SKIP_FILES`), so the plain dumper is correct here.
        # ...and the second quadratic term: every mutating command
        # regenerates its whole module, so replaying N methods regenerated
        # all M objects N times. Coalesced to one flush per module.
        # Order matters: `deferred_module_regen` flushes on exit, and a
        # `with` unwinds in reverse — so it must be entered *after* the
        # redirect, or the flush's progress output (naming temp paths)
        # escapes to the user's terminal, which is exactly what the
        # redirect above exists to prevent.
        # gh-764: `deferred_save` sits *outside* `deferred_module_regen`
        # so it exits later — the module-regen flush itself saves, and
        # those writes must fold into the one deferred flush rather than
        # escaping it. It stays inside `scratch_writes` and the redirect
        # for the same reason `deferred_module_regen` does: its flush
        # writes a manifest and must use the cheap dumper and stay quiet.
        with (
            C.scratch_writes(),
            contextlib.redirect_stdout(io.StringIO()),
            C.deferred_save(),
            _obj_mod.deferred_module_regen(),
            C.replay_dependency_graph(cfg),
        ):
            _replay(cfg, temp_root, project_root)
    finally:
        _obj_mod._DOC_ROOT_OVERRIDE = None


def _replay(cfg: dict, temp_root: Path, project_root: Path) -> None:
    """Re-run the full scaffold for *cfg* into the pristine *temp_root*.

    *project_root* is the source project (not the temp) — `impl_file`
    paths in the TOML are resolved relative to it."""
    from . import (
        _capsule,
        _composer,
        _function,
        _handle,
        _method,
        _module,
        _new,
        _object,
        _property,
        _view,
        _warning,
        _error,
    )

    project = C.project_name(cfg)
    mods = C.modules(cfg)
    module_owned = {o for m in mods for o in C.module_objects(cfg, m)}
    standalone = [c for c in C.components(cfg) if c not in module_owned]

    _new.run(
        project,
        temp_root,
        [],
        [],
        build_system=C.build_system(cfg),
        perf=C.is_perf(cfg),
        pytest_=C.is_pytest(cfg),
        pytest_benchmark_=C.is_pytest_benchmark(cfg),
        # gh-1368: no `platforms` -- it no longer changes what is rendered.
        # gh-960: and the same C-formatting opt-in, so the replay renders its
        # own `.clang-format`. Without it the replay had none, `run` copied
        # the real one in, and the file `status` compared was therefore the
        # project's own — equal by construction, so an out-of-date style file
        # could never be reported. Both keys, not just the opt-in: `_new.run`
        # would otherwise fill in the default command for a project that
        # declares its own.
        c_style=C.c_style(cfg),
        c_format_command=cfg.get("project", {}).get("c_format_command"),
        # gh-1583: the schema decides the header layout; a replay in any
        # other layout would put every header somewhere the project has none.
        schema=C.schema_version(cfg),
    )
    # Stamp the real project's version so generated files (pyproject, .pyi)
    # carry it rather than the `new` default.
    tcfg = C.load(temp_root)
    tcfg["project"]["version"] = C.project_version(cfg)
    # gh-353: carry the top-level [[enum]] SSOT into the temp manifest so a
    # replayed `jm function` with an enum param validates the name (and renders
    # the enum tables) against the same declared enums as the real project.
    if cfg.get("enum"):
        tcfg["enum"] = cfg["enum"]
    # gh-554: likewise carry the [codec.X] SSOT so a replayed codec-pack method
    # resolves its `codec` against the same declared table as the real project.
    if cfg.get("codec"):
        tcfg["codec"] = cfg["codec"]
    # gh-393: carry [project.bench] so the replayed scaffold honours the
    # project's configured benchmark block_sizes (#390) — otherwise a new
    # object materialised by `jm apply` reintroduces the default _1k suite.
    bench = cfg.get("project", {}).get("bench")
    if bench:
        tcfg["project"]["bench"] = bench
    C.save(temp_root, tcfg)

    # `new` with no objects writes the minimal package __init__.py; the
    # first standalone object is what generates the full one (DLL-dir
    # preamble included). Drop the placeholder so that object regenerates
    # it exactly as a normal scaffold would.
    if standalone:
        (temp_root / "src" / project / "__init__.py").unlink(missing_ok=True)

    for mod in mods:
        if C.is_no_generate_module(cfg, mod):
            continue
        # gh-286: a capsule module has no object-group scaffold — generate its
        # binding / CMake / .pyi directly from the manifest instead.
        if C.is_capsule_module(cfg, mod):
            _capsule.materialize(cfg, temp_root, mod)
            continue
        # gh-287: a composer module emits its OO-type binding / CMake / .pyi
        # directly from the manifest (no object-group scaffold).
        if C.is_composer_module(cfg, mod):
            _composer.materialize(
                cfg, temp_root, mod, project_root=project_root
            )
            continue
        # gh-306: a handle module emits its typed-class binding / CMake / .pyi
        # directly from the manifest (no object-group scaffold).
        if C.is_handle_module(cfg, mod):
            _handle.materialize(cfg, temp_root, mod, project_root=project_root)
            continue
        # gh-523: `package` must reach the temp scaffold *at module-creation
        # time* — it decides where every Python artifact is written, so the
        # later metadata copy-down (extra_link_libs & co.) would be too late.
        # gh-645: `doc` is read at module-creation time (it renders the
        # re-export __init__.py's docstring), so it has to be forwarded
        # here for the same reason `package` is -- the later metadata
        # copy-down would be too late. This is the gh-663 shape: a new
        # manifest key silently dropped by the replay.
        _module.run(
            temp_root,
            mod,
            package=C.module_package(cfg, mod),
            doc=C.module_doc(cfg, mod),
            # gh-1463: at creation too, so a module with no members yet still
            # renders its CMake guard in the temp tree.
            platforms=cfg.get("module", {}).get(mod, {}).get("platforms"),
        )

    # After module scaffolding, copy module-level metadata (e.g.
    # extra_link_libs) from the real project TOML into the temp TOML so
    # _regenerate_module() inside object.run() picks it up.
    _mods_need_update = [
        m
        for m in mods
        if not C.is_no_generate_module(cfg, m)
        and not C.is_capsule_module(cfg, m)
        and not C.is_composer_module(cfg, m)
        and not C.is_handle_module(cfg, m)
        and (
            cfg.get("module", {}).get(m, {}).get("extra_link_libs")
            or cfg.get("module", {}).get(m, {}).get("extra_types")
            or cfg.get("module", {}).get(m, {}).get("extra_include_dirs")
            # gh-247: functions_in_core must reach the temp TOML *before* the
            # function replay below, else _function.run() falls back to
            # per-function .c files and the build double-defines the symbols.
            or cfg.get("module", {}).get(m, {}).get("functions_in_core")
        )
    ]
    if _mods_need_update:
        tcfg2 = C.load(temp_root)
        for mod in _mods_need_update:
            mod_data = cfg["module"][mod]
            tmod = tcfg2.setdefault("module", {}).setdefault(mod, {})
            if mod_data.get("extra_types"):
                tmod["extra_types"] = mod_data["extra_types"]
            if mod_data.get("extra_link_libs"):
                tmod["extra_link_libs"] = mod_data["extra_link_libs"]
            if mod_data.get("extra_include_dirs"):
                tmod["extra_include_dirs"] = mod_data["extra_include_dirs"]
            if mod_data.get("functions_in_core"):
                tmod["functions_in_core"] = mod_data["functions_in_core"]
        C.save(temp_root, tcfg2)

    # Seed _extra.c files from the real project so _regenerate_module()
    # detects and re-includes them in the temp aggregator.
    # gh-543: standalone objects gained the same hook, so seed them too --
    # otherwise the temp render drops the #include and apply reports the real
    # project's correctly-wired _ext.c as drift.
    for cname in [C.module_paths(m).cname for m in mods] + list(standalone):
        src_dir = project_root / "native" / "src" / cname
        dst_dir = temp_root / "native" / "src" / cname
        if src_dir.is_dir():
            dst_dir.mkdir(parents=True, exist_ok=True)
            for extra in src_dir.glob("*_extra.c"):
                shutil.copy2(extra, dst_dir / extra.name)

    for comp in standalone:
        octx = _object_ctx(cfg, comp, None)
        sec = cfg.get(comp, {})
        impl = _resolve_impl(sec, octx, project_root, f"object {comp}")
        create_impl = _resolve_impl(
            sec,
            octx,
            project_root,
            f"object {comp} create",
            impl_key="create_impl",
            impl_file_key="create_impl_file",
        )
        reset_impl = _resolve_impl(
            sec,
            octx,
            project_root,
            f"object {comp} reset",
            impl_key="reset_impl",
            impl_file_key="reset_impl_file",
        )
        destroy_impl = _resolve_impl(
            sec,
            octx,
            project_root,
            f"object {comp} destroy",
            impl_key="destroy_impl",
            impl_file_key="destroy_impl_file",
        )
        _object.run(
            temp_root,
            comp,
            None,
            # gh-856: the SOURCE manifest's methods. The temp tree has not
            # replayed them yet, so `exit` cannot resolve against it.
            declared_methods=C.methods(cfg, comp),
            impl_body=impl,
            create_impl_body=create_impl,
            reset_impl_body=reset_impl,
            destroy_impl_body=destroy_impl,
            **_object_kwargs(cfg, comp),
        )
    for mod in mods:
        if C.is_no_generate_module(cfg, mod):
            continue
        for comp in C.module_objects(cfg, mod):
            octx = _object_ctx(cfg, comp, mod)
            sec = cfg.get(comp, {})
            impl = _resolve_impl(sec, octx, project_root, f"object {comp}")
            create_impl = _resolve_impl(
                sec,
                octx,
                project_root,
                f"object {comp} create",
                impl_key="create_impl",
                impl_file_key="create_impl_file",
            )
            reset_impl = _resolve_impl(
                sec,
                octx,
                project_root,
                f"object {comp} reset",
                impl_key="reset_impl",
                impl_file_key="reset_impl_file",
            )
            destroy_impl = _resolve_impl(
                sec,
                octx,
                project_root,
                f"object {comp} destroy",
                impl_key="destroy_impl",
                impl_file_key="destroy_impl_file",
            )
            _object.run(
                temp_root,
                comp,
                mod,
                impl_body=impl,
                create_impl_body=create_impl,
                reset_impl_body=reset_impl,
                destroy_impl_body=destroy_impl,
                # gh-860: the module-object replay, the sibling of the
                # standalone site above. Same reason, same source: the temp
                # tree has not replayed this object's methods yet. Set at
                # the CALL SITE rather than in _object_kwargs, which is
                # shared with callers that do not want it.
                declared_methods=C.methods(cfg, comp),
                **_object_kwargs(cfg, comp),
            )

    all_comps = standalone + [
        o for m in mods for o in C.module_objects(cfg, m)
    ]

    # gh-504: one replay per member, reused for both an object's members and a
    # view's own (via view=). `view` routes _property.run/_method.run onto the
    # named view; "" is the object itself.
    def _replay_method(comp, mod, m, view=""):
        mctx = _object_ctx(cfg, comp, mod) | {"method": m["name"]}
        m_impl = _resolve_impl(m, mctx, project_root, f"{comp}.{m['name']}")
        _method.run(
            temp_root,
            comp,
            m["name"],
            mod,
            m.get("arg_type", "void"),
            m.get("return_type", "float _Complex"),
            bool(m.get("variable_output")),
            list(m.get("multi_output", [])),
            # gh-432: pass params through as full dicts — the old
            # (name, type, default) tuple flattening silently dropped every
            # other per-param key (capsule, header, out) on the replay path.
            params=[
                dict(p) for p in (m.get("extra_args") or m.get("params", []))
            ],
            out_type=m.get("out_type"),
            out_divisor=int(m.get("out_divisor", 1)),
            batch=bool(m.get("batch")),
            impl_body=m_impl,
            none_on_empty=bool(m.get("none_on_empty")),
            strict=bool(m.get("strict")),  # gh-1426 B
            error_on_empty=bool(m.get("error_on_empty")),
            result_fields=list(m.get("result_fields", [])),
            max_results=int(m.get("max_results", 64)),
            single=bool(m.get("single")),
            record_name=m.get("record_name", ""),
            record_module=m.get("record_module", ""),
            record_doc=m.get("record_doc", ""),
            # gh-788: dropping this made the replay disagree with the manifest
            # about the method's very SHAPE, not just a detail — without it
            # `result_fields` reads as the list-of-records form and apply
            # rewrote the header prototype to `(state, size_t *result, size_t
            # max_results)`, over a `_core.c` definition still using the
            # record one. gh-632's replace-by-name warning is what surfaced
            # it; before that it would have landed silently.
            record_dtype=m.get("record_dtype", ""),
            # gh-1312: forwarded EXPLICITLY, like record_dtype above and
            # for the same reason -- this loop names keys one by one, so
            # an unnamed one is silently absent and the replayed method
            # loses its shape.
            borrow=bool(m.get("borrow")),
            borrow_count=m.get("borrow_count", ""),
            borrow_writeable=bool(m.get("borrow_writeable")),
            # gh-1418: same rule, same reason. Dropped here, `apply` would
            # rebuild a borrow whose NULL raises one blanket ValueError
            # again -- exit 0, and end-of-stream back to being an error.
            status_fn=m.get("status_fn", ""),
            releases=m.get("releases") or None,  # gh-1426 A
            release_count=m.get("release_count", ""),
            status_errors=m.get("status_errors") or None,
            py_return_type=m.get("py_return_type", ""),
            max_out=int(m.get("max_out", 0)),
            varargs=bool(m.get("varargs")),
            manual_stub=bool(m.get("manual_stub")),
            pass_capacity=bool(m.get("pass_capacity")),
            exact_max_out=bool(m.get("exact_max_out")),
            count_default=m.get("count_default", ""),
            count_name=m.get("count_name", ""),
            nogil=bool(m.get("nogil")),
            status_return=bool(m.get("status_return")),
            # gh-805 §A2/§B. `apply` enumerates method keys ONE BY ONE, so a
            # key not named here is silently absent from the replay — which
            # is how a previous key made `apply` rewrite the sacred _core.h
            # prototype to the wrong shape.
            fn=m.get("fn", ""),
            error_negative=bool(m.get("error_negative")),
            error=m.get("error", ""),
            error_message=m.get("error_message", ""),
            doc=m.get("doc", ""),
            from_apply=True,
            view=view,
            codec=m.get("codec", ""),
            sink_fn=m.get("sink_fn", ""),
            # gh-1011: every argument above turned an absent manifest key
            # into a default, so `run` can no longer see what the entry
            # actually said. The entry's own keys ARE that fact, and this is
            # the only place still holding them.
            declared=frozenset(m),
        )

    def _replay_property(comp, mod, p, view=""):
        _property.run(
            temp_root,
            comp,
            p["name"],
            mod,
            p.get("type") or p.get("ctype", "size_t"),
            bool(p.get("writable")),
            field=bool(p.get("field")),
            buf_field=p.get("buf_field", ""),
            len_field=p.get("len_field", "n"),
            valid_field=p.get("valid_field", ""),
            expr=p.get("expr", ""),
            doc=p.get("doc", ""),
            view=view,
            enum=p.get("enum", ""),  # gh-519
            value_type=p.get("value_type", ""),  # gh-543
            count_fn=p.get("count_fn", ""),
            key_fn=p.get("key_fn", ""),
            value_fn=p.get("value_fn", ""),
            codec=p.get("codec", ""),  # gh-554
            entry_fn=p.get("entry_fn", ""),
            capsule=p.get("capsule", ""),  # gh-788
            entry_type=p.get("entry_type", ""),
            type_field=p.get("type_field", ""),
            count_field=p.get("count_field", ""),
            value_field=p.get("value_field", ""),
        )

    for comp in all_comps:
        mod = C.component_module(cfg, comp)
        # gh-1411: the record declarations FIRST, and into the temp manifest
        # rather than through a command. Every member that references one
        # resolves it by reading the manifest it is being replayed into, so
        # a method replayed before its record sees no declaration at all and
        # the renderer dies on `_CTYPE_META['iq16_t']`.
        #
        # That is what shipped in 0.79.0: `jm method` wrote `arg_type =
        # "iq16_t[]"` happily and `jm apply` on the same manifest refused it,
        # so gh-1405's feature never worked on the manifest-first path its
        # adopter uses. Same ordering rule `jm script` needed in gh-1407.
        _recs = C.records(cfg, comp)
        if _recs:
            _temp_cfg = C.load(temp_root)
            _temp_cfg.setdefault(comp, {})["records"] = [
                dict(r) for r in _recs
            ]
            C.save(temp_root, _temp_cfg)
        # gh-1426 C: properties FIRST. A method may now reference a
        # property in a status message (`{capacity}`), and a property
        # never references a method -- so this is the dependency
        # order, not a preference. Replayed the other way round the
        # declaration is refused as out of scope in the scratch tree,
        # while the same command succeeds against the real project.
        for p in C.properties(cfg, comp):
            _replay_property(comp, mod, p)
        for m in C.methods(cfg, comp):
            _replay_method(comp, mod, m)
        # gh-481. Without this replay a declared warning never reaches a fresh
        # checkout: the object is scaffolded with no warnings and nothing puts
        # them back. That is the exact failure this feature exists to fix —
        # delete-the-fragment-and-apply is jm's own sanctioned migration
        # mechanic, so the manifest has to be able to rebuild the glue alone.
        for w in C.warnings(cfg, comp):
            _warning.run(
                temp_root,
                comp,
                w["condition"],
                w["message"],
                module=mod,
                category=w.get("category", "UserWarning"),
                after=w.get("after", "__init__"),
                stacklevel=int(w.get("stacklevel", 1) or 1),
            )
        # gh-482: same reasoning as the warnings replay above — a declared
        # create_error must reach a fresh checkout from the manifest alone.
        if C.create_error(cfg, comp):
            _error.run(
                temp_root,
                comp,
                C.create_error(cfg, comp),
                C.create_error_message(cfg, comp),
                module=mod,
            )
        # gh-504: same reasoning — a declared view (a second class over this
        # object's core) must rebuild from the manifest alone, or a fresh
        # checkout (and every `jm status`/`jm apply`) would drop it.
        for v in C.views(cfg, comp):
            _view.run(
                temp_root,
                comp,
                v["class_name"],
                mod,
                v["create_fn"],
                init_params=[dict(p) for p in v.get("init_params", [])],
                exclude_properties=list(v.get("exclude_properties", [])),
                exclude_methods=list(v.get("exclude_methods", [])),
                doc=v.get("doc", ""),
                # gh-1017: the view's own create-failure translation (gh-580).
                # `.get` yields None when the key is absent, which is what
                # keeps "undeclared, inherit the parent's" distinct from the
                # explicit `create_error = ""` opt-out. Forwarded here rather
                # than replayed through `_error.run` afterwards because only a
                # verbatim copy can carry that third state.
                create_error=v.get("create_error"),
                create_error_message=v.get("create_error_message"),
                from_apply=True,
            )
            # gh-504: the view's OWN added/overriding members, materialized
            # after the view exists (methods before properties so an override
            # method's shared C symbol is present).
            cls = v["class_name"]
            for m in C.view_methods(v):
                _replay_method(comp, mod, m, view=cls)
            for p in C.view_properties(v):
                _replay_property(comp, mod, p, view=cls)
            # gh-509: a view's OWN warnings, same replay reasoning as the
            # object's above — the manifest must rebuild the view's
            # PyErr_WarnEx block from a fresh checkout alone.
            for w in C.view_warnings(v):
                _warning.run(
                    temp_root,
                    comp,
                    w["condition"],
                    w["message"],
                    module=mod,
                    category=w.get("category", "UserWarning"),
                    after=w.get("after", "__init__"),
                    stacklevel=int(w.get("stacklevel", 1) or 1),
                    view=cls,
                )

    for mod in mods:
        if C.is_no_generate_module(cfg, mod):
            continue
        for fn in C.module_functions(cfg, mod):
            fctx = {
                "function": fn["name"],
                "module": mod,
                "Module": _to_title(mod),
                "return_type": fn.get("return_type", "void"),
            }
            f_impl = _resolve_impl(
                fn, fctx, project_root, f"function {fn['name']}"
            )
            _function.run(
                temp_root,
                fn["name"],
                mod,
                doc=fn.get("doc", ""),
                params=[
                    # gh-170: `mutable` is accepted as a synonym for `out` —
                    # both drop the `const` on a writable array param.
                    # gh-353: replay the 5-tuple including `default` and `enum`
                    # so path/enum/defaulted params survive `jm apply`
                    # regeneration (the rendered _ext.c keeps its enum/path
                    # handling, otherwise these would silently drop).
                    (
                        p["name"],
                        p["type"],
                        bool(p.get("out") or p.get("mutable")),
                        p.get("default", ""),
                        p.get("enum", ""),
                        # gh-1493: manifest-only; see `_function.run`.
                        p.get("doc", ""),
                    )
                    for p in fn.get("params", [])
                ],
                return_type=fn.get("return_type", "void"),
                impl_body=f_impl,
                # An inline function lives as a `static inline` body in the
                # module header with no `.c` file. Without replaying the flag
                # the replay treats it as a regular function and materializes
                # a `<name>.c` that redefines the header body — a double
                # definition the linker rejects. Peer to out_type/enum/default
                # below: the replay must preserve every shape-bearing key.
                inline=bool(fn.get("inline")),
                out_type=fn.get("out_type", ""),
                result_fields=fn.get("result_fields", []),
                max_results_param=fn.get("max_results_param", ""),
                max_results=int(fn.get("max_results", 64)),
                # gh-335: self-sizing output for module functions. Without
                # these the replayed temp manifest loses them and the rendered
                # _ext.c under-allocates (out first, _dim = 1 / first array
                # length) → heap overrun in the C kernel.
                variable_output=bool(fn.get("variable_output")),
                out_size=fn.get("out_size", ""),
                check_return=bool(fn.get("check_return")),
            )


#: DOTALL because the marker WRAPS now (gh-1219): without it `.` stops at the
#: newline and a two-line marker is never stripped, so the drift comparison
#: below would read jm's own comment as author code that changed.
_MARKER_LINE_RE = re.compile(
    # `in\s` not `in ` -- the wrap can break the line exactly there, and a
    # literal space then fails to match the very form this must strip.
    r"[ \t]*/\* jm: body sourced from \[[^\]]*\] impl/impl_file in\s.*?\*/\n?",
    re.DOTALL,
)


def _impl_owner_rel(comp: str, root: Path) -> Path:
    """The project-relative path of the file that owns ``[comp]``.

    gh-609 review: a split-layout project (`jm split-objects`) moves a
    component's TOML section into ``objects/<comp>.toml`` — the top-level
    manifest no longer has the ``impl``/``impl_file`` key at all. `_impl_marker`
    and the overwrite warning both need to point a reader at whichever file
    actually holds the key, so both funnel through `C._provenance`, the same
    owner-tracking `save()` uses to route writes back to the right file."""
    owners, _module_owners, _includes = C._provenance(root)
    owner = owners.get(comp, root / C.FILENAME)
    try:
        return owner.relative_to(root)
    except ValueError:
        return owner


#: Columns the wrapped marker fits in, INCLUDING the four spaces `_indent4`
#: adds. 76 clears the common `ColumnLimit` floor of 79/80 with room to spare;
#: anything above that leaves the comment alone, and clang-format never JOINS a
#: wrapped block comment (measured across `ReflowComments` true/Always/false at
#: limits 80/100/120/0), so the wrapped form is a fixed point rather than one
#: end of a cycle.
_MARKER_COLS = 76


def _wrap_c_comment(text: str, indent: int = 4) -> str:
    """*text* as a C block comment, pre-wrapped so a formatter leaves it alone.

    gh-1219. The marker used to be emitted as one 132-plus-character line into
    `<comp>_core.h`, and `native/inc/**` is deliberately excluded from
    `c_format_command` (splice-patching is whitespace-sensitive, gh-493). So a
    project whose `.clang-format` sets a `ColumnLimit` below that wrapped the
    comment, `jm apply` wrote the long line back, and the two alternated
    forever — a permanently STALE header, in the count `--check` gates on.

    Wrapping here rather than running the formatter over the header is the
    smaller fix and the only one compatible with that exclusion: the text is a
    fixed string plus a short relative path, so the result is deterministic and
    needs no formatter at all.
    """
    width = max(_MARKER_COLS - indent, 20)
    lines = textwrap.wrap(
        text,
        width=width - 3,  # "/* " on the first line, " * " on the rest
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]
    out = [f"/* {lines[0]}"]
    out += [f" * {ln}" for ln in lines[1:]]
    out[-1] += " */"
    return "\n".join(out)


def _impl_marker(comp: str, root: Path) -> str:
    """gh-609: one-line provenance comment for a manifest-``impl``-sourced body.

    `_patch_step_impls` re-injects this component's ``_step`` body from
    ``[comp].impl``/``impl_file`` on every apply — the generated header
    otherwise gives no hint that the function body is a build product rather
    than hand-written C, which is exactly what let a hand-edit to the header
    look like ordinary DSP code worth keeping, right up until `apply` silently
    reverted it. The comment is regenerated fresh from ``impl_body`` on every
    write, so it never drifts out of sync with which manifest key is the
    actual source.

    gh-609 review: naming a hardcoded "the project manifest" was itself the
    discoverability problem the issue was filed over — see `_impl_owner_rel`."""
    rel = _impl_owner_rel(comp, root)
    return _wrap_c_comment(
        f"jm: body sourced from [{comp}] impl/impl_file in {rel}"
        f" — edit there, not here; `jm apply` overwrites this."
    )


def _patch_step_impls(root: Path, cfg: dict) -> list[Path]:
    """Inject ``impl``/``impl_file`` bodies from the manifest into headers.

    ``_sync_missing`` only creates files that are absent in the project tree;
    existing ``_core.h`` files are left untouched even when the user has since
    added an ``impl`` key to the TOML.  This function runs afterwards and
    patches every component that carries an ``impl`` or ``impl_file`` key,
    using ``patch_function_body()`` which is safe to run on both newly-created
    and pre-existing headers (it replaces only the matching function body).

    gh-609: the injected body always carries `_impl_marker()` as its first
    line, so a hand-editor sees at a glance that a "real C" function is
    actually a build product. And if the underlying CODE of the ON-DISK body
    (before this patch, marker line stripped) neither matches what the
    manifest currently says NOR still carries the fresh-scaffold TODO stub
    (`_remove._STUB_MARKER`), something changed it since the last apply —
    most likely a hand-edit of the generated header instead of the manifest
    `impl`. That divergence is about to be silently overwritten, which is
    exactly what cost the gh-609 reporter an afternoon; a warning at the
    point of the overwrite is the cheap fix for it.

    gh-609 review: the WARN decision is deliberately based on stripped-marker
    *content*, not on whether the marker-prefixed text changed. Comparing the
    marked text would fire on every pre-existing `impl` component the first
    time a project adopts this feature (and again any time `_impl_marker`'s
    own wording shifts, e.g. after `split-objects` moves the owning file) —
    the marker line itself is new/changed text, even when the code beneath it
    never diverged. The write still always happens so the marker gets
    added/refreshed; only the warning is gated on real content drift."""
    from . import _impl as I
    from ._remove import _STUB_MARKER

    mods = C.modules(cfg)
    module_owned = {o for m in mods for o in C.module_objects(cfg, m)}
    all_comps = [c for c in C.components(cfg) if c not in module_owned]
    all_comps += [o for m in mods for o in C.module_objects(cfg, m)]

    patched: list[Path] = []
    for comp in all_comps:
        if C.is_no_step(cfg, comp):
            continue
        csym = CSYM.stem(cfg, comp)
        sec = cfg.get(comp, {})
        if not sec.get("impl") and not sec.get("impl_file"):
            continue
        octx = _object_ctx(cfg, comp, C.component_module(cfg, comp))
        impl_body = _resolve_impl(sec, octx, root, f"object {comp}")
        if impl_body is None:
            continue
        h_path = INC.core_h(root, comp)
        if not h_path.exists():
            continue
        original = h_path.read_text(encoding="utf-8")
        marker = _impl_marker(comp, root)
        marked_body = f"{marker}\n{impl_body}"
        updated = I.patch_function_body(original, f"{csym}_step", marked_body)
        if updated != original:
            # Strip any existing marker line (whatever it said, however
            # stale) before comparing, so the warning tracks real code drift
            # rather than the marker's own (possibly first-time, possibly
            # reworded) text.
            original_unmarked = _MARKER_LINE_RE.sub("", original, count=1)
            updated_unmarked = I.patch_function_body(
                original_unmarked, f"{csym}_step", impl_body
            )
            content_changed = updated_unmarked != original_unmarked
            if content_changed and _STUB_MARKER not in original:
                rel = h_path.relative_to(root)
                owner_rel = _impl_owner_rel(comp, root)
                _report.warn(
                    f"{rel}: {csym}_step body differs from the"
                    f" [{comp}] impl/impl_file in {owner_rel}; the"
                    " manifest is the source of truth — overwriting"
                    " the header from it. If you meant to change the"
                    f" body, edit {comp}'s impl/impl_file in {owner_rel}"
                    " instead.",
                    # Advisory: apply resolves it in the same breath by
                    # writing the header from the manifest.
                    gates=False,
                )
            _textio.write_text(h_path, updated)
            patched.append(h_path)
    return patched


def _patch_destroy_signatures(root: Path, cfg: dict) -> list[Path]:
    """Promote an existing component's destructor to ``int`` (gh-541).

    ``returns = "int"`` is the one thing in ``[<comp>.destroy]`` that reaches
    the *sacred* files: the glue calls ``int rc = <comp>_destroy(...)``, so the
    declaration in ``_core.h`` and the definition in ``_core.c`` have to agree.
    A freshly scaffolded component gets that for free — the templates carry the
    slot. An *already scaffolded* one does not: ``_core.c`` is never
    re-rendered and ``_core.h`` only ever gains missing declarations, so
    without this the first build after declaring the table fails with a
    conflicting-types error.

    The patch is deliberately narrow and idempotent:

    - ``void <comp>_destroy`` becomes ``int <comp>_destroy`` (both files);
      anything already ``int`` is left alone.
    - ``return 0;`` is appended to the ``_core.c`` body only when that body
      contains no ``return`` at all, i.e. it is still the generated
      ``free(state);`` stub. A body the user has already given a return path
      is never touched — jm has no basis for guessing which branch should
      report success.

    Only the ``int`` direction is handled. Dropping the table back to ``void``
    leaves a wider C signature whose status the glue simply ignores, which
    still compiles — so there is nothing to undo, and undoing it would mean
    deleting return statements the user wrote.

    Returns
    -------
    list of Path
        The files actually changed.
    """
    from ._init import _matching_brace

    patched: list[Path] = []
    mods = C.modules(cfg)
    module_owned = {o for m in mods for o in C.module_objects(cfg, m)}
    all_comps = [c for c in C.components(cfg) if c not in module_owned]
    all_comps += [o for m in mods for o in C.module_objects(cfg, m)]

    for comp in all_comps:
        if not C.destroy_returns_int(cfg, comp):
            continue
        csym = CSYM.stem(cfg, comp)
        void_decl = re.compile(rf"\bvoid(\s+){csym}_destroy\b")

        h_path = INC.core_h(root, comp)
        if h_path.exists():
            text = h_path.read_text(encoding="utf-8")
            new = void_decl.sub(rf"int\g<1>{csym}_destroy", text)
            if new != text:
                _textio.write_text(h_path, new)
                patched.append(h_path)

        c_path = root / "native" / "src" / comp / f"{comp}_core.c"
        if not c_path.exists():
            continue
        text = c_path.read_text(encoding="utf-8")
        new = void_decl.sub(rf"int\g<1>{csym}_destroy", text)
        # Give the stub a success path. Located by the definition's own
        # opening brace so a `<comp>_destroy` mentioned in a comment or a
        # sibling function cannot be mistaken for it.
        idx = new.find(f"{csym}_destroy")
        while idx != -1:
            brace = new.find("{", idx)
            paren = new.find("(", idx)
            if brace != -1 and paren != -1 and paren < brace:
                end = _matching_brace(new, brace)
                body = new[brace + 1 : end - 1]
                if "return" not in body:
                    new = (
                        new[: end - 1].rstrip("\n")
                        + "\n    return 0;\n"
                        + new[end - 1 :]
                    )
                break
            idx = new.find(f"{csym}_destroy", idx + 1)
        if new != text:
            _textio.write_text(c_path, new)
            patched.append(c_path)
    return patched


def _owned_fragments(root: Path, cfg: dict) -> set:
    """Relative paths of the fragments the manifest declares as jm's.

    `fragment = "generated"` on an object makes `<mod>_ext_<obj>.c` glue
    like `<comp>_ext.c` already is for a standalone object -- rendered
    whole here, drift-gated by `status --check`. A view has no manifest
    table, so the key on its PARENT governs its fragment too; the id list
    is the one `_status` already builds (gh-1448).
    """
    from ._object import _view_frag_id

    out: set = set()
    for mod in C.modules(cfg):
        if C.is_no_generate_module(cfg, mod):
            continue
        cname = C.module_paths(mod).cname
        for obj in C.module_objects(cfg, mod):
            if C.fragment_kind(cfg, obj) != C.FRAGMENT_GENERATED:
                continue
            ids = [obj] + [_view_frag_id(v) for v in C.views(cfg, obj)]
            for fid in ids:
                out.add(
                    Path("native") / "src" / cname / f"{cname}_ext_{fid}.c"
                )
    return out


def _refuse_owned_that_would_lose(
    temp_root: Path, root: Path, owned: set
) -> None:
    """Guard the FIRST whole render of an owned fragment (gh-1448).

    The key is a TOML line anyone can write, and on its own it cannot tell
    "about to be adopted" from "adopted last month". The file can: an owned
    render writes `_render.owned_token`, naming the file.

    * **Token present** -- steady state. The file came from an owned
      render, so any difference is jm's own drift (a later release changing
      a wrapper) and is overwritten. That is what ownership is FOR; a guard
      here would make an owned fragment un-upgradable, which is the review's
      first rule as written, and mine too until it was checked against a
      release that changes a wrapper.
    * **Token absent** -- first adoption. The body predates the key and may
      be hand-written, so it is judged by `_adopt.flip_verdict`, THE
      predicate `adopt --check` uses. Two of them had already drifted:
      this guard refused only a unit that existed on disk alone, so a
      hand-keyed `wfm_writer` was rewritten with rc 0 and lost five
      accepted `sample_type` strings.

    Anything short of `clean` refuses and nothing is written -- one refusal
    aborts the apply, so a parent and its views are refused as a set.
    Until `--accept` exists, only a `would flip` fragment can be adopted,
    which on doppler is 44 of 87 with no way to lose anything.

    The reference is the file already rendered into *temp_root*: a read,
    not a second render.
    """
    from . import _adopt
    from ._render import is_owned_render

    blocked: list = []
    for rel in sorted(owned):
        dst, src = root / rel, temp_root / rel
        if not (dst.is_file() and src.is_file()):
            continue
        existing = dst.read_text(encoding="utf-8")
        if is_owned_render(existing, dst.name):
            continue
        v = _adopt.flip_verdict(
            rel.as_posix(),
            "",
            existing,
            src.read_text(encoding="utf-8"),
        )
        if v.state != "clean":
            blocked.append(v)
    if not blocked:
        return
    lines = [
        'error: `fragment = "generated"` on a fragment jm has never '
        "rendered whole,",
        "  and rendering it now would lose what is below.",
    ]
    for v in blocked:
        lines.append(f"  {v.frag}")
        for u in v.only_here:
            lines.append(f"    only here: {u}")
        for u in v.ahead:
            lines.append(f"    binding ahead: {u}")
        for u in v.differing:
            lines.append(f"    differs:   {u}")
    lines += [
        "",
        "  A unit only here, or a binding ahead of the manifest, would be",
        "  deleted outright. A unit that DIFFERS may be hand-written: jm",
        "  cannot tell that from its own older render, so it will not",
        "  guess. Move hand-written code to the `_extra.c` beside the",
        "  fragment, or drop the key.",
        "  `just-makeit adopt --check` reports this without writing.",
    ]
    raise SystemExit("\n".join(lines))


#: The scaffolded files born carrying jm's ownership token: the test
#: (gh-1489) and the benchmark (gh-1528), which both construct the object and
#: so follow its constructor while the token is there; and the two packaging
#: templates (gh-1589), which hold no authored content, so a fix to either
#: reaches every project. One list, so a file that gains the token gains the
#: whole mechanism.
OWNED_SCAFFOLDS = (
    "src/**/tests/test_*.py",
    "src/**/benchmarks/bench_*.py",
    "cmake/*.pc.in",
    "cmake/*-config.cmake.in",
)


def _owned_scaffolds(temp_root: Path, root: Path) -> set:
    """Scaffolded Python files that still carry jm's ownership token.

    gh-1489. `jm new` / `jm object` write ``tests/test_<comp>.py`` against
    the constructor as it is then. It was create-only, so a later init
    param left it calling the old signature -- 5 of 5 tests failing -- and
    `status` could not say so, because a file the author owns is expected
    to differ from its render.

    The file is now born with `_render.owned_token`, the gh-1448 mechanism
    rather than a second one: while the token names the file, the file is
    jm's and `apply` renders it whole. Deleting the token makes it the
    author's, and this never selects it again. A project scaffolded before
    the token existed has none, so nothing here touches its tests.

    No guard beside it, deliberately, and for gh-1448's reason: a token
    present is steady state, and any difference is jm's own drift. A
    refusal to delete a test the render no longer produces was tried and
    measured -- declaring `no_reset` removes the `test_reset` jm itself
    rendered, and the refusal made that manifest change un-appliable.
    `status --check` reports an edit to an owned test before `apply`
    overwrites it, and the file's header says jm regenerates it.

    Read from the REAL tree: the token records the file's state, and the
    render always carries one.
    """
    from ._render import is_owned_render

    out: set = set()
    for pattern in OWNED_SCAFFOLDS:
        for src in temp_root.glob(pattern):
            rel = src.relative_to(temp_root)
            dst = root / rel
            if dst.is_file() and is_owned_render(
                dst.read_text(encoding="utf-8"), dst.name
            ):
                out.add(rel)
    return out


def _sync_missing(
    temp_root: Path, root: Path, owned: "set | None" = None
) -> list[Path]:
    """Copy every file present in *temp_root* but missing from *root*.

    *owned* is the set of relative paths the manifest declares as jm's
    content (gh-1448). Those are OVERWRITTEN rather than skipped -- the
    one line that made a module fragment sacred and a standalone object's
    `_ext.c` glue, for the same generated wrapper code.

    Returns the created paths, relative to *root*."""
    # Stamp newly-created source files 2 s in the future so GNU Make
    # (1-second timestamp resolution on macOS/Windows) always considers them
    # newer than any pre-existing object files in the build directory.
    _future = time.time() + 2.0
    created: list[Path] = []
    # gh-1472: a file jm now writes under a new name is not created beside
    # the author's copy under the old one -- that default would silently
    # stand in for whatever they had added. `upgrade` renames it instead.
    renamed_to = {Path(new) for _old, new in _createonly.superseded(root)}
    # Ordinal by the POSIX spelling, the same order on every platform (see
    # `_status._walk_managed`).
    for src in sorted(temp_root.rglob("*"), key=lambda q: q.as_posix()):
        if not src.is_file():
            continue
        rel = src.relative_to(temp_root)
        if is_skipped(rel) or rel in renamed_to:
            continue
        dst = root / rel
        if dst.exists() and not (owned and rel in owned):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        os.utime(dst, times=(_future, _future))
        created.append(rel)
    return created


_EXTDEPS_BEGIN = "# ── External deps"
_EXTDEPS_END = "# ── End external deps"

# The sentinel lines every cmake splice anchors on, and what each one carries.
#
# Every splice below locates its anchor by string and treats an absent one as
# "nothing to do" (`_splice_cmake_components._insert`, `_add_cmake_block_for`).
# That return value is indistinguishable from "already correct", so a top
# CMakeLists missing an anchor loses the wiring silently — `apply` printed
# nothing, `status --check` exited 0, and the module was never built (gh-975).
#
# One place, read by the splices themselves and by `_createonly.missing_anchors`
# — the check `status` and `apply` both report from. A second copy of these
# strings is exactly the peer pair that goes out of step.
#
# `_EXTDEPS_BEGIN` / `_EXTDEPS_END` are deliberately NOT here. That block is
# *created* on demand (anchored on `# ── Components`) rather than spliced into,
# so its absence from a project that has never declared a dependency is normal
# and `apply` fixes it — reporting it would fire on every such project.
_COMPONENTS_ANCHOR = "# ── Components"
_MODULES_ANCHOR = "# ── Modules"

CMAKE_SPLICE_ANCHORS = {
    _COMPONENTS_ANCHOR: "the add_subdirectory() wiring for standalone objects",
    _MODULES_ANCHOR: "the add_subdirectory() wiring for modules",
}


def _splice_root_install(real_path: Path, temp_path: Path) -> bool:
    """Render the root CMakeLists's managed install block (gh-1589).

    The block is everything between ``# ── Install`` and ``# ── End install``
    (:func:`_rootcmake.install_block`): the targets' install rules, the
    soname, the package config and version files, the build-tree export and
    the ``.pc``. Every packaging fix of epic gh-1584 lives there, and before
    this the section was create-only, so none reached an existing project.

    The block is replaced only when its CMake COMMANDS differ from the
    render's (:func:`_rootcmake.calls`: comments dropped, layout ignored), so
    a project whose formatter reflowed the block is not rewritten on every
    apply, and a comment-only template change is not worth a rewrite. A file
    without both sentinels is left alone: its install section is the
    author's until `jm adopt --packaging` hands it over, and `status`
    reports it (``ROOT CMAKE install-block``).

    Returns True if the file was modified.
    """
    from . import _rootcmake

    real = real_path.read_text(encoding="utf-8")
    temp = temp_path.read_text(encoding="utf-8")
    rs = _rootcmake.install_block(real)
    ts = _rootcmake.install_block(temp)
    if rs is None or ts is None:
        return False
    old, new = real[rs[0] : rs[1]], temp[ts[0] : ts[1]]
    if _rootcmake.calls(old) == _rootcmake.calls(new):
        return False
    _textio.write_text(real_path, real[: rs[0]] + new + real[rs[1] :])
    return True


def _splice_cmake_external_deps(real_path: Path, cfg: dict) -> bool:
    """Insert or replace the managed external-deps block in the top CMakeLists.

    Reads ``[project] find_packages`` and ``[project] pkg_modules`` from
    *cfg*, generates the corresponding ``find_package()`` /
    ``pkg_check_modules()`` lines, and either:

    - Replaces the content between existing ``# ── External deps`` /
      ``# ── End external deps`` sentinel lines, or
    - Inserts the whole block (including sentinels) immediately before the
      ``# ── Components`` sentinel when the block is absent.

    Returns True if the file was modified.  If both sentinel lines are absent
    and there is nothing to write, the file is left untouched."""
    find_pkgs = C.find_packages(cfg)
    pkg_mods = C.pkg_module_entries(cfg)

    real = real_path.read_text(encoding="utf-8")

    lines: list[str] = []
    if find_pkgs:
        for pkg in find_pkgs:
            lines.append(f"find_package({pkg} REQUIRED)\n")
    if pkg_mods:
        lines.append("find_package(PkgConfig REQUIRED)\n")
        # gh-1578: the prefix (and so `PkgConfig::<PREFIX>`) is the module
        # NAME; the quoted spec carries any version bound.
        for mod in pkg_mods:
            lines.append(
                f"pkg_check_modules({mod.prefix} REQUIRED IMPORTED_TARGET"
                f' "{mod.cmake_spec}")\n'
            )
    if lines:
        # gh-1572: the archive's link interface names these packages'
        # targets (``_libwiring.combined_link_c``), so a consumer of the
        # INSTALLED project must find them too. The config template
        # substitutes this where it loads the exported targets -- doppler's
        # `find_dependency(Threads)` for the same reason.
        deps = [f"find_dependency({pkg})" for pkg in find_pkgs]
        if pkg_mods:
            deps.append("find_dependency(PkgConfig)")
            deps += [
                f"pkg_check_modules({m.prefix} REQUIRED IMPORTED_TARGET"
                f' "{m.cmake_spec}")'
                for m in pkg_mods
            ]
        body = "\n".join(["include(CMakeFindDependencyMacro)", *deps])
        lines.append(f"set(JM_FIND_DEPENDENCIES [=[\n{body}\n]=])\n")
        # gh-1573/gh-1576: the `.pc` face of the same fact, per pc(5). A
        # dependency with its own `.pc` goes to `Requires.private`: its Cflags
        # always (a jm header may include the dependency's), its Libs with
        # `--static`. One without goes to `Libs.private`. A `pkg_modules`
        # entry IS a module name; a `find_packages` entry names a CMake
        # package, so the author states its `pkg_config` or `libs_private` --
        # nothing maps one namespace to the other, and a wrong guess would
        # break `pkg-config --cflags` for everyone.
        entries = C.find_package_entries(cfg)
        requires = [e.pkg_config for e in entries if e.pkg_config]
        requires += [m.pc_spec for m in pkg_mods if m.name not in requires]
        if requires:
            lines.append(
                "set(JM_PC_REQUIRES_PRIVATE "
                f'"Requires.private: {", ".join(requires)}")\n'
            )
        libs_private = [e.libs_private for e in entries if e.libs_private]
        if libs_private:
            lines.append(
                "set(JM_PC_LIBS_PRIVATE "
                f'"Libs.private: {" ".join(libs_private)}")\n'
            )
        # gh-1579: the compile half of the no-`.pc` case. pc(5) has no
        # private Cflags -- a header's includes are needed however the
        # consumer links -- so these append to the `.pc`'s own `Cflags`.
        cflags = [e.cflags for e in entries if e.cflags]
        if cflags:
            lines.append(f'set(JM_PC_CFLAGS " {" ".join(cflags)}")\n')

    # gh-1599: what the installed headers need of every consumer -- and of
    # this project's own build first. Placed here, above the components, so
    # `add_compile_definitions` reaches every target in this directory tree
    # (the combined libraries' compile included) and `link_libraries` every
    # target created after it: each core's tests, benchmarks and Python
    # extension. The combined libraries were created above this block, and
    # their PUBLIC face -- what the export and the .pc read -- is set in the
    # managed install block from the two variables.
    pub_defs = C.public_defines(cfg)
    pub_libs = C.public_link_libs(cfg)
    if pub_defs:
        joined = " ".join(pub_defs)
        lines.append(f"add_compile_definitions({joined})\n")
        lines.append(f"set(JM_PUBLIC_DEFINES {joined})\n")
    if pub_libs:
        joined = " ".join(pub_libs)
        lines.append(f"link_libraries({joined})\n")
        lines.append(f"set(JM_PUBLIC_LINK_LIBS {joined})\n")

    has_begin = _EXTDEPS_BEGIN in real
    has_end = _EXTDEPS_END in real

    # Nothing to declare: leave a file without the block alone, but empty an
    # existing one -- a dependency (or a public flag) removed from the
    # manifest must leave the build too, not linger in a stale block.
    if not lines and not (has_begin and has_end):
        return False

    content = "".join(lines)

    if has_begin and has_end:
        begin_idx = real.index(_EXTDEPS_BEGIN)
        begin_line_end = real.index("\n", begin_idx) + 1
        end_idx = real.index(_EXTDEPS_END)
        new_real = real[:begin_line_end] + content + real[end_idx:]
    elif not has_begin and not has_end:
        if _COMPONENTS_ANCHOR not in real:
            return False
        idx = real.index(_COMPONENTS_ANCHOR)
        block = f"{_EXTDEPS_BEGIN}\n{content}{_EXTDEPS_END}\n\n"
        new_real = real[:idx] + block + real[idx:]
    else:
        return False  # mismatched sentinels — leave the file alone

    if new_real != real:
        _textio.write_text(real_path, new_real)
        return True
    return False


_WIRED_CORE = re.compile(
    r"^target_sources\(\w+ PRIVATE \$<TARGET_OBJECTS:(\w+)>\)[ \t]*\n"
)

_SUBDIR_BLOCK = re.compile(
    r"^add_subdirectory\(native/src/(\w+)\)[ \t]*\n"
    r"(?:^target_sources\(\w+ PRIVATE \$<TARGET_OBJECTS:\w+_core>\)[ \t]*\n)*",
    re.MULTILINE,
)


def _splice_cmake_components(
    real_path: Path, temp_path: Path, cfg: dict
) -> bool:
    """Reconcile the top CMakeLists's component / module wiring.

    Extracts every `add_subdirectory(native/src/X)` block (with adjacent
    `target_sources(... TARGET_OBJECTS:X_core)` lines) from *temp_path*
    wherever they appear, removes any existing blocks from *real_path*,
    and inserts them into the `# ── Components` / `# ── Modules` sentinel
    sections — components in one, modules in the other, so the layout
    matches a freshly-scaffolded project. Content outside those two
    sentinels (e.g. doppler's vendored-libzmq block) is preserved."""
    real = real_path.read_text(encoding="utf-8")
    temp = temp_path.read_text(encoding="utf-8")

    # The regex captures the native-dir token, which for a nested module is its
    # cname (dsp_filters), not the dotted id — so classify against cnames.
    module_names = C.module_cnames(cfg)
    component_blocks: list[str] = []
    module_blocks: list[str] = []
    for m in _SUBDIR_BLOCK.finditer(temp):
        (
            module_blocks if m.group(1) in module_names else component_blocks
        ).append(m.group(0))

    # c_deps: pure add_subdirectory, no Python scaffolding.
    # Prepended so their targets exist before any depending component emits
    # target_sources(...TARGET_OBJECTS:dep_core...).
    seen_blocks = {b.split("\n")[0] for b in component_blocks}
    dep_blocks: list[str] = []
    for dep in C.c_deps(cfg):
        line = f"add_subdirectory(native/src/{dep})\n"
        if line.rstrip("\n") not in seen_blocks:
            dep_blocks.append(line)
    component_blocks = dep_blocks + component_blocks

    # no_generate modules: add_subdirectory only; all source files are hand-written.
    seen_mod_blocks = {b.split("\n")[0] for b in module_blocks}
    for mod in C.modules(cfg):
        if C.is_no_generate_module(cfg, mod):
            line = (
                f"add_subdirectory(native/src/{C.module_paths(mod).cname})\n"
            )
            if line.rstrip("\n") not in seen_mod_blocks:
                module_blocks.append(line)

    new_real = _SUBDIR_BLOCK.sub("", real)

    def _insert(text: str, sentinel: str, content: str) -> str:
        if not content or sentinel not in text:
            return text
        idx = text.index(sentinel)
        idx = text.index("\n", idx) + 1
        return text[:idx] + content + text[idx:]

    new_real = _insert(new_real, _COMPONENTS_ANCHOR, "".join(component_blocks))
    new_real = _insert(new_real, _MODULES_ANCHOR, "".join(module_blocks))

    # gh-984: drop a wiring line naming a `_core` that no longer exists.
    #
    # The reinstatement above only covers lines the block regex lifted, and
    # that regex requires an `add_subdirectory` immediately above — so an
    # orphaned `target_sources` on its own (an interrupted removal, a bad
    # merge) survived every apply. That is not cosmetic: cmake resolves
    # `$<TARGET_OBJECTS:>` at CONFIGURE time, so the project does not build,
    # and `status` reported it with nothing able to clear it.
    #
    # Known = declared in the real tree OR in the replay. The union matters
    # because the two disagree exactly when apply is doing its job: a
    # component the manifest declares but the tree has lost exists only in
    # the replay at this point (`_sync_missing` restores it afterwards), and
    # a hand-written `c_deps` dir exists only in the real tree.
    known = set(_libwiring.declared_cores(real_path.parent)) | set(
        _libwiring.declared_cores(temp_path.parent)
    )
    # gh-1338: drop a re-emitted wiring line for a core the REAL project
    # already folds in from somewhere other than its root.
    #
    # The replay tree is a fresh scaffold, so jm's canonical unguarded pair
    # is always in it -- which is why deleting the pair from the root by hand
    # does not stick, and why guarding it by hand leaves two copies. A
    # component that declares its core inside a platform guard and wires it
    # there (doppler's POSIX-only timing core, kept conditional precisely to
    # stay out of jm's block) then got an UNGUARDED duplicate: redundant
    # where the guard holds, and a CONFIGURE error where it does not, since
    # cmake resolves `$<TARGET_OBJECTS:>` before compiling anything.
    #
    # Read from the real tree, never the replay: the replay has only jm's own
    # root wiring, so asking it would always answer "nothing is external".
    external = _libwiring.externally_wired(real_path.parent)
    new_real = "".join(
        line
        for line in new_real.splitlines(keepends=True)
        if not (
            (m := _WIRED_CORE.match(line))
            and (m.group(1) not in known or m.group(1) in external)
        )
    )

    if new_real != real:
        _textio.write_text(real_path, new_real)
        return True
    return False


def _merge_pkg_init(real_path: Path, temp_path: Path) -> bool:
    """Splice every missing `from .X import Y` import from *temp_path* into
    *real_path*, preserving user content. Returns True if modified."""
    from ._init import _splice_init_py, ensure_dll_preamble

    temp_text = temp_path.read_text(encoding="utf-8")
    imports = re.findall(
        r"^from \.(\w+) import (\w+)", temp_text, re.MULTILINE
    )
    changed = False
    for comp, Component in imports:
        cur = real_path.read_text(encoding="utf-8")
        if f"from .{comp} import {Component}" in cur:
            continue
        _splice_init_py(real_path, comp, Component)
        changed = True
    # gh-1181: and UNCONDITIONALLY, not only when an import was added. Every
    # project built before this had its imports spliced in already, so gating
    # the preamble on a new one would leave exactly the projects that need it
    # — the ones with a standalone object — permanently without it. `apply`
    # merges this file rather than overwriting it, which is why the omission
    # was invisible to `status`: the difference was never between the two
    # sides it compares.
    cur = real_path.read_text(encoding="utf-8")
    fixed = ensure_dll_preamble(cur)
    if fixed != cur:
        _textio.write_text(real_path, fixed)
        print(f"  update  {real_path}")
        changed = True
    return changed


def _merge_module_init_file(
    real_path: Path,
    module: str,
    temp_path: Path,
    reexports: dict[str, list[str]] | None = None,
    siblings: list[str] | None = None,
    platforms: "dict[str, tuple[str, ...] | None] | None" = None,
) -> bool:
    """Run _merge_module_init against *real_path*, using the export list
    parsed out of *temp_path*'s import line. Preserves any user wrapper
    classes already in the real file. *reexports* (from the manifest) are
    folded into the import block and __all__ so a no_generate sibling's
    re-exported names regenerate cleanly instead of being hand-edited glue.
    *siblings* (gh-523) are the leaf names of other modules sharing this
    package — their exports are protected from the ``__all__`` rewrite.
    *platforms* (gh-1463) guards a platform-restricted leaf's import."""
    from ._object import (
        _leading_docstring,
        _merge_module_docstring,
        _merge_module_init,
    )

    temp_text = temp_path.read_text(encoding="utf-8")
    # Indentation allowed: a platform-restricted module's own line sits
    # inside its gh-1463 guard in the temp render, and the names are the same.
    m = re.search(
        rf"^[ \t]*from \.{re.escape(module)} import[ \t]*"
        r"(\([^)]*\)|[^\n]*)[^\n]*$",
        temp_text,
        re.MULTILINE,
    )
    if not m:
        return False
    raw = re.sub(r"#[^\n]*", "", m.group(1)).strip().strip("()")
    exports = [n.strip() for n in raw.split(",") if n.strip()]
    if not exports:
        return False

    existing = real_path.read_text(encoding="utf-8")
    merged = _merge_module_init(
        existing,
        module,
        exports,
        reexports,
        siblings=siblings,
        platforms=platforms,
    )
    # gh-695: carry the module docstring across too. `[module.X] doc` reached
    # this file only via the template, which apply renders into *temp* and
    # then never copies — so a module that gained a `doc` after scaffolding
    # kept a docstring-less shim while the same string did reach the C
    # extension's m_doc. Taken from the temp render rather than re-derived, so
    # there is one place that turns the manifest string into Python source.
    merged = _merge_module_docstring(merged, _leading_docstring(temp_text))
    if merged != existing:
        _textio.write_text(real_path, merged)
        return True
    return False


def _status_allowed(cfg: dict, rel: str) -> bool:
    """True when *rel* matches a ``[project] status_allow`` pattern (gh-441).

    One predicate for every reconcile that honours the list, so a writer that
    re-renders a file by some other route than :func:`_overwrite_if_changed`
    cannot quietly stop honouring it.
    """
    return bool(rel) and any(
        rel == pat or fnmatch.fnmatch(rel, pat) for pat in C.status_allow(cfg)
    )


def _overwrite_if_changed(
    real: Path,
    temp: Path,
    cfg: dict | None = None,
    rel: str = "",
    honor_status_allow: bool = True,
) -> bool:
    """Overwrite *real* with *temp*'s bytes if they differ.

    For a ``.pyi`` target, *cfg* (if given) is used to splice any
    manual_stub method's hand-written text from *real* back over the
    freshly rendered placeholder in *temp* before comparing (gh-428) —
    without this, this reconcile step is exactly what silently clobbers a
    hand-written manual_stub stub on every plain `jm apply`.

    *rel* — the project-relative posix path of *real* — is checked against
    ``[project] status_allow`` when *honor_status_allow* is true (gh-441): a
    hand-maintained file `jm status --check` already treats as allowed
    drift must never be silently overwritten by apply's reconcile step, so
    a match skips the write entirely rather than only suppressing the
    status warning. `_status.py` sets *honor_status_allow* false for its
    internal throwaway replay, which must keep computing the real diff
    (allowed files still need genuine before/after content to classify as
    ALLOWED rather than OK, and gh-426 dropped-symbol detection must see
    them too) instead of silently matching by never having written it.
    """
    if not real.exists() or not temp.exists():
        return False
    if honor_status_allow and cfg is not None and _status_allowed(cfg, rel):
        return False
    new_bytes = temp.read_bytes()
    if cfg is not None and real.suffix == ".pyi":
        try:
            new_bytes = S._splice_manual_stub_bodies(
                cfg,
                real.read_text(encoding="utf-8"),
                new_bytes.decode("utf-8"),
                path=real,
            ).encode("utf-8")
        except UnicodeDecodeError:
            pass
    if real.read_bytes() == new_bytes:
        return False
    if real.name == "CMakeLists.txt" and real.parent.parent.name == "src":
        try:
            _warn_dropped_cmake(
                real,
                real.read_text(encoding="utf-8"),
                new_bytes.decode("utf-8"),
            )
        except UnicodeDecodeError:
            pass
    real.write_bytes(new_bytes)
    return True


def _object_core_extra_sources(text: str, comp: str) -> list:
    """Sources the component's OBJECT lib compiles besides ``<comp>_core.c``.

    gh-275's signal, on its own. :func:`_is_hand_owned_object_cmake` ORs it
    with the presence of `set_source_files_properties` /
    `add_custom_command` / `add_custom_target`, which answers a different
    question — "may jm re-render this file" — and is true for *every*
    scaffolded object, because jm's own template emits a POST_BUILD
    `add_custom_command`. gh-1294 asked the narrow question and reached for
    the composite one, and got "hand-owned" for a plain `jm object`.
    """
    m = re.search(
        rf"add_library\(\s*{re.escape(comp)}_core\s+OBJECT\s+([^)]*)\)",
        text,
    )
    if not m:
        return []
    return [s for s in m.group(1).split() if s != f"{comp}_core.c"]


#: Build statements the manifest has no way to express. A file carrying MORE
#: of one than jm's own render of it was given that rule by hand.
_HAND_OWNED_CMAKE_KEYWORDS = (
    "set_source_files_properties",
    "add_custom_command",
    "add_custom_target",
)


def _is_hand_owned_object_cmake(
    text: str, comp: str, rendered: str = ""
) -> bool:
    r"""True when a component's ``CMakeLists.txt`` carries bespoke build wiring.

    ``jm apply`` re-renders a component's CMakeLists from the manifest — a
    module object's per-object file (gh-271) and a standalone object's own
    (gh-1301). That is safe only while the file stays within the shape jm
    emits; once it gains build rules the manifest cannot express — extra
    ``add_library`` sources (vendored ``.c`` compiled into ``<comp>_core``),
    ``set_source_files_properties``, or a custom build step — re-rendering
    would silently drop them (gh-275: doppler's ``fft_core`` compiles in
    pocketfft / PFFFT, breaking every FFT consumer). Such a file is
    *hand-owned*: jm leaves it untouched and ``status --check`` treats it as
    up to date.

    Detected signals:

    - an ``add_library(<comp>_core OBJECT …)`` source list naming anything
      besides ``<comp>_core.c``;
    - more ``set_source_files_properties`` / ``add_custom_command`` /
      ``add_custom_target`` occurrences than *rendered*, jm's own render of
      the same file.

    The second is a COUNT against the render, not presence, because the
    standalone template emits a POST_BUILD ``add_custom_command`` itself.
    Presence was right for the module-object file only because its template
    happened to emit none; applied to a standalone file it would call every
    plain ``jm object`` hand-owned and freeze its glue forever (gh-1301).

    Examples
    --------
    >>> plain = "add_library(foo_core OBJECT foo_core.c)\n"
    >>> _is_hand_owned_object_cmake(plain, "foo")
    False
    >>> _is_hand_owned_object_cmake(
    ...     "add_library(foo_core OBJECT foo_core.c vendor.c)\n", "foo")
    True
    >>> post = plain + "add_custom_command(TARGET foo POST_BUILD)\n"
    >>> _is_hand_owned_object_cmake(post, "foo", rendered=post)
    False
    >>> _is_hand_owned_object_cmake(post, "foo", rendered=plain)
    True
    """
    if _object_core_extra_sources(text, comp):
        return True
    return any(
        text.count(kw) > rendered.count(kw)
        for kw in _HAND_OWNED_CMAKE_KEYWORDS
    )


#: A CMake command at the start of a line: `name(`.
_CMAKE_CMD_RE = re.compile(r"^[ \t]*([A-Za-z_]\w*)[ \t]*\(", re.M)


def dropped_cmake_commands(original: str, rendered: str) -> "list[str]":
    """Commands *original* uses that jm's *rendered* replacement never does.

    gh-1351, the silent half. When apply re-renders a generated CMakeLists, a
    statement the author added is gone and nothing said so. This names the
    ones it can be sure of: a command whose NAME appears nowhere in jm's own
    render of the file -- ``target_compile_definitions`` in a file jm never
    writes one into is the author's by construction. A changed
    ``target_link_libraries`` is not reported, because jm writes those itself
    and a manifest edit legitimately changes them.

    Comments and string contents are masked first, so a command named in
    jm's own prose -- or the author's -- is not mistaken for a call.

    Examples
    --------
    >>> dropped_cmake_commands(
    ...     "add_library(o_core OBJECT o_core.c)\\n"
    ...     "target_compile_definitions(o_core PRIVATE X=1)\\n",
    ...     "add_library(o_core OBJECT o_core.c)\\n",
    ... )
    ['target_compile_definitions']
    """
    from ._docsync import _code_mask

    def names(text: str) -> "list[str]":
        return _CMAKE_CMD_RE.findall(_code_mask(text.replace("#", "//")))

    have = {n.lower() for n in names(rendered)}
    out: list[str] = []
    for n in names(original):
        if n.lower() not in have and n not in out:
            out.append(n)
    return out


def _warn_dropped_cmake(real: Path, original: str, rendered: str) -> None:
    """Say which of the author's CMake statements apply is about to drop."""
    dropped = dropped_cmake_commands(original, rendered)
    if not dropped:
        return
    d = real.parent.name
    _report.warn(
        f"native/src/{d}/CMakeLists.txt is regenerated, and it carries "
        f"{', '.join(f'{n}()' for n in dropped)} that jm does not write -- "
        f"`jm apply` drops it. Move it into native/src/{d}/{d}_extra.cmake, "
        "which the generated file includes and jm never touches (gh-1351)."
    )


def _reconcile_object_core_cmake(
    real: Path, temp: Path, comp: str, include_dirs: "list[str]"
) -> bool:
    """Reconcile a component's own ``CMakeLists.txt`` (gh-271, gh-1301).

    Serves both a non-collocated module object's per-object file and a
    standalone object's. The standalone path used to overwrite its file
    wholesale, so the protections below existed for one of two peers: a
    vendored source, a per-source property and an ``if(VAR)`` block all
    survived ``jm apply`` in a module and were silently dropped standalone
    (gh-1301). Standalone callers pass no *include_dirs* — their template
    renders ``extra_include_dirs`` itself.

    A module object's per-object ``native/src/<obj>/CMakeLists.txt`` is glue,
    but ``jm apply`` historically only *added* missing link/include lines to it
    (``_inject_object_core_cmake``), so a *change* to the object's ``depends_on``
    never reached the file once its ``target_link_libraries(<obj>_core PUBLIC …)``
    block already existed — the new dep cores were silently dropped from the
    object's own ``_core`` / ``test`` / ``bench`` link lines and the C test
    failed to link. ``jm status --check`` missed the drift because it observes
    the same skipped reconcile.

    The fix overwrites the file from the freshly-replayed canonical render in
    *temp* (which carries the current ``extra_link_libs`` + ``depends_on`` cores
    on every link line), then restores the two things the manifest-driven render
    cannot reproduce:

    1. component-level ``extra_include_dirs`` — the per-object template has no
       slot for them, so they only ever reach the file via this injection
       (mirrors the standalone path's gh-174 behaviour);
    2. user ``if(VAR) … endif()`` external-library blocks (e.g.
       ``if(DOPPLER_C_LIB)``) — hand-added wiring jm cannot re-derive.

    A file that has gone *hand-owned* (bespoke ``add_library`` sources /
    ``set_source_files_properties`` the manifest can't express — gh-275) is left
    untouched, since re-rendering it would drop those rules.

    Returns True if the file changed."""
    if not real.exists() or not temp.exists():
        return False
    from ._object import _external_cmake_blocks

    original = real.read_text(encoding="utf-8")
    new = temp.read_text(encoding="utf-8")
    # gh-275: never re-render a hand-owned file (vendored sources, per-source
    # build properties) — the canonical render cannot reproduce them.
    if _is_hand_owned_object_cmake(original, comp, rendered=new):
        return False
    # (1) re-add component extra_include_dirs as a second PUBLIC include block,
    # just before the test executable (matches _inject_object_core_cmake).
    if include_dirs and include_dirs[0] not in new:
        anchor = f"add_executable(test_{comp}_core"
        if anchor in new:
            block = (
                f"target_include_directories({comp}_core PUBLIC\n    "
                + "\n    ".join(include_dirs)
                + ")\n"
            )
            new = new.replace(anchor, f"{block}{anchor}", 1)
    # (2) preserve user external-library blocks the canonical render omits.
    for block in _external_cmake_blocks(original):
        if block not in new:
            new = new.rstrip("\n") + "\n\n" + block + "\n"
    if new == original:
        return False
    _warn_dropped_cmake(real, original, new)
    _textio.write_text(real, new)
    return True


def _refresh_core_h_decls(
    real: Path, temp: Path, comp: str, family: "C.CoreFamily | None" = None
) -> bool:
    """Bring the real ``_core.h`` up to date with the manifest, splice-free.

    The temp header is freshly rendered from the manifest and carries every
    declaration the spec implies.  We extract those prototypes and reconcile
    them into the user's header — the sacred state struct and inline
    ``step()`` body are never touched.

    gh-632: this used to claim apply "only *adds* decls", while
    :func:`_init._inject_decls_into_core_h`, the function it calls, documented
    the opposite and **replaced by name**. Both docstrings were internally
    coherent and described different behaviours; the code did the second.

    Replace-by-name is the policy, and is deliberate. ``_core.h`` is a hybrid
    — the struct and the inline ``step()`` are sacred, the declarations are
    glue — so a prototype that no longer matches the manifest is stale glue,
    and a purely additive refresh would freeze a changed signature out of the
    header permanently while the generated ``_ext.c`` called the new one.

    What was wrong is that it happened *silently*, so the author learned
    about it from a build failure in `_core.c` or at a call site, one step
    removed from the edit. It now warns, naming the old and new prototypes.
    ``skip_names`` remains the targeted exception, for declarations whose
    author-written form jm reads back as a contract (gh-761's ``*_max_out``
    arity) and would otherwise flip-flop on each apply.

    gh-1310: for a macro-family member (*family*) it also keeps the one
    ``DECLARE_...(...)`` line in sync with the manifest's ``core_args`` --
    here rather than in a writer of its own, because the line is a
    declaration in the same sense the prototypes are: glue the manifest owns,
    inside a header the author owns. See :func:`_refresh_family_invocation`.

    Returns True if the real header changed."""
    if not temp.exists():
        return False
    from ._init import _core_h_decl_lines, _inject_decls_into_core_h

    if not real.exists():
        # Nothing to merge into — _sync_missing copies the temp header.
        return False
    decls = _core_h_decl_lines(temp.read_text(encoding="utf-8"))
    # gh-761: the author owns every `*_max_out` signature they have already
    # declared. Its arity is a real contract — whether the bound depends on
    # the caller's block or only on the state — and jm now *reads* it to
    # decide the binding and the stub. Re-declaring the count-bearing form
    # over a state-only one would revert that contract and, worse, make the
    # fix unstable: the next apply would read back jm's own rewrite and flip
    # both faces to match it.
    # Only names the header actually declares are protected; a brand-new
    # accessor still gets jm's default, so a fresh project is unchanged.
    from ._docstring import declared_max_outs

    skip = declared_max_outs(real.read_text(encoding="utf-8"))
    changed = _inject_decls_into_core_h(
        real, comp, decls, skip_names=skip, family=family
    )
    if family is not None and _refresh_family_invocation(real, family):
        changed = True
    return changed


def _refresh_family_invocation(path: Path, family: "C.CoreFamily") -> bool:
    """Make *path* invoke ``family.macro`` exactly once, with the manifest's
    arguments (gh-1310).

    The arguments are the manifest's -- the ``instances`` row of a template
    -- so the header must not state them a second time by hand: a changed
    ``scale`` has to reach the C, and a hand edit to the line is a second
    declaration of one value. The line is found on `_docsync`'s code mask, so
    an ``@code`` example that shows the invocation is never taken for it, and
    it may span lines. Absent, it is added where `append_component_body`
    puts a header-only component's definitions: before the ``extern "C"``
    close. Found more than once, nothing is touched and jm says so -- which
    one the author meant is not something to guess.

    Returns True when the file was written.
    """
    from ._docsync import _code_mask

    text = path.read_text(encoding="utf-8")
    mask = _code_mask(text)
    spans = []
    for m in re.finditer(
        rf"^[ \t]*({re.escape(family.macro)})\s*\(", mask, re.MULTILINE
    ):
        depth, i = 0, m.end() - 1
        while i < len(mask):
            depth += {"(": 1, ")": -1}.get(mask[i], 0)
            if depth == 0:
                break
            i += 1
        if depth:
            # Unclosed: replacing "to the close" would take the rest of the
            # file with it.
            _report.warn(
                f"{path.name}: the {family.macro}( invocation never closes;"
                " jm leaves the header alone until it does.",
                gates=True,
            )
            return False
        end = i + 1
        while end < len(mask) and mask[end] in " \t;":
            end += 1
        spans.append((m.start(1), end))
    if len(spans) > 1:
        _report.warn(
            f"{path.name} invokes {family.macro} {len(spans)} times; jm keeps"
            " exactly one in sync with the manifest's core_args and cannot"
            " tell which is meant. Remove the others.",
            gates=True,
        )
        return False
    if spans:
        start, end = spans[0]
        if text[start:end].rstrip() == family.invocation:
            return False
        new = text[:start] + family.invocation + text[end:]
    else:
        cut = text.rfind("#ifdef __cplusplus")
        if cut == -1:
            cut = text.rfind("#endif")
        new = text[:cut] + family.invocation + "\n\n" + text[cut:]
    _textio.write_text(path, new)
    return True


def missing_family_headers(root: Path, cfg: dict) -> "list[tuple[str, str]]":
    """``(component, header)`` for each family header that does not exist.

    gh-1310. jm never writes the family header -- it is the one hand-written
    file every member's definitions come from -- so a member declared before
    its header exists would be a tree that does not compile. `apply` refuses
    it, naming the file and the macro to define in it.
    """
    out = []
    for comp in C.components(cfg):
        fam = C.core_family(cfg, comp)
        if fam and not (INC.inc_dir(root) / fam.header).is_file():
            out.append((comp, fam.header))
    return out


def _add_cmake_block_for(
    real_path: Path, temp_path: Path, comp: str, cfg: dict
) -> bool:
    """Insert the `add_subdirectory` block for *comp* into *real_path*.

    Reads the generated cmake from *temp_path*, locates the block that
    matches *comp* via _SUBDIR_BLOCK, and inserts it immediately after the
    sentinel line `# ── Modules` (when *comp* is a module) or
    `# ── Components` (otherwise).  If the block is already present in
    *real_path*, or cannot be found in *temp_path*, returns False.

    This is the narrow-scope variant used by --only: it adds exactly one
    component's wiring instead of re-splicing all components.
    """
    real = real_path.read_text(encoding="utf-8")
    # Guard: block already wired in.
    if f"add_subdirectory(native/src/{comp})" in real:
        return False

    temp = temp_path.read_text(encoding="utf-8")
    block: str | None = None
    for m in _SUBDIR_BLOCK.finditer(temp):
        if m.group(1) == comp:
            block = m.group(0)
            break
    if block is None:
        return False

    module_names = set(C.modules(cfg))
    sentinel = _MODULES_ANCHOR if comp in module_names else _COMPONENTS_ANCHOR
    if sentinel not in real:
        return False

    idx = real.index(sentinel)
    idx = real.index("\n", idx) + 1
    new_real = real[:idx] + block + real[idx:]
    _textio.write_text(real_path, new_real)
    return True


def _add_umbrella_include(real_path: Path, temp_path: Path, comp: str) -> bool:
    """Insert `#include "comp/comp_core.h"` into the umbrella header.

    Reads *temp_path* to confirm the include line is present in the
    generated output (module objects are NOT in the umbrella, so we skip
    them gracefully).  If the line already exists in *real_path*, or is
    absent from *temp_path*, returns False without touching anything.

    The line is inserted immediately before the final `#endif` so the
    header remains valid C.
    """
    include_line = f'#include "{INC.core_include(comp, real_path)}"'
    real = real_path.read_text(encoding="utf-8")
    if include_line in real:
        return False
    temp = temp_path.read_text(encoding="utf-8")
    if include_line not in temp:
        return False

    # Insert before the last #endif
    last_endif = real.rfind("#endif")
    if last_endif == -1:
        return False
    new_real = real[:last_endif] + include_line + "\n" + real[last_endif:]
    _textio.write_text(real_path, new_real)
    return True


def _sync_aggregates(
    temp_root: Path,
    root: Path,
    cfg: dict,
    *,
    only_mod: str | None = None,
    only_comp: str | None = None,
    honor_status_allow: bool = True,
) -> list[Path]:
    """Reconcile wiring files that already exist on disk and so are
    skipped by _sync_missing but need to absorb newly-materialized
    components: top CMakeLists, umbrella header, package __init__.py,
    and each module's __init__.py / ext.c / CMakeLists / .pyi.

    When *only_comp* is set (e.g. ``--only fir`` where fir lives in the
    dsp module):

    - Root CMakeLists: only *comp*'s single block is inserted (additive);
      other components are left untouched.
    - Umbrella header: only *comp*'s include line is inserted (additive).
    - Module loop: only the module that owns *comp* is processed.

    When *only_mod* is set but *only_comp* is None (e.g. ``--only dsp``):

    - Root CMakeLists: full splice for all spectral-owned components.
    - Umbrella header: full overwrite.
    - Module loop: only the named module is processed.

    Package __init__.py is always merged (it is already additive and safe
    to run unconditionally).
    """
    pkg = C.project_name(cfg)
    updated: list[Path] = []

    real_cmake = root / "CMakeLists.txt"
    temp_cmake = temp_root / "CMakeLists.txt"
    if real_cmake.exists() and temp_cmake.exists():
        if only_comp is not None:
            if _add_cmake_block_for(real_cmake, temp_cmake, only_comp, cfg):
                updated.append(real_cmake)
        else:
            if _splice_cmake_components(real_cmake, temp_cmake, cfg):
                updated.append(real_cmake)
    # Maintain the external-deps sentinel block regardless of --only.
    if real_cmake.exists():
        if _splice_cmake_external_deps(real_cmake, cfg):
            if real_cmake not in updated:
                updated.append(real_cmake)
    # gh-1589: and the managed install block, likewise.
    if real_cmake.exists() and temp_cmake.exists():
        if _splice_root_install(real_cmake, temp_cmake):
            if real_cmake not in updated:
                updated.append(real_cmake)

    umbrella = INC.path(root, f"{pkg}.h")
    temp_umbrella = INC.path(temp_root, f"{pkg}.h")
    if only_comp is not None:
        if umbrella.exists() and temp_umbrella.exists():
            if _add_umbrella_include(umbrella, temp_umbrella, only_comp):
                updated.append(umbrella)
    else:
        if _overwrite_if_changed(umbrella, temp_umbrella):
            updated.append(umbrella)

    pkg_init = root / "src" / pkg / "__init__.py"
    temp_pkg_init = temp_root / "src" / pkg / "__init__.py"
    if pkg_init.exists() and temp_pkg_init.exists():
        if _merge_pkg_init(pkg_init, temp_pkg_init):
            updated.append(pkg_init)

    for mod in C.modules(cfg):
        if C.is_no_generate_module(cfg, mod):
            continue
        if only_mod is not None and mod != only_mod:
            continue
        # gh-286/gh-287/gh-306: a capsule OR composer OR handle module's three
        # glue files (binding, CMake, .pyi) regenerate from the manifest like
        # any other module aggregator. There is no _core.h / object loop /
        # module __init__.py to reconcile — the owning package re-exports the
        # public names via [module.X.reexports]. (Handle uses the same three
        # glue files; the composer's _cli.c is composer-only.)
        if (
            C.is_capsule_module(cfg, mod)
            or C.is_composer_module(cfg, mod)
            or C.is_handle_module(cfg, mod)
        ):
            mp = C.module_paths(mod)
            out_pkg = C.module_package(cfg, mod) or mp.pypath
            glue = [
                f"native/src/{mp.cname}/{mp.cname}_ext.c",
                f"native/src/{mp.cname}/CMakeLists.txt",
                f"src/{pkg}/{out_pkg}/{mp.leaf}.pyi",
            ]
            # gh-287: a composer with the optional c-face CLI also regenerates
            # its <cname>_cli.c (glue).
            from . import _composer

            if C.is_composer_module(cfg, mod) and _composer.composer_cli(
                cfg, mod
            ).get("enabled"):
                glue.append(f"native/src/{mp.cname}/{mp.cname}_cli.c")
            # gh-998: and its published straight-C seams. Gated on the same
            # predicate `materialize` writes the file under — a list here that
            # disagreed with what was written into the temp tree is precisely
            # how gh-942's enumerated source shapes went missing, one at a
            # time, with nothing noticing.
            if C.is_composer_module(cfg, mod) and _composer.render_bridge_h(
                cfg, mod
            ):
                glue.append(INC.rel(f"{mp.cname}/{mp.cname}_bridge.h", root))
            for rel in glue:
                if _overwrite_if_changed(
                    root / rel,
                    temp_root / rel,
                    cfg,
                    rel=rel,
                    honor_status_allow=honor_status_allow,
                ):
                    updated.append(root / rel)
            continue
        # Nested-module forms: cname (flat native dir), pypath (nested Python
        # dir), leaf (.so basename / import). Flat modules collapse all to mod.
        mp = C.module_paths(mod)
        # gh-523: an object module may declare `package` to land its Python
        # artifacts inside a sibling package; unset it is the module's own
        # pypath, so unpackaged modules reconcile exactly as before.
        out_pkg = C.module_package(cfg, mod) or mp.pypath
        # Re-create any intermediate package markers the user may have deleted
        # (create-only — never clobbers a hand-edited marker).
        from ._init import ensure_parent_packages

        for init in ensure_parent_packages(root, pkg, mp, out_pkg):
            updated.append(init)
        # Module subpackage __init__.py — merged so user wrapper classes
        # below the re-exports survive (the gh#1 contract). The import line is
        # `from .<leaf> import ...`, so merge against the leaf.
        from ._object import package_siblings as _pkg_siblings

        mod_init = root / "src" / pkg / out_pkg / "__init__.py"
        temp_mod_init = temp_root / "src" / pkg / out_pkg / "__init__.py"
        if mod_init.exists() and temp_mod_init.exists():
            if _merge_module_init_file(
                mod_init,
                mp.leaf,
                temp_mod_init,
                C.module_reexports(cfg, mod),
                siblings=_pkg_siblings(cfg, mod),
                platforms=_modplatforms.init_platforms(cfg, mod),
            ):
                updated.append(mod_init)
        # The rest of the module wiring is pure-generated.
        for rel in (
            f"native/src/{mp.cname}/{mp.cname}_ext.c",
            f"native/src/{mp.cname}/CMakeLists.txt",
            f"src/{pkg}/{out_pkg}/{mp.leaf}.pyi",
        ):
            if _overwrite_if_changed(
                root / rel,
                temp_root / rel,
                cfg,
                rel=rel,
                honor_status_allow=honor_status_allow,
            ):
                updated.append(root / rel)
        # Module function bodies live in their own sacred <fn>.c (create-only
        # via _sync_missing), so <mod>_core.c is just the include scaffold —
        # also create-only. The module header accumulates function
        # declarations: inject any the manifest implies that are missing,
        # splice-free.
        rel = INC.core_rel(mp.cname, root)
        if _refresh_core_h_decls(root / rel, temp_root / rel, mp.cname):
            if root / rel not in updated:
                updated.append(root / rel)
        # gh-170: each module object's own _core.h gains its depends_on
        # includes (the per-object headers are otherwise sacred / never
        # refreshed on apply).
        from ._init import _inject_includes_into_core_h

        for obj in C.module_objects(cfg, mod):
            obj_h = INC.core_h(root, obj)
            if _inject_includes_into_core_h(
                obj_h,
                obj,
                C.depends_on(cfg, obj),
                extra=C.param_headers(cfg, obj),
                root=root,
            ):
                updated.append(obj_h)
            # gh-271: a non-collocated module object's OBJECT-core CMakeLists is
            # glue, so reconcile it from the canonical replay render — this picks
            # up a *changed* depends_on / extra_link_libs on the object's own
            # _core / test / bench link lines (the old surgical-add path skipped
            # the link block once it already existed, dropping new deps). The
            # reconcile preserves component extra_include_dirs and user external
            # if(VAR) blocks. Collocated objects share the module CMakeLists
            # (handled above).
            if obj != mod:
                obj_cmake = root / "native" / "src" / obj / "CMakeLists.txt"
                temp_cmake = (
                    temp_root / "native" / "src" / obj / "CMakeLists.txt"
                )
                if _reconcile_object_core_cmake(
                    obj_cmake,
                    temp_cmake,
                    obj,
                    list(C.component_extra_include_dirs(cfg, obj)),
                ):
                    updated.append(obj_cmake)

    # Standalone components: the sacred/glue split. Glue files (binding,
    # CMake, type stub) regenerate from the manifest on every apply, so a
    # TOML edit — a new method, init param, extra_link_libs — propagates
    # without a re-scaffold. The sacred sources are never re-rendered: _core.c
    # is create-only (_sync_missing), and _core.h only gains missing
    # declarations (struct + step left alone). A new *state field* is
    # structural and reaches the struct via jm regenerate, not apply.
    module_owned = {
        o for m in C.modules(cfg) for o in C.module_objects(cfg, m)
    }
    for comp in C.components(cfg):
        if comp in module_owned:
            continue
        if only_comp is not None and comp != only_comp:
            continue
        # The temp scaffold was replayed with a trivial header, so its .pyi
        # carries the generic "<Component> component." class summary. If the
        # real (sacred) header enriches create()'s @brief/@param, re-render the
        # temp .pyi from those blocks so apply/status agree with bind/regenerate
        # — otherwise a header-authored class docstring would read as drift. An
        # un-enriched header returns {} and this is skipped entirely (the temp
        # .pyi is already correct and byte-identical), so it costs nothing there.
        from ._object import _load_doc_blocks

        _real_blocks = _load_doc_blocks(root, comp)
        # gh-1117: a standalone object's rendezvous is a CROSS-COMPONENT
        # fact -- which component declared `process_global`, which modules
        # link its core -- and none of that is known while the replay is
        # scaffolding components one at a time. The binding rendered during
        # the replay therefore has an empty block, and this post-replay pass
        # is the first point where the whole manifest exists. Same reason the
        # CMake wiring is reconciled here rather than emitted at scaffold
        # time.
        _pg = _procglobal.rendezvous_c(cfg, comp)
        # gh-1165: the manifest's own `[<comp>] doc` is a third reason to
        # re-render, and it was not one. The trigger above asks whether the
        # HEADER enriched anything, but the render it guards carries the
        # MANIFEST doc too -- `_glue.component_ctx` feeds it to
        # `authored_class_brief`, where it outranks the header's @brief. With
        # a plain header and a manifest doc there were no `_real_blocks`, so
        # the branch was skipped and the temp scaffold's trivial text stood:
        # the value reached NEITHER face, single- or multi-paragraph, and
        # `jm regenerate` did not pick it up either.
        #
        # Safe to widen for exactly the reason gh-805 §F records in `_glue`:
        # the hazard behind this narrow gate is about *header* doc_blocks,
        # which `jm object` renders without and `jm apply` renders with -- so
        # an unconditional re-render would make a fresh scaffold report STALE
        # against itself. A manifest `doc` comes from the manifest, which both
        # paths read alike, so it cannot produce that disagreement.
        #
        # gh-1172 fixed the same omission a layer down: `_object_kwargs` now
        # carries `doc` into the replay, so the temp `.pyi` already has it and
        # this re-render is an identity for a manifest doc alone. It stays
        # because the OTHER two triggers are real, and because a standalone
        # object whose only enrichment is a manifest doc is then covered
        # twice rather than not at all -- which is the direction to be wrong
        # in. `TestTheReplayCarriesTheKey` is what holds the layer below, since
        # every stub assertion stays green while this branch is here.
        _mdoc = (cfg.get(comp) or {}).get("doc", "")
        if _real_blocks or _pg or _mdoc:
            from . import _glue
            from . import _render as _R

            if _real_blocks:
                cfg.setdefault(comp, {})["_doc_blocks"] = _real_blocks
            # gh-676/gh-644: BOTH faces, from the one enriched context. Only
            # the .pyi was re-rendered here, so a standalone object's runtime
            # __doc__ kept the temp scaffold's trivial-header text -- which is
            # why its tp_doc read "<Component> component. Wraps <c>_state_t."
            # however the author documented create(), while the .pyi beside it
            # showed the real brief.
            # gh-994: `temp_root`, not `root` — this render's output goes into
            # the temp tree below, and the question this answers ("did the
            # built-in or a declared method end up owning `<comp>_reset`") is
            # settled by the `_core.c` the replay just wrote there. The real
            # tree's own `_core.c` is not merged until after this loop.
            _ctx = _glue.component_ctx(cfg, comp, pkg, temp_root)
            _ctx["extra_include"] = _glue.standalone_extra_include(root, comp)
            # gh-744: the `.pyi` renders through `render_component_pyi` (which
            # reflows to 79 cols) on this path too. `status --check` compares
            # against what this writes, so rendering it raw here would report
            # every project permanently stale — the gh-635 failure, one file
            # over.
            for _rel, _render in (
                (f"src/{pkg}/{comp}.pyi", _R.render_component_pyi),
                (
                    f"native/src/{comp}/{comp}_ext.c",
                    lambda c: _R.render(_R.COMPONENT_EXT_C, c),
                ),
            ):
                _dst = temp_root / _rel
                if _dst.exists():
                    _textio.write_text(_dst, _render(_ctx))
        # Glue — pure boilerplate, no user content. Overwrite from the
        # freshly-rendered scaffold so manifest edits reach the binding,
        # stub, and build wiring.
        #
        # The CMakeLists is glue with author riders — vendored sources,
        # per-source properties, `if(VAR)` blocks — so it goes through the
        # same reconcile as a module object's (gh-1301), not a blind
        # overwrite.
        _cml = f"native/src/{comp}/CMakeLists.txt"
        if not (honor_status_allow and _status_allowed(cfg, _cml)):
            if _reconcile_object_core_cmake(
                root / _cml, temp_root / _cml, comp, []
            ):
                updated.append(root / _cml)
        for rel in (
            f"native/src/{comp}/{comp}_ext.c",
            f"src/{pkg}/{comp}.pyi",
        ):
            if _overwrite_if_changed(
                root / rel,
                temp_root / rel,
                cfg,
                rel=rel,
                honor_status_allow=honor_status_allow,
            ):
                updated.append(root / rel)
        # _core.h is a hybrid: the inline step() body and the state struct
        # are sacred; the function declarations are glue. Apply injects any
        # TOML-declared prototype the header is missing (a new method/property
        # reaches the public API) without ever re-rendering the struct/step.
        # The body is left to the user — a clean link error until written, or
        # `jm regenerate` for a structural change. _core.c is fully sacred:
        # never in any merge loop, created once by _sync_missing.
        if _refresh_component_core_h(root, temp_root, cfg, comp):
            updated.append(root / INC.core_rel(comp, root))

    # gh-627: a module object's header needs the accessor prototype for a
    # manifest-declared property, or the freshly spliced binding calls an
    # undeclared function. Deliberately NOT the full `_refresh_core_h_decls`
    # the standalone loop above runs: that reconciles *every* declaration, and
    # a module object's header has never been reconciled, so switching it on
    # would replay years of accumulated drift in one apply — measured on
    # doppler as 44 sacred headers and 125 changed declarations, including a
    # `create()` prototype rewritten to disagree with its own definition and
    # every call site. That is a migration, and it needs to be evaluated as
    # one; this is the narrow slice gh-627 actually needs.
    #
    # Additive only: `skip_names` names every prototype being offered, so an
    # accessor the header already declares (with any signature) is left
    # exactly as written and only a genuinely new one is inserted.
    for comp in sorted(module_owned):
        if only_comp is not None and comp != only_comp:
            continue
        if comp not in cfg:
            continue
        core_h = INC.core_h(root, comp)
        if not core_h.exists():
            continue
        # gh-1310: a macro-family member takes the FULL reconcile a standalone
        # header gets. The measured reason the loop below is additive -- years
        # of drift in hand-maintained module headers -- cannot apply to one:
        # its header holds declarations only, jm wrote every one of them, and
        # a template change has to reach them. An additive refresh would keep
        # the `int16_t` prototypes after the family moved to `int32_t`.
        if C.core_family(cfg, comp) is not None:
            if _refresh_component_core_h(root, temp_root, cfg, comp):
                updated.append(core_h)
            continue
        # gh-1302: methods alongside gh-627's accessors. Same loop, same
        # additive rule -- `skip_names` below names every prototype offered,
        # so one the header already declares (with any signature) is left
        # exactly as written and only a genuinely new one is inserted. The
        # measured reason that matters: switching on the full reconcile here
        # would replay 125 changed declarations across doppler's 44 sacred
        # headers, including a `create()` rewritten to disagree with its own
        # definition. Adding a missing declaration is not that.
        decls = _property_accessor_decls(cfg, comp) + _declared_method_decls(
            cfg, comp, temp_root
        )
        if not decls:
            continue
        from ._init import _inject_decls_into_core_h

        names = frozenset(
            re.search(r"(\w+)\s*\(", d).group(1)
            for d in decls
            if re.search(r"(\w+)\s*\(", d)
        )
        # Never a family member: those left the loop above.
        if _inject_decls_into_core_h(
            core_h, comp, decls, skip_names=names, family=None
        ):
            updated.append(core_h)

    updated += _reconcile_procglobal_headers(
        temp_root,
        root,
        cfg,
        only_mod=only_mod,
        only_comp=only_comp,
        honor_status_allow=honor_status_allow,
    )

    return updated


def _reconcile_procglobal_headers(
    temp_root: Path,
    root: Path,
    cfg: dict,
    *,
    only_mod: str | None = None,
    only_comp: str | None = None,
    honor_status_allow: bool = True,
) -> list[Path]:
    """Rewrite each declared ``<comp>_procglobal.h`` from the manifest.

    gh-1140. `_procglobal.render_header` had exactly two callers, both on the
    scaffolding path (`_init.run`, `_object.run`), so the file was written
    once and never maintained. Both of them, and `render_header` itself, said
    in a comment that `apply`'s glue list was gated on the same call — the
    list did not exist. The cost was measured in doppler: gh-1134 corrected
    the import path the rendezvous uses, `apply` carried the fix into every
    generated ``_ext.c``, and the copy of that same path in this header stayed
    as it was. A ``no_generate`` module — the case gh-1128 published the
    header *for* — then adopted through the stale macro and failed at import
    exactly as before, for 100 collection errors under a pin bump whose whole
    subject was that line.

    Reconciled from the replayed tree, like the composer's ``_bridge.h``
    (gh-998) and for the same reason: the two scaffold writers are the one
    place that decides this file's bytes, so a second renderer here is a peer
    implementation waiting to drift from them.

    That the replay's render is the *finished* one is an assumption, and it is
    checked rather than defended against — `TestApplyMaintainsTheHeader`
    compares what `apply` leaves on disk against `render_header` over the
    project's own manifest, so a scaffold-time render that turned out to be
    partial would be named by a failing test instead of quietly papered over
    by a third writer. Measured before relying on it: over every `apply`,
    `status` and process-global test in the suite, the replay's bytes and the
    whole-manifest render were identical every time.

    The file has no hand-owned half to preserve — three ``#define``s and two
    prototypes over names jm invents — so it is overwritten outright, which is
    what makes its ``DO NOT EDIT`` banner true. ``status_allow`` is honoured
    like any other glue: a project that has taken the file over keeps it.

    A component that *stops* declaring ``process_global`` has no header in the
    replayed tree, so this skips it and the one on disk is left untouched — jm
    does not delete files on `apply`, and the stale prototypes still compile
    against the accessors the author wrote. That is gh-1142.
    """
    updated: list[Path] = []
    for comp in C.components(cfg):
        if only_comp is not None:
            if comp != only_comp:
                continue
        elif only_mod is not None:
            if comp not in C.module_objects(cfg, only_mod):
                continue
        rel = INC.rel(_procglobal.header_name(comp), root)
        if not (temp_root / rel).is_file():
            continue
        if _overwrite_if_changed(
            root / rel,
            temp_root / rel,
            cfg,
            rel=rel,
            honor_status_allow=honor_status_allow,
        ):
            updated.append(root / rel)
    return updated


def _declared_method_decls(cfg: dict, comp: str, temp_root: Path) -> list[str]:
    """Method prototypes for *comp*, read off the temp render.

    gh-1302, the sibling of gh-627's accessor slice. A module object's
    manifest-declared method got a call in the fragment and a prototype in
    **no** header, so the freshly spliced binding called an undeclared
    function and the build failed on implicit declaration — the standalone
    path put the same prototype in `<comp>_core.h` all along.

    The text comes from the temp tree's header rather than from a second call
    to `_build_method_prototype`. That builder takes twenty-odd keys off the
    declaration and `_apply._replay_method` already passes all of them; a
    second call site would be a second place to forget one, which is exactly
    how gh-788 put the wrong `create()` shape into a sacred header.

    Which names to offer comes from the manifest, so an unrelated declaration
    in the temp header is not swept along. `variable_output` methods declare a
    sibling `_max_out`, and it is as undeclared as its method.
    """
    temp_h = INC.core_h(temp_root, comp)
    if not temp_h.exists():
        return []
    from ._init import _core_h_decl_lines

    want: set = set()
    for m in C.methods(cfg, comp):
        fn = C.method_c_symbol(CSYM.stem(cfg, comp), m)
        want.add(fn)
        want.add(f"{fn}_max_out")
    # gh-1509: the binding calls the serializable triplet the same way, and
    # until then no path declared it in any header.
    want |= _serializable_triplet(cfg, comp)
    out = []
    for d in _core_h_decl_lines(temp_h.read_text(encoding="utf-8")):
        m = re.search(r"(\w+)\s*\(", d)
        if m and m.group(1) in want:
            out.append(d)
    return out


def _property_accessor_decls(cfg: dict, comp: str) -> list[str]:
    """Getter/setter prototypes for *comp*'s plainly-backed properties.

    Only properties with no other backing: a ``field`` lives in the struct, an
    ``expr`` is inline, a ``buf_field`` is a buffer view, and a container /
    codec property's accessors are the user's to declare (see
    ``_property.run``). Those shapes declare nothing here, so offering a
    prototype for them would be wrong rather than merely redundant.
    """
    from . import _types as _T
    from ._property import plain_accessor_decls

    out: list[str] = []
    for p in C.properties(cfg, comp):
        if (
            p.get("field")
            or p.get("expr")
            or p.get("buf_field")
            or p.get("codec")
            or p.get("value_fn")
            or p.get("count_fn")
            # gh-788: a capsule property reads a pointer it already has —
            # pure glue, like expr/buf_field, so nothing is declared.
            or p.get("capsule")
        ):
            continue
        ctype = p.get("type", "")
        if ctype not in _T._CTYPE_META:
            continue  # dict/list/tuple and friends are declared by the user
        out += plain_accessor_decls(
            comp,
            p["name"],
            ctype,
            str(p.get("writable", "")).lower() in ("true", "1", "yes"),
            csym=CSYM.stem(cfg, comp),
        )
    return out


def _refresh_component_core_h(
    root: Path, temp_root: Path, cfg: dict, comp: str
) -> bool:
    """Bring *comp*'s sacred ``_core.h`` up to date with the manifest.

    ``_core.h`` is a hybrid: the inline ``step()`` body and the state struct
    are sacred, the function declarations are glue. This injects any
    TOML-declared prototype the header is missing (so a new method or property
    reaches the public C API) without ever re-rendering the struct or step.
    The *body* stays the user's problem — a clean link error until written, or
    ``jm regenerate`` for a structural change.

    **Standalone components only.** A module object's header gets the narrow,
    additive accessor injection above instead (gh-627): this reconciles every
    declaration, and a module header has never been reconciled, so running it
    there would replay years of accumulated drift as a side effect of a
    property fix. Extending it to module objects is a migration in its own
    right. Returns True when the file changed.
    """
    rel = INC.core_rel(comp, root)
    changed = _refresh_core_h_decls(
        root / rel, temp_root / rel, comp, C.core_family(cfg, comp)
    )
    # gh-170: also inject `#include "<dep>/<dep>_core.h"` for each depends_on
    # entry, so opaque fields of a dependency's types compile.
    from ._init import _inject_includes_into_core_h

    if _inject_includes_into_core_h(
        root / rel,
        comp,
        C.depends_on(cfg, comp),
        extra=C.param_headers(cfg, comp),
        root=root,
    ):
        changed = True
    return changed


def missing_core_definitions(
    rendered: str, existing: str, siblings: "list[str]" = ()
) -> list:
    """Function names *rendered* defines that *existing* and *siblings* do not.

    gh-1294. One comparison with two callers: `apply` splices the bodies in,
    `status` gates on the ones it cannot. Two copies would drift the usual
    way — `apply` taught about a new shape, `status` quietly still wrong —
    and the whole point of the gate is that it agrees with what `apply` does.

    *siblings* are the other ``.c`` files the component owns. gh-275: a
    component's OBJECT lib may compile sources besides ``<comp>_core.c``, so
    a definition can legitimately live next door, and reporting it missing
    would be a false positive on doppler's `fft_core`.
    """
    from ._object import _extract_c_function_bodies

    ref = _extract_c_function_bodies(rendered, require_static=False)
    return [
        n
        for n in ref
        if not any(_defines(src, n) for src in (existing, *siblings))
    ]


def _defines(source: str, name: str) -> bool:
    """Whether *source* defines *name* — read tolerantly of formatting.

    NOT `_extract_c_function_bodies`, which is the obvious reuse and is
    wrong here. That function wants `<returntype>\n<name>(` on adjacent
    lines, so it does not see

        void o_steps(
            o_state_t *state, ...)

    -- jm's own multi-line render. It happens not to matter when both sides
    are read with it, because both are blind identically. The moment they are
    not, the asymmetry appends a SECOND definition of a symbol that was
    already there: measured by reformatting one signature onto a single line,
    after which `apply` wrote a duplicate `o_create` and the project stopped
    linking. Any `c_style` project runs a formatter over this file.

    So the question is asked the way the compiler asks it, on masked source so
    a mention in a comment or a string cannot answer it: the name, an argument
    list, and a body brace rather than a `;`.
    """
    from ._docsync import _code_mask

    return bool(
        re.search(
            rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
            _code_mask(source),
            re.S,
        )
    )


def component_core_sources(root: Path, comp: str) -> "list[str]":
    """Every source that can DEFINE one of the component's symbols.

    Every ``.c`` the component owns except its own ``_core.c`` (gh-275: an
    OBJECT lib may compile sources besides it, so a definition can legitimately
    live next door) -- **and the sacred ``_core.h``** (gh-1328).

    The header is not an afterthought here, it is the case that bit. A
    function may be defined ``static inline`` in the header and never appear
    in ``_core.c`` at all: `step` is jm's own example, and an author may do the
    same for anything. Asking only the ``.c`` files reports such a symbol
    MISSING, and gh-1294's splice then appends a placeholder definition into a
    file jm's contract says it never writes.

    doppler measured it on 0.76.0: `cic_decimate` (defined inline at
    `cic_core.h:278`) and `dp_tlm_set_now` both got empty placeholder bodies
    appended to their sacred ``_core.c``. A redefinition error was the LUCKY
    outcome -- move the real definition to another TU and the placeholder
    links, and a decimator silently returns 0 forever with nothing in the tree
    pointing at jm.

    `_method.already_provides` has stated this rule since gh-994 -- *"reads the
    header as well as the source"* -- and gh-1294 did not carry it across. It
    is one list now so a third reader cannot get it wrong either.
    """
    d = root / "native" / "src" / comp
    out = [
        p.read_text(encoding="utf-8")
        for p in sorted(d.glob("*.c"))
        if p.name != f"{comp}_core.c"
    ]
    hdr = INC.core_h(root, comp)
    if hdr.is_file():
        out.append(hdr.read_text(encoding="utf-8"))
    return out


def _serializable_triplet(cfg: dict, comp: str) -> frozenset:
    """The serializable triplet's C names, when *comp* declares one.

    gh-1509 made a fresh scaffold define these three in ``_core.c``. They are
    NOT spliced into an existing one, unlike a declared method's body: before
    gh-1509 the triplet was the author's to write, so every serializable
    object that builds already has one -- and a project may define it where
    no source reader can see a definition. doppler does, in 18 cores, through
    ``DP_DEFINE_POD_STATE(boxcar, ...)``; splicing there appended a second
    definition of each (measured over doppler's tree). A body that really is
    missing still fails loudly: at link, named by gh-1361's link check.
    """
    if not C.is_serializable(cfg, comp):
        return frozenset()
    return frozenset(
        f"{CSYM.stem(cfg, comp)}_{n}"
        for n in ("state_bytes", "get_state", "set_state")
    )


def _splice_missing_core_definitions(
    root: Path, temp_root: Path, cfg: dict
) -> list:
    """Append a declared method's missing ``_core.c`` body (gh-1294).

    A method declared in the manifest gets a prototype in ``_core.h`` and a
    call in the binding. Declared through the CLI it also gets a stub body;
    declared in TOML and materialised by ``apply`` it did not — so the
    extension did not **link**, while ``apply`` printed "Project already
    matches just-makeit.toml — nothing to do" and ``status --check`` returned
    0. TOML is the documented way to declare a multi-method component, so the
    route jm recommends was the one that did not build.

    **Additive, never a re-render**, which is the rule ``_core.c`` has always
    had: gh-541 already patches it in place (``void`` -> ``int`` on a
    ``destroy`` that declares a status) on the same licence, and ``_core.h``
    "only ever gains missing declarations". A body that exists is untouched;
    this only ever appends one that does not.

    **The text is the temp tree's, not a second emitter.** ``apply`` already
    replays every declared method into its throwaway scaffold to build the
    reference, so the stub jm would write is on disk there, produced by the
    same four-way shape dispatch ``jm method`` uses. Re-deriving it here would
    be a fifth copy of that dispatch, which is how the four in
    ``_build_method_prototype`` came about.

    **What stops a duplicate symbol.** gh-275: a component's OBJECT lib may
    compile sources besides ``<comp>_core.c`` (doppler's ``fft_core`` pulls in
    pocketfft), so a definition can legitimately live in a sibling file.
    Every ``.c`` in the component's own directory is read before deciding,
    and a symbol found in any of them is left alone. When the CMake names
    hand-owned sources AND the symbol is nowhere in the directory, jm warns
    instead of appending: the author has put their sources somewhere jm
    cannot enumerate, and a wrong guess here is a duplicate definition, which
    is worse than the missing one.
    """
    from ._object import _extract_c_function_bodies

    changed: list = []
    mods = C.modules(cfg)
    module_owned = {o for m in mods for o in C.module_objects(cfg, m)}
    comps = [c for c in C.components(cfg) if c not in module_owned]
    comps += [o for m in mods for o in C.module_objects(cfg, m)]

    for comp in comps:
        real_dir = root / "native" / "src" / comp
        core_c = real_dir / f"{comp}_core.c"
        temp_c = temp_root / "native" / "src" / comp / f"{comp}_core.c"
        if not core_c.exists() or not temp_c.exists():
            continue
        temp_text = temp_c.read_text(encoding="utf-8")
        ref = _extract_c_function_bodies(temp_text, require_static=False)
        missing = [
            n
            for n in missing_core_definitions(
                temp_text,
                core_c.read_text(encoding="utf-8"),
                component_core_sources(root, comp),
            )
            if n not in _serializable_triplet(cfg, comp)
        ]
        if not missing:
            continue
        cmake = real_dir / "CMakeLists.txt"
        _extra = (
            _object_core_extra_sources(cmake.read_text(encoding="utf-8"), comp)
            if cmake.exists()
            else []
        )
        if _extra:
            _report.warn(
                f"native/src/{comp}/{comp}_core.c does not define "
                f"{', '.join(f'{n}()' for n in missing)}, which the manifest "
                "declares and the binding calls — the extension will not "
                f"link. {comp}_core also compiles "
                f"{', '.join(_extra)}, so the body may live somewhere jm "
                "cannot enumerate and appending it could define the symbol "
                "twice -- write it yourself, or say where by putting it in "
                f"native/src/{comp}/.",
                gates=True,
            )
            continue
        # With its `/* <<IMPLEMENT: … >> */` marker, which is load-bearing
        # rather than decorative: `already_provides` reads it through
        # `_is_method_stub` to tell jm's own stub from a built-in body
        # (gh-994), so a body spliced without one would make a later
        # `jm method` of the same name decide a built-in already provides it
        # and skip. `_leading_comment_start` is the primitive `_docsync`
        # already uses for "the comment immediately above this".
        from ._docsync import _leading_comment_start

        bodies = []
        for n in missing:
            at = temp_text.index(ref[n])
            bodies.append(
                temp_text[_leading_comment_start(temp_text, at) : at]
                + ref[n].rstrip("\n")
                + "\n"
            )
        text = core_c.read_text(encoding="utf-8")
        _textio.write_text(
            core_c, text.rstrip("\n") + "\n\n" + "\n".join(bodies)
        )
        changed.append((core_c, missing))
    return changed


def _reconcile_bench_cmake(root: Path, cfg: dict) -> list[Path]:
    """Append a missing bench_*_core CMake target to each component CMakeLists.

    Existing projects that were scaffolded before the bench target was added
    to the template will have the bench source file but no CMake target.  This
    is idempotent: if the target is already present the file is not touched."""
    from . import _render as R

    updated: list[Path] = []
    for comp in C.components(cfg):
        cmake_path = root / "native" / "src" / comp / "CMakeLists.txt"
        if not cmake_path.exists():
            continue
        text = cmake_path.read_text(encoding="utf-8")
        if f"bench_{comp}_core" in text:
            continue
        # gh-1305: libm by path, never the bare name `m`, which resolves
        # as a CMake TARGET first and so is shadowed by a module named
        # `m`. This file is APPENDED to rather than re-rendered, so it may
        # predate the preamble that declares the variable -- and a
        # reference to an undeclared variable expands to nothing and links
        # no libm at all, which is the same broken build by a quieter
        # route. Seed it here when absent.
        if "JM_MATH_LIBRARY" not in text:
            text = R.LIBM_PREAMBLE + "\n" + text
        bench_block = (
            f"\nadd_executable(bench_{comp}_core\n"
            f"    ${{CMAKE_SOURCE_DIR}}/native/benchmarks/"
            f"bench_{comp}_core.c)\n"
            f"target_link_libraries(bench_{comp}_core"
            f" PRIVATE {comp}_core " + R.LIBM_REF + ")\n"
            f"target_include_directories(bench_{comp}_core\n"
            f"    PRIVATE {INC.CMAKE_INC}\n"
            f"            ${{CMAKE_SOURCE_DIR}}/native/benchmarks)\n"
        )
        _textio.write_text(cmake_path, text.rstrip() + bench_block)
        updated.append(cmake_path)
    return updated


_INCLUDE_LINE = 'include = ["objects/*.toml"]\n'


def _wire_module_object(manifest: Path, mod_name: str, comp: str) -> bool:
    """Append *comp* to the `objects = [...]` line of [module.mod_name] in
    *manifest*. Returns True if the file was modified.

    Uses a targeted in-place text edit so fragment files (which may contain
    `impl` bodies not tracked by `_dump`) are never touched."""
    text = manifest.read_text(encoding="utf-8")
    pat = re.compile(
        rf"(\[module\.{re.escape(mod_name)}\][^\[]*?"
        rf"objects\s*=\s*\[)([^\]]*)\]",
        re.DOTALL,
    )
    m = pat.search(text)
    if not m:
        return False
    existing = [
        s.strip().strip('"')
        for s in m.group(2).split(",")
        if s.strip().strip('"')
    ]
    if comp in existing:
        return False
    items = existing + [comp]
    new_list = ", ".join(f'"{x}"' for x in items)
    _textio.write_text(
        manifest, text[: m.start(2)] + new_list + text[m.end(2) :]
    )
    return True


def _validate_fragment_impl_keys(fragment: dict, label: str) -> None:
    """Check impl/impl_file mutual-exclusion on every section in *fragment*
    before any side-effects happen.  Covers impl, create_impl, reset_impl,
    and destroy_impl pairs."""
    _impl_pairs = [
        ("impl", "impl_file"),
        ("create_impl", "create_impl_file"),
        ("reset_impl", "reset_impl_file"),
        ("destroy_impl", "destroy_impl_file"),
    ]
    for key, value in fragment.items():
        if key in ("project", "module", "include"):
            continue
        if not isinstance(value, dict):
            continue
        for ik, ifk in _impl_pairs:
            if value.get(ik) and value.get(ifk):
                raise ValueError(
                    f"{label}: object {key}: `{ik}` and `{ifk}` are "
                    f"mutually exclusive — set one or the other."
                )
        opaque_state = [s for s in value.get("state", []) if s.get("opaque")]
        if opaque_state and not (
            value.get("create_impl") or value.get("create_impl_file")
        ):
            names = ", ".join(s.get("name", "?") for s in opaque_state)
            raise ValueError(
                f"{label}: object {key}: opaque state field(s) [{names}] "
                f"require `create_impl` or `create_impl_file` to initialize "
                f"them — the auto-generated create() would leave them "
                f"uninitialized."
            )
        for m in value.get("methods", []):
            if m.get("impl") and m.get("impl_file"):
                raise ValueError(
                    f"{label}: {key}.{m.get('name', '?')}: `impl` and "
                    f"`impl_file` are mutually exclusive."
                )


def _fragment_already_included(root: Path, fragment_path: Path) -> bool:
    """Return True when *fragment_path* is already covered by the manifest's
    ``include`` glob — i.e. the file is in place and the loader already sees
    its objects in the merged config.  This is the normal state after a user
    manually copies a fragment into ``objects/`` before running ``jm apply``."""
    manifest = C.load_manifest(root)
    includes = manifest.get("include")
    if not includes:
        return False
    fragment_resolved = fragment_path.resolve()
    return any(
        p.resolve() == fragment_resolved
        for p in C._resolve_includes(root, includes)
    )


def _compose_fragment(root: Path, fragment_path: Path) -> Path:
    """Validate *fragment_path*, copy it into `objects/`, and ensure the
    manifest's `include` glob covers it. Returns the destination path.

    Errors with the design-specified remedy if the fragment declares an
    object that the project already has.

    If the fragment is already on disk under the existing include glob (e.g.
    the user placed it in ``objects/`` manually before running ``jm apply``),
    the copy and conflict-check steps are skipped and the command proceeds
    directly to materialization — identical to running bare ``jm apply``.

    If a component section carries `module = "X"`, the component is wired
    into `[module.X].objects` in the manifest so `_replay` routes it to the
    module directory instead of generating standalone files."""
    if not fragment_path.exists():
        raise FileNotFoundError(f"fragment not found: {fragment_path}")

    with fragment_path.open("rb") as f:
        fragment = tomllib.load(f)

    already_included = _fragment_already_included(root, fragment_path)

    if not already_included:
        # Test-merge against the current resolved cfg to surface conflicts
        # before we touch any files. _merge_fragment raises ValueError naming
        # the conflicting object and the recommended remedy.
        # Skip when already_included: the "conflict" is the glob loading the
        # fragment itself, which is the expected state.
        C._merge_fragment(C.load(root), fragment, fragment_path)
    _validate_fragment_impl_keys(fragment, str(fragment_path))

    # Collect module-routing directives and validate before side-effects.
    module_directives: list[tuple[str, str]] = []
    for key, value in fragment.items():
        if key in ("project", "module", "include") or not isinstance(
            value, dict
        ):
            continue
        mod_name = value.get("module")
        if isinstance(mod_name, str) and mod_name:
            module_directives.append((key, mod_name))
    if module_directives:
        known_mods = C.modules(C.load_manifest(root))
        for comp, mod_name in module_directives:
            if mod_name not in known_mods:
                raise ValueError(
                    f"object '{comp}' declares module='{mod_name}' but "
                    f"[module.{mod_name}] is not in {C.FILENAME}. "
                    f"Defined modules: {known_mods or ['(none)']}."
                )

    objects_dir = root / "objects"
    objects_dir.mkdir(exist_ok=True)
    dest = objects_dir / fragment_path.name
    src_resolved = fragment_path.resolve()
    if dest.resolve() != src_resolved:
        if dest.exists():
            raise FileExistsError(
                f"{dest} already exists. Move or rename the incoming "
                f"fragment, or `jm remove` the object first."
            )
        shutil.copy2(fragment_path, dest)
        print(f"  copy    {fragment_path} -> {dest}")

    # Ensure `include = ["objects/*.toml"]` is present at the top of the
    # manifest. Phase 1 does a minimal targeted text edit; the
    # format-preserving multi-file writer arrives with the provenance
    # work in Phase 2.
    manifest = root / C.FILENAME
    text = manifest.read_text(encoding="utf-8")
    if "include" not in C.load_manifest(root):
        _textio.write_text(manifest, _INCLUDE_LINE + "\n" + text)
        print(f'  update  {manifest}  (include = ["objects/*.toml"])')

    for comp, mod_name in module_directives:
        if _wire_module_object(manifest, mod_name, comp):
            print(f"  update  {manifest}  ([module.{mod_name}])")

    return dest


def _dangling_object_fragments(root: Path, cfg: dict) -> list[str]:
    """Objects defined in the manifest, referenced by no module, whose native
    sources are still in *module-object* shape (gh-327).

    An object section listed in no ``[module.X].objects`` is normally a
    standalone object — apply gives it its own ``.so``. But when an object is
    *removed* from its module and its ``objects/<obj>.toml`` fragment is left
    behind, it looks identical to a standalone object, so apply silently
    promotes it: it scaffolds a standalone module *over* the object's existing
    native dir, clobbering any hand-written ``<obj>_core`` lib that lived there
    (doppler's ``ddcr_core`` composed vendored sources — apply overwrote its
    CMakeLists).

    The two are distinguishable on disk: a real standalone object's
    ``native/src/<obj>/CMakeLists.txt`` builds its own extension
    (``Python3_add_library(<obj> MODULE …)``); a module object's carries only
    the ``<obj>_core`` OBJECT lib (its ``.so`` is the module's). So an object
    referenced by no module whose existing CMakeLists lacks that extension
    target is a *dangling* former-module fragment, not a standalone object.

    A brand-new standalone object (no native dir yet) has nothing to clobber and
    is materialized normally — only an existing module-shaped dir trips this.
    """
    module_owned = {
        o for m in C.modules(cfg) for o in C.module_objects(cfg, m)
    }
    dangling: list[str] = []
    for comp in C.components(cfg):
        if comp in module_owned:
            continue
        cml = root / "native" / "src" / comp / "CMakeLists.txt"
        if not cml.exists():
            continue
        text = cml.read_text(encoding="utf-8")
        if not re.search(
            rf"Python3_add_library\(\s*{re.escape(comp)}\s+MODULE", text
        ):
            dangling.append(comp)
    return dangling


def run(
    root: Path,
    fragment: Path | None = None,
    only: str | None = None,
    *,
    honor_status_allow: bool = True,
    replay_out: Path | None = None,
) -> None:
    """Reconcile *root* against its manifest.

    ``replay_out`` — gh-949. When given, the manifest replay (jm's current
    render of every file, formatted to the project's own style) is copied
    there before anything in *root* is touched, and survives this call. Only
    `status` uses it, to compare create-only files that `apply` itself will
    never rewrite; passing it does not change what `apply` does.
    """
    # gh-823: apply is the command where these findings are actionable, so
    # it is the one that counts them and says how many matter. Reset here
    # rather than at import: a process running apply twice (the test suite
    # does, constantly) must not accumulate.
    _report.reset()

    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first, or author a manifest to apply.",
            file=sys.stderr,
        )
        sys.exit(1)

    if fragment is not None:
        print(f"just-makeit: composing fragment {fragment}")
        try:
            _compose_fragment(root, fragment)
        except (FileNotFoundError, FileExistsError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        print()

    cfg = C.load(root)
    # gh-1310: a family member whose family header is missing, before anything
    # is written -- jm cannot write that file, and every member would render
    # an `#include` of nothing.
    _missing = missing_family_headers(root, cfg)
    if _missing:
        for _comp, _hdr in _missing:
            _fam = C.core_family(cfg, _comp)
            print(
                f"error: `{_comp}` takes its definitions from"
                f" {_fam.macro}(...), and {INC.INC_DIR}/{_hdr} does not exist."
                f" Write it, defining {_fam.macro} with"
                f" {len(_fam.args)} argument(s) -- jm writes each member's"
                " declarations and the invocation, never the family header.",
                file=sys.stderr,
            )
        sys.exit(1)
    # gh-1117: refuse a `process_global` jm cannot make true, before anything
    # is written. A declaration that generated nothing and said nothing would
    # be gh-1118 in a new place -- a key read, accepted, silently doing
    # nothing -- and the thing it silently fails to do is the one the author
    # declared it for.
    try:
        _procglobal.validate(cfg)
    except _procglobal.ProcGlobalRefusal as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    # gh-1578: the external-deps readers refuse a malformed entry. Read them
    # here, before the stamp below or any other write, so a refusal leaves
    # the tree exactly as it was rather than half-applied.
    C.find_package_entries(cfg)
    C.pkg_module_entries(cfg)
    C.public_link_libs(cfg)
    C.public_defines(cfg)
    # gh-1128: an ADOPTER jm cannot generate into is reported, not refused.
    # Every other module still shares one state; this one keeps its own copy
    # until its author adds the adopt to the binding they already write, and
    # `<comp>_procglobal.h` now carries the three names that needs.
    for _comp, _mod in _procglobal.hand_written_adopters(cfg):
        _report.warn(
            f"module '{_mod}' links {_comp}_core and is `no_generate`, so jm"
            f" writes no PyInit_ there: it keeps its OWN copy of {_comp}'s"
            f" process-global state while every other module shares one."
            f" Add the adopt to its hand-written binding —"
            f" {INC.rel(_procglobal.header_name(_comp), root)} shows it, with the"
            f" owner, attribute and capsule names as #defines.",
            gates=False,
        )
    # gh-183: record the generating jm version (monotonic; surgical write).
    _stamped = C.stamp_jm_version(root, cfg)
    if _stamped:
        print(f"  stamp   {C.FILENAME}  [project] jm_version = {_stamped}\n")
    if not C.components(cfg) and not C.modules(cfg):
        print(
            "error: manifest declares no objects or modules — nothing to materialize.",
            file=sys.stderr,
        )
        sys.exit(1)

    # gh-595 / gh-598: refuse to generate from a manifest declaring a type no
    # binding can convert. Left unchecked neither failed — an unknown
    # return_type produced a binding that dropped the C result and returned
    # None, and an unmapped result-field type reached Py_BuildValue under an
    # "i" with no cast, truncating wide values. Both compiled cleanly. The CLI
    # front-ends have always rejected the same spellings; this closes the gap
    # on the TOML path, which is the one the manifest-as-SSOT workflow uses.
    type_errors = C.manifest_type_errors(cfg)
    if type_errors:
        print(
            f"error: unsupported type in {C.FILENAME}:\n"
            + "\n".join(type_errors),
            file=sys.stderr,
        )
        sys.exit(1)

    # gh-327: refuse to silently promote a former module object — whose
    # fragment was left behind after `objects = [...]` dropped it — into a
    # standalone module over its existing (possibly hand-owned) native dir.
    # Scoped to a full apply; `--only X` never touches unrelated objects.
    if only is None:
        dangling = _dangling_object_fragments(root, cfg)
        if dangling:
            lines = [
                f"error: object '{o}' is defined but not listed in any "
                f"[module.X].objects, and native/src/{o}/ already holds a "
                f"module-style core lib.\n"
                f"  Promoting it to a standalone module would overwrite those "
                f"files. Resolve by either:\n"
                f"    - add '{o}' back to a module's objects list, or\n"
                f"    - remove the objects/{o}.toml fragment (and its "
                f"native/src/{o}/ dir) if the object is gone."
                for o in dangling
            ]
            print("\n".join(lines), file=sys.stderr)
            sys.exit(1)

    # Resolve --only to (only_mod, only_comp).  A module name produces
    # only_mod with only_comp=None (full splice for that module).  A
    # component name produces only_comp plus the owning module (or None for
    # standalone components).
    only_mod: str | None = None
    only_comp: str | None = None
    if only is not None:
        mods = C.modules(cfg)
        comps = C.components(cfg)
        if only in mods:
            only_mod = only
        elif only in comps:
            only_comp = only
            only_mod = C.component_module(cfg, only)
        else:
            print(
                f"error: --only: '{only}' is not a known module or "
                f"component in {C.FILENAME}",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"just-makeit: applying {C.FILENAME}")
    print()

    # gh-1474: before anything below writes, so the report can be decided by
    # the bytes each file ends with rather than by who wrote to it.
    _before = _tree_digests(root)

    from . import _object as _obj_mod

    with tempfile.TemporaryDirectory(prefix="jm-apply-") as tmp:
        temp_root = Path(tmp) / C.project_name(cfg)
        # The generators print progress for the throwaway temp tree; that
        # output names temp paths and would only confuse the user.
        try:
            # The replay's scopes and why each is there: `replay_project`.
            replay_project(cfg, temp_root, root)
        except (ValueError, FileNotFoundError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        # gh-493: reformat the throwaway scaffold to the project's house style
        # *before* it is compared against the real tree, so a c_style project's
        # on-disk (formatted) *_ext.c glue matches the freshly rendered glue
        # instead of reading as perpetual drift on every `apply`/`status`. Only
        # *_ext.c is touched (see _cfmt._generated_c_files); sacred sources are
        # unformatted on both sides and already compare equal. No-op unless
        # c_style is set, and a soft no-op if clang-format is absent (both
        # sides then stay jm-style, still equal). The real project's
        # .clang-format decides the layout on both sides, so it is what the
        # temp tree must hold *for the pass*: a project that has edited its
        # style would otherwise have its glue formatted two ways and read as
        # perpetual drift.
        #
        # gh-960: the replay now renders a `.clang-format` of its own, which
        # is the whole point — it is what makes an out-of-date one visible to
        # `status`. So jm's render is put back afterwards, before the snapshot
        # the create-only comparison is taken from. The pass sees the
        # project's file; the comparison sees jm's.
        #
        # A project that formats its C and ships no style file keeps jm's
        # render for the pass. Its glue reads as stale for exactly one run —
        # `_sync_missing` below writes the style file and `_sync_aggregates`
        # rewrites the glue to match it, so the two converge together rather
        # than the style file landing first and the glue going stale after.
        from . import _cfmt

        real_cf = root / ".clang-format"
        temp_cf = temp_root / ".clang-format"
        rendered_cf = temp_cf.read_bytes() if temp_cf.is_file() else None
        if C.c_formatting_on(cfg) and real_cf.is_file():
            shutil.copy2(real_cf, temp_cf)
        _cfmt.format_project(temp_root, cfg, quiet=True)
        if rendered_cf is not None:
            temp_cf.write_bytes(rendered_cf)
        # gh-746: the same symmetry for generated Python. Formatting the real
        # tree but not the tree it is compared against is what makes a drift
        # gate unclearable (gh-635); both sides run the same command.
        from . import _pyfmt as _pyfmt_mod

        _pyfmt_mod.format_project(temp_root, cfg, quiet=True)
        # gh-949: hand the finished replay back to the caller. It is jm's
        # current render of the whole project — the only tree that knows what
        # a create-only file *would* look like today — and it dies with the
        # `with` block, so `status` cannot compute OUTDATED without it.
        #
        # Snapshotted here, after the formatting passes (so both sides have
        # been through the project's own style) and before `_sync_missing`,
        # which is the first step to touch the real tree. A caller that
        # diffs a post-reconcile snapshot would be comparing against a tree
        # some of whose files it had just written itself.
        if replay_out is not None:
            shutil.copytree(temp_root, replay_out, dirs_exist_ok=True)
        # gh-975: measured against the same snapshot, and before the splices
        # below run — a splice with no anchor writes nothing and reports
        # "unchanged", which is what made the loss silent. Printed after the
        # created/updated lines so it is the last thing on screen.
        unanchored = _createonly.missing_anchors(root, temp_root)
        # gh-1185: the reconcile phase refuses too, and its refusals are
        # manifest questions — the stub splice will not write a file that
        # would lose hand-owned content (gh-765/gh-1092). Only the REPLAY was
        # inside a handler, so those arrived as a stack trace: a traceback for
        # something the author can fix by editing a TOML line, with the
        # diagnostic jm had carefully written buried at the bottom of it.
        try:
            _owned = _owned_fragments(root, cfg)
            _refuse_owned_that_would_lose(temp_root, root, _owned)
            _scaffolds = _owned_scaffolds(temp_root, root)
            created = _sync_missing(temp_root, root, _owned | _scaffolds)
            impl_patched = _patch_step_impls(root, cfg)
            # gh-541: promote an already-scaffolded component's sacred
            # destructor to `int` when the manifest now declares it fallible.
            # Must run before _sync_aggregates writes the glue that calls it.
            impl_patched += _patch_destroy_signatures(root, cfg)
            updated = _sync_aggregates(
                temp_root,
                root,
                cfg,
                only_mod=only_mod,
                only_comp=only_comp,
                honor_status_allow=honor_status_allow,
            )
        except ValueError as e:
            # Same shape as the replay's handler above: the message is
            # already written for a reader, so print it and stop rather than
            # decorating a stack trace with it.
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)

        # gh-1294: a declared method with no body in `_core.c`. INSIDE the
        # `with`, because the stub text is the temp tree's and that tree is a
        # TemporaryDirectory -- placed after it, `temp_root` no longer exists
        # and the splice silently found nothing to do, which is the same
        # silence the issue is about.
        core_spliced = _splice_missing_core_definitions(root, temp_root, cfg)
        for _p, _names in core_spliced:
            _rel_c = _p.relative_to(root) if _p.is_absolute() else _p
            print(
                f"  update  {_rel_c}: scaffolded "
                f"{', '.join(f'{n}()' for n in _names)}"
            )

    bench_updated = _reconcile_bench_cmake(root, cfg)

    # Refresh runtime __doc__ in per-object binding fragments
    # (<mod>_ext_<obj>.c). _sync_aggregates reconciles the module
    # aggregator/.pyi/CMake but not these sacred fragments, so a header Doxygen
    # edit reaches the .pyi while the runtime PyMethodDef / tp_doc / PyGetSetDef
    # docs keep the stale scaffold fallback. _docsync transplants only the
    # doc-string slots into the existing fragment — every function body and
    # every hand-written non-manifest binding is left byte-for-byte identical.
    from . import _docsync

    frag_doc_updated = _docsync.refresh_module_fragment_docs(
        root, cfg, only_mod=only_mod
    )

    # gh-917: format what was just written into the REAL tree. The pass above
    # (line ~2120) formats the throwaway scaffold, which is the right thing for
    # the comparison it feeds — but the member-level reconciliation writes
    # here, afterwards, from a fresh render. On a c_style project that left one
    # fragment holding two C styles: jm's for the members it had rewritten, the
    # project's for everything around them, with a `}` in the middle keeping
    # the old indentation.
    #
    # Scoped to the files this run touched, not the whole tree — an unrelated
    # `apply` should not turn a drifted project into a hundred-file diff.
    from . import _cfmt as _cfmt_mod

    _cfmt_mod.format_files(
        root, cfg, [*created, *updated, *frag_doc_updated, *bench_updated]
    )

    # gh-975: a splice whose anchor is gone writes nothing and says
    # "unchanged", which reads exactly like "already correct". Name it, with
    # what was not written and where the line belongs — the alternative jm
    # cannot take is putting the anchor back itself, since its position in a
    # file the author owns is a guess, and guessing wrong wires a component in
    # ahead of the targets it needs.
    for rel, anchor in unanchored:
        _report.warn(
            f"{rel} has no `{anchor}` line, so jm did not write "
            f"{CMAKE_SPLICE_ANCHORS[anchor]} into it. "
            "Restore the line (jm's scaffold puts it after "
            "`enable_testing()`), or keep that wiring yourself and silence "
            "this with [project] status_allow.",
            gates=True,
            stream=sys.stdout,
            indent="  ",
        )

    # gh-1472: named on every apply until it is gone -- held back above, so
    # without this line the rename would simply never happen.
    for _old, _new in _createonly.superseded(root):
        _report.warn(superseded_advice(root, _old, _new), gates=False)

    # gh-442: non-fatal — jm has no way to know which side (manifest or
    # hand-written header doc) is the stale one, so it warns rather than
    # failing the apply. `jm status --check` promotes this to a CI-gating
    # DRIFT section for projects that want it enforced.
    # gh-1141: the same shape, one level up — the generated copies of
    # `[project] version` that disagree with it. Non-fatal for the same
    # reason: a release bumps `pyproject.toml` and never the manifest, so jm
    # has no standing to say which side is stale, and `status --check`
    # promotes it to a gating VERSION section for projects that want it.
    from . import _projversion

    for _v in _projversion.drift(root, cfg):
        _report.warn(
            f"{_v.rel} says version {_v.found!r}, manifest says "
            f"{_v.expected!r} — one of these is stale",
            gates=True,
            stream=sys.stdout,
            indent="  ",
        )

    # gh-1142: and the contract header left behind by a component that has
    # stopped declaring `process_global`. Warned HERE, not only in `status`,
    # because this run is what created the orphan — it stripped the rendezvous
    # from every generated PyInit_ moments ago, and the moment that happens is
    # when the author can still remember why.
    for _orphan in _procglobal.orphan_headers(root, cfg):
        _report.warn(
            f"{_orphan} describes a rendezvous that is no longer generated: "
            f"{_orphan.split('/')[2].removesuffix('_procglobal.h')} does not "
            "declare `process_global`. jm does not delete files — remove it, "
            "or re-declare the key",
            gates=True,
            stream=sys.stdout,
            indent="  ",
        )

    # gh-1154: a manifest `doc` carrying more than one paragraph. The
    # renderer drops the rest for a function and reflows it into prose for a
    # method, both silently; this says so and names the header path, which
    # already renders a complete numpy docstring from Doxygen.
    from . import _docstring as _doc_mod

    for _md in _doc_mod.manifest_docs_with_sections(cfg):
        _report.warn(
            _doc_mod.manifest_doc_advice(_md),
            gates=True,
            stream=sys.stdout,
            indent="  ",
        )

    for obj in C.components(cfg):
        for name, m_dflt, h_dflt in _obj_mod.init_param_drift(cfg, root, obj):
            # Gating: `init_param_drift` is exactly what fills `status`'s
            # DRIFT section, which reaches `drift_count` — so leaving this
            # unactioned fails `--check`. Stays on stdout, inside apply's own
            # block, with the mark carrying the weight.
            _report.warn(
                f"{obj}.{name} default mismatch: "
                f"manifest={m_dflt!r} header={h_dflt!r} "
                f"({INC.core_rel(obj, root)}) — one of these is stale",
                gates=True,
                stream=sys.stdout,
                indent="  ",
            )

    # gh-1361: each component's link-check table, from the binding and header
    # as they now stand. From the real tree, so `status` -- which replays this
    # on a copy -- computes the same bytes.
    from . import _linkcheck

    for obj in C.components(cfg):
        if _linkcheck.write(root, cfg, obj):
            updated.append(_linkcheck.symbols_file(root, obj))

    # gh-1404: the element contract between a writer and a reader. Beside
    # the link-check table above and for the same reason -- the author's
    # own test file is create-only, so an invariant appended there would
    # reach a new project and never an existing one.
    from . import _invariants

    _pkg = C.project_name(cfg)
    for obj in C.components(cfg):
        if _invariants.write(root, cfg, obj, _pkg):
            updated.append(
                _invariants.file_for(
                    root, _pkg, obj, C.module_of(cfg, obj) or ""
                )
            )

    # gh-184: re-materialise the recorded app, not a default one. Passing
    # the [app] record's target/name/object keeps `jm apply` from rewriting
    # it to <project>/<first object>.
    #
    # gh-1477: BEFORE the report, with the verb's own progress captured, so
    # its writes are reported by the bytes they leave like every other write
    # here. It ran after the summary and printed its own unconditional
    # `update` lines, so an unchanged app project announced three rewrites on
    # every apply. Its warning that edits were discarded goes to stderr and
    # is not captured: it fires only when the bytes really differ.
    app_written: list = []
    _app_rec = C.app_config(cfg)
    if _app_rec:
        from . import _app

        with contextlib.redirect_stdout(io.StringIO()):
            app_written = _app.run(
                root,
                cfg,
                target=_app_rec.get("target", "c"),
                name=_app_rec.get("name"),
                object_=_app_rec.get("object"),
                function_=_app_rec.get("function"),
                module=_app_rec.get("module"),
                flags=_app_rec.get("flags"),
                commands=_app_rec.get("commands"),
            )

    # gh-1474: after the LAST write -- the formatter pass, the link-check
    # tables, the invariants file, the app -- each path decided by the bytes
    # it ends with. A write the formatter undid, or a file two modules merged
    # in turn, changed nothing and says nothing; and the summary below counts
    # the same lines it follows, so the two cannot disagree.
    _changed = _changed_report(
        root,
        _before,
        [
            *created,
            *impl_patched,
            *updated,
            *bench_updated,
            *frag_doc_updated,
            *app_written,
        ],
    )
    for verb in ("create", "update"):
        for v, rel in _changed:
            if v == verb:
                print(f"  {verb}  {rel}")

    print()
    _impl_rels = {_project_rel(root, p) for p in impl_patched}
    _n_created = sum(1 for v, _ in _changed if v == "create")
    _n_impl = sum(1 for v, r in _changed if v == "update" and r in _impl_rels)
    _reconciled = len(_changed) - _n_created - _n_impl
    if _changed:
        print(
            f"Done!  Materialized {_n_created} new file(s), "
            f"patched {_n_impl} impl(s), and "
            f"reconciled {_reconciled} wiring file(s)"
            f" from {C.FILENAME}."
        )
    elif unanchored:
        # gh-975: "already matches — nothing to do" is the sentence that made
        # the loss invisible. jm did nothing here because it could not, which
        # is the opposite claim.
        print(
            f"Done!  No file changed — but {len(unanchored)} splice(s) above "
            f"had nowhere to go, so this run did NOT\n"
            f"       reconcile {C.FILENAME}."
        )
    else:
        print(f"Done!  Project already matches {C.FILENAME} — nothing to do.")

    # gh-752: the stubs have just been written, so this measures them rather
    # than predicting. Reported after the summary because it is a note about
    # the *author's* text, not a result of the apply.
    from . import _codecheck

    _codecheck.report(root, cfg)
    # gh-1493: and a manifest `doc` line, which renders verbatim too.
    _codecheck.report_docs(root, cfg)

    # gh-806: apply is the command that materialises a scaffold over a renamed
    # component's target, so it is the one that must say the previous file is
    # now compiled by nothing. Reported after the summary and on stderr, not
    # inside the create/update block: the finding is about files apply did
    # *not* touch, and the ones it did are already listed above.
    from . import _hollow

    _hollow.report(root, cfg, indent="")

    # gh-823: last, after everything that could warn. A count is what survives
    # a long scroll when individual lines do not — the warning that cost
    # doppler months was correct, printed every run, and indistinguishable
    # from the dozen advisory ones around it.
    _report.trailer()
