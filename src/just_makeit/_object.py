"""
_object.py — `just-makeit object` command.

Adds a Python type to an existing project:

  Standalone (own .so):
    just-makeit object gain                  # no --module -> standalone
    from my_pkg import Gain

  In-module (shared .so subpackage):
    just-makeit object fir --module filter   # grouped under filter subpackage
    from my_pkg.filter import Fir
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

from . import _color as Color
from . import _config as C
from . import _context as Ctx
from . import _render as R
from . import _stubs as S
from . import _types as T
from ._init import (
    _make_component_ctx,
    _to_title,
    _write,
    _write_compile_commands,
    ensure_parent_packages,
)
from ._docstring import extract_doc_blocks, parse_doxygen_block
from ._context._parse import _build_ml_doc

# When `jm apply` regenerates glue, it replays the scaffold into a throwaway
# temp tree whose headers carry only template Doxygen. Docstring derivation
# must instead read the REAL project's sacred `_core.h`, so apply sets this
# override to the real project root for the duration of the replay.
_DOC_ROOT_OVERRIDE: Path | None = None


def _load_doc_blocks(root: Path, obj: str) -> dict:
    """Parse Doxygen comments from the sacred ``<obj>_core.h``.

    Returns ``{c_function_name: DoxyBlock}`` for every documented declaration,
    or ``{}`` when the header is absent or carries no usable comments. The
    header is the single source of truth for docstrings; generators derive
    Python docs from these blocks and fall back to name-based stubs otherwise.
    """
    doc_root = _DOC_ROOT_OVERRIDE or root
    header = doc_root / "native" / "inc" / obj / f"{obj}_core.h"
    if not header.exists():
        return {}
    raw = extract_doc_blocks(header.read_text(encoding="utf-8"))
    out: dict = {}
    for cname, block_text in raw.items():
        # strip the comp_ prefix to recover the bare method/verb name for the
        # triviality check (e.g. ddc_execute -> execute).
        verb = cname[len(obj) + 1 :] if cname.startswith(obj + "_") else cname
        parsed = parse_doxygen_block(block_text, name=verb)
        if parsed is None:
            continue
        if _is_scaffold_brief(obj, verb, parsed):
            continue
        out[cname] = parsed
    return out


def _is_scaffold_brief(obj: str, verb: str, block) -> bool:
    """True if *block* is just jm's own scaffold-template Doxygen.

    Deriving docs from jm's boilerplate (``Create a <obj> instance.``,
    ``Get current <field>.``, ``Set <field>.``) would (a) be no richer than
    the name fallback and (b) break idempotence: a manifest-only rebuild has
    no header to read, so it must produce the same output as a fresh scaffold.
    Only a non-template brief is derived. jm's lifecycle/accessor scaffolds
    also emit boilerplate @param/@return, so the brief alone is the signal —
    matching it means the whole block is boilerplate.
    """
    brief = block.brief.strip().rstrip(".").lower()
    if not brief:
        return False
    templates = {
        f"create a {obj} instance",
        f"destroy a {obj} instance and release all memory",
        f"reset {obj} to its post-create state",
    }
    if verb.startswith("get_"):
        templates.add(f"get current {verb[4:]}")
        templates.add(f"get a read-only pointer to {verb[4:]}")
        templates.add(f"return a read-only pointer to {verb[4:]}")
    if verb.startswith("set_"):
        templates.add(f"set {verb[4:]}")
        templates.add(f"set {verb[4:]} from src")
    if verb in ("step", "steps"):
        # jm's own scaffold @brief for the built-in step()/steps() methods, by
        # I/O shape. Filtering these keeps a fresh-scaffold header from enriching
        # the .pyi differently than a manifest-only rebuild; a hand-written
        # @brief (anything not in this set) is still derived.
        templates.update(
            {
                "advance state by one tick (no i/o)",
                "consume one input sample (sink; no output)",
                "generate a block of output samples",
                "generate one output sample from internal state",
                "process a block of input samples (no output)",
                "process a block of samples",
                "process n iterations (no scalar output)",
                "process one input buffer and return a result",
                "process one input buffer (no scalar output)",
                "process one input sample",
            }
        )
    return brief in templates


def _indent_body(body: str, indent: str = "    ") -> str:
    """Indent each non-empty line of *body* with *indent* (default 4 spaces).

    Strips leading/trailing whitespace from the full body first so that TOML
    triple-quoted strings with a leading newline don't produce a blank first
    line.
    """
    return "\n".join(
        indent + line if line.strip() else line
        for line in body.strip().splitlines()
    )


def _make_object_ctx(
    component: str,
    module: str,
    pkg: str,
    version: str,
    state_vars: list[tuple[str, str, str]],
    arg_type: str = "float _Complex",
    return_type: str | None = None,
    perf: bool = False,
    array_args: list[tuple[str, str]] = (),
    no_state: bool = False,
    no_step: bool = False,
    mutable: bool = False,
    step_delegates: bool = False,
    init_params: list[tuple] = (),
    init_post_parse_impl: str = "",
    class_name: str | None = None,
    opaque_fields: list[tuple[str, str]] = (),
    no_ctor_names: "frozenset[str]" = frozenset(),
    doc_blocks: dict | None = None,
) -> dict:
    """Build the render ctx for an object."""
    ctx = _make_component_ctx(component)
    if class_name is not None:
        ctx["Component"] = class_name
    ctx.update(
        {
            "module": module,
            "Module": _to_title(module),
            "package": pkg,
            "PACKAGE": pkg.upper(),
            "project": pkg.replace("_", "-"),
            "project_underscore": pkg,
            "version": version,
        }
    )
    ctx.update(Ctx.make_sample_ctx(arg_type, return_type))
    ctx.update(
        Ctx.make_state_ctx(
            ctx["component"],
            ctx["Component"],
            state_vars,
            array_args=array_args,
            no_state=no_state,
            init_params=init_params,
            init_post_parse_impl=init_post_parse_impl,
            opaque_fields=opaque_fields,
            no_ctor_names=no_ctor_names,
        )
    )
    ctx.update(Ctx.make_perf_ctx(perf))
    # gh-92: keep this default in lockstep with make_sample_ctx via the
    # shared resolver — see Ctx.resolve_return_type's docstring.
    _rt = Ctx.resolve_return_type(arg_type, return_type)
    ctx.update(
        Ctx.make_step_ctx(
            ctx,
            arg_type,
            _rt,
            no_step=no_step,
            mutable=mutable,
            delegate=step_delegates,
            doc_blocks=doc_blocks,
        )
    )
    # Re-generate pyi_examples now that package and Component are in ctx.
    # make_state_ctx emits placeholder text; we replace it with the real values.
    scalar_state = (
        [
            (n, ct, dflt)
            for n, ct, dflt in (state_vars or [])
            if not T.parse_array_type(ct)
        ]
        if not no_state
        else []
    )
    has_aa = bool(array_args)
    import_line = f"from {pkg} import {ctx['Component']}"
    ctx["pyi_examples"] = (
        Ctx._pyi_examples_block(
            scalar_state,
            has_aa,
            import_line,
            ctx.get("py_create_args", ""),
            ctx["Component"],
        )
        if (scalar_state or has_aa)
        else ""
    )
    return ctx


def _copy_external_cmake_blocks(
    root: Path, new_comp: str, new_cmake_path: Path
) -> None:
    """Copy ``if(VAR) … endif()`` blocks from sibling CMakeLists to *new_comp*.

    Looks at ``native/src/*/CMakeLists.txt`` files (excluding the new
    component's own file) for ``if(SOME_VAR)`` blocks that contain
    ``target_include_directories`` or ``target_link_libraries``.  The first
    match is adapted (component name replaced) and appended to *new_cmake_path*
    so the new OBJECT library gets the same external library wiring without
    manual edits.

    This handles projects like doppler-based extensions that set
    ``if(DOPPLER_C_LIB)`` blocks — every new component inherits the same
    conditional include/link paths automatically.
    """
    src_dir = root / "native" / "src"
    if not src_dir.exists():
        return

    # Match a standalone if(VAR) … endif() block at the top level of cmake
    block_pat = re.compile(
        r"(if\s*\(\s*(\w+)\s*\)\n(?:[^\n]*\n)*?endif\s*\(\s*\))",
        re.MULTILINE,
    )

    # Guards emitted by just-makeit's own templates — never external wiring.
    known_guards = {"BUILD_PYTHON"}

    for cmake_file in sorted(src_dir.glob("*/CMakeLists.txt")):
        if cmake_file.parent.name == new_comp:
            continue
        text = cmake_file.read_text(encoding="utf-8")
        for m in block_pat.finditer(text):
            block = m.group(1)
            if m.group(2) in known_guards:
                continue
            if (
                "target_include_directories" in block
                or "target_link_libraries" in block
            ):
                old_comp = cmake_file.parent.name
                adapted = block.replace(old_comp, new_comp)
                existing = new_cmake_path.read_text(encoding="utf-8")
                if adapted not in existing:
                    new_cmake_path.write_text(
                        existing.rstrip("\n") + "\n\n" + adapted + "\n",
                        encoding="utf-8",
                    )
                    print(f"  update  {new_cmake_path}  (external lib block)")
                return  # one source file is enough


def _import_re(module: str) -> "re.Pattern[str]":
    """Match a ``from .<module> import ...`` line, single- or multi-line.

    The parenthesised alternative is tried first because its ``(`` would
    otherwise be captured as a "name" by the single-line branch (gh#5); its
    ``[^)]*`` spans newlines, so a formatter-wrapped block matches too.
    """
    return re.compile(
        rf"^from \.{re.escape(module)} import[ \t]*"
        r"(\([^)]*\)|[^\n]*)[^\n]*$",
        re.MULTILINE,
    )


_ALL_RE = re.compile(r"^__all__\s*=\s*\[([^\]]*)\]", re.MULTILINE)


def _parse_import_names(stmt: str) -> list[str]:
    """Names imported by a ``from .x import ...`` statement (either form)."""
    body = re.sub(r"^from \.\w+ import", "", stmt, count=1)
    body = re.sub(r"#[^\n]*", "", body).strip().strip("()")
    out: list[str] = []
    for n in body.split(","):
        name = n.strip()
        if name and name not in out:
            out.append(name)
    return out


def _fmt_from_import(module: str, names: list[str]) -> str:
    """Render a ``from .<module> import ...`` line (single-line canonical).

    jm's ``__init__.py`` glue is single-line by contract — a formatter may
    wrap a long import, and :func:`_import_re` collapses it back on the next
    pass (gh#5/#6). Reexport lines follow the same convention, so adding the
    key never reflows a project's other modules.
    """
    return f"from .{module} import {', '.join(names)}  # noqa: E402"


def _fmt_all(names: list[str]) -> str:
    """Render an ``__all__`` assignment (single-line canonical)."""
    return "__all__ = [" + ", ".join(f'"{n}"' for n in names) + "]"


def _merge_module_init(
    existing: str,
    module: str,
    all_exports: list[str],
    reexports: dict[str, list[str]] | None = None,
) -> str:
    """Merge new exports into an existing __init__.py without destroying content.

    Updates the ``from .<module> import ...`` line and ``__all__`` list to
    include any newly added type/function names, leaving wrapper classes,
    docstrings, and all other user content intact.

    *reexports* (``{submodule: [name, ...]}``, from the manifest) additionally
    emits a ``from .<submodule> import ...`` line per sibling and appends those
    names to ``__all__`` — so symbols re-exported from a hand-written
    ``no_generate`` sibling regenerate cleanly instead of being clobbered.
    Output is single-line canonical, matching jm's existing ``__init__.py``
    glue, so adding the key never reflows a project's other modules.

    >>> src = ('# dsp/__init__.py\\n'
    ...        'from .dsp import Nco  # noqa: E402\\n'
    ...        '__all__ = ["Nco"]\\n')
    >>> print(_merge_module_init(src, 'dsp', ['Nco', 'Mixer']))
    # dsp/__init__.py
    from .dsp import Nco, Mixer  # noqa: E402
    __all__ = ["Nco", "Mixer"]
    <BLANKLINE>

    A fresh module has only ``__all__`` and no import line yet; the import
    line is inserted ahead of it:

    >>> print(_merge_module_init('__all__ = []\\n', 'dsp', ['Nco']))
    from .dsp import Nco  # noqa: E402
    <BLANKLINE>
    __all__ = ["Nco"]
    <BLANKLINE>

    A formatter may reflow a long import into a parenthesized multi-line
    block; the merge collapses it back to a clean single line:

    >>> src = ('from .dsp import (  # noqa: E402\\n'
    ...        '    Ema,\\n'
    ...        '    Iad,\\n'
    ...        ')\\n'
    ...        '__all__ = ["Ema", "Iad"]\\n')
    >>> print(_merge_module_init(src, 'dsp', ['Ema', 'Iad', 'Nco']))
    from .dsp import Ema, Iad, Nco  # noqa: E402
    __all__ = ["Ema", "Iad", "Nco"]
    <BLANKLINE>
    """
    import_pat = _import_re(module)

    m = import_pat.search(existing)
    existing_names = _parse_import_names(m.group(0)) if m else []

    merged: list[str] = list(existing_names)
    seen = set(merged)
    for name in all_exports:
        if name not in seen:
            merged.append(name)
            seen.add(name)

    # Reexports from sibling submodules: merge declared names with any already
    # present in that submodule's import line, in declaration order.
    reexport_lines: dict[str, str] = {}
    reexport_names: list[str] = []
    for sub, names in (reexports or {}).items():
        sm = _import_re(sub).search(existing)
        sub_names = _parse_import_names(sm.group(0)) if sm else []
        sub_seen = set(sub_names)
        for n in names:
            if n not in sub_seen:
                sub_names.append(n)
                sub_seen.add(n)
        if not sub_names:
            continue
        reexport_lines[sub] = _fmt_from_import(sub, sub_names)
        for n in sub_names:
            if n not in reexport_names:
                reexport_names.append(n)

    all_names = merged + [n for n in reexport_names if n not in seen]
    if not all_names:
        return existing

    result = existing

    # 1. Upsert the module's own import line.
    if merged:
        new_import = _fmt_from_import(module, merged)
        if m:
            result = import_pat.sub(lambda _: new_import, result, count=1)
        elif _ALL_RE.search(result):
            result = _ALL_RE.sub(
                lambda am: f"{new_import}\n\n{am.group(0)}", result, count=1
            )
        else:
            result = result.rstrip("\n") + f"\n\n{new_import}\n"

    # 2. Upsert each reexport import line, after the module's own import.
    for sub, line in reexport_lines.items():
        sub_pat = _import_re(sub)
        if sub_pat.search(result):
            result = sub_pat.sub(lambda _: line, result, count=1)
        else:
            anchor = import_pat.search(result) if merged else None
            if anchor:
                result = (
                    result[: anchor.end()]
                    + "\n"
                    + line
                    + (result[anchor.end() :])
                )
            elif _ALL_RE.search(result):
                result = _ALL_RE.sub(
                    lambda am: f"{line}\n\n{am.group(0)}", result, count=1
                )
            else:
                result = result.rstrip("\n") + f"\n{line}\n"

    # 3. Upsert __all__ (module exports followed by reexported names).
    new_all = _fmt_all(all_names)
    if _ALL_RE.search(result):
        result = _ALL_RE.sub(lambda _: new_all, result, count=1)
    else:
        result = result.rstrip("\n") + f"\n{new_all}\n"
    return result


def _extract_c_function_bodies(source: str) -> dict[str, str]:
    """Extract static function bodies from C source.

    Returns ``{function_name: full_function_text}`` for every
    ``static <returntype>\\n<name>(`` function in *source*.  Covers
    ``static PyObject *`` method wrappers and ``static int`` init/traverse
    functions so that hand-patches to any generated function survive
    regeneration of module_ext.c.

    Uses brace-counting rather than a regex for the body so that nested
    braces and parentheses inside parameter lists (e.g. ``Py_UNUSED(...)``)
    are handled correctly.
    """
    # Match "static <return-type>\n<name>(" for any return type.
    # [^\n]+ stops at the newline so struct/array definitions (which have
    # no "(" immediately after the identifier) are not captured.
    header_pat = re.compile(r"static [^\n]+\n(\w+)\(")
    result: dict[str, str] = {}
    for hm in header_pat.finditer(source):
        fn_name = hm.group(1)
        start = hm.start()
        # Scan forward from the match to find the opening '{' of the body.
        i = hm.end()
        # Skip past the parameter list (balanced parens from the '(' we just
        # passed — but header_pat consumed the '(' so depth starts at 1).
        depth = 1
        while i < len(source) and depth:
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
            i += 1
        # Now scan for the opening '{'.
        while i < len(source) and source[i] != "{":
            i += 1
        if i >= len(source):
            continue
        brace_start = i
        # Collect the full body using balanced brace counting.
        depth = 0
        end = brace_start
        while end < len(source):
            if source[end] == "{":
                depth += 1
            elif source[end] == "}":
                depth -= 1
                if depth == 0:
                    end += 1
                    break
            end += 1
        result[fn_name] = source[start:end]
    return result


def _restore_c_function_bodies(
    new_source: str, preserved: dict[str, str]
) -> str:
    """Replace stub implementations in *new_source* with *preserved* bodies.

    Only replaces functions that already existed in the old source AND still
    exist in the newly generated source.  New functions (first-time stubs) are
    left unchanged, so fresh scaffolded methods get their TODO stubs.

    Buffer-lifecycle functions (_dealloc, _init) are always regenerated from
    the template so that newly added variable_output free() and malloc() calls
    are never silently dropped when the old fragment has no buffers yet.

    The stream generator's glue (gh-203: ``*StreamIter_next``, ``_stream``,
    ``_getiter``, ``_make_iter``, and the ``*StreamIter_dealloc``) is likewise
    always regenerated — it is pure generated code that must track the manifest
    (e.g. the producer method name), never a hand-edited body. Without this, a
    fragment frozen at first generation keeps a stale producer when an object
    becomes streamable or gains a variable_output method. The ``_stream`` /
    ``_getiter`` / ``_make_iter`` wrappers are only treated as stream glue when
    the fragment actually carries a ``StreamIter`` type, so a hypothetical
    user method literally named ``stream`` on a non-streamable object keeps its
    hand-written body.
    """
    _INFRA_SUFFIXES = ("_dealloc", "_init")
    _STREAM_SUFFIXES = ("_stream", "_getiter", "_make_iter")
    _has_stream = any("StreamIter" in n for n in preserved)
    for fn_name, old_body in preserved.items():
        if "StreamIter" in fn_name:
            continue
        if _has_stream and fn_name.endswith(_STREAM_SUFFIXES):
            continue
        if fn_name.endswith(_INFRA_SUFFIXES):
            continue
        # Locate the function in new_source using the same brace-counting
        # approach (handles Py_UNUSED and other nested-paren params).
        header_pat = re.compile(
            r"static [^\n]+\n" + re.escape(fn_name) + r"\("
        )
        hm = header_pat.search(new_source)
        if not hm:
            continue
        start = hm.start()
        i = hm.end()
        depth = 1
        while i < len(new_source) and depth:
            if new_source[i] == "(":
                depth += 1
            elif new_source[i] == ")":
                depth -= 1
            i += 1
        while i < len(new_source) and new_source[i] != "{":
            i += 1
        if i >= len(new_source):
            continue
        depth = 0
        end = i
        while end < len(new_source):
            if new_source[end] == "{":
                depth += 1
            elif new_source[end] == "}":
                depth -= 1
                if depth == 0:
                    end += 1
                    break
            end += 1
        new_source = new_source[:start] + old_body + new_source[end:]
    return new_source


def build_component_ctxs(
    root: Path,
    cfg: dict,
    module: str,
    pkg: str,
    *,
    force_fallback: bool = False,
) -> list[dict]:
    """Build the per-object template contexts for every object in *module*.

    Factored out of :func:`_regenerate_module` so a doc-only refresh can render
    a *reference* fragment (the form jm would generate, carrying the derived
    docstrings) without writing anything or touching the aggregator / CMake.
    Each context is what :func:`_render.render_module_ext_fragment` consumes,
    including the derived ``extra_methods_pymethoddef`` / ``getset_def`` /
    ``tp_doc`` doc slots resolved from the sacred header's Doxygen.

    With *force_fallback* both the header Doxygen **and** the authoritative
    TOML ``doc`` overrides are ignored, so every doc slot renders in its pure
    name-based *scaffold* form. The doc refresh renders both variants and only
    overwrites a fragment slot that still holds the scaffold form (or is
    empty), never a hand-written one.
    """
    if force_fallback:
        # Strip every authoritative `doc` so the render falls all the way back
        # to the name-based default (e.g. "Zero-copy view…", "<Class> type.").
        # A deep copy keeps the real cfg (and its transient _doc_blocks stash)
        # untouched.
        cfg = copy.deepcopy(cfg)
        for _obj in C.module_objects(cfg, module):
            _od = cfg.get(_obj)
            if not isinstance(_od, dict):
                continue
            _od.pop("doc", None)
            for _key in ("methods", "properties"):
                for _entry in _od.get(_key, []) or []:
                    if isinstance(_entry, dict):
                        _entry.pop("doc", None)

    comp_ctxs: list[dict] = []
    for obj in C.module_objects(cfg, module):
        # Parse the sacred header's Doxygen once; stash transiently on cfg so
        # the .pyi generator (_stubs, which receives cfg) sees the same blocks
        # without re-reading. The underscore key is dropped by _config._dump.
        _doc_blocks = {} if force_fallback else _load_doc_blocks(root, obj)
        cfg.setdefault(obj, {})["_doc_blocks"] = _doc_blocks
        state_vars = C.state_vars(cfg, obj)
        arg_type_ = C.arg_type(cfg, obj)
        return_type_ = C.return_type(cfg, obj)
        perf = C.is_perf(cfg)
        ctx = _make_object_ctx(
            obj,
            module,
            pkg,
            C.project_version(cfg),
            state_vars,
            arg_type_,
            return_type_,
            perf=perf,
            array_args=C.array_args(cfg, obj),
            no_state=C.is_no_state(cfg, obj),
            no_step=C.is_no_step(cfg, obj),
            mutable=C.is_mutable(cfg, obj),
            step_delegates=C.step_delegates(cfg, obj),
            init_params=C.init_params(cfg, obj),
            class_name=C.class_name(cfg, obj),
            opaque_fields=C.opaque_fields(cfg, obj),
            no_ctor_names=C.no_ctor_names(cfg, obj),
            doc_blocks=_doc_blocks,
        )
        ctx.update(
            Ctx.make_methods_ctx(
                ctx["component"],
                ctx["Component"],
                C.methods(cfg, obj),
                pkg=pkg,
                py_create_args=ctx.get("py_create_args", ""),
                no_state=C.is_no_state(cfg, obj),
                doc_blocks=_doc_blocks,
            )
        )
        ctx.update(
            Ctx.make_properties_ctx(
                ctx["component"],
                ctx["Component"],
                C.properties(cfg, obj),
                frozenset(n for n, _, _ in state_vars),
                doc_blocks=_doc_blocks,
            )
        )
        # Stream generator (gh-203): a `--streamable` module object gets the
        # same stream()/__iter__ as a standalone, filled into its
        # COMPONENT_TYPE_SECTION slots; the per-object PyType_Ready for the
        # iterator type rides along in `stream_module_ready` for the aggregator.
        ctx.update(
            Ctx.make_stream_ctx(
                ctx["component"],
                ctx["Component"],
                ctx["ComponentW"],
                streamable=C.is_streamable(cfg, obj),
                async_stream=C.is_async_stream(cfg, obj),
                methods=C.methods(cfg, obj),
                arg_type=arg_type_,
                return_type=return_type_,
                default_block=C.stream_block_default(cfg, obj),
            )
        )
        # Class C __doc__ (tp_doc): TOML `doc` > create()'s @brief > default.
        _cblk = _doc_blocks.get(f"{obj}_create")
        _cdoc = (
            cfg.get(obj, {}).get("doc")
            or (_cblk.brief if (_cblk and _cblk.brief) else "")
            or f"{ctx['Component']} type."
        )
        ctx["tp_doc"] = _build_ml_doc([_cdoc])
        # Nested-module slots: override `module` to the cname (the fragment
        # file is <cname>_ext_<comp>.c) and supply `module_tp` for the dotted
        # tp_name. For a flat module these equal today's values (zero churn).
        ctx.update(Ctx.make_module_ctx(module, pkg))
        comp_ctxs.append(ctx)
    return comp_ctxs


def _regenerate_module(root: Path, cfg: dict, module: str, pkg: str) -> None:
    """Regenerate module_ext.c, module CMakeLists, and subpackage __init__."""
    object_names = C.module_objects(cfg, module)
    # cname drives the flat native dir / file prefixes; leaf is the .so basename
    # and the collocated-object name; pypath is the nested Python dir. For a
    # flat module all three equal `module` (zero churn).
    mp = C.module_paths(module)
    cname = mp.cname
    Module = _to_title(cname)

    comp_ctxs = build_component_ctxs(root, cfg, module, pkg)

    # Per-object fragment files (<module>_ext_<comp>.c).
    # Each fragment is preserved independently: body-preservation reads from
    # the existing fragment when present, or from the old monolithic ext.c
    # when migrating from a pre-split layout.  Only functions whose names
    # appear in the freshly rendered template survive (no cross-contamination).
    functions = C.module_functions(cfg, module)
    ext_dir = root / "native" / "src" / cname
    ext_c_path = ext_dir / f"{cname}_ext.c"

    # Load migration source once: existing monolith bodies (used as fallback
    # when a fragment file does not yet exist).
    monolith_bodies: dict[str, str] = {}
    if ext_c_path.exists() and comp_ctxs:
        raw = ext_c_path.read_text(encoding="utf-8")
        # Only treat the file as a migration source when it is a monolith
        # (i.e. it does not yet contain #include lines for fragments).
        first_frag = f"{cname}_ext_{comp_ctxs[0]['component']}.c"
        if first_frag not in raw:
            monolith_bodies = _extract_c_function_bodies(raw)

    for ctx in comp_ctxs:
        comp = ctx["component"]
        frag_path = ext_dir / f"{cname}_ext_{comp}.c"
        # Prefer bodies from the existing fragment; fall back to the monolith
        # during migration (only matching function names will be spliced in).
        if frag_path.exists():
            preserved = _extract_c_function_bodies(
                frag_path.read_text(encoding="utf-8")
            )
        else:
            preserved = monolith_bodies
        frag = R.render_module_ext_fragment(ctx)
        if preserved:
            frag = _restore_c_function_bodies(frag, preserved)
        _write(frag_path, frag, "update" if frag_path.exists() else "create")

    # Discover *_extra.c files — jm never creates or modifies them, but
    # includes them in the aggregator so hand-written types survive regen.
    extra_files: set[str] = set()
    for ctx in comp_ctxs:
        comp = ctx["component"]
        if (ext_dir / f"{cname}_ext_{comp}_extra.c").exists():
            extra_files.add(f"{cname}_ext_{comp}_extra.c")
    if (ext_dir / f"{cname}_ext_extra.c").exists():
        extra_files.add(f"{cname}_ext_extra.c")

    # Aggregator (<module>_ext.c) — always overwritten; extra files wired in.
    aggregator = R.render_module_ext_aggregator(
        module,
        comp_ctxs,
        functions,
        extra_files,
        extra_types=C.extra_types(cfg, module),
    )
    _write(ext_c_path, aggregator, "update")

    # Module CMakeLists
    object_list = ", ".join(ctx["Component"] for ctx in comp_ctxs)
    # Comment built here so a functions-only module (empty object list) doesn't
    # render a trailing space (cmake-lint C0303).
    module_comment = f"{module} Python module" + (
        f" — aggregates: {object_list}" if object_list else ""
    )
    # Each non-inline module-level function lives in its own sacred
    # <fn>.c (inline ones live entirely in the header).  Those sources are
    # compiled into the module's OBJECT library alongside <mod>_core.c.
    fn_srcs = [f"{fn['name']}.c" for fn in functions if not fn.get("inline")]
    core_srcs = " ".join([f"{cname}_core.c", *fn_srcs])
    # Collocated case: when an object shares the module name (e.g. module="fft",
    # object="fft"), CMAKE_LISTS_OBJECT_CORE is prepended and already defines
    # <mod>_core; the function sources are appended to that library below.
    # Non-collocated: we define <mod>_core separately so that module-level
    # functions are compiled and linked in.
    # A collocated object shares the module's leaf name (e.g. module "a.fft",
    # object "fft"): CMAKE_LISTS_OBJECT_CORE already defines <leaf>_core.
    has_collocated = mp.leaf in object_names
    extra_inc_dirs = C.extra_include_dirs(cfg, module)
    inc_dirs_extra = (
        "\n    " + "\n    ".join(extra_inc_dirs) if extra_inc_dirs else ""
    )
    if has_collocated:
        # <mod>_core is the collocated object's OBJECT lib; it's already in
        # object_names so it will appear in object_core_libs below.
        module_core_lib_block = ""
        libs_parts = [f"{obj}_core" for obj in object_names]
    else:
        # Module-only OBJECT lib (no collocated object). Use PUBLIC for the
        # include dirs so any extra include dirs propagate transitively to
        # the Python extension when it links against {module}_core.
        module_core_lib_block = (
            f"add_library({cname}_core OBJECT {core_srcs})\n"
            f"target_include_directories({cname}_core PUBLIC"
            f" ${{CMAKE_SOURCE_DIR}}/native/inc{inc_dirs_extra})\n\n"
        )
        libs_parts = [f"{cname}_core"] + [
            f"{obj}_core" for obj in object_names
        ]
    extra_libs = C.extra_link_libs(cfg, module)
    # gh-160: each aggregated object's own extra_link_libs must be linked
    # DIRECTLY onto the module's Python extension. CMake does not pull a
    # PUBLIC-linked OBJECT lib's objects transitively through another OBJECT
    # lib into the final .so, so a cross-module dep like ["obj_a_core"] on a
    # member object would otherwise be missing → ImportError: undefined symbol.
    # gh-225: a member object's `depends_on = [{name="dep", link=true}]` adds
    # the dependency's `<dep>_core` to the module .so link line too, alongside
    # its own extra_link_libs — same direct-link rationale as gh-160.
    _seen = set(libs_parts) | set(extra_libs)
    for _obj in object_names:
        for _lib in C.component_extra_link_libs(
            cfg, _obj
        ) + C.depends_link_libs(cfg, _obj):
            if _lib not in _seen:
                _seen.add(_lib)
                libs_parts.append(_lib)
    object_core_libs = "\n    ".join(libs_parts)
    extra_link_libs_block = (
        "\n    ".join(extra_libs) + "\n    " if extra_libs else ""
    )
    # Collect varargs binding .c files from all objects in this module.
    # These compile into the Python DSO (not the OBJECT lib) because they use
    # Python.h.  Paths are relative to the module CMakeLists location.
    _varargs_srcs: list[str] = []
    for _obj, _ctx_ in zip(object_names, comp_ctxs):
        for _bf in _ctx_.get("varargs_binding_files", []):
            if _obj == mp.leaf:
                _varargs_srcs.append(_bf)
            else:
                _varargs_srcs.append(f"../{_obj}/{_bf}")
    extra_ext_sources = "".join(f" {f}" for f in _varargs_srcs)

    cmake_ctx = {
        # Nested-module slots (module=cname, module_pypath, module_output_name);
        # flat modules collapse these to today's values.
        **Ctx.make_module_ctx(module, pkg),
        "Module": Module,
        "object_list": object_list,
        "module_comment": module_comment,
        "object_core_libs": object_core_libs,
        "module_core_lib_block": module_core_lib_block,
        "extra_link_libs_block": extra_link_libs_block,
        "extra_include_dirs_block": inc_dirs_extra,
        "extra_ext_sources": extra_ext_sources,
        # gh-213: Windows runtime-DLL block, off unless the project targets it.
        **Ctx.make_platform_ctx(C.is_windows_target(cfg), module=cname),
    }
    # Collocated objects share the same CMakeLists file as the module itself;
    # their OBJECT library cmake is prepended before CMAKE_LISTS_MODULE.
    # Migration: if a legacy _methods.c exists on disk, preserve it in the
    # CMakeLists so old projects don't break on regen.  New projects never
    # have _methods.c — stubs go in _core.c.
    collocated_cmake = ""
    for obj, ctx_ in zip(object_names, comp_ctxs):
        if obj == mp.leaf:
            # gh-132: inject the module-level extra_link_libs_block so that
            # the collocated object's test/bench targets link against the
            # same extra libraries as the Python extension.
            # gh-160: also PUBLIC-link them onto the collocated OBJECT lib so
            # the deps propagate transitively to the Python extension. The
            # `jm object` path sets extra_link_on_object_core (run()); apply
            # rebuilds the collocated CMakeLists here, so it must set it too —
            # otherwise the `<<extra_link_on_object_core>>` placeholder leaks
            # into the generated CMakeLists and breaks the build.
            extra_link_on_object_core = (
                f"target_link_libraries({obj}_core PUBLIC\n    "
                + "\n    ".join(extra_libs)
                + ")\n"
                if extra_libs
                else ""
            )
            ctx_cmake = {
                **ctx_,
                "extra_link_libs_block": extra_link_libs_block,
                "extra_link_on_object_core": extra_link_on_object_core,
            }
            obj_cmake = R.render(R.CMAKE_LISTS_OBJECT_CORE, ctx_cmake)
            # Append the collocated object's extra sources: a legacy
            # _methods.c (migration) plus every module-level function .c.
            extra_srcs = []
            methods_c = root / "native" / "src" / obj / f"{obj}_methods.c"
            if methods_c.exists():
                extra_srcs.append(f"{obj}_methods.c")
            extra_srcs.extend(fn_srcs)
            if extra_srcs:
                old_lib = f"add_library({obj}_core OBJECT {obj}_core.c)"
                new_lib = (
                    f"add_library({obj}_core OBJECT "
                    + " ".join([f"{obj}_core.c", *extra_srcs])
                    + ")"
                )
                obj_cmake = obj_cmake.replace(old_lib, new_lib)
            collocated_cmake += obj_cmake
    _write(
        root / "native" / "src" / cname / "CMakeLists.txt",
        collocated_cmake + R.render(R.CMAKE_LISTS_MODULE, cmake_ctx),
        "update",
    )

    # Subpackage __init__.py — merge new exports into existing file so that
    # user-written wrapper classes and docstrings are not destroyed.
    Components = [ctx["Component"] for ctx in comp_ctxs]
    fn_names = [f["name"] for f in functions]
    all_exports = Components + fn_names
    reexports = C.module_reexports(cfg, module)
    # Nested module: ensure the intermediate packages exist, then write under
    # the nested pypath.
    ensure_parent_packages(root, pkg, mp)
    pkg_module_dir = root / "src" / pkg / mp.pypath
    init_path = pkg_module_dir / "__init__.py"
    existed = init_path.exists()
    if existed:
        base = init_path.read_text(encoding="utf-8")
    else:
        # Fresh scaffold: render the template with the module's own exports,
        # then fold in any reexports through the same idempotent merge path.
        base = R.render(
            R.MODULE_INIT_PY,
            {
                **Ctx.make_module_ctx(module, pkg),
                "Module": Module,
                "object_imports": ", ".join(all_exports),
                "object_all": ", ".join(f'"{name}"' for name in all_exports),
            },
        )
    # The import line in __init__.py is `from .<leaf> import ...`, so the merge
    # must match/emit against the leaf, not the dotted id.
    merged = _merge_module_init(base, mp.leaf, all_exports, reexports)
    _write(init_path, merged, "update" if existed else "create")

    # Type stubs — regenerated in full every time the module changes.
    _write(
        pkg_module_dir / f"{mp.leaf}.pyi",
        S.make_module_pyi(cfg, module),
        "update",
    )


def run(
    root: Path,
    object_name: str,
    module: str | None,
    state_vars: list[tuple[str, str, str]] | None = None,
    perf: bool | None = None,
    arg_type: str = "float _Complex",
    return_type: str | None = None,
    array_args: list[tuple[str, str]] = (),
    no_state: bool = False,
    no_step: bool = False,
    mutable: bool = False,
    step_delegates: bool = False,
    streamable: bool = False,
    async_stream: bool = False,
    stream_block_default: int | None = None,
    impl_body: str | None = None,
    create_impl_body: str | None = None,
    reset_impl_body: str | None = None,
    destroy_impl_body: str | None = None,
    init_params: list[tuple] = (),
    init_post_parse_impl: str = "",
    opaque_fields: list[tuple[str, str]] = (),
    no_ctor_names: "frozenset[str]" = frozenset(),
    variable_output: bool = False,
    multi_output: list[str] = (),
    method_name: str = "run",
    class_name: str | None = None,
    depends_on: list[str] = (),
    extra_link_libs: list[str] = (),
    extra_include_dirs: list[str] = (),
    max_out: int = 0,
    _hint: bool = True,
) -> None:
    if not object_name.replace("_", "").isalnum() or object_name[0].isdigit():
        print(
            f"error: '{object_name}' is not a valid object name.\n"
            "Use lowercase letters, digits, and underscores only; "
            "must not start with a digit.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)

    # No --module -> standalone object (own .so)
    if module is None:
        from . import _init

        _init.run(
            root,
            object_name,
            state_vars,
            perf=perf,
            arg_type=arg_type,
            return_type=return_type,
            array_args=array_args,
            no_state=no_state,
            no_step=no_step,
            mutable=mutable,
            step_delegates=step_delegates,
            streamable=streamable,
            async_stream=async_stream,
            stream_block_default=stream_block_default,
            impl_body=impl_body,
            create_impl_body=create_impl_body,
            reset_impl_body=reset_impl_body,
            destroy_impl_body=destroy_impl_body,
            init_params=init_params,
            opaque_fields=opaque_fields,
            no_ctor_names=no_ctor_names,
            class_name=class_name,
            depends_on=list(depends_on),
            extra_link_libs=list(extra_link_libs),
            extra_include_dirs=list(extra_include_dirs),
            _hint=_hint and not variable_output,
        )
        if variable_output:
            from . import _method as _M

            _rt = return_type or arg_type
            _M.run(
                root,
                object_name,
                method_name,
                module=None,
                arg_type="void",
                return_type=_rt,
                variable_output=True,
                multi_output=list(multi_output),
                max_out=max_out,
            )
        return

    # --module given -> in-module path
    mods = C.modules(cfg)
    if module not in mods:
        print(
            f"error: module '{module}' not found. "
            f"Run 'just-makeit module {module}' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    if object_name in C.module_objects(cfg, module):
        print(
            f"error: object '{object_name}' already exists in module '{module}'.",
            file=sys.stderr,
        )
        sys.exit(1)
    if object_name in C.components(cfg):
        print(
            f"error: '{object_name}' already exists as a standalone component.",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = C.project_name(cfg)
    version = C.project_version(cfg)
    if perf is None:
        perf = C.is_perf(cfg)

    # state_vars is None only when the CLI got no --state (use the starter
    # `gain` so a fresh `jm object` isn't empty); an explicit [] (e.g. apply
    # replaying an object whose last field was removed) stays empty.
    if no_state:
        vars_ = []
    elif state_vars is None:
        vars_ = [("gain", "double", "0.0")]
    else:
        vars_ = list(state_vars)
    ctx = _make_object_ctx(
        object_name,
        module,
        pkg,
        version,
        vars_,
        arg_type,
        return_type,
        perf=perf,
        array_args=array_args,
        no_state=no_state,
        no_step=no_step,
        mutable=mutable,
        step_delegates=step_delegates,
        init_params=init_params,
        init_post_parse_impl=init_post_parse_impl,
        class_name=class_name,
        opaque_fields=opaque_fields,
        no_ctor_names=no_ctor_names,
    )
    ctx.update(
        Ctx.make_methods_ctx(
            ctx["component"],
            ctx["Component"],
            [],
            pkg=pkg,
            py_create_args=ctx.get("py_create_args", ""),
            no_state=no_state,
        )
    )

    if create_impl_body is not None:
        ctx["create_assignments"] = _indent_body(create_impl_body)
    if reset_impl_body is not None:
        ctx["reset_assignments"] = _indent_body(reset_impl_body)
    if destroy_impl_body is not None:
        ctx["destroy_impl"] = _indent_body(destroy_impl_body) + "\n"

    # The object's OWN test/bench executables must link both its explicit
    # extra_link_libs AND its depends_on cores — the core calls the
    # dependency's functions (e.g. a sibling's create() via create_impl), so
    # without the dep OBJECT lib the test/bench fail to link (gh-174 follow-up).
    # A depends_on entry may be a component (`nco` -> nco_core) or a bare link
    # target (`lo_core`); normalise to a single `<name>_core` (gh-130).
    _dep_cores = [
        (d[:-5] if d.endswith("_core") else d) + "_core"
        for d in C.dep_names(depends_on)
    ]
    _obj_libs = list(extra_link_libs) + _dep_cores
    # gh-132: expose extra_link_libs_block so CMakeLists_object_core.cmake
    # can include the deps in test/bench target_link_libraries.
    ctx["extra_link_libs_block"] = (
        "\n    ".join(_obj_libs) + "\n    " if _obj_libs else ""
    )
    # gh-160: also link the OBJECT lib itself (PUBLIC) so the deps propagate
    # transitively to every consumer — the aggregating Python extension as well
    # as test/bench. Mirrors extra_link_on_core (_init.py) for standalone
    # components. Without this, a cross-module dep like ["obj_a_core"] reaches
    # only test/bench and the Python .so fails with undefined symbols.
    if _obj_libs:
        _elibs = "\n    ".join(_obj_libs)
        ctx["extra_link_on_object_core"] = (
            f"target_link_libraries({ctx['component']}_core PUBLIC\n"
            f"    {_elibs})\n"
        )
    else:
        ctx["extra_link_on_object_core"] = ""

    # gh-170: include each depends_on component's header so opaque fields of
    # its types compile (mirrors the standalone path in _init.run). Only deps
    # with a real header are included — a bare link target like `lo_core` is
    # skipped.
    from ._init import _dep_header_includes

    ctx["depends_includes"] = "".join(
        "\n" + inc
        for inc in _dep_header_includes(
            root / "native" / "inc", C.dep_names(depends_on)
        )
    )

    def r(tmpl):
        return R.render(tmpl, ctx)

    comp = ctx["component"]
    print(
        f"just-makeit: adding object '{comp}' to module '{module}' in project '{pkg}'"
    )
    print()

    # Perf headers (the JM_DEFINE_STEPS macro + SIMD helpers) live once at the
    # project-root include dir, shared by every component. The standalone path
    # writes them in _init.run; the module path must do the same, else a
    # --perf object added to a non-perf project emits `#include "jm_perf.h"`
    # in its core.h against a file that was never created -> fatal build
    # error. Persist project.perf too so jm apply reproduces the perf build.
    if perf:
        if not C.is_perf(cfg):
            cfg.setdefault("project", {})["perf"] = "true"
        perf_h = root / "native" / "inc" / "jm_perf.h"
        if not perf_h.exists():
            _write(perf_h, r(R.JM_PERF_H), "create")
        simd_h = root / "native" / "inc" / "jm_simd.h"
        if not simd_h.exists():
            _write(simd_h, R.JM_SIMD_H, "create")

    # C library files (OBJECT lib only — no standalone Python module).
    # Object creation is create-only (the verb errors on a duplicate name),
    # so the sacred files are written fresh — never spliced.
    core_h_path = root / "native" / "inc" / comp / f"{comp}_core.h"
    _write(
        core_h_path,
        r(R.COMPONENT_CORE_H),
        "update" if core_h_path.exists() else "create",
    )
    if impl_body is not None and not no_step:
        from . import _impl as I

        h_text = core_h_path.read_text(encoding="utf-8")
        h_text = I.patch_function_body(h_text, f"{comp}_step", impl_body)
        core_h_path.write_text(h_text, encoding="utf-8")
    core_c_path = root / "native" / "src" / comp / f"{comp}_core.c"
    _write(
        core_c_path,
        r(R.COMPONENT_CORE_C),
        "update" if core_c_path.exists() else "create",
    )
    obj_cmake_path = root / "native" / "src" / comp / "CMakeLists.txt"
    _write(obj_cmake_path, r(R.CMAKE_LISTS_OBJECT_CORE))
    # Propagate any external-library cmake blocks from sibling objects so the
    # new component picks up the same if(SOME_LIB) include/link wiring without
    # manual edits (e.g. if(DOPPLER_C_LIB) in doppler-based projects).
    _copy_external_cmake_blocks(root, comp, obj_cmake_path)
    _write(
        root / "native" / "tests" / f"test_{comp}_core.c",
        r(R.COMPONENT_TEST_C),
    )
    _write(
        root / "native" / "benchmarks" / f"bench_{comp}_core.c",
        r(R.NO_STEP_BENCH_C if no_step else R.COMPONENT_BENCH_C),
    )
    jm_bench_h = root / "native" / "benchmarks" / "jm_bench.h"
    if not jm_bench_h.exists():
        _write(jm_bench_h, R.JM_BENCH_H)

    # Python tests and benchmarks for this module object — under the nested
    # pypath (src/<pkg>/dsp/filters/) for a dotted module id.
    pkg_mod_dir = root / "src" / pkg / C.module_paths(module).pypath
    tests_init = pkg_mod_dir / "tests" / "__init__.py"
    if not tests_init.exists():
        _write(tests_init, R.TESTS_INIT_PY)
    test_py_tmpl = (
        R.MODULE_PYTEST_TEST_PURE if C.is_pytest(cfg) else R.MODULE_PYTEST_TEST
    )
    bench_py_tmpl = (
        R.MODULE_BENCH_PYTEST_BM
        if C.is_pytest_benchmark(cfg)
        else R.MODULE_BENCH_PY
    )
    _write(pkg_mod_dir / "tests" / f"test_{comp}.py", r(test_py_tmpl))
    benchmarks_init = pkg_mod_dir / "benchmarks" / "__init__.py"
    if not benchmarks_init.exists():
        _write(benchmarks_init, "")
    _write(pkg_mod_dir / "benchmarks" / f"bench_{comp}.py", r(bench_py_tmpl))

    # Update config before regenerating module (so module_objects is up-to-date)
    C.add_to_module(cfg, module, comp)
    C.add_component(
        cfg,
        comp,
        vars_,
        arg_type_=arg_type,
        return_type_=return_type,
        array_args_=array_args,
        no_state_=no_state,
        no_step_=no_step,
        mutable_=mutable,
        step_delegates_=step_delegates,
        streamable_=streamable,
        async_stream_=async_stream,
        stream_block_default_=stream_block_default,
        init_params_=init_params,
        class_name_=class_name,
        depends_on_=list(depends_on),
        opaque_fields_=list(opaque_fields),
        no_ctor_names_=no_ctor_names,
        # gh-160: persist extra_link_libs/extra_include_dirs for module objects
        # too (the standalone path already did). Without this they're dropped
        # from the manifest, so the module aggregation can't propagate a
        # cross-module dep to the Python extension and reload/apply lose it.
        extra_link_libs_=list(extra_link_libs),
        extra_include_dirs_=list(extra_include_dirs),
    )

    # Regenerate module ext.c + CMakeLists + subpackage __init__
    _regenerate_module(root, cfg, module, pkg)

    # Root CMakeLists: insert add_subdirectory into Components sentinel section,
    # then wire OBJECT library into both shared and static C library targets.
    # These two operations are independent: a same-name module (module.agc with
    # object "agc") may have the add_subdirectory already present from the
    # `just-makeit module` step, but the target_sources lines still need adding.
    cmake_path = root / "CMakeLists.txt"
    if cmake_path.exists():
        cmake_text = cmake_path.read_text(encoding="utf-8")
        changed = False
        sub = f"add_subdirectory(native/src/{comp})\n"
        if sub not in cmake_text:
            sentinel = "# ── Components"
            if sentinel in cmake_text:
                idx = cmake_text.index(sentinel)
                idx = cmake_text.index("\n", idx) + 1
                cmake_text = cmake_text[:idx] + sub + cmake_text[idx:]
            else:
                cmake_text += sub
            changed = True
        obj_lines = ""
        for dep in C.dep_names(depends_on):
            # gh-130: strip a trailing _core suffix so callers can write
            # depends_on = ["lo_core"] without getting lo_core_core.
            dep_name = dep[:-5] if dep.endswith("_core") else dep
            ts = (
                f"target_sources({pkg}_lib PRIVATE "
                f"$<TARGET_OBJECTS:{dep_name}_core>)\n"
            )
            if ts not in cmake_text:
                obj_lines += (
                    ts + f"target_sources({pkg}_lib_static PRIVATE "
                    f"$<TARGET_OBJECTS:{dep_name}_core>)\n"
                )
        ts = f"target_sources({pkg}_lib PRIVATE $<TARGET_OBJECTS:{comp}_core>)\n"
        if ts not in cmake_text:
            obj_lines += (
                ts + f"target_sources({pkg}_lib_static PRIVATE "
                f"$<TARGET_OBJECTS:{comp}_core>)\n"
            )
        if obj_lines:
            sub_idx = cmake_text.find(sub)
            if sub_idx != -1:
                ins = cmake_text.index("\n", sub_idx) + 1
                cmake_text = cmake_text[:ins] + obj_lines + cmake_text[ins:]
            else:
                cmake_text += obj_lines
            changed = True
        if changed:
            cmake_path.write_text(cmake_text, encoding="utf-8")
            print(f"  update  {cmake_path}")

    # Umbrella header
    umbrella = root / "native" / "inc" / f"{pkg}.h"
    include_line = f'#include "{comp}/{comp}_core.h"\n'
    if umbrella.exists():
        umbrella_text = umbrella.read_text(encoding="utf-8")
        if include_line not in umbrella_text:
            umbrella_text = umbrella_text.replace(
                "#ifdef __cplusplus\n}\n#endif\n\n#endif",
                f"{include_line}\n#ifdef __cplusplus\n}}\n#endif\n\n#endif",
            )
            umbrella.write_text(umbrella_text, encoding="utf-8")
            print(f"  update  {umbrella}")

    # compile_commands.json
    all_comps = C.components(cfg)
    _write_compile_commands(root, all_comps, C.modules(cfg))

    # Save config
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    if variable_output:
        from . import _method as _M

        _rt = return_type or arg_type
        _M.run(
            root,
            object_name,
            method_name,
            module=module,
            arg_type="void",
            return_type=_rt,
            variable_output=True,
            multi_output=list(multi_output),
            max_out=max_out,
        )
    else:
        print()
        print(f"{Color.done('Done!')}  {Color.cmd('cmake --build build')}")
