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

import ast as _ast
import copy
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

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
from ._docstring import (
    DoxyBlock,
    extract_doc_blocks,
    extract_member_docs,
    member_doc_key,
    header_default,
    authored_class_brief,
    is_scaffold_doc,
    max_out_arity_key,
    parse_doxygen_block,
    scan_max_out_arity,
)
from ._context._parse import _build_ml_doc

# When `jm apply` regenerates glue, it replays the scaffold into a throwaway
# temp tree whose headers carry only template Doxygen. Docstring derivation
# must instead read the REAL project's sacred `_core.h`, so apply sets this
# override to the real project root for the duration of the replay.
_DOC_ROOT_OVERRIDE: Path | None = None


# A project-local include: `#include "psd/psd_core.h"`. Angle-bracket includes
# are system or third-party headers and are never followed.
_LOCAL_INCLUDE_RE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.M)

# Parsed member docs per header file. `jm apply` re-derives for every object,
# and a shared header (doppler's `measure/measure_core.h`) is included by many
# of them, so without this the same file is read and regex-scanned once per
# object — the shape of the quadratic that made apply hang in gh-698.
#
# Keyed on identity AND stat, because a test (or a user mid-session) can
# rewrite a header between two applies in one process; a path-only key would
# serve the pre-edit text and the edit would look like it did nothing.
_MEMBER_DOC_CACHE: dict[tuple[Path, int, int], tuple[dict, str]] = {}


def _header_member_docs(path: Path) -> tuple[dict[str, str], str]:
    """``(member_docs, text)`` for one header, memoized on path+mtime+size."""
    st = path.stat()
    key = (path, st.st_mtime_ns, st.st_size)
    hit = _MEMBER_DOC_CACHE.get(key)
    if hit is None:
        body = path.read_text(encoding="utf-8", errors="replace")
        hit = (extract_member_docs(body), body)
        _MEMBER_DOC_CACHE[key] = hit
    return hit


def _included_member_docs(inc_root: Path, text: str) -> dict[str, str]:
    """Member docs from the project headers *text* includes (gh-724).

    A struct a component returns is often declared in a *shared* header that
    the component's own header includes — doppler's `tonemeas_core.h` includes
    `measure/measure_core.h`, where `tone_meas_t` and its per-field comments
    actually live. Reading only the sacred header meant the `///<` fallback
    that gh-671 gave properties could never reach a result record's fields,
    so the same sentence had to be restated as a manifest ``doc=``.

    Deliberately narrow, and each limit is load-bearing:

    - **Member docs only.** Declaration blocks stay owned by the component's
      own header. Following includes for those would let one component's
      `@brief` land on another's method of the same name.
    - **Quoted, project-local includes only**, resolved under ``native/inc``.
      A system header cannot document this project's fields.
    - **Transitive, with a visited set**, because a shared header may itself
      include the one holding the struct. Cycles are the normal case in C
      (every header has an include guard), so the set is required, not a
      precaution.

    The caller merges these *under* the sacred header's own, so a name declared
    in both keeps the component's own text — the same "nearest wins" rule the
    rest of derivation follows.
    """
    out: dict[str, str] = {}
    seen: set[Path] = set()
    pending = [text]
    while pending:
        for rel in _LOCAL_INCLUDE_RE.findall(pending.pop()):
            path = (inc_root / rel).resolve()
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            docs, body = _header_member_docs(path)
            pending.append(body)
            for name, doc in docs.items():
                out.setdefault(name, doc)
    return out


def _load_doc_blocks(root: Path, obj: str) -> dict:
    """Parse Doxygen comments from the sacred ``<obj>_core.h``.

    Returns ``{c_function_name: DoxyBlock}`` for every documented declaration,
    or ``{}`` when the header is absent or carries no usable comments. The
    header is the single source of truth for docstrings; generators derive
    Python docs from these blocks and fall back to name-based stubs otherwise.
    """
    doc_root = _DOC_ROOT_OVERRIDE or root
    inc_root = doc_root / "native" / "inc"
    header = inc_root / obj / f"{obj}_core.h"
    if not header.exists():
        return {}
    text = header.read_text(encoding="utf-8")
    raw = extract_doc_blocks(text)
    out: dict = {}
    # gh-671: trailing `///<` / `/**<` member docs ride in the same map under a
    # reserved key. They are not declaration blocks, so the scaffold-brief
    # filter below does not apply to them — jm never scaffolds a member doc, so
    # anything found here was written by a human.
    #
    # gh-724: included project headers first, so the component's own header
    # overwrites them below and wins any name it declares itself.
    for _mname, _mdoc in _included_member_docs(inc_root, text).items():
        out[member_doc_key(_mname)] = DoxyBlock(brief=_mdoc)
    for _mname, _mdoc in extract_member_docs(text).items():
        out[member_doc_key(_mname)] = DoxyBlock(brief=_mdoc)
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
    # gh-761: the `_max_out` prototypes' arity, from the same header read.
    # Not a doc block, but it rides the same map for the same reason the
    # member docs above do — see `_docstring.max_out_arity_key`.
    #
    # Only when non-empty: a freshly scaffolded header derives nothing, and
    # callers (and gh-666's test) read an empty map as "this header says
    # nothing yet". A key whose value is an empty set would make that false
    # while meaning exactly the same thing.
    _arity = scan_max_out_arity(text)
    if _arity:
        out[max_out_arity_key()] = _arity
    return out


def init_param_drift(
    cfg: dict, root: Path, obj: str
) -> list[tuple[str, str, str]]:
    """Init-params whose manifest default disagrees with the header's own.

    Returns ``(name, manifest_default, header_default)`` for every
    ``init_params`` entry whose manifest ``default`` and the sacred
    ``<obj>_core.h`` create()'s ``@param name ... (default: X)`` numerically
    disagree (gh-442) — jm juxtaposes both sources when building the ``.pyi``
    docstring, so an out-of-band edit to just one of them (a manifest default
    retuned, or hand doc left stale, or vice versa) silently reads as
    corruption in the generated stub with no other signal. Best-effort:
    either side missing, or not parseable as a number, is skipped rather than
    reported — a false negative here is fine, a false positive is not.
    """
    doc_blocks = _load_doc_blocks(root, obj)
    create_blk = doc_blocks.get(f"{obj}_create")
    if create_blk is None:
        return []
    drift: list[tuple[str, str, str]] = []
    for name, _ctype, dflt, *_rest in C.init_params(cfg, obj):
        if not dflt:
            continue
        hdr_dflt = header_default(create_blk.param_desc(name))
        if hdr_dflt is None:
            continue
        try:
            if float(dflt) == float(hdr_dflt):
                continue
        except ValueError:
            continue
        drift.append((name, dflt, hdr_dflt))
    return drift


def _load_module_doc_blocks(root: Path, module: str) -> dict:
    """Parse Doxygen for a module's free functions from ``<module>_core.h``.

    Mirrors :func:`_load_doc_blocks` but for ``[[module.X.functions]]``
    declarations, whose Doxygen lives in
    ``native/inc/<module>/<module>_core.h`` keyed by the **bare** function
    name (no ``<obj>_`` prefix). Returns ``{c_function_name: DoxyBlock}``, or
    ``{}`` when the header is absent or carries no usable comments — so a
    freshly scaffolded function (jm injects only a bare declaration, no
    Doxygen) falls back to the name-based stub, preserving idempotence.
    """
    doc_root = _DOC_ROOT_OVERRIDE or root
    header = doc_root / "native" / "inc" / module / f"{module}_core.h"
    if not header.exists():
        return {}
    raw = extract_doc_blocks(header.read_text(encoding="utf-8"))
    out: dict = {}
    for cname, block_text in raw.items():
        parsed = parse_doxygen_block(block_text, name=cname)
        if parsed is None:
            continue
        # The obj-lifecycle scaffold templates never match a free-function
        # brief, so this is a harmless guard against any boilerplate.
        if _is_scaffold_brief(module, cname, parsed):
            continue
        out[cname] = parsed
    return out


def _is_scaffold_brief(obj: str, verb: str, block) -> bool:
    """True if *block* is just jm's own scaffold-template Doxygen.

    Thin owner-aware wrapper over :func:`_docstring.is_scaffold_doc`, which is
    the single definition of what jm's own boilerplate looks like (gh-666).
    This once carried its own copy of the template set; the copy predated the
    equivalent check inside ``parse_doxygen_block``, and the two disagreed —
    the parser recognised ``@brief <verb>.`` only for a block with no
    ``@param`` at all, so the method skeleton, which does carry generated
    ``@param`` lines, was derived into the ``.pyi`` as if authored.
    """
    return is_scaffold_doc(block, verb, obj)


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
    no_reset: bool = False,
    mutable: bool = False,
    step_delegates: bool = False,
    init_params: list[tuple] = (),
    init_post_parse_impl: str = "",
    class_name: str | None = None,
    opaque_fields: list[tuple[str, str]] = (),
    opaque_state: bool = False,
    no_ctor_names: "frozenset[str]" = frozenset(),
    controllable: list[tuple[str, str]] = (),
    doc_blocks: dict | None = None,
    block_sizes: "list[int] | None" = None,
    create_fn: str | None = None,
) -> dict:
    """Build the render ctx for an object (or a view — gh-504).

    A view passes ``class_name`` (its Python-facing name) and ``create_fn``
    (its C constructor) while ``component`` stays the parent's, so the ctx
    shares the parent's ``<component>_state_t``/core but registers a distinct
    type built from a different constructor.
    """
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
    ctx.update(Ctx.make_sample_ctx(arg_type, return_type, block_sizes))
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
            opaque_state=opaque_state,
            no_ctor_names=no_ctor_names,
            create_fn=create_fn,
            no_reset=no_reset,
            # gh-644: reset() was the one built-in whose runtime __doc__ never
            # consulted the header on this path either -- step/steps derived,
            # reset kept the canned literal, from the same parsed blocks.
            doc_blocks=doc_blocks,
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
            controllable=controllable,
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
            no_reset=no_reset,
        )
        if (scalar_state or has_aa)
        else ""
    )
    return ctx


# A standalone `if(VAR) … endif()` block at the top level of a CMakeLists.
# Group 1 is the whole block, group 2 the guard variable.
_EXTERNAL_BLOCK_RE = re.compile(
    r"(if\s*\(\s*(\w+)\s*\)\n(?:[^\n]*\n)*?endif\s*\(\s*\))",
    re.MULTILINE,
)
# Guards emitted by just-makeit's own templates — never external wiring.
_CMAKE_KNOWN_GUARDS = {"BUILD_PYTHON"}


def _external_cmake_blocks(text: str) -> "list[str]":
    """Return the external ``if(VAR) … endif()`` wiring blocks in *text*.

    A block qualifies when its guard is not one jm itself emits
    (:data:`_CMAKE_KNOWN_GUARDS`) and it carries
    ``target_link_libraries`` / ``target_include_directories`` — i.e. a
    user-added conditional like ``if(DOPPLER_C_LIB)``. These are content
    ``jm apply`` cannot re-derive from the manifest, so the reconcile path
    preserves them across an overwrite (gh-271)."""
    out: list[str] = []
    for m in _EXTERNAL_BLOCK_RE.finditer(text):
        block, guard = m.group(1), m.group(2)
        if guard in _CMAKE_KNOWN_GUARDS:
            continue
        if (
            "target_include_directories" in block
            or "target_link_libraries" in block
        ):
            out.append(block)
    return out


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

    for cmake_file in sorted(src_dir.glob("*/CMakeLists.txt")):
        if cmake_file.parent.name == new_comp:
            continue
        text = cmake_file.read_text(encoding="utf-8")
        for block in _external_cmake_blocks(text):
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


def _parse_all_names(body: str) -> list[str]:
    """Names listed in an ``__all__ = [...]`` body (the text between the
    brackets, as captured by :data:`_ALL_RE`).

    Comments are stripped and quoting is normalised, so both ``"Nco"`` and
    ``'Nco'`` parse. Order is preserved and duplicates collapse.

    >>> _parse_all_names('"Nco", "Mixer"')
    ['Nco', 'Mixer']
    >>> _parse_all_names('')
    []
    """
    body = re.sub(r"#[^\n]*", "", body)
    out: list[str] = []
    for chunk in body.split(","):
        name = chunk.strip().strip("\"'")
        if name and name not in out:
            out.append(name)
    return out


def _fmt_all(names: list[str]) -> str:
    """Render an ``__all__`` assignment (single-line canonical)."""
    return "__all__ = [" + ", ".join(f'"{n}"' for n in names) + "]"


def package_siblings(cfg: dict, module: str) -> list[str]:
    """Leaf names of the other modules whose Python artifacts share
    *module*'s package directory (gh-523).

    Two modules land in one package when one of them declares
    ``package = "<the other>"`` (doppler's ``wfm_reader`` into ``wfm``), so
    they merge into the *same* ``__init__.py``. Each is authoritative only for
    its own ``from .<leaf> import ...`` line; the returned leaves let
    :func:`_merge_module_init` recognise the neighbour's exports and leave them
    alone instead of pruning them out of ``__all__`` on every other apply.
    """
    mp = C.module_paths(module)
    out_pkg = C.module_package(cfg, module) or mp.pypath
    return [
        C.module_paths(other).leaf
        for other in C.modules(cfg)
        if other != module
        and (C.module_package(cfg, other) or C.module_paths(other).pypath)
        == out_pkg
    ]


def _leading_docstring(text: str) -> str:
    """The module docstring at the top of *text*, trailing blank lines included.

    The inverse of :func:`_merge_module_docstring`'s input: it reads back what
    that function (or the ``MODULE_INIT_PY`` template) wrote, so ``apply`` can
    carry a freshly rendered docstring onto the real file without re-deriving
    it from the manifest. Empty when there is none.

    >>> _leading_docstring('\"\"\"Filters.\"\"\"\\n\\nimport os\\n')
    '\"\"\"Filters.\"\"\"\\n\\n'
    >>> _leading_docstring('import os\\n')
    ''
    """
    try:
        tree = _ast.parse(text)
    except SyntaxError:
        return ""
    body = tree.body
    if not (
        body
        and isinstance(body[0], _ast.Expr)
        and isinstance(body[0].value, _ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return ""
    lines = text.splitlines(keepends=True)
    end = body[0].end_lineno or body[0].lineno
    while end < len(lines) and not lines[end].strip():
        end += 1
    return "".join(lines[:end])


def _merge_module_docstring(existing: str, docstring_py: str) -> str:
    """Put the manifest's module docstring at the top of an ``__init__.py``.

    gh-695. ``[module.X] doc`` reached this file only through the template,
    and the template is rendered **only when the file does not yet exist** —
    so a module that gained a ``doc`` after it was scaffolded kept its
    docstring-less shim forever, while the same string did reach the C
    extension's ``m_doc``. The result was the worst of both: `help()` on the
    inner ``pkg.mod.mod`` was documented and the public ``pkg.mod`` that
    everyone actually imports was not.

    Overwriting the file is not an option — it is a merge target that holds
    user-written wrapper classes — so the docstring is spliced in place.

    Two rules, both load-bearing:

    - **An empty *docstring_py* changes nothing.** Undeclared, this must not
      strip a docstring somebody wrote by hand; jm owns the string only when
      the manifest declares one.
    - **A declared doc replaces the existing leading docstring**, because the
      manifest is the source of truth for it (the same precedence ``m_doc``
      uses). Editing the prose in the manifest therefore updates the file on
      the next ``apply``, which is the whole point.

    Parsing is by :mod:`ast` rather than by regex so a docstring containing
    ``#``, quotes, or a blank line is located exactly. A file that does not
    parse (mid-edit, or hand-broken) is left alone rather than mangled.

    >>> src = '# m/__init__.py\\nfrom .m import A  # noqa: E402\\n'
    >>> print(_merge_module_docstring(src, '\"\"\"Filters.\"\"\"\\n\\n'))
    \"\"\"Filters.\"\"\"
    <BLANKLINE>
    # m/__init__.py
    from .m import A  # noqa: E402
    <BLANKLINE>

    An existing docstring is replaced, not duplicated -- so this is
    idempotent, which the manifest-drift gate requires:

    >>> once = _merge_module_docstring(src, '\"\"\"Filters.\"\"\"\\n\\n')
    >>> once == _merge_module_docstring(once, '\"\"\"Filters.\"\"\"\\n\\n')
    True

    With nothing declared, a hand-written docstring survives untouched:

    >>> hand = '\"\"\"Mine.\"\"\"\\n\\nfrom .m import A\\n'
    >>> _merge_module_docstring(hand, '') == hand
    True
    """
    if not docstring_py:
        return existing
    try:
        tree = _ast.parse(existing)
    except SyntaxError:
        return existing
    lines = existing.splitlines(keepends=True)
    body = tree.body
    if (
        body
        and isinstance(body[0], _ast.Expr)
        and isinstance(body[0].value, _ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        # Drop the old docstring and any blank lines padding it, so replacing
        # it does not accumulate a blank line per apply.
        end = body[0].end_lineno or body[0].lineno
        while end < len(lines) and not lines[end].strip():
            end += 1
        rest = "".join(lines[end:])
    else:
        rest = existing
    return docstring_py + rest


def _merge_module_init(
    existing: str,
    module: str,
    all_exports: list[str],
    reexports: dict[str, list[str]] | None = None,
    siblings: list[str] | None = None,
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

    # Authoritative: the module's own import line holds exactly its current
    # C-extension exports. Keep surviving names in their existing order, drop
    # any the manifest no longer declares (gh-329: a removed object must not
    # linger as a stale `from .<module> import Old` → ImportError at runtime),
    # then append newly added names.
    export_set = set(all_exports)
    merged: list[str] = [n for n in existing_names if n in export_set]
    seen = set(merged)
    # gh-523: names bound by a *sibling* module's own import line in this same
    # package (`package = "..."` puts two modules in one __init__.py). They are
    # that module's to manage, so they must survive this one's `__all__`
    # rewrite — otherwise the two prune each other on alternate applies and the
    # file never converges.
    protected: set[str] = set()
    for sib in siblings or []:
        sm = _import_re(sib).search(existing)
        if sm:
            protected |= set(_parse_import_names(sm.group(0)))
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
        existing_sub = _parse_import_names(sm.group(0)) if sm else []
        # Authoritative within the sub too (gh-329): drop names the manifest
        # dropped from this reexport, keep surviving order, append the rest.
        declared = set(names)
        sub_names = [n for n in existing_sub if n in declared]
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

    # gh-342: deliberately NO whole-line sweep here. A `from .<sub> import …`
    # line whose submodule is no longer in the manifest cannot be reliably
    # distinguished from a hand-written import — the noqa glue marker is not
    # jm-exclusive (doppler hand-writes it), and a line-based filter shears
    # the opener off an adjacent multi-line `from .x import ( … )`, leaving an
    # orphaned body (IndentationError). The reconcile therefore only ever
    # rewrites the statements it owns (the module's own line and the manifest's
    # *current* reexport lines, both handled above and multi-line-safe via
    # _import_re); it never deletes a statement jm cannot prove it generated.
    # The cost is that a fully-removed reexport sibling leaves a stale line for
    # the user to delete — strictly better than corrupting hand content.

    # 3. Upsert __all__ (module exports followed by reexported names). gh-523:
    # a sibling module sharing this package keeps its own entries, in their
    # existing positions, so the two modules converge instead of taking turns
    # deleting each other. Everything else is still authoritative — a name the
    # manifest dropped goes (gh-329).
    am = _ALL_RE.search(result)
    keep_set = set(all_names) | protected
    ordered = (
        [n for n in _parse_all_names(am.group(1)) if n in keep_set]
        if am
        else []
    )
    new_all = _fmt_all(ordered + [n for n in all_names if n not in ordered])
    if am:
        result = _ALL_RE.sub(lambda _: new_all, result, count=1)
    else:
        result = result.rstrip("\n") + f"\n{new_all}\n"
    return result


_NORMALIZE_WS_RE = re.compile(r"\s+")


def _extract_c_function_bodies(
    source: str, require_static: bool = True
) -> dict[str, str]:
    """Extract function bodies from C source.

    Returns ``{function_name: full_function_text}`` for every
    ``static <returntype>\\n<name>(`` function in *source* (or, with
    ``require_static=False``, every ``<returntype>\\n<name>(`` function,
    static or not — used to preserve hand-written bodies in a sacred
    ``_core.c``/``_core.h``, whose public API functions carry no ``static``
    keyword). Covers ``static PyObject *`` method wrappers and ``static int``
    init/traverse functions so that hand-patches to any generated function
    survive regeneration of module_ext.c.

    Uses brace-counting rather than a regex for the body so that nested
    braces and parentheses inside parameter lists (e.g. ``Py_UNUSED(...)``)
    are handled correctly. The "type on its own line, name( on the next"
    house style (this project's clang-format convention) means single-line
    signatures — the generated dispatch loops (``*_steps``) and macro
    invocations — never match, so pure boilerplate is naturally excluded
    without an explicit deny-list.

    gh-770: the gap between the name and ``(`` is optional whitespace, not
    nothing. GNU style — which is what ``BasedOnStyle: GNU`` gives, and what
    every downstream that runs clang-format over jm's C ends up with — sets
    ``SpaceBeforeParens: Always`` and writes ``name (args)``. Anchoring on
    ``name(`` made this function return ``{}`` for such a file, and an empty
    extraction is not inert: :func:`_restore_c_function_bodies` then has
    nothing to restore and the caller writes the fresh render over every
    hand-patched body in the fragment. Silent, total, and only visible once
    the code is already gone.
    """
    # Match "[static ]<return-type>\n<name>[ ](" for any return type.
    # [^\n]+ stops at the newline so struct/array definitions (which have
    # no "(" after the identifier) are not captured. Only spaces and tabs
    # are allowed before the "(" — a newline there would let the scan walk
    # into an unrelated construct.
    prefix = r"static " if require_static else r"(?:static )?"
    header_pat = re.compile(prefix + r"[^\n]+\n(\w+)[ \t]*\(")
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
        # Now scan for the opening '{', bailing out on a bare ';' first —
        # a forward-declared prototype (only possible when require_static
        # is False; _core.h declares create/destroy/reset/getters/setters
        # ahead of their definitions) has no body, and without this guard
        # the scan would run past it into an unrelated function's braces.
        while i < len(source) and source[i] not in "{;":
            i += 1
        if i >= len(source) or source[i] == ";":
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
    new_source: str,
    preserved: dict[str, str],
    require_static: bool = True,
    force_regen: "tuple[str, ...]" = (),
) -> str:
    """Replace stub implementations in *new_source* with *preserved* bodies.

    Only replaces functions that already existed in the old source AND still
    exist in the newly generated source.  New functions (first-time stubs) are
    left unchanged, so fresh scaffolded methods get their TODO stubs.

    ``require_static`` mirrors :func:`_extract_c_function_bodies` — pass
    False when *new_source* is a sacred ``_core.c``/``_core.h`` whose public
    functions carry no ``static`` keyword.

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

    *force_regen* names further functions to regenerate rather than preserve.
    gh-541 uses it for the teardown wrappers of an object that declares
    ``[<obj>.destroy]``: whether ``__exit__`` propagates a failed close is
    manifest-derived, and a fragment frozen before the declaration would keep
    the swallowing body — silently, which is the precise bug the declaration
    exists to remove. Applied only to declaring objects, so a fragment whose
    object has no destroy table is preserved exactly as before.
    """
    _INFRA_SUFFIXES = ("_dealloc", "_init")
    _STREAM_SUFFIXES = ("_stream", "_getiter", "_make_iter")
    _has_stream = any("StreamIter" in n for n in preserved)
    for fn_name, old_body in preserved.items():
        if fn_name in force_regen:
            continue
        if "StreamIter" in fn_name:
            continue
        if _has_stream and fn_name.endswith(_STREAM_SUFFIXES):
            continue
        if fn_name.endswith(_INFRA_SUFFIXES):
            continue
        # Locate the function in new_source using the same brace-counting
        # approach (handles Py_UNUSED and other nested-paren params).
        prefix = r"static " if require_static else r"(?:static )?"
        header_pat = re.compile(
            prefix + r"[^\n]+\n" + re.escape(fn_name) + r"[ \t]*\("
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
        # Bail on a bare ';' (forward-declared prototype) before scanning
        # for '{' — see the matching guard in _extract_c_function_bodies.
        while i < len(new_source) and new_source[i] not in "{;":
            i += 1
        if i >= len(new_source) or new_source[i] == ";":
            continue
        # gh-267: bail if the signature itself changed (e.g. `jm add`/
        # `jm remove --state` growing or shrinking a lifecycle function's
        # parameter list). Splicing an old body under a new signature either
        # fails to compile or silently skips initializing new parameters —
        # worse than just keeping the freshly regenerated body. Only reached
        # when require_static=False; ext.c wrapper signatures are fixed
        # CPython boilerplate that never varies across a regeneration.
        # gh-770: compared with ALL whitespace removed, not collapsed to
        # single spaces. The two sides routinely differ only in layout — the
        # fragment on disk has been through the project's formatter, the
        # fresh render has not — and `PyObject *\nFir_step (FirObject *self)`
        # vs `PyObject *\nFir_step(FirObject *self)` is the same signature
        # written twice. Collapsing to " " kept that space and read as a
        # changed signature, so the restore was skipped and the hand-written
        # body dropped. Removing whitespace outright can only conflate
        # spellings that are not both valid C.
        old_sig = _NORMALIZE_WS_RE.sub("", old_body[: old_body.index("{")])
        new_sig = _NORMALIZE_WS_RE.sub("", new_source[start:i])
        if old_sig != new_sig:
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


def _view_frag_id(view: dict) -> str:
    """Fragment id for a view's per-type section file (gh-504).

    Distinct from the parent's ``component`` so the view's fragment
    (``<module>_ext_<frag_id>.c``) does not overwrite the parent's. Derived
    from the lowercased class_name, which is already validated unique within
    the module by the view generator.
    """
    return view["class_name"].lower()


def _make_view_ctx(
    root: Path,
    cfg: dict,
    module: str,
    pkg: str,
    obj: str,
    view: dict,
    doc_blocks: dict,
) -> dict:
    """Build the render ctx for a view (gh-504): a second class over *obj*'s core.

    The ctx reuses the parent object's ``component`` (so it shares
    ``<component>_state_t``, the core ``#include`` and ``_destroy``/``_reset``)
    but overrides ``class_name`` and ``create_fn``, so it registers a distinct
    PyTypeObject built from a different constructor. Its property surface is the
    parent's minus ``exclude_properties``, and its methods the parent's minus
    ``exclude_methods``. It
    carries no warnings/errors/stream. A distinct ``frag_id`` keeps its fragment
    file separate from the parent's.
    """
    state_vars = C.state_vars(cfg, obj)
    arg_type_ = C.arg_type(cfg, obj)
    return_type_ = C.return_type(cfg, obj)
    excluded = C.view_exclude_properties(view)
    excluded_methods = C.view_exclude_methods(view)
    ctx = _make_object_ctx(
        obj,
        module,
        pkg,
        C.project_version(cfg),
        state_vars,
        arg_type_,
        return_type_,
        perf=C.is_perf(cfg),
        array_args=C.array_args(cfg, obj),
        no_state=C.is_no_state(cfg, obj),
        no_step=C.is_no_step(cfg, obj),
        no_reset=C.is_no_reset(cfg, obj),
        mutable=C.is_mutable(cfg, obj),
        step_delegates=C.step_delegates(cfg, obj),
        init_params=C.view_init_params(cfg, obj, view),
        class_name=view["class_name"],
        create_fn=view["create_fn"],
        opaque_fields=C.opaque_fields(cfg, obj),
        opaque_state=C.is_opaque_state(cfg, obj),
        no_ctor_names=C.no_ctor_names(cfg, obj),
        controllable=C.controllable_state_vars(cfg, obj),
        doc_blocks=doc_blocks,
        block_sizes=C.project_bench_block_sizes(cfg),
    )
    # gh-504: a view's surface is the parent's, minus excludes, with its OWN
    # members merged over by name — an own entry OVERRIDES a parent one of the
    # same name (e.g. a different doc), a new name ADDS. So a view is not just a
    # subset of the parent (the doppler Acquisition/BurstAcquisition case: burst
    # adds `reps`, overrides `doppler_bins`'s doc).
    own_methods = C.view_methods(view)
    own_method_names = {m["name"] for m in own_methods}
    merged_methods = [
        m
        for m in C.methods(cfg, obj)
        if m["name"] not in excluded_methods
        and m["name"] not in own_method_names
    ] + own_methods
    own_props = C.view_properties(view)
    own_prop_names = {p["name"] for p in own_props}
    merged_props = [
        p
        for p in C.properties(cfg, obj)
        if p["name"] not in excluded and p["name"] not in own_prop_names
    ] + own_props
    ctx.update(
        Ctx.make_methods_ctx(
            ctx["component"],
            ctx["Component"],
            merged_methods,
            pkg=pkg,
            py_create_args=ctx.get("py_create_args", ""),
            no_state=C.is_no_state(cfg, obj),
            serializable=C.is_serializable(cfg, obj),
            doc_blocks=doc_blocks,
            codecs=C.codecs(cfg),
        )
    )
    ctx.update(
        Ctx.make_properties_ctx(
            ctx["component"],
            ctx["Component"],
            merged_props,
            frozenset(n for n, _, _ in state_vars),
            doc_blocks=doc_blocks,
            enums=C.enums(cfg),  # gh-519
            codecs=C.codecs(cfg),  # gh-554
        )
    )
    # gh-509: a view declares its OWN warnings ([[<obj>.views.warnings]]) —
    # its underpowered PyErr_WarnEx guards a bool field on the shared state
    # struct, exactly as the parent's would. stream remains the parent's
    # concern (an empty ctx keeps those COMPONENT_TYPE_SECTION slots blank).
    ctx.update(
        Ctx.make_warnings_ctx(
            ctx["component"], ctx["Component"], C.view_warnings(view)
        )
    )
    # gh-580: errors go the OTHER way from warnings — a view INHERITS the
    # parent's create_error unless it declares its own. A view and its parent
    # build the same object through different constructors, so the parent's
    # translation is right for both; passing "" here (as this did until
    # gh-580) meant the flavor with the most ways to be given a bad argument
    # was the only one that could not report them, falling back to the blanket
    # MemoryError gh-482 exists to replace.
    ctx.update(
        Ctx.make_errors_ctx(
            ctx["component"],
            C.view_create_error(cfg, obj, view),
            C.view_create_error_message(cfg, obj, view),
            create_fn=view["create_fn"],
        )
    )
    # gh-541: a view is a second Python type over the SAME core, so it shares
    # the parent's destructor contract — a view whose __exit__ swallowed a
    # failure the parent's reports would be the original bug wearing a
    # different class name.
    ctx.update(
        Ctx.make_destroy_ctx(
            ctx["component"], ctx["ComponentW"], C.destroy_spec(cfg, obj)
        )
    )
    ctx.update(
        Ctx.make_stream_ctx(
            ctx["component"],
            ctx["Component"],
            ctx["ComponentW"],
            streamable=False,
            async_stream=False,
            methods=C.methods(cfg, obj),
            arg_type=arg_type_,
            return_type=return_type_,
            default_block=C.stream_block_default(cfg, obj),
        )
    )
    _vdoc = view.get("doc") or f"{ctx['Component']} type."
    ctx["tp_doc"] = _build_ml_doc([_vdoc])
    ctx.update(
        Ctx.make_module_ctx(
            module,
            pkg,
            C.module_package(cfg, module),
            C.module_doc(cfg, module),
        )
    )
    ctx["frag_id"] = _view_frag_id(view)
    return ctx


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
    view_ctxs: list[dict] = []  # gh-504: appended after all real objects
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
            no_reset=C.is_no_reset(cfg, obj),
            mutable=C.is_mutable(cfg, obj),
            step_delegates=C.step_delegates(cfg, obj),
            init_params=C.init_params(cfg, obj),
            class_name=C.class_name(cfg, obj),
            opaque_fields=C.opaque_fields(cfg, obj),
            opaque_state=C.is_opaque_state(cfg, obj),
            no_ctor_names=C.no_ctor_names(cfg, obj),
            controllable=C.controllable_state_vars(cfg, obj),
            doc_blocks=_doc_blocks,
            block_sizes=C.project_bench_block_sizes(cfg),
            create_fn=C.object_create_fn(cfg, obj),
        )
        ctx.update(
            Ctx.make_methods_ctx(
                ctx["component"],
                ctx["Component"],
                C.methods(cfg, obj),
                pkg=pkg,
                py_create_args=ctx.get("py_create_args", ""),
                no_state=C.is_no_state(cfg, obj),
                serializable=C.is_serializable(cfg, obj),
                doc_blocks=_doc_blocks,
                codecs=C.codecs(cfg),
            )
        )
        ctx.update(
            Ctx.make_properties_ctx(
                ctx["component"],
                ctx["Component"],
                C.properties(cfg, obj),
                frozenset(n for n, _, _ in state_vars),
                doc_blocks=_doc_blocks,
                enums=C.enums(cfg),  # gh-519
                codecs=C.codecs(cfg),  # gh-554
            )
        )
        # Declared warnings (gh-481) for a module object, filled into its
        # COMPONENT_TYPE_SECTION slots by the aggregator render.
        ctx.update(
            Ctx.make_warnings_ctx(
                ctx["component"], ctx["Component"], C.warnings(cfg, obj)
            )
        )
        # gh-482: a module object's declared create_error, filled into its
        # COMPONENT_TYPE_SECTION slot by the aggregator render.
        ctx.update(
            Ctx.make_errors_ctx(
                ctx["component"],
                C.create_error(cfg, obj),
                C.create_error_message(cfg, obj),
                create_fn=C.object_create_fn(cfg, obj),
            )
        )
        # gh-541/gh-544: a module object's declared destructor contract,
        # filled into its COMPONENT_TYPE_SECTION slots by the aggregator.
        ctx.update(
            Ctx.make_destroy_ctx(
                ctx["component"],
                ctx["ComponentW"],
                C.destroy_spec(cfg, obj),
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
        # gh-602: an object with a `create_fn` override (e.g.
        # `acq_create_continuous`) is actually constructed by that function,
        # not the derived `<obj>_create` — its Doxygen is what tp_init
        # actually calls, so the transplant must key off it too.
        _cdoc = authored_class_brief(
            _doc_blocks,
            C.object_create_fn(cfg, obj) or f"{obj}_create",
            cfg.get(obj, {}).get("doc", ""),
        )
        # gh-642: when the header documents create(), tp_doc carries the whole
        # class block the .pyi carries — same builder, so they cannot disagree.
        # With nothing authored there is no block to build and the generic
        # one-liner stands, exactly as before.
        ctx["tp_doc"] = _build_ml_doc(
            S.class_runtime_doc(
                obj,
                ctx["Component"],
                state_vars,
                C.is_no_state(cfg, obj),
                C.init_params(cfg, obj),
                f"from {pkg} import {ctx['Component']}",
                ctx.get("py_create_args", ""),
                doc_blocks=_doc_blocks,
                manifest_doc=cfg.get(obj, {}).get("doc", ""),
                custom_reset=bool(C.init_params(cfg, obj))
                or C.is_no_reset(cfg, obj),
                create_fn=C.object_create_fn(cfg, obj),
            )
            if _cdoc
            else [f"{ctx['Component']} type."]
        )
        # Nested-module slots: override `module` to the cname (the fragment
        # file is <cname>_ext_<comp>.c) and supply `module_tp` for the dotted
        # tp_name. For a flat module these equal today's values (zero churn).
        ctx.update(
            Ctx.make_module_ctx(
                module,
                pkg,
                C.module_package(cfg, module),
                C.module_doc(cfg, module),
            )
        )
        # gh-504: a real object's fragment id is its component name (today's
        # behaviour); a view (below) overrides it so its fragment file differs.
        ctx["frag_id"] = ctx["component"]
        comp_ctxs.append(ctx)
        # gh-504: each view of this object becomes an extra ctx — a second
        # PyTypeObject over the same core. Collected and appended AFTER every
        # real object so the zip(object_names, comp_ctxs) pairing downstream
        # (which walks module_objects, excluding views) stays aligned.
        for view in C.views(cfg, obj):
            view_ctxs.append(
                _make_view_ctx(root, cfg, module, pkg, obj, view, _doc_blocks)
            )
    comp_ctxs.extend(view_ctxs)
    return comp_ctxs


_DEFERRED_REGEN: "dict[tuple[str, str], tuple] | None" = None


@contextmanager
def deferred_module_regen() -> "Iterator[None]":
    """Coalesce ``_regenerate_module`` calls to one per module (gh-698).

    Every mutating command finishes by regenerating its whole module —
    aggregator, CMakeLists, every per-object fragment, the module ``.pyi``.
    That is right for a single command, and quadratic for ``apply``, which
    replays one command **per method**: a module with M objects and N methods
    regenerates all M objects N times, and only the last pass survives.

    Measured on a synthetic 6-object / 48-method project, this was 1.4 s of a
    1.6 s replay — the dominant cost once the manifest rewrites (see
    ``_config.scratch_writes``) were removed, and between them the reason
    doppler's apply never finished rather than merely being slow.

    Deferring is sound because the calls are idempotent and the arguments
    accumulate: each records the *live* ``cfg`` dict, which the replay keeps
    mutating, so the single flush at exit renders the final state — exactly
    what the last call would have produced. Intermediate renders were only
    ever overwritten.

    Only ``apply``'s replay uses this. An ordinary ``jm method`` still
    regenerates immediately, because its caller expects the files on disk when
    the command returns.
    """
    global _DEFERRED_REGEN
    prev = _DEFERRED_REGEN
    _DEFERRED_REGEN = {}
    try:
        yield
    finally:
        pending, _DEFERRED_REGEN = _DEFERRED_REGEN, prev
        for root, module, pkg, drop in (pending or {}).values():
            # Re-read rather than replay a stored cfg. Each command does its
            # own `C.load(root)`, so a captured cfg is a *snapshot* of the
            # manifest partway through the replay — and rendering the last
            # snapshot is not the same as rendering the final state. A method
            # whose `[codec.X]` is declared by a later step is present in that
            # snapshot while its codec is not, which the immediate path never
            # saw because it rendered before the method existed at all.
            _regenerate_module_now(root, C.load(root), module, pkg, drop)


def _regenerate_module(
    root: Path,
    cfg: dict,
    module: str,
    pkg: str,
    drop_members: "frozenset[str]" = frozenset(),
) -> None:
    """Regenerate a module's generated files, or defer under a replay.

    *drop_members* names members this command has just removed from the
    manifest. gh-770 carries a binding the fresh render lacks, on the
    assumption it is hand-written — and a member `jm remove` deleted looks
    exactly like one. Naming it here is what separates the two.
    """
    if _DEFERRED_REGEN is not None:
        # Keyed by (root, module) so repeated calls collapse; the value is
        # replaced each time so the flush uses the newest pkg for that module,
        # but the drop sets accumulate — a replay may remove several members
        # before the single flush, and losing any of them resurrects it.
        _prev = _DEFERRED_REGEN.get((str(root), module))
        _drop = (_prev[3] | drop_members) if _prev else drop_members
        _DEFERRED_REGEN[(str(root), module)] = (root, module, pkg, _drop)
        return
    _regenerate_module_now(root, cfg, module, pkg, drop_members)


def _regenerate_module_now(
    root: Path,
    cfg: dict,
    module: str,
    pkg: str,
    drop_members: "frozenset[str]" = frozenset(),
) -> None:
    """Regenerate module_ext.c, module CMakeLists, and subpackage __init__."""
    object_names = C.module_objects(cfg, module)
    # cname drives the flat native dir / file prefixes; leaf is the .so basename
    # and the collocated-object name; pypath is the nested Python dir. For a
    # flat module all three equal `module` (zero churn).
    mp = C.module_paths(module)
    cname = mp.cname
    Module = _to_title(cname)
    # gh-523: `package` redirects every Python-side artifact (.so output dir,
    # .pyi, __init__ re-exports, tests/, benchmarks/) into a sibling package;
    # unset it collapses to the module's own pypath, so nothing changes.
    out_pkg = C.module_package(cfg, module) or mp.pypath

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
        # gh-504: a view fragment is keyed on its frag_id (lowercased
        # class_name), not the shared parent component, so it doesn't overwrite
        # the parent's fragment. Real objects have frag_id == component.
        comp = ctx.get("frag_id", ctx["component"])
        frag_path = ext_dir / f"{cname}_ext_{comp}.c"
        # Prefer bodies from the existing fragment; fall back to the monolith
        # during migration (only matching function names will be spliced in).
        existing_frag = (
            frag_path.read_text(encoding="utf-8")
            if frag_path.exists()
            else None
        )
        if existing_frag is not None:
            preserved = _extract_c_function_bodies(existing_frag)
            # gh-770: an empty extraction is not the same fact as an empty
            # file, and conflating them is what destroyed hand-written C for
            # every project whose formatter puts a space before the paren.
            # Refuse rather than overwrite: the fragment does not gain this
            # command's member, which is visible and recoverable, instead of
            # losing bodies that exist nowhere else, which is neither.
            from . import _docsync as _ds

            if not preserved and _ds.extraction_failed(existing_frag):
                print(
                    f"warning: {frag_path}: jm cannot parse the function\n"
                    "  definitions in this fragment, so it will not rewrite "
                    "it — anything\n  hand-written in it would be lost. The "
                    "binding for this change was NOT\n  added. Please report "
                    "the file's formatting (jm-770).",
                    file=sys.stderr,
                )
                continue
        else:
            preserved = monolith_bodies
        frag = R.render_module_ext_fragment(ctx)
        if preserved:
            # gh-541: a declared destructor contract owns the teardown
            # wrappers — see _restore_c_function_bodies' force_regen.
            _w = ctx["ComponentW"]
            _force = (
                (f"{_w}_destroy", f"{_w}_exit")
                if C.destroy_spec(cfg, ctx["component"])
                else ()
            )
            frag = _restore_c_function_bodies(
                frag, preserved, force_regen=_force
            )
        if existing_frag is not None:
            # gh-770: _restore_c_function_bodies only ever *replaces* a body
            # whose name the fresh render also has, so a hand-ADDED binding —
            # one the manifest cannot express, which is the whole reason these
            # fragments are sacred — was written away here. Carry it, and its
            # PyMethodDef/PyGetSetDef row, into the render.
            from . import _docsync

            frag = _docsync.transplant_hand_written(
                existing_frag, frag, drop_members
            )
        _write(frag_path, frag, "update" if frag_path.exists() else "create")

    # Discover *_extra.c files — jm never creates or modifies them, but
    # includes them in the aggregator so hand-written types survive regen.
    extra_files: set[str] = set()
    for ctx in comp_ctxs:
        comp = ctx.get("frag_id", ctx["component"])
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
        # gh-353: a module function's enum param needs the SSOT enum tables.
        enums=C.enums(cfg),
        # gh-645: `[module.X] doc` -> m_doc, the same string the re-export
        # __init__.py docstring gets.
        module_doc_c=Ctx.make_module_ctx(
            module,
            pkg,
            C.module_package(cfg, module),
            C.module_doc(cfg, module),
        )["module_doc_c"],
        # gh-643: the module header's Doxygen for its free functions — the
        # same blocks the .pyi derives from (gh-384), so `help(fn)` and the
        # stub carry the same text.
        fn_doc_blocks=_load_module_doc_blocks(root, module),
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
    # gh-247: in functions-in-core mode every function body lives in
    # <module>_core.c, so there are no per-function .c sources to list.
    if C.functions_in_core(cfg, module):
        fn_srcs = []
    else:
        fn_srcs = [
            f"{fn['name']}.c" for fn in functions if not fn.get("inline")
        ]
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
    # gh-280: flatten each member's link=true closure so the .so pulls in every
    # transitively-composed core, not just the directly-declared ones.
    _seen = set(libs_parts) | set(extra_libs)
    for _obj in object_names:
        for _lib in C.component_extra_link_libs(
            cfg, _obj
        ) + C.transitive_dep_cores(
            cfg, C.depends_on_raw(cfg, _obj), link_only=True
        ):
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
        # flat modules collapse these to today's values. gh-523: `package`
        # overrides module_pypath so LIBRARY_OUTPUT_DIRECTORY points at the
        # sibling package the .so is meant to land in.
        **Ctx.make_module_ctx(
            module,
            pkg,
            C.module_package(cfg, module),
            C.module_doc(cfg, module),
        ),
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
            # gh-254: a collocated object whose <obj>_core.c COMPOSES a sibling
            # core needs that <dep>_core directly on its OWN test/bench links
            # too — not just the module .so. The .so link (object_core_libs)
            # already carries link=true deps; here we make link=true *additive*
            # rather than a move by also linking every depends_on core onto
            # test_<obj>_core / bench_<obj>_core (which link <obj>_core, whose
            # .c calls the dep's symbols). OBJECT-lib PUBLIC deps don't
            # propagate to a final target (gh-160), so the link must be direct
            # on the test/bench targets, exactly as the create path does for
            # non-collocated objects. Mirror that `_dep_cores` normalisation so
            # a bare `lo_core` entry doesn't become `lo_core_core`. gh-280: the
            # closure is flattened so a collocated object also need only declare
            # its direct deps.
            _obj_dep_cores = C.transitive_dep_cores(
                cfg, C.depends_on_raw(cfg, obj)
            )
            _colib: list[str] = []
            for _l in list(extra_libs) + _obj_dep_cores:
                if _l not in _colib:
                    _colib.append(_l)
            _colib_block = "\n    ".join(_colib) + "\n    " if _colib else ""
            # gh-537: the two consumers below want DIFFERENT sets. test/bench
            # (_colib_block, above) link every declared dep including test-only
            # ones — that is what a test-only dep is for. The core's PUBLIC link
            # must not, or the dependency propagates straight back into the
            # Python extension and ships after all, which is the bug.
            _test_only_cores = C.depends_test_only_cores(cfg, obj)
            _core_pub = [c for c in _colib if c not in _test_only_cores]
            # gh-160: also PUBLIC-link them onto the collocated OBJECT lib so
            # the deps propagate transitively to the Python extension. The
            # `jm object` path sets extra_link_on_object_core (run()); apply
            # rebuilds the collocated CMakeLists here, so it must set it too —
            # otherwise the `<<extra_link_on_object_core>>` placeholder leaks
            # into the generated CMakeLists and breaks the build.
            extra_link_on_object_core = (
                f"target_link_libraries({obj}_core PUBLIC\n    "
                + "\n    ".join(_core_pub)
                + ")\n"
                if _core_pub
                else ""
            )
            # gh-531: the module's extra_include_dirs reach the module's own
            # core and the .so, but never reached a COLLOCATED object's core —
            # so a core whose .c/.h includes a vendored header (cJSON.h) could
            # not compile, and the only way out was reshaping the project. They
            # are PUBLIC so test/bench inherit them transitively too.
            _coinc = C.extra_include_dirs(cfg, module) if module else []
            extra_include_dirs_on_object_core = (
                f"target_include_directories({obj}_core PUBLIC\n    "
                + "\n    ".join(_coinc)
                + ")\n"
                if _coinc
                else ""
            )
            ctx_cmake = {
                **ctx_,
                "extra_link_libs_block": _colib_block,
                "extra_link_on_object_core": extra_link_on_object_core,
                "extra_include_dirs_on_object_core": (
                    extra_include_dirs_on_object_core
                ),
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
    # gh-342: extra_types are names jm itself emits into the `from .<module>
    # import …` line (types from a hand-written sibling compiled into the same
    # .so). They must be in the keep-set or the gh-329 prune strips them from
    # both the import and __all__ (data loss of declared public types).
    all_exports = Components + fn_names + list(C.extra_types(cfg, module))
    reexports = C.module_reexports(cfg, module)
    # Nested module: ensure the intermediate packages exist, then write under
    # the nested pypath (or the gh-523 `package` destination).
    ensure_parent_packages(root, pkg, mp, out_pkg)
    pkg_module_dir = root / "src" / pkg / out_pkg
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
                **Ctx.make_module_ctx(
                    module,
                    pkg,
                    C.module_package(cfg, module),
                    C.module_doc(cfg, module),
                ),
                "Module": Module,
                "object_imports": ", ".join(all_exports),
                "object_all": ", ".join(f'"{name}"' for name in all_exports),
            },
        )
    # The import line in __init__.py is `from .<leaf> import ...`, so the merge
    # must match/emit against the leaf, not the dotted id.
    merged = _merge_module_init(
        base,
        mp.leaf,
        all_exports,
        reexports,
        siblings=package_siblings(cfg, module),
    )
    # gh-695: and the module docstring, which the template above only supplies
    # on the create path — so a module that gained a `doc` after scaffolding
    # never got one on the surface users import.
    merged = _merge_module_docstring(
        merged,
        Ctx.make_module_ctx(
            module,
            pkg,
            C.module_package(cfg, module),
            C.module_doc(cfg, module),
        )["module_docstring_py"],
    )
    _write(init_path, merged, "update" if existed else "create")

    # Type stubs — regenerated in full every time the module changes.
    pyi_path = pkg_module_dir / f"{mp.leaf}.pyi"
    old_pyi = pyi_path.read_text(encoding="utf-8") if pyi_path.exists() else ""
    new_pyi = S.make_module_pyi(cfg, module, root)
    # gh-428: preserve any manual_stub method's hand-written text across
    # the otherwise-blind regen above.
    _write(
        pyi_path,
        S._splice_manual_stub_bodies(cfg, old_pyi, new_pyi, path=pyi_path),
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
    no_reset: bool = False,
    mutable: bool = False,
    step_delegates: bool = False,
    serializable: bool = False,
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
    opaque_state: bool = False,
    no_ctor_names: "frozenset[str]" = frozenset(),
    controllable_names: "frozenset[str]" = frozenset(),
    variable_output: bool = False,
    multi_output: list[str] = (),
    method_name: str = "run",
    class_name: str | None = None,
    depends_on: list[str] = (),
    extra_link_libs: list[str] = (),
    extra_include_dirs: list[str] = (),
    max_out: int = 0,
    create_fn: str | None = None,
    destroy: "dict | None" = None,
    _hint: bool = True,
) -> None:
    # gh-588: `opaque_state` forward-declares the struct, so anything that
    # dereferences it from the PUBLIC header is incoherent. Say so here rather
    # than let the user meet it as an incomplete-type error in generated C they
    # did not write.
    if opaque_state and not no_step:
        print(
            "error: --opaque-state requires --no-step.\n"
            f"The generated {object_name}_step() is `static inline` in the "
            "public header and\ndereferences the state, which an opaque type "
            "cannot satisfy. Use --no-step,\nor drop --opaque-state.",
            file=sys.stderr,
        )
        sys.exit(1)
    if opaque_state and state_vars:
        print(
            "error: --opaque-state cannot be combined with --state.\n"
            "A declared state variable generates a field in the struct jm "
            "publishes, and\n--opaque-state exists to stop publishing it. "
            "Hand-write the fields in\n"
            f"{object_name}_core.c and expose what callers need as "
            "properties.",
            file=sys.stderr,
        )
        sys.exit(1)
    C.require_name(object_name, "object")

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
            no_reset=no_reset,
            mutable=mutable,
            step_delegates=step_delegates,
            serializable=serializable,
            streamable=streamable,
            async_stream=async_stream,
            stream_block_default=stream_block_default,
            impl_body=impl_body,
            create_impl_body=create_impl_body,
            reset_impl_body=reset_impl_body,
            destroy_impl_body=destroy_impl_body,
            init_params=init_params,
            opaque_fields=opaque_fields,
            opaque_state=opaque_state,
            no_ctor_names=no_ctor_names,
            controllable_names=controllable_names,
            class_name=class_name,
            depends_on=list(depends_on),
            extra_link_libs=list(extra_link_libs),
            extra_include_dirs=list(extra_include_dirs),
            create_fn=create_fn,
            destroy=destroy,
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
        no_reset=no_reset,
        mutable=mutable,
        step_delegates=step_delegates,
        init_params=init_params,
        init_post_parse_impl=init_post_parse_impl,
        class_name=class_name,
        opaque_fields=opaque_fields,
        no_ctor_names=no_ctor_names,
        controllable=[
            (n, ct) for n, ct, _ in vars_ if n in controllable_names
        ],
        block_sizes=C.project_bench_block_sizes(cfg),
        create_fn=create_fn,
    )
    ctx.update(
        Ctx.make_methods_ctx(
            ctx["component"],
            ctx["Component"],
            [],
            pkg=pkg,
            py_create_args=ctx.get("py_create_args", ""),
            no_state=no_state,
            serializable=serializable,
        )
    )
    # gh-481: a fresh object declares no warnings, but the slot must resolve
    # or this path's _ext.c ships with a literal <<init_warn_block>> in it.
    ctx.update(Ctx.make_warnings_ctx(ctx["component"], ctx["Component"], []))
    # gh-482: undeclared at creation -> the historical MemoryError block.
    # gh-509: name the override constructor in the NULL message when set.
    ctx.update(Ctx.make_errors_ctx(ctx["component"], create_fn=create_fn))
    # gh-541/gh-544: same as the standalone path in _init.run — this render
    # stamps the sacred _core.h/_core.c destroy signature as well as the glue.
    ctx.update(
        Ctx.make_destroy_ctx(ctx["component"], ctx["ComponentW"], destroy)
    )

    if create_impl_body is not None:
        ctx["create_assignments"] = _indent_body(create_impl_body)
    # gh-542: with no_reset there is no <comp>_reset() to hold a body, so a
    # lifted reset impl would be spliced into the file bare. Drop it.
    if reset_impl_body is not None and not no_reset:
        ctx["reset_assignments"] = _indent_body(reset_impl_body)
    if destroy_impl_body is not None:
        ctx["destroy_impl"] = _indent_body(destroy_impl_body) + "\n"

    # The object's OWN test/bench executables must link both its explicit
    # extra_link_libs AND its depends_on cores — the core calls the
    # dependency's functions (e.g. a sibling's create() via create_impl), so
    # without the dep OBJECT lib the test/bench fail to link (gh-174 follow-up).
    # A depends_on entry may be a component (`nco` -> nco_core) or a bare link
    # target (`lo_core`); normalise to a single `<name>_core` (gh-130). gh-280:
    # flatten the closure so an object need only declare its direct deps — jm
    # adds the transitive cores the test/bench link line otherwise lacks.
    _dep_cores = C.transitive_dep_cores(cfg, depends_on)
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
    # gh-537: test-only deps stay off the core's PUBLIC link line — from there
    # they would propagate into the Python extension and ship after all, which
    # is exactly what test_only exists to prevent. They remain on
    # extra_link_libs_block (test/bench) above.
    _test_only = [
        (n if n.endswith("_core") else f"{n}_core")
        for n in C.dep_names([d for d in depends_on if C._dep_test_only(d)])
    ]
    _core_pub_libs = [lib for lib in _obj_libs if lib not in _test_only]
    if _core_pub_libs:
        _elibs = "\n    ".join(_core_pub_libs)
        ctx["extra_link_on_object_core"] = (
            f"target_link_libraries({ctx['component']}_core PUBLIC\n"
            f"    {_elibs})\n"
        )
    else:
        ctx["extra_link_on_object_core"] = ""
    # gh-531: same for include dirs. The module's own extra_include_dirs count
    # too — a collocated object belongs to the module, and if the module needs a
    # vendored header its objects' cores generally do as well. Without this the
    # only fix was reshaping the project (doppler moved a private header into
    # native/inc/ to work around it).
    _mod_incs = C.extra_include_dirs(cfg, module) if module else []
    _all_incs = list(extra_include_dirs) + [
        d for d in _mod_incs if d not in extra_include_dirs
    ]
    if _all_incs:
        _eincs = "\n    ".join(_all_incs)
        ctx["extra_include_dirs_on_object_core"] = (
            f"target_include_directories({ctx['component']}_core PUBLIC\n"
            f"    {_eincs})\n"
        )
    else:
        ctx["extra_include_dirs_on_object_core"] = ""

    # gh-170: include each depends_on component's header so opaque fields of
    # its types compile (mirrors the standalone path in _init.run). Only deps
    # with a real header are included — a bare link target like `lo_core` is
    # skipped. gh-432: method params' `header` keys (a capsule param's
    # foreign type) are included the same way when the header exists.
    from ._init import _dep_header_includes

    _inc_root = root / "native" / "inc"
    ctx["depends_includes"] = "".join(
        "\n" + inc
        # gh-537: a test_only dep's header stays out of the object's PUBLIC
        # core header. The C test includes it directly; pulling it in here
        # would make the shipped header advertise a dependency the shipped
        # artifact does not have — the same untruth test_only exists to fix.
        for inc in _dep_header_includes(
            _inc_root,
            C.dep_names([d for d in depends_on if not C._dep_test_only(d)]),
        )
        + [
            f'#include "{h}"'
            for h in C.param_headers(cfg, object_name)
            if (_inc_root / h).exists()
        ]
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
    # gh-806: via the one renderer that stamps the scaffold check count.
    _write(
        root / "native" / "tests" / f"test_{comp}_core.c",
        R.render_component_test_c(ctx),
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
    # gh-523: honour the module's `package` override so the per-object tests
    # and benchmarks land beside the .so, not in a directory named after the
    # module that nothing else uses.
    _mp = C.module_paths(module)
    _out_pkg = C.module_package(cfg, module) or _mp.pypath
    pkg_mod_dir = root / "src" / pkg / _out_pkg
    # The generated test/bench import `from <package>.<module> import <Class>`,
    # which must name the package the .so actually lands in — the `package`
    # override when set, else the module's own pypath as a dotted import path.
    # Without a package override this is exactly the module id (flat or
    # dotted), so unpackaged modules render byte-identically.
    _py_ctx = {**ctx, "module": _out_pkg.replace("/", ".")}

    def r_py(tmpl):
        return R.render(tmpl, _py_ctx)

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
    _write(pkg_mod_dir / "tests" / f"test_{comp}.py", r_py(test_py_tmpl))
    benchmarks_init = pkg_mod_dir / "benchmarks" / "__init__.py"
    if not benchmarks_init.exists():
        _write(benchmarks_init, "")
    _write(
        pkg_mod_dir / "benchmarks" / f"bench_{comp}.py", r_py(bench_py_tmpl)
    )

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
        no_reset_=no_reset,
        opaque_state_=opaque_state,
        mutable_=mutable,
        step_delegates_=step_delegates,
        serializable_=serializable,
        streamable_=streamable,
        async_stream_=async_stream,
        stream_block_default_=stream_block_default,
        init_params_=init_params,
        class_name_=class_name,
        create_fn_=create_fn,
        depends_on_=list(depends_on),
        opaque_fields_=list(opaque_fields),
        no_ctor_names_=no_ctor_names,
        controllable_names_=controllable_names,
        # gh-160: persist extra_link_libs/extra_include_dirs for module objects
        # too (the standalone path already did). Without this they're dropped
        # from the manifest, so the module aggregation can't propagate a
        # cross-module dep to the Python extension and reload/apply lose it.
        extra_link_libs_=list(extra_link_libs),
        extra_include_dirs_=list(extra_include_dirs),
    )
    # gh-541/gh-544: persist the destructor contract BEFORE the aggregate
    # re-render below — _regenerate_module reads it back out of this same
    # in-memory cfg to fill the object's COMPONENT_TYPE_SECTION slots.
    C.set_destroy_spec(cfg, comp, destroy or {})

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
