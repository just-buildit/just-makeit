"""
_upgrade.py — schema migration for existing just-makeit projects.

Each migration entry in MIGRATIONS maps a schema version N to a list of
steps that advance the project from schema N to schema N+1.  Steps are
applied in order; each is idempotent (safe to run repeatedly).

Step types
----------
AddFile(path, template_attr)
    Write the named template to `path` (relative to project root) if and
    only if the file does not already exist.  `path` supports the same
    ``<<package>>`` / ``<<project>>`` placeholders used by template strings.
    `template_attr` is the attribute name on the ``_templates`` module.

AddTomlKey(section, key, default)
    Add `key = default` under `[section]` in the TOML if the key is absent.
    `section` is a list of strings, e.g. ``["project"]``.

PrefixHeaders()
    Schema 8 (gh-1583): move the project's headers under
    ``native/inc/<pkg>/`` and respell every reference to them.

Usage
-----
    just-makeit upgrade          # advance to CURRENT_SCHEMA
"""

from __future__ import annotations

from . import _textio

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from . import _config as C
from . import _context as Ctx
from . import _keys
from . import _render as R
from . import _incpath as INC
from . import _csym as CSYM


@dataclass
class AddFile:
    """Write a rendered template file if it does not already exist."""

    path: str  # relative path; may contain <<package>>/<<project>>
    template_attr: str  # attribute name on the _templates module


@dataclass
class AddTomlKey:
    """Insert a key with a default value into a top-level TOML section if absent."""

    section: str  # top-level section name, e.g. "project"
    key: str
    default: str


@dataclass
class RegenBench:
    """Re-render bench_<comp>_core.c for every standalone component.

    Unlike AddFile, this overwrites existing files so that new template
    features (e.g. method timing blocks) land in projects that were
    scaffolded before the feature was added.
    """


@dataclass
class MigrateBenchHistory:
    """Schema 5: dated, trimmed benchmark snapshots.

    Ensures ``benchmarks/history/.gitkeep`` exists and rewrites the
    Makefile so ``make bench`` delegates to ``just-makeit bench`` — which
    drops raw per-iteration arrays before writing a snapshot, so a single
    run no longer bloats the JSON to 100+ MB.  Any older ``bench-python``
    / ``bench-c`` targets and ``BENCH_*`` variables are removed.
    """


@dataclass
class PrefixHeaders:
    """Schema 8 (gh-1583): the project's headers move under their package.

    ``native/inc`` stays the ``-I`` directory; everything in it moves one
    level down, into ``native/inc/<pkg>/``, so the installed tree is
    ``include/<pkg>/`` and two projects' headers cannot collide. That rule
    is right for EVERY schema-7 tree, because ``native/inc`` was its header
    root: whatever was at ``native/inc/X`` was included as ``"X"`` and is now
    ``"<pkg>/X"`` -- a component named after the package included.

    What moves is read from the tree, not listed, and so is what gets
    respelled -- a reference is rewritten only if it RESOLVES to a moved file
    (:func:`_prefix_headers`), so a hand-written header is carried along and
    a vendored library's own ``"config.h"`` is not.
    """


# `make bench` after migration: a one-line delegation to the jm CLI,
# which owns building, running, trimming, and snapshotting.
_BENCH_TARGET = "bench:\n\tjust-makeit bench\n"


def _rewrite_makefile_bench(text: str) -> str:
    """Return *text* with the bench target(s) collapsed to _BENCH_TARGET.

    Drops the ``bench`` / ``bench-python`` / ``bench-c`` target blocks and
    every ``BENCH_*`` variable line, then re-emits a single ``bench``
    target where the old ``bench`` target stood.  Idempotent: a Makefile
    already in the target form is returned unchanged.
    """
    lines = text.splitlines(keepends=True)
    drop_targets = {"bench", "bench-python", "bench-c"}
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        body = lines[i].rstrip("\n")
        # Orphan BENCH_* variable assignment → drop it and any
        # backslash-continued lines that belong to it.
        if re.match(r"BENCH_[A-Z_]+\s*[:?]?=", body):
            while True:
                cont = body.endswith("\\")
                i += 1
                if not cont or i >= n:
                    break
                body = lines[i].rstrip("\n")
            continue
        m = re.match(r"([A-Za-z0-9_.-]+)\s*:(?!=)", body)
        if m and m.group(1) in drop_targets:
            is_bench = m.group(1) == "bench"
            i += 1
            # A recipe is the run of tab-indented lines that follows.
            while i < n and lines[i].startswith("\t"):
                i += 1
            # Absorb one blank line that trailed the dropped block.
            if i < n and lines[i].strip() == "":
                i += 1
            if is_bench:
                out.append(_BENCH_TARGET + "\n")
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


# ── schema-independent repairs (gh-1248) ─────────────────────────────────────
#
# A repair is not a migration: it is keyed to nothing, runs on every `upgrade`,
# and is idempotent. gh-887 already established that `upgrade` is where "your
# project is current by schema number and still needs work" lives -- it reports
# stale manifest keys there rather than claiming "already up to date", because
# the schema number is not a compatibility statement.
#
# gh-887 also refuses to REWRITE what it reports, and states its reason: "the
# replacement can alter what your API does, so it is yours to make, not a
# migration's." That is the line, and this falls on the other side of it.
# `complex` is a `<complex.h>` macro for `_Complex`, so the two spellings are
# the same tokens after preprocessing -- the rewrite cannot change what the
# project does, which is exactly what makes it a migration's to make.
#
# gh-1246 changed what jm EMITS. An existing project keeps the old spelling in
# its own C; `apply` cannot reach it (the inline `step()` lives in the sacred
# header) and neither can `regenerate`. Before this, the answer in
# `docs/upgrading.md` was a `perl -pi -e` block the author ran by hand.
_COMPLEX_DIRS = (
    INC.INC_DIR,
    "native/src",
    "native/tests",
    "native/benchmarks",
)

#: Longest first. Not load-bearing -- rewriting `double complex` inside
#: `long double complex` yields the same string either way -- but the order
#: states the intent, and a future pair may not be so forgiving.
_COMPLEX_SPELLING = (
    (re.compile(r"\blong double complex\b"), "long double _Complex"),
    (re.compile(r"\bfloat complex\b"), "float _Complex"),
    (re.compile(r"\bdouble complex\b"), "double _Complex"),
)


def _respell_code_only(text: str, pairs=None, repl=None) -> str:
    """*text* with *pairs* (``(pattern, replacement)``, longest first;
    default the old complex spelling) rewritten in CODE only. A pair whose
    replacement is None takes it from ``repl(match)`` instead -- one pattern
    over a whole rename map (gh-1591).

    gh-1382: comments and string literals are prose, and rewriting prose
    changes what it says. doppler's `dp_complex.h` explains why the UCRT's
    `#define complex _complex` breaks the spelling `float complex`; respelled,
    the comment claimed doppler writes `float _Complex` -- which that macro
    cannot touch -- and so contradicted itself. The "identical tokens after
    preprocessing" argument that licenses this rewrite covers code tokens,
    and nothing else.

    Matches are found on `_docsync._code_mask` (same length, comments and
    literal contents blanked) and the spans replaced in the original.

    Examples
    --------
    >>> print(_respell_code_only(
    ...     "/* writes float complex */ float complex x;"
    ... ))
    /* writes float complex */ float _Complex x;
    """
    from ._docsync import _code_mask

    mask = _code_mask(text)
    spans = []
    for pat, fixed in _COMPLEX_SPELLING if pairs is None else pairs:
        for m in pat.finditer(mask):
            # Longest first: a span inside one already taken (`double complex`
            # inside `long double complex`) is the same rewrite, once.
            if not any(a <= m.start() < b for a, b, _ in spans):
                # The mask keeps code characters as they are, so a match on
                # it is the original's text there.
                spans.append(
                    (
                        m.start(),
                        m.end(),
                        fixed if fixed is not None else repl(m),
                    )
                )
    for a, b, new in sorted(spans, reverse=True):
        text = text[:a] + new + text[b:]
    return text


def _repair_complex_spelling(root: Path) -> "list[Path]":
    """Rewrite the pre-gh-1246 complex spelling in the project's own C.

    Returns the files changed, in path order; empty when there is nothing to
    do -- which is the second run of this, and every run on a project
    scaffolded after gh-1246.

    Code only (gh-1382, :func:`_respell_code_only`). That also retires the
    special case this used to need: `clib_common.h` was skipped by name
    because its comment QUOTES the old spelling while explaining why it was a
    problem, and a blanket pass rewrote that prose into a false statement. A
    comment is now never touched, in that file or any other.
    """
    changed: list[Path] = []
    walked: "set[Path]" = set()
    for rel in _COMPLEX_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix not in (".c", ".h"):
                continue
            walked.add(path.resolve())
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            new = _respell_code_only(text)
            if new != text:
                _textio.write_text(path, new)
                changed.append(path)
    # gh-1647: and the bodies jm renders a header FROM. Respelling only the
    # header left `apply` to put `float complex` back from the manifest, and
    # every later upgrade re-reported the same file.
    for path in CSYM._manifest_files(root):
        text = path.read_text(encoding="utf-8")
        # gh-1684: a `replace` key is matched against its body, so it moves
        # only when the body did -- not when that body is an `impl_file`
        # this walk leaves alone, exactly as the `c_prefix` respell reads it.
        frozen = CSYM.unfollowed_bodies(text, root, walked)
        new = CSYM.respell_manifest_c(
            text, _respell_code_only, CSYM.IMPL_KEYS, frozen
        )
        if new != text:
            _textio.write_text(path, new)
            changed.append(path)
    return changed


# Migration table: schema N → N+1.
# Keep migrations append-only; never modify an existing entry.
MIGRATIONS: dict[int, list] = {
    1: [
        # Schema 2 adds docs/coverage scaffolding (zensical.toml + docs/).
        AddFile("zensical.toml", "ZENSICAL_TOML"),
        AddFile("docs/index.md", "DOCS_INDEX_MD"),
        AddFile("docs/api.md", "DOCS_API_MD"),
    ],
    2: [
        # Schema 3 regenerates bench files so method timing blocks appear
        # in projects that were scaffolded before this feature was added.
        RegenBench(),
    ],
    3: [
        # Schema 4 adds jm_bench.h (per-round stats + pytest-benchmark JSON)
        # and regenerates bench C files to use the new timing structure.
        AddFile("native/benchmarks/jm_bench.h", "JM_BENCH_H"),
        RegenBench(),
    ],
    4: [
        # Schema 5 moves benchmarking under `just-makeit bench`, which
        # trims raw per-iteration arrays and writes dated snapshots to
        # benchmarks/history/.
        MigrateBenchHistory(),
    ],
    5: [
        # Schema 6 gates the `include = [...]` key for split per-object
        # TOMLs. Projects stay single-file unless they opt in by adding
        # `include`; the bump alone is the migration.
    ],
    6: [
        # Schema 7 gates the top-level `[[enum]]` single-source-of-truth and
        # the `type = "enum:<name>"` parameter reference (gh-285). Projects
        # keep inlining `string_enum:a,b,c` unless they opt in by declaring an
        # `[[enum]]`; the bump alone is the migration. (Older jm cannot read a
        # manifest that declares `[[enum]]`, so the bump also signals the
        # minimum tool version.)
    ],
    7: [
        # Schema 8 prefixes the header layout (gh-1583): headers move under
        # native/inc/<pkg>/ and every include of one is spelled "<pkg>/...".
        PrefixHeaders(),
    ],
}


def _build_ctx(cfg: dict) -> dict[str, str]:
    """Build a minimal render context from project config."""
    name = C.project_name(cfg)
    return {
        "package": name,
        "project": name.replace("_", "-"),
        "project_underscore": name,
        "version": C.project_version(cfg),
        **INC.ctx_slots(cfg),
    }


def _apply_step(root: Path, step, ctx: dict[str, str]) -> None:
    if isinstance(step, AddFile):
        dest = root / R.render(step.path, ctx)
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        template = getattr(R, step.template_attr)
        _textio.write_text(dest, R.render(template, ctx))
        print(f"  create  {dest.relative_to(root)}")

    elif isinstance(step, AddTomlKey):
        target = C.load(root)
        section = target.setdefault(step.section, {})
        if step.key not in section:
            section[step.key] = step.default
            C.save(root, target)
            print(f"  update  just-makeit.toml  [{step.section}] {step.key}")

    elif isinstance(step, MigrateBenchHistory):
        gitkeep = root / "benchmarks" / "history" / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.parent.mkdir(parents=True, exist_ok=True)
            _textio.write_text(gitkeep, "")
            print(f"  create  {gitkeep.relative_to(root)}")
        makefile = root / "Makefile"
        if makefile.exists():
            old = makefile.read_text(encoding="utf-8")
            new = _rewrite_makefile_bench(old)
            if new != old:
                _textio.write_text(makefile, new)
                print("  update  Makefile  (bench → just-makeit bench)")

    elif isinstance(step, PrefixHeaders):
        _prefix_headers(root)

    elif isinstance(step, RegenBench):
        cfg = C.load(root)
        pkg = C.project_name(cfg)
        version = C.project_version(cfg)
        perf = C.is_perf(cfg)
        for comp in C.components(cfg):
            bench_c = root / "native" / "benchmarks" / f"bench_{comp}_core.c"
            if not bench_c.exists():
                continue
            no_step = C.is_no_step(cfg, comp)
            tmpl = R.NO_STEP_BENCH_C if no_step else R.COMPONENT_BENCH_C
            comp_ctx: dict = {"component": comp, "Component": comp.title()}
            comp_ctx.update(
                {
                    "package": pkg,
                    "PACKAGE": pkg.upper(),
                    "project": pkg.replace("_", "-"),
                    "project_underscore": pkg,
                    "version": version,
                    **INC.ctx_slots(cfg),
                    **CSYM.slots(cfg, comp),
                }
            )
            arg_type = C.arg_type(cfg, comp)
            return_type = C.return_type(cfg, comp)
            comp_ctx.update(Ctx.make_sample_ctx(arg_type, return_type))
            comp_ctx.update(
                Ctx.make_state_ctx(
                    comp,
                    comp_ctx["Component"],
                    C.state_vars(cfg, comp),
                    array_args=C.array_args(cfg, comp),
                    no_state=C.is_no_state(cfg, comp),
                    init_params=C.init_params(cfg, comp),
                    csym=comp_ctx["csym"],
                )
            )
            comp_ctx.update(Ctx.make_perf_ctx(perf))
            comp_ctx.update(
                Ctx.make_step_ctx(
                    comp_ctx,
                    arg_type,
                    return_type,
                    no_step=no_step,
                    mutable=C.is_mutable(cfg, comp),
                )
            )
            comp_ctx.update(
                Ctx.make_methods_ctx(
                    comp,
                    comp_ctx["Component"],
                    C.methods(cfg, comp),
                    pkg=pkg,
                    py_create_args=comp_ctx.get("py_create_args", ""),
                    no_state=C.is_no_state(cfg, comp),
                    serializable=C.is_serializable(cfg, comp),
                    codecs=C.codecs(cfg),
                    csym=comp_ctx["csym"],
                )
            )
            # NO_STEP components with no init_params have an empty
            # c_create_args — the _create() signature is user-managed.
            # In that case suppress method blocks (obj is unavailable)
            # and emit a TODO comment instead of a broken _create() call.
            if no_step:
                csym = comp_ctx["csym"]
                c_args = comp_ctx.get("c_create_args", "")
                if c_args:
                    comp_ctx["bench_create_stmt"] = (
                        f"    {csym}_state_t *obj = {csym}_create({c_args});"
                    )
                    comp_ctx["bench_destroy_stmt"] = (
                        f"    {csym}_destroy(obj);"
                    )
                else:
                    comp_ctx["bench_create_stmt"] = (
                        f"    /* TODO: {csym}_state_t *obj"
                        f" = {csym}_create(...); */"
                    )
                    comp_ctx["bench_destroy_stmt"] = ""
                    comp_ctx["bench_methods_timing_block"] = ""
            _textio.write_text(bench_c, R.render(tmpl, comp_ctx))
            print(f"  update  {bench_c.relative_to(root)}")


#: The files whose ``#include`` lines the header move respells.
_C_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"}
_INCLUDE_LINE = re.compile(
    r'^([ \t]*#[ \t]*include[ \t]*)(["<])([^">\n]+)([">])', re.M
)
#: A TOML string value -- a manifest's header references (gh-1583). The one
#: pattern `_csym` also rewrites the C-bearing values with (gh-1653).
_TOML_STRING = CSYM.TOML_STRING


def _nested_projects(root: Path) -> "list[Path]":
    """Directories below *root* holding a manifest of their own.

    Each is ANOTHER project -- a downstream example, a test fixture -- with
    its own ``native/inc`` and its own layout. doppler carries one
    (``examples/downstream-jm``), and respelling its ``"clib_common.h"`` as
    this project's would have pointed it at the wrong file. Its own `jm
    upgrade` is what moves it.
    """
    return sorted(
        m.parent
        for m in root.rglob(C.FILENAME)
        if m.parent != root and not _skipped_dir(m.relative_to(root).parts)
    )


def _skipped_dir(parts: "tuple[str, ...]") -> bool:
    return any(p.startswith(".") or p.startswith("build") for p in parts[:-1])


def _project_files(root: Path, want) -> "list[Path]":
    """The project's own files *want* accepts: not build trees, not dot-dirs,
    not the virtualenv, and nothing inside a nested project."""
    nested = _nested_projects(root)
    out = []
    for path in sorted(root.rglob("*")):
        if _skipped_dir(path.relative_to(root).parts):
            continue
        if any(n in path.parents for n in nested):
            continue
        if path.is_file() and want(path):
            out.append(path)
    return out


def _prefix_headers(root: Path) -> None:
    """Move ``native/inc/*`` under ``native/inc/<pkg>/`` and respell every
    reference to a moved header (gh-1583, schema 7 -> 8).

    Respelled, each only where it resolves to a file that moves:

    - an ``#include`` in any of the project's C or C++ files. A quoted
      include that resolves beside its own file is left alone, because C
      finds it there first and the two move together;
    - a quoted string in the manifest or a fragment it includes (``header =
      "wfm/wfm.h"`` and its kin: jm emits them into ``#include`` verbatim);
    - a ``native/inc/<entry>`` path in any CMake file.

    Every rewrite is printed, so the author can review what changed. The
    references are rewritten BEFORE the move, while the old tree still says
    what resolves.
    """
    cfg = C.load(root)
    inc = INC.inc_dir(root)
    if not inc.is_dir():
        return
    pkg = C.project_name(cfg)
    # The spellings of the layout the project is moving TO. The manifest still
    # says schema 7 while the step runs, so `_incpath` is asked about a
    # schema-8 owner explicitly rather than about this project.
    to = INC.prefixed_owner(pkg)
    # A tree already in the prefixed layout is not moved again, whatever its
    # manifest says: jm's own clib_common.h sits under the package and not at
    # the include root. Moving it would nest it twice (`<pkg>/<pkg>/`).
    if (
        INC.path(root, "clib_common.h", to).is_file()
        and not INC.path(root, "clib_common.h", cfg).is_file()
    ):
        print(
            f"  skip    {INC.rel('', to)}  (headers already under the package)"
        )
        return
    moved = {
        p.relative_to(inc).as_posix() for p in inc.rglob("*") if p.is_file()
    }
    # A configured template moves the header it produces, too: doppler's
    # `wfm/wfm_plan_dsp_hash.h.in` becomes `${CMAKE_BINARY_DIR}/native/inc/
    # wfm/wfm_plan_dsp_hash.h`, whose output path is respelled below with the
    # rest of the CMake -- so the include naming the GENERATED file must be
    # respelled with it, though that file is never in the source tree.
    moved |= {m[: -len(".in")] for m in moved if m.endswith(".in")}
    entries = sorted(p.name for p in inc.iterdir())

    def spell(match: "re.Match[str]", here: Path) -> str:
        lead, open_, name, close = match.groups()
        if name not in moved:
            return match.group(0)
        # A quoted include is looked up beside its own file first. Kept only
        # when that finds a DIFFERENT file from the -I lookup: then the
        # author's spelling decides what is included. The same file -- every
        # header directly in native/inc, the umbrella among them -- takes the
        # canonical spelling, as jm renders it.
        beside = here.parent / name
        if (
            open_ == '"'
            and beside.is_file()
            and beside.resolve() != (inc / name).resolve()
        ):
            return match.group(0)
        return f"{lead}{open_}{INC.include(name, to)}{close}"

    touched: "list[str]" = []
    for f in _project_files(root, lambda p: p.suffix in _C_SUFFIXES):
        text = f.read_text(encoding="utf-8", errors="surrogateescape")
        new = _INCLUDE_LINE.sub(lambda m: spell(m, f), text)
        if new != text:
            _textio.write_text(f, new)
            touched.append(f.relative_to(root).as_posix())

    manifests = [root / C.FILENAME] + _manifest_fragments(root)
    for f in manifests:
        text = f.read_text(encoding="utf-8")
        new = _TOML_STRING.sub(
            lambda m: (
                f"{m.group(1)}{INC.include(m.group(2), to)}{m.group(1)}"
                if m.group(2) in moved
                else m.group(0)
            ),
            text,
        )
        if new != text:
            _textio.write_text(f, new)
            touched.append(f.relative_to(root).as_posix())

    old_rel = re.compile(
        rf"{re.escape(INC.INC_DIR)}/({'|'.join(map(re.escape, entries))})(?=[/\s\")}}$]|$)",
        re.M,
    )
    for f in _project_files(
        root, lambda p: p.name == "CMakeLists.txt" or p.suffix == ".cmake"
    ):
        text = f.read_text(encoding="utf-8")
        new = (
            old_rel.sub(lambda m: INC.rel(m.group(1), to), text)
            if entries
            else text
        )
        if new != text:
            _textio.write_text(f, new)
            touched.append(f.relative_to(root).as_posix())

    # The move itself: the whole directory, one level down. Through a
    # sibling so a component named after the package moves too.
    staging = inc.with_name(inc.name + ".jm-upgrade")
    inc.rename(staging)
    inc.mkdir()
    staging.rename(INC.header_root(root, to))
    print(
        f"  move    {INC.INC_DIR}/* -> {INC.rel('', to)}"
        f"  ({len(moved)} file(s))"
    )
    # Named where each file IS now: a header respelled above has moved since.
    moved_prefix = INC.INC_DIR + "/"
    for rel in touched:
        if rel.startswith(moved_prefix):
            rel = INC.rel(rel[len(moved_prefix) :], to)
        print(f"  update  {rel}  (header references)")
    for nested in _nested_projects(root):
        print(
            f"  skip    {nested.relative_to(root).as_posix()}/  (its own"
            f" project: run `jm upgrade` there; an include of this"
            f' project\'s headers becomes "{INC.prefix(to)}...")'
        )


def _manifest_fragments(root: Path) -> "list[Path]":
    """The fragment files the manifest's ``include`` names."""
    raw = C.load_manifest(root)
    return C._resolve_includes(root, raw.get("include") or [])


def _where_declared(root: Path, unknown) -> str:
    """``"objects/<name>.toml: "`` when that fragment exists, else ``""``.

    `_config.load` merges split-layout fragments before anything sees them, so
    provenance is gone by the time a key is judged. A project with forty
    fragment files gets told *which object* and left to find the file, which is
    the same "left to the reader" gap this whole report exists to close.

    Derived by **existence**, never guessed: the owning object is the first
    dotted segment of `where`, and the path is only printed when that file is
    really there. A module function or a single-file manifest yields nothing
    and the message is unchanged — a wrong path would be worse than none.
    """
    owner = unknown.where.split(".")[0]
    frag = root / "objects" / f"{owner}.toml"
    return f"{frag.relative_to(root).as_posix()}: " if frag.exists() else ""


def _rename_superseded(root: Path) -> None:
    """Move each file jm has renamed to its new name (gh-1472).

    A rename, not a re-render: the old file may carry the author's additions
    (a ``jb.toml`` with ``[runtime.*]`` packages), and a fresh default under
    the new name would drop them. When both names exist jm cannot tell which
    holds the edits, so it renames nothing and says so -- the same advice
    `apply` and `status` print, from the same function.
    """
    from . import _apply
    from . import _createonly

    for old, new in _createonly.superseded(root):
        if (root / new).exists():
            print(f"\n{_apply.superseded_advice(root, old, new)}")
            continue
        (root / old).rename(root / new)
        # An owned file's token names it (gh-1589): carry the ownership
        # across the rename, or the moved file silently becomes the author's.
        from . import _render as R

        moved = root / new
        text = moved.read_text(encoding="utf-8")
        was = R.owned_token(Path(old).name)
        if R.is_owned_render(text, Path(old).name) and text.count(was) == 1:
            _textio.write_text(
                moved, text.replace(was, R.owned_token(Path(new).name))
            )
        print(f"\nrenamed {old} -> {new}; your edits came along.")


def _respell_c_prefix(root: Path) -> "tuple[list[Path], dict[str, str]]":
    """Respell the project's own C onto its ``[project] c_prefix`` (gh-1591
    phase 3): every derived identifier jm renders prefixed, where the tree
    still spells it bare.

    The rename set is not a pattern: it is :func:`_csym.renames` of a replay
    -- exactly the identifiers jm's render of THIS manifest declares, old
    spelling to new -- the same map `apply`'s refusal asks about. The
    matcher is the refusal's too (:func:`_csym.references`): whole
    identifier and case-sensitive, so ``acc_state_t`` moves and an author's
    ``ACC_STATE_MAGIC`` does not; code only, so a comment or string that
    quotes a name keeps it; and references only, so a struct member, a
    parameter or a local spelled like a derived name keeps it (gh-1668).
    A stem passed to a macro that pastes it into derived names moves with
    them (:func:`_csym.pasted_stems`, gh-1669). The file set is gh-1583's walk (:func:`_project_files`):
    every C/C++ file of the project, ``native/examples/`` included, nested
    projects not.

    Idempotent: a respelled ``zz_fir_create`` cannot match ``fir_create``.
    Returns the files changed and the map, both empty when nothing changed.
    """
    import tempfile

    from . import _apply

    cfg = C.load(root)
    # A prefix changed or REMOVED is refused before anything else, so
    # upgrade never reports "nothing to do" over a tree `apply` would then
    # refuse -- and a removed key is exactly the case with no prefix to read.
    if CSYM.stray_prefixes(root, cfg):
        _apply.prefix_errors(cfg, root, root)
    if CSYM.prefix(cfg) is None:
        return [], {}
    with tempfile.TemporaryDirectory() as tmp:
        _apply.replay_project(cfg, Path(tmp), root, prefix_checks=False)
        # gh-1653: plus `<stem>_step_batch`, which no render declares but
        # `JM_DEFINE_STEPS` pastes from the stem.
        names = CSYM.with_macro_names(CSYM.renames(Path(tmp), cfg))
    stems = CSYM.macro_stems(cfg)
    if not names and not stems:
        return [], {}
    # gh-1669: the macros that paste a stem, read before any file moves.
    macros = CSYM.project_macros(root)
    changed = []
    for path in _project_files(root, lambda p: p.suffix in _C_SUFFIXES):
        text = path.read_text(encoding="utf-8")
        new = CSYM.respell_c(text, names, stems, macros)
        if new != text:
            _textio.write_text(path, new)
            changed.append(path)
    # gh-1653: the author's C that lives in the MANIFEST -- `*_impl` bodies,
    # a sibling's `type`, and (gh-1666) an expression such as `out_size` --
    # which jm copies into the C verbatim (`CSYM.MANIFEST_C_KEYS`). Each
    # value is replaced in place; the file is never re-serialised.
    # An `*_impl_file`'s function follows its file when the walk above
    # respelled that file (`CSYM.IMPL_FILE_VALUE`).
    followed = CSYM.walked(root)
    for path in CSYM._manifest_files(root):
        text = path.read_text(encoding="utf-8")
        new = CSYM.respell_manifest(text, names, stems, root, followed, macros)
        if new != text:
            _textio.write_text(path, new)
            changed.append(path)
    return changed, names if changed else {}


def _report_c_prefix(root: Path) -> None:
    changed, names = _respell_c_prefix(root)
    if not changed:
        return
    cfg = C.load(root)
    print(
        f"\nrespelled {len(changed)} file(s) onto [project] c_prefix = "
        f"{C.c_prefix(cfg)!r} (gh-1591):"
    )
    for path in changed:
        print(f"  {path.relative_to(root)}")
    # One line per rename, `old<TAB>new` and nothing else on it, so the
    # table is a TSV as printed -- `awk -F'\t' 'NF == 2'` lifts it for
    # code jm does not own (another language's FFI, docs) with no flag or
    # file this command would otherwise need.
    print(f"\nthe rename table ({len(names)}), old<TAB>new:")
    for old in sorted(names):
        print(f"{old}\t{names[old]}")
    print(
        "\n  Comments, strings, and members, parameters and locals spelled"
        " like a derived\n  name are unchanged; a stem your macro pastes"
        " into derived names moved with them.\n  Review the files above,"
        " then `jm apply` and rebuild. Consumers of the"
        " installed library see a new ABI."
    )


def _report_repairs(root: Path) -> None:
    """Run the schema-independent repairs and say what they changed.

    Called from BOTH exits of :func:`run`. A project already at
    `CURRENT_SCHEMA` is the common case for this one -- gh-1246 needed no
    schema bump -- so wiring it only into the migration loop would have meant
    it never ran for anybody who was up to date, which is everybody it is for.
    """
    _rename_superseded(root)
    _report_c_prefix(root)
    fixed = _repair_complex_spelling(root)
    if not fixed:
        return
    print(
        f"\nrespelled the complex types in {len(fixed)} file(s) "
        f"(`complex` -> `_Complex`, gh-1246):"
    )
    for path in fixed:
        print(f"  {path.relative_to(root)}")
    print(
        "  Same type, same ABI -- identical tokens after preprocessing.\n"
        "  Rebuild and run your tests. If you adopted jm's new "
        "clib_common.h,\n  a C++11 caller can use `std::complex` again."
    )


def _refuse_prefix_collisions(root: Path, cfg: dict) -> None:
    """Refuse, before ANY step writes, a ``c_prefix`` whose derived names the
    author's C already declares (gh-1657, :func:`_csym.collisions`), whose
    renamed symbols an author-named manifest key still names (gh-1671,
    :func:`_csym.author_named`), and a prefix changed or removed after the
    tree was prefixed (gh-1650).

    First, not inside :func:`_respell_c_prefix`: a schema migration runs
    before the repairs, so a refusal there would leave a project half
    upgraded. The replay is of the manifest as it stands, so its owning
    files are at the tree's current paths.
    """
    import tempfile

    from . import _apply

    # gh-1660: a prefix REMOVED is refused here too, before the no-prefix
    # return below. Asked only in `_respell_c_prefix`, it came after the
    # migrations and `_rename_superseded` had already written.
    if CSYM.stray_prefixes(root, cfg):
        _apply.prefix_errors(cfg, root, root)
    if CSYM.prefix(cfg) is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        _apply.replay_project(cfg, Path(tmp), root, prefix_checks=False)
        clash = CSYM.collisions(root, Path(tmp), cfg)
        # gh-1671: and an author-named key naming a symbol the prefix
        # renames, which this upgrade never respells.
        clash += CSYM.author_named(root, cfg, Path(tmp))
    if clash:
        C._refuse(clash)


def run(root: Path) -> None:
    """Advance the project at *root* to CURRENT_SCHEMA."""
    cfg = C.load(root)
    if not cfg:
        print("error: no just-makeit.toml found.", file=sys.stderr)
        sys.exit(1)
    _refuse_prefix_collisions(root, cfg)

    current = C.schema_version(cfg)
    target = C.CURRENT_SCHEMA

    if current >= target:
        # gh-887: the schema number is not a compatibility statement, and
        # "already up to date" is read as one. doppler sat at schema 7 —
        # genuinely current — while carrying `check_return` on a method, a
        # function-only key this jm no longer honours. `upgrade` reported up
        # to date, changed nothing, and the next `jm apply` refused with a
        # message naming neither the key nor the file. The connection was
        # left to the reader, across two commands.
        #
        # Reported, never rewritten. The replacement is mechanically trivial
        # and the warning already states it — but `check_return` ->
        # `status_return` changes whether a non-zero return raises, and that
        # is the author's call. A schema migration moves jm-owned structure;
        # silently editing a *declaration* changes what the project says its
        # own API does. A migration that quietly alters runtime behaviour is
        # worse than one that refuses.
        stale = _keys.unknown_keys(cfg)
        if not stale:
            print(f"already up to date (schema {current})")
            _report_repairs(root)
            return
        print(f"schema {current} — current.")
        print(
            f"\n{len(stale)} manifest key(s) not valid for just-makeit "
            f"{C.jm_cli_version()}:"
        )
        for unknown in stale:
            print(f"  {_where_declared(root, unknown)}{unknown.message()}")
        print(
            "\nNot up to date: `just-makeit apply` will refuse until these "
            "are resolved.\nEach must be changed by hand — the replacement "
            "can alter what your API does,\nso it is yours to make, not a "
            "migration's."
        )
        _report_repairs(root)
        return

    ctx = _build_ctx(cfg)

    for version in range(current, target):
        steps = MIGRATIONS.get(version, [])
        print(f"migrating schema {version} → {version + 1}")
        for step in steps:
            # Reload cfg each step so AddTomlKey changes are cumulative.
            cfg = C.load(root)
            _apply_step(root, step, ctx)

        cfg = C.load(root)
        C.set_schema_version(cfg, version + 1)
        C.save(root, cfg)

    print(f"project is now at schema {target}")
    _report_repairs(root)
