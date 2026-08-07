"""
_init.py — standalone object scaffolding (internal).

Called by `just-makeit object` (no --module) and `just-makeit new --object`.
"""

from __future__ import annotations

import json
import re
import sysconfig
import sys
from pathlib import Path

from . import _config as C
from . import _report
from . import _context as Ctx
from . import _render as R
from . import _types as T
from ._docstring import scaffold_doc_block


def _to_title(snake: str) -> str:
    """C-side class name for *snake* — see ``_config.default_class_name``.

    Kept as a local alias because the C generators call it in a dozen places;
    the derivation itself lives in one module (gh-628), shared with the stub
    generator that used to disagree with it.
    """
    return C.default_class_name(snake)


def standalone_extra_include(root: Path, component: str) -> str:
    """``#include`` for a standalone object's hand-written extra (gh-543).

    Mirrors what the module aggregator has always done for
    ``<module>_ext_<obj>_extra.c`` (``_render.render_module_ext_aggregator``).
    jm never creates or modifies the file; it only wires it in when it exists,
    so hand-written code survives every regeneration of the glue around it.

    Returns ``""`` when there is no such file, which is the overwhelmingly
    common case and renders byte-identically to before this slot existed.
    """
    extra = f"{component}_ext_extra.c"
    if (root / "native" / "src" / component / extra).exists():
        return (
            f'#include "{extra}"  /* hand-written — jm never modifies */\n\n'
        )
    return ""


def _make_component_ctx(component: str) -> dict[str, str]:
    return {
        "component": component,
        "Component": _to_title(component),
        "COMPONENT": component.upper(),
        # Default empty; paths with `depends_on` override it (gh-170). Keeps
        # the `<<depends_includes>>` slot from leaking on paths that have no
        # dependency info to inject.
        "depends_includes": "",
        # Stream-generator slots (gh-201). Default empty so every render path
        # has them — a non-streamable object renders byte-identical, and the
        # streamable paths overwrite these via Ctx.make_stream_ctx.
        "stream_iter_block": "",
        "stream_def_entry": "",
        "stream_tp_iter": "",
        "stream_tp_async": "",
        "stream_type_ready": "",
        "stream_module_ready": "",
        "pyi_stream_typing": "",
        "pyi_stream_methods": "",
        # gh-519: `, Literal` when some property declares an `enum`; empty
        # otherwise, so a project without enum properties renders unchanged.
        # make_properties_ctx overwrites it on every path that has properties.
        "pyi_property_typing": "",
        # gh-623: `\nimport os` when a `path` init-param puts `os.PathLike` in
        # the signature; empty otherwise, so an object without one renders
        # byte-identical. make_state_ctx overwrites it.
        "pyi_os_import": "",
        # gh-644: the runtime class docstring. Seeded here -- the one place
        # every component render path passes through -- with the fixed text
        # this slot replaced, so a path that does not derive (a view, a fresh
        # scaffold with no header yet) renders byte-identically to before.
        # _glue.component_ctx overrides it from create()'s @brief.
        "tp_doc": (
            f'"{_to_title(component)} component. Wraps {component}_state_t."'
        ),
        # gh-543: a standalone object's hand-written `<comp>_ext_extra.c`,
        # #included when it exists. Module objects have had this since the
        # aggregator was introduced; a standalone object had no hook at all,
        # so a property whose value_fn returns a PyObject * -- which needs
        # Python.h and so cannot live in the pure-C core -- had nowhere to go.
        # Seeded here, the one place every component render path passes
        # through, so the five COMPONENT_EXT_C call sites resolve it unchanged.
        "extra_include": "",
        # Windows CMake boilerplate is opt-in (gh-213); default off so the
        # generated CMakeLists has no `if(WIN32 …)` block unless the project
        # lists `windows` in [project] platforms.
        "win_cmake_component": "",
        "win_cmake_module": "",
        # gh-541/gh-544: destructor slots. Seeded here — the one place every
        # component render path passes through — so no path can leak a literal
        # <<destroy_*>> into generated C. The undeclared values reproduce the
        # pre-gh-541 hardcoded template text byte for byte. This is only a
        # safety net: `ComponentW` is not settled until make_state_ctx has run
        # (a no_state object's prefix gains an `Obj` suffix), so every real
        # path re-runs make_destroy_ctx with the real prefix and the manifest's
        # spec right after that.
        **Ctx.make_destroy_ctx(component, _to_title(component)),
        # gh-542: the slots that wrap a reset body — the sacred _core.c
        # function and the generated test defs. Seeded here for the same
        # reason as the destructor slots above: no render path may leak a
        # literal <<reset_c_open>> into generated C. make_state_ctx overwrites
        # them (and blanks them under `no_reset`) on every real path.
        **Ctx._reset_wrapper_slots(component),
    }


def _write(path: Path, content: str, verb: str = "create") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  {verb}  {path}")


def ensure_parent_packages(
    root: Path, pkg: str, mp: "C.ModulePaths", pypath: str | None = None
) -> list[Path]:
    """Create plain ``__init__.py`` markers for a nested module's parents.

    A dotted module ``dsp.filters`` lives at ``src/<pkg>/dsp/filters/``; the
    intermediate ``dsp`` package needs an ``__init__.py`` for ``pkg.dsp`` to be
    importable. Create-if-missing (never clobbers a hand-edited marker);
    returns the paths newly created. No-op for a flat module (no parents).

    *pypath* overrides the module's own package path with the gh-523
    ``package`` destination (which may itself be nested, e.g. ``dsp/wfm``);
    the parents are then that path's own directory prefixes. The destination
    directory itself is never created here — its ``__init__.py`` is the
    module's re-export file, written (and merged) by the caller.
    """
    created: list[Path] = []
    base = root / "src" / pkg
    parents = (
        [s for s in pypath.replace(".", "/").split("/") if s][:-1]
        if pypath is not None
        else list(mp.parents)
    )
    for depth in range(1, len(parents) + 1):
        init = base.joinpath(*parents[:depth]) / "__init__.py"
        if not init.exists():
            _write(init, R.SUBPACKAGE_INIT_PY)
            created.append(init)
    return created


# ── Core-file body preservation ──────────────────────────────────────────────
# Regenerating commands re-render <comp>_core.h / <comp>_core.c from templates,
# which would otherwise wipe any hand-written algorithm code.  The helpers
# below splice the bodies of an existing core file into freshly rendered
# template text — the same idea as the `static PyObject *` body preservation
# already done for <module>_ext.c, generalised to plain C functions.


def _matching_brace(source: str, open_idx: int) -> int:
    """Return the index just past the '}' that matches the '{' at open_idx."""
    depth = 0
    for i in range(open_idx, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(source)


def _func_span_before_brace(
    source: str, brace_idx: int
) -> tuple[str, int] | None:
    """``(function_name, name_start)`` for the function whose body opens at
    brace_idx.

    Scans back over whitespace, the balanced ``(...)`` parameter list and more
    whitespace, then reads the identifier.  Returns None when the '{' opens
    something that is not a function definition (e.g. an array initialiser,
    whose '{' is preceded by '=').
    """
    i = brace_idx - 1
    while i >= 0 and source[i] in " \t\r\n":
        i -= 1
    if i < 0 or source[i] != ")":
        return None
    depth = 0
    while i >= 0:
        if source[i] == ")":
            depth += 1
        elif source[i] == "(":
            depth -= 1
            if depth == 0:
                break
        i -= 1
    if i < 0:
        return None
    i -= 1
    while i >= 0 and source[i] in " \t\r\n":
        i -= 1
    end = i + 1
    while i >= 0 and (source[i].isalnum() or source[i] == "_"):
        i -= 1
    name = source[i + 1 : end]
    return (name, i + 1) if name else None


def _extract_core_c_funcs(source: str) -> dict[str, str]:
    """Map ``{function_name: definition}`` for a generated <comp>_core.c.

    The captured text runs from the function name through the closing brace
    (parameter list + body) so a hand-edited signature — e.g. a dropped
    ``const`` on a mutating step — is preserved too.  The leading return-type
    line is left to the template.  File-scope initialisers are skipped.
    """
    funcs: dict[str, str] = {}
    i = 0
    while True:
        brace = source.find("{", i)
        if brace == -1:
            break
        end = _matching_brace(source, brace)
        span = _func_span_before_brace(source, brace)
        if span:
            funcs[span[0]] = source[span[1] : end]
        i = end
    return funcs


def _restore_core_c_funcs(new_source: str, preserved: dict[str, str]) -> str:
    """Swap each function in *new_source* for its preserved definition.

    Functions that exist only in *new_source* (freshly scaffolded stubs) keep
    their generated body; functions only in *preserved* are dropped.
    """
    out: list[str] = []
    i = 0
    while True:
        brace = new_source.find("{", i)
        if brace == -1:
            out.append(new_source[i:])
            break
        end = _matching_brace(new_source, brace)
        span = _func_span_before_brace(new_source, brace)
        if span and span[0] in preserved:
            out.append(new_source[i : span[1]])
            out.append(preserved[span[0]])
        else:
            out.append(new_source[i:end])
        i = end
    return "".join(out)


_STRUCT_RE = re.compile(
    r"(typedef struct \{\n)(.*?)(\n\} \w+_state_t;)", re.DOTALL
)
_FIELD_RE = re.compile(r"^\s+.*?(\w+)\s*(?:\[[^\]]*\])?\s*;", re.MULTILINE)


def _merge_struct_fields(new_fields: str, old_fields: str) -> str:
    """Keep *new_fields*, appending any field line from *old_fields* whose name
    is absent — so hand-added struct members survive regeneration without
    dropping fields the template now emits.
    """
    new_names = set(_FIELD_RE.findall(new_fields))
    extra = [
        line
        for line in old_fields.split("\n")
        if (m := _FIELD_RE.match(line)) and m.group(1) not in new_names
    ]
    if not extra:
        return new_fields
    return new_fields.rstrip("\n") + "\n" + "\n".join(extra)


def _step_func_span(source: str, comp: str) -> tuple[int, int] | None:
    """(start, end) spanning the inline ``<comp>_step`` name, parameter list
    and body, or None when there is no inline step definition.

    The leading ``JM_FORCEINLINE``/return-type line is excluded so it stays
    in sync with the template; the name onward (including a hand-edited
    non-const ``state`` parameter) is preserved.
    """
    pat = re.compile(r"\b" + re.escape(comp) + r"_step\s*\(")
    for m in pat.finditer(source):
        i = m.end()
        depth = 1
        while i < len(source) and depth:
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
            i += 1
        while i < len(source) and source[i] in " \t\r\n":
            i += 1
        if i < len(source) and source[i] == "{":
            return m.start(), _matching_brace(source, i)
    return None


# Qualifiers that decorate a prototype without changing the function it
# declares. The user may add `JM_RESTRICT` (perf) or drop a `const` on a
# mutable buffer param to their hand-tuned header decl; `apply` must treat such
# a decl as the same one it would generate (idempotent) instead of replacing or
# duplicating it (gh-169).
_DECL_QUALIFIER_RE = re.compile(
    r"\b(?:JM_RESTRICT|__restrict__|__restrict|restrict|const)\b"
)


def _normalize_decl(decl: str) -> str:
    """Canonicalize a prototype for identity comparison.

    Strips the decorative qualifiers above and all whitespace, so e.g.
    ``void f(const float *JM_RESTRICT x);`` and ``void f(float *x);`` compare
    equal — same function, differently decorated.
    """
    return re.sub(r"\s+", "", _DECL_QUALIFIER_RE.sub("", decl))


def _with_scaffold_doc(decl: str, doc_members: "dict[str, str]") -> str:
    """Prefix *decl* with jm's doc skeleton when it names a known member.

    A decl jm cannot describe faithfully (see
    :func:`_docstring.scaffold_doc_block`) is returned unchanged rather than
    given an approximate comment.
    """
    m = re.search(r"(\w+)\s*\(", decl)
    if m is None or m.group(1) not in doc_members:
        return decl
    doc = scaffold_doc_block(decl, doc_members[m.group(1)])
    return f"{doc}\n{decl}" if doc else decl


def _inject_decls_into_core_h(
    path: Path,
    comp: str,
    decls: "list[str]",
    skip_names: "frozenset[str] | None" = None,
    doc_members: "dict[str, str] | None" = None,
) -> bool:
    """Surgically refresh C declarations in an object's ``<comp>_core.h``.

    Splice-free: a declaration whose exact text is already present is left
    alone (idempotent).  A prototype that shares its function *name* with an
    existing single-line decl **replaces** it in place — so a method that
    overrides a builtin (e.g. a parameterised ``reset``) or a refreshed
    signature never produces a duplicate.  Anything genuinely new is inserted
    just before the ``extern "C"`` close (falling back to the header guard).
    The sacred state struct and inline ``step()`` body are never touched.

    gh-632: a replacement **warns**, naming the old and new prototypes. This
    is a rewrite inside a sacred file, and it is not distinguishable from a
    hand edit being discarded — `_apply._refresh_core_h_decls`, the caller,
    documents why replace-by-name is nevertheless the right policy.

    skip_names — function names that should NOT be replaced even if a
    declaration with that name already exists.  When the header already
    declares a name in skip_names (with any signature), the incoming decl is
    silently dropped so the user's existing declaration is preserved.

    doc_members — ``{c_function_name: bare_member_name}``.  A decl being
    inserted for the first time gets jm's prose-free Doxygen skeleton above it
    (gh-666) so the author fills prose rather than structure.  Only *new*
    insertions are decorated: a refreshed signature replaces the prototype
    line alone, leaving whatever documentation is already above it untouched,
    so re-running a command never re-stamps a skeleton over authored prose.

    Returns True if the header changed."""
    if not path.exists():
        return False
    text = original = path.read_text(encoding="utf-8")
    norm_text = _normalize_decl(text)
    to_insert: list[str] = []
    for d in decls:
        d = d.strip()
        if not d or d in text:  # genuinely-new decls only; exact = idempotent
            continue
        # gh-169: a decl already present modulo decorative qualifiers
        # (JM_RESTRICT / a dropped const on a mutable buffer) is the same
        # declaration — leave the user's hand-tuned version untouched rather
        # than replacing or duplicating it.
        if _normalize_decl(d) in norm_text:
            continue
        m = re.search(r"(\w+)\s*\(", d)
        if m:
            fn_name = m.group(1)
            # gh-133/gh-468: if the header already has an inline (or
            # JM_FORCEINLINE) *definition* of this function, don't append a
            # bare extern declaration — for a `static inline`/`static
            # JM_FORCEINLINE` definition that would violate C11 §6.7.4¶7
            # (conflicting linkage on the same TU); for a non-static one
            # (module-level header-only functions, e.g. doppler's
            # `square_clip`, dropped `static` to silence GCC
            # `-Wstatic-in-inline` for a non-static caller) the definition
            # already satisfies the manifest just the same, so injecting a
            # redundant declaration would only ever be a malformed duplicate.
            # `static` is therefore optional here, not required.
            static_inline_pat = re.compile(
                r"(?:\bstatic\b[^;{]*)?\b(?:inline|JM_FORCEINLINE)\b[^;{]*\b"
                + re.escape(fn_name)
                + r"\s*\(",
                re.MULTILINE,
            )
            if static_inline_pat.search(text):
                continue
            # Replace an existing prototype of the same name (a builtin
            # override or a refreshed signature). Try a single-line match
            # first; fall back to a multi-line match (gh-137: a prototype
            # wrapped across lines — e.g. a 5-arg variable_output
            # `*_execute(..., out, max_out)` — was previously missed and the
            # generated decl appended as a conflicting/duplicate declaration).
            # Both forms require the prototype to end in ');' so neither ever
            # matches the inline step() definition (which ends in '{').
            pat = re.compile(
                r"^[ \t]*[A-Za-z_][^\n{]*\b"
                + re.escape(fn_name)
                + r"\s*\([^\n{]*\);[ \t]*$",
                re.MULTILINE,
            )
            pat_ml = re.compile(
                r"^[ \t]*[A-Za-z_][^{;]*?\b"
                + re.escape(fn_name)
                + r"\s*\([^{;]*?\)\s*;",
                re.MULTILINE,
            )
            m_sl = pat.search(text)
            use = pat if m_sl else pat_ml if pat_ml.search(text) else None
            if use is not None:
                if skip_names and fn_name in skip_names:
                    # Name is in skip_names — preserve the existing decl.
                    continue
                # gh-632: say so. Reaching here means the header's prototype
                # differs from the manifest's beyond the decorative
                # qualifiers `_normalize_decl` already forgives, so this is a
                # real rewrite of a declaration in a *sacred* file. jm cannot
                # tell an intended manifest change from a hand edit it is
                # about to discard — and the author wants to hear about it
                # either way, because the definition in `_core.c` and every
                # call site still use the old prototype. Silent, the next
                # build fails somewhere else entirely.
                _old = use.search(text)
                _prev = _old.group(0).strip() if _old else ""
                new_text, n = use.subn(d, text, count=1)
                if n:
                    if _prev:
                        _report.warn(
                            f"{path}: replacing the declaration of"
                            f" {fn_name}() to match the manifest\n"
                            f"    was: {_prev}\n"
                            f"    now: {d}\n"
                            "  The definition in _core.c and any call sites"
                            " still use the old prototype. If the header was"
                            " right, update the manifest instead — this"
                            " refresh runs on every apply.",
                            # Advisory: apply performs the replacement.
                            gates=False,
                        )
                    text = new_text
                    continue
        to_insert.append(d)
    if to_insert:
        if doc_members:
            # Blank line *between* batched decls only -- a comment block butted
            # against the preceding declaration reads as documenting it. The
            # first entry needs none: the insertion point already follows the
            # template's own spacing.
            to_insert = [
                (("\n" if i else "") + _with_scaffold_doc(d, doc_members))
                for i, d in enumerate(to_insert)
            ]
        block = "\n".join(to_insert) + "\n"
        cpp_end = "#ifdef __cplusplus\n}\n#endif"
        if cpp_end in text:
            text = text.replace(cpp_end, f"{block}{cpp_end}", 1)
        else:
            guard = f"#endif /* {comp.upper()}_CORE_H */"
            text = text.replace(guard, f"{block}{guard}", 1)
    if text == original:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def _dep_header_includes(inc_root: Path, deps: "list[str]") -> "list[str]":
    """``#include "<dep>/<dep>_core.h"`` for each dep whose header exists.

    A ``depends_on`` entry may name a component (``lfsr`` → ``lfsr/lfsr_core.h``,
    which exists) or a bare OBJECT-library link target (``lfsr_core`` →
    ``lfsr_core/lfsr_core_core.h``, which does **not** exist). Only emit an
    include that actually resolves, so a link-only dependency never injects a
    broken ``#include`` (gh-170 follow-up). *inc_root* is ``native/inc``.
    """
    out: list[str] = []
    for d in deps:
        if (inc_root / d / f"{d}_core.h").exists():
            out.append(f'#include "{d}/{d}_core.h"')
    return out


def _inject_includes_into_core_h(
    path: Path, comp: str, deps: "list[str]", extra: "tuple | list" = ()
) -> bool:
    """Add a ``#include "<dep>/<dep>_core.h"`` per *deps* entry, idempotently.

    gh-170: ``depends_on`` links a component against another's OBJECT lib, but
    the dependent's header also *uses* the dependency's types (e.g. an opaque
    ``lfsr_state_t *`` field), so it must include the dependency's header to
    compile. Only deps whose header actually exists are injected (a bare link
    target like ``lo_core`` is skipped — see :func:`_dep_header_includes`).
    gh-432: *extra* carries verbatim header paths from method params'
    ``header`` keys (a capsule param's foreign type, e.g.
    ``telemetry/telemetry.h``) — injected the same way when the header file
    exists under ``native/inc``.
    The includes are placed after the last existing ``#include`` at the top of
    the header; one already present is skipped. The sacred struct and inline
    ``step()`` body are never touched. Returns True if changed."""
    if not path.exists() or not (deps or extra):
        return False
    text = original = path.read_text(encoding="utf-8")
    inc_root = path.parent.parent
    wanted = _dep_header_includes(inc_root, deps) + [
        f'#include "{h}"' for h in extra if (inc_root / h).exists()
    ]
    missing = [inc for inc in wanted if inc not in text]
    if not missing:
        return False
    block = "\n".join(missing)
    incs = list(re.finditer(r"^#include\s+[\"<][^\n]*$", text, re.MULTILINE))
    if incs:
        pos = incs[-1].end()
        text = text[:pos] + "\n" + block + text[pos:]
    else:  # no includes yet — fall back to before the extern "C" / guard
        anchor = "#ifdef __cplusplus"
        if anchor in text:
            text = text.replace(anchor, f"{block}\n\n{anchor}", 1)
        else:
            return False
    if text == original:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def _inject_struct_field(path: Path, comp: str, field_decl: str) -> bool:
    """Surgically insert a member into the ``<comp>_state_t`` struct.

    Additive and splice-free: the field is placed just before the struct's
    closing ``} <comp>_state_t;`` and skipped if already present (idempotent).
    Used for field-backed properties, whose member needs no ``create``/
    ``reset`` wiring (it is set via the property setter, not the constructor),
    so it can be added without rebuilding the sacred lifecycle.  Returns True
    if the header changed."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    if field_decl.strip() in text:
        return False
    # Match the struct's closing brace line WITH its own leading whitespace, so
    # we preserve that indent and align the new field to the struct's existing
    # members. gh-511: the old bare-substring `} <comp>_state_t;` replace left
    # the brace's indent stuck in front of the field (double-indented it) and
    # de-indented the closing brace — mangling any struct nested inside an
    # `extern "C"` block (e.g. doppler's acq_state_t).
    close_re = re.compile(
        rf"^([ \t]*)\}}[ \t]*{re.escape(comp)}_state_t;", re.MULTILINE
    )
    m = close_re.search(text)
    if not m:
        return False
    brace_indent = m.group(1)
    # Indent the field to match the last member above the brace (any struct
    # style); fall back to brace_indent + two spaces for an empty struct.
    field_indent = brace_indent + "  "
    for line in reversed(text[: m.start()].splitlines()):
        if line.strip():
            lead = line[: len(line) - len(line.lstrip())]
            if len(lead) > len(brace_indent):
                field_indent = lead
            break
    text = (
        text[: m.start()]
        + f"{field_indent}{field_decl.strip()}\n"
        + text[m.start() :]
    )
    path.write_text(text, encoding="utf-8")
    return True


_DECL_RE = re.compile(r"^[A-Za-z_][^{}]*\([^{}]*\);$")


def _core_h_decl_lines(text: str) -> "list[str]":
    """Extract single-line C function prototypes from a rendered header.

    A prototype contains ``(`` and ends in ``);`` on one line; struct fields
    (no ``(``), the closing ``} <comp>_state_t;``, and inline definitions
    (which carry ``{``) are excluded.  Used by ``jm apply`` to inject any
    declaration the manifest implies that the user's header is missing —
    additively, never re-rendering the sacred struct/``step()``."""
    return [
        line
        for raw in text.splitlines()
        if _DECL_RE.match(line := raw.strip())
    ]


def _param_headers_at_create(
    cfg: dict, component: str, init_params: "list[tuple]"
) -> "list[str]":
    """Foreign headers this component's params need, at CREATION time.

    gh-790. :func:`C.param_headers` reads the manifest, and at creation the
    component is not in it yet — ``run()`` persists it at the end. For a
    method's capsule header that never mattered, because a method is added to
    an object that already exists. An init-param arrives *with* the object,
    and its foreign type lands in the ``<comp>_create()`` prototype inside the
    sacred ``_core.h``, so missing the include leaves a header that does not
    parse until someone happens to run ``jm apply``.

    So the creation path reads the headers off the ``init_params`` argument —
    the same trick ``depends_on`` already uses two blocks above — and unions
    them with whatever the manifest does know.
    """
    out = list(C.param_headers(cfg, component))
    for p in init_params:
        h = p[11] if len(p) > 11 else ""
        if h and h not in out:
            out.append(h)
    return out


def _splice_init_py(init_py: Path, component: str, Component: str) -> None:
    """Add `from .component import Component` and update __all__ in-place.

    Only appends — never removes or reorders — so user edits are preserved.
    No-op if the import is already present.
    """
    text = init_py.read_text(encoding="utf-8")
    import_line = f"from .{component} import {Component}\n"
    if import_line in text:
        return

    # Insert after the last `from .xxx import` line, or before __all__ if none.
    lines = text.splitlines(keepends=True)
    last_import_idx = -1
    for i, line in enumerate(lines):
        if re.match(r"^from \.\w+ import \w+", line):
            last_import_idx = i
    if last_import_idx >= 0:
        lines.insert(last_import_idx + 1, import_line)
    else:
        lines.append(import_line)
    text = "".join(lines)

    # Append Component to __all__ = [...] (handles single- and multi-line).
    # If __all__ is absent entirely, append it.
    all_re = re.compile(r"(__all__\s*=\s*\[)(.*?)(\])", re.DOTALL)
    if all_re.search(text):

        def _splice_all(m: re.Match) -> str:
            inner = m.group(2)
            if f'"{Component}"' in inner or f"'{Component}'" in inner:
                return m.group(0)
            stripped = inner.rstrip()
            sep = ", " if stripped.rstrip(",") else ""
            return f'{m.group(1)}{stripped.rstrip(",")}{sep}"{Component}"{m.group(3)}'

        text = all_re.sub(_splice_all, text)
    else:
        text = text.rstrip("\n") + f'\n\n__all__ = ["{Component}"]\n'

    init_py.write_text(text, encoding="utf-8")
    print(f"  update  {init_py}")


def _write_compile_commands(
    root: Path,
    all_components: list[str],
    all_modules: list[str] | None = None,
) -> None:
    r = root.resolve()
    python_inc = sysconfig.get_path("include")
    try:
        import numpy as np

        numpy_inc = np.get_include()
    except ImportError:
        numpy_inc = None

    base_inc = [str(r / "native" / "inc")]
    if python_inc:
        base_inc.append(python_inc)
    if numpy_inc:
        base_inc.append(numpy_inc)
    base_flags = "cc -std=c99 -Wall -Wextra -fPIC " + " ".join(
        f"-I{d}" for d in base_inc
    )

    def _entry(src_rel: str, flags: str) -> dict:
        abs_src = str(r / src_rel)
        return {
            "directory": str(r),
            "command": f"{flags} -c {abs_src} -o /dev/null",
            "file": abs_src,
        }

    entries = []

    for comp in all_components:
        comp_inc = base_inc + [str(r / "native" / "inc" / comp)]
        comp_flags = "cc -std=c99 -Wall -Wextra -fPIC " + " ".join(
            f"-I{d}" for d in comp_inc
        )
        test_flags = (
            f"cc -std=c99 -Wall"
            f" -I{r / 'native' / 'inc'}"
            f" -I{r / 'native' / 'inc' / comp}"
        )
        entries.append(_entry(f"native/src/{comp}/{comp}_core.c", comp_flags))
        # Standalone objects have their own _ext.c; module objects do not
        # (they share <module>_ext.c, added below in the modules loop).
        ext_c = r / "native" / "src" / comp / f"{comp}_ext.c"
        if ext_c.exists():
            entries.append(
                _entry(f"native/src/{comp}/{comp}_ext.c", comp_flags)
            )
        entries += [
            _entry(f"native/tests/test_{comp}_core.c", test_flags),
            _entry(f"native/benchmarks/bench_{comp}_core.c", test_flags),
        ]

    for mod in all_modules or []:
        entries.append(_entry(f"native/src/{mod}/{mod}_ext.c", base_flags))
        entries.append(_entry(f"native/src/{mod}/{mod}_core.c", base_flags))

    dest = root / "compile_commands.json"
    verb = "create" if not dest.exists() else "update"
    dest.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print(f"  {verb}  {dest}")


def run(
    root: Path,
    component: str,
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
    init_params: list[tuple[str, str, str]] = (),
    opaque_fields: list[tuple[str, str]] = (),
    opaque_state: bool = False,
    no_ctor_names: "frozenset[str]" = frozenset(),
    controllable_names: "frozenset[str]" = frozenset(),
    pytest_: bool | None = None,
    pytest_benchmark_: bool | None = None,
    class_name: str | None = None,
    depends_on: list[str] = (),
    extra_link_libs: list[str] = (),
    extra_include_dirs: list[str] = (),
    create_fn: str | None = None,
    destroy: "dict | None" = None,
    _hint: bool = True,
) -> None:
    C.require_name(component, "component")

    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\nRun 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)
    if component in C.components(cfg):
        print(
            f"error: component '{component}' already exists in this project.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Inject the starter "gain" field only when the CLI got no --state
    # (state_vars is None) and the struct isn't opaque-managed. An explicit
    # [] — e.g. apply replaying an object whose last field was removed —
    # stays empty rather than resurrecting `gain`.
    _has_opaque = bool(opaque_fields)
    if no_state:
        vars_ = []
    elif state_vars is None:
        vars_ = [] if _has_opaque else [("gain", "float", "0.0f")]
    else:
        vars_ = list(state_vars)
    pkg = C.project_name(cfg)
    version = C.project_version(cfg)
    if perf is None:
        perf = C.is_perf(cfg)
    if pytest_ is not None:
        cfg.setdefault("project", {})["pytest"] = (
            "true" if pytest_ else "false"
        )
    if pytest_benchmark_ is not None:
        cfg.setdefault("project", {})["pytest_benchmark"] = (
            "true" if pytest_benchmark_ else "false"
        )

    ctx = _make_component_ctx(component)
    if class_name is not None:
        ctx["Component"] = class_name
    ctx.update(
        {
            "package": pkg,
            "PACKAGE": pkg.upper(),
            "project": pkg.replace("_", "-"),
            "project_underscore": pkg,
            "version": version,
        }
    )

    sample_ctx = Ctx.make_sample_ctx(
        arg_type, return_type, C.project_bench_block_sizes(cfg)
    )
    ctx.update(sample_ctx)

    ctx.update(
        Ctx.make_state_ctx(
            ctx["component"],
            ctx["Component"],
            vars_,
            array_args=array_args,
            no_state=no_state,
            init_params=init_params,
            opaque_fields=opaque_fields,
            no_ctor_names=no_ctor_names,
            create_fn=create_fn,
            no_reset=no_reset,
            opaque_state=opaque_state,
        )
    )
    ctx.update(Ctx.make_perf_ctx(perf))
    # gh-92: route through the canonical resolver so this matches the
    # default already applied inside make_sample_ctx. Inline string
    # defaults here used to disagree on the void-arg case, causing the
    # bench to be rendered against a (non-void) sample_ctx return type
    # while step_ctx received "void" — the resulting bench would assign
    # the void step() return into a typed _sink, failing the build.
    _rt = Ctx.resolve_return_type(arg_type, return_type)
    ctx.update(
        Ctx.make_step_ctx(
            ctx,
            arg_type,
            _rt,
            no_step=no_step,
            mutable=mutable,
            delegate=step_delegates,
            controllable=[
                (n, ct) for n, ct, _ in vars_ if n in controllable_names
            ],
        )
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
    # No properties exist at creation time (jm property adds them later) —
    # still call this so property_stubs_pyi resolves to "" rather than
    # leaving <<property_stubs_pyi>> unrendered in the fresh .pyi (gh-446).
    ctx.update(
        Ctx.make_properties_ctx(
            ctx["component"],
            ctx["Component"],
            [],
        )
    )
    # Same reasoning as properties above (gh-481): a fresh object declares no
    # warnings, but the slot must still resolve or the new _ext.c ships with a
    # literal <<init_warn_block>> in it.
    ctx.update(Ctx.make_warnings_ctx(ctx["component"], ctx["Component"], []))
    # gh-482: likewise undeclared at creation, which yields the historical
    # MemoryError block — so this render stays byte-identical to before.
    ctx.update(Ctx.make_errors_ctx(ctx["component"], create_fn=create_fn))
    # gh-541/gh-544: the destructor contract. Re-run here with the settled
    # ComponentW and the caller's spec, which `jm apply` supplies when
    # replaying a manifest that declares [<comp>.destroy]. This is the render
    # that also stamps the SACRED _core.h/_core.c signature, so a fresh
    # scaffold of a fallible destructor gets `int <comp>_destroy(...)` and a
    # `return 0;` stub without any hand edit.
    ctx.update(
        Ctx.make_destroy_ctx(ctx["component"], ctx["ComponentW"], destroy)
    )
    # Stream generator (gh-201). At creation there are no extra methods yet, so
    # a streamable object resolves its producer to the built-in source `steps`;
    # a later variable_output method re-points it (recomputed on every render).
    ctx.update(
        Ctx.make_stream_ctx(
            ctx["component"],
            ctx["Component"],
            ctx["ComponentW"],
            streamable=streamable,
            async_stream=async_stream,
            methods=[],
            arg_type=arg_type,
            return_type=_rt,
            default_block=(
                stream_block_default
                if stream_block_default is not None
                else 1024
            ),
        )
    )
    # Re-generate pyi_examples with the actual package name (not placeholder).
    scalar_state = (
        [
            (n, ct, dflt)
            for n, ct, dflt in (vars_ or [])
            if not T.parse_array_type(ct)
        ]
        if not no_state
        else []
    )
    import_line = f"from {pkg} import {ctx['Component']}"
    # gh-273: suppress the construction doctest when a required init-param has
    # no default — there is no valid seed and a validating ctor would reject the
    # type's zero under `pytest --doctest-glob='*.pyi'`.
    ctx["pyi_examples"] = (
        Ctx._pyi_examples_block(
            scalar_state,
            bool(array_args),
            import_line,
            ctx.get("py_create_args", ""),
            ctx["Component"],
            no_reset=no_reset,
        )
        if scalar_state and not Ctx._unseedable_required(init_params)
        else ""
    )
    # Class docstring via the one shared builder (same as the module .pyi path),
    # so the summary/Parameters can never drift between the two generators. The
    # scaffold/create path has only jm's own boilerplate Doxygen in the header,
    # so doc_blocks=None yields the generic "<Component> component." — a header
    # enriched later re-derives on the next `jm bind`/mutation (the _glue path).
    from . import _stubs as _S

    ctx["class_docstring"] = _S.class_docstring_block(
        component,
        ctx["Component"],
        vars_ or [],
        no_state,
        init_params,
        import_line,
        ctx.get("py_create_args", ""),
        doc_blocks=None,
        custom_reset=bool(init_params) or no_reset,
    )

    if create_impl_body is not None:
        from ._object import _indent_body

        ctx["create_assignments"] = _indent_body(create_impl_body)
    # gh-542: with no_reset there is no <comp>_reset() to hold a body, so a
    # lifted reset impl would be spliced into the file bare. Drop it.
    if reset_impl_body is not None and not no_reset:
        from ._object import _indent_body

        ctx["reset_assignments"] = _indent_body(reset_impl_body)
    if destroy_impl_body is not None:
        from ._object import _indent_body

        ctx["destroy_impl"] = _indent_body(destroy_impl_body) + "\n"

    # gh-225: a `depends_on = [{name="dep", link=true}]` entry adds the
    # dependency's `<dep>_core` directly to the consuming target's link line
    # (the same slot extra_link_libs feeds), so its symbols resolve in the
    # built .so. These go on the .so link only — NOT PUBLIC on <comp>_core,
    # because CMake won't propagate the objects transitively into the .so
    # (gh-160). The header-only behaviour of a bare-string dep is unchanged.
    # Read from the depends_on param, not cfg: this component is not yet in
    # the loaded manifest at render time (it's persisted at the end of run()).
    # gh-280: flatten the depends_on closure so the consuming target links every
    # core it transitively pulls in (a CMake OBJECT lib won't propagate them).
    block_libs = list(extra_link_libs) + C.transitive_dep_cores(
        cfg, depends_on, link_only=True
    )
    extra_link_libs_block = (
        "\n    ".join(block_libs) + "\n    " if block_libs else ""
    )
    ctx["extra_link_libs_block"] = extra_link_libs_block
    ctx["extra_ext_sources"] = ""  # populated on jm method --varargs
    # gh-213: emit the Windows runtime-DLL CMake block only when the project
    # targets Windows; default off leaves the component CMakeLists clean.
    ctx.update(
        Ctx.make_platform_ctx(
            C.is_windows_target(cfg), component=ctx["component"]
        )
    )
    # extra_include_dirs is a list of CMake include dirs (literals or ${VAR}
    # references). Each dir lands on its own indented line inside the
    # target_include_directories(...) blocks; leading "\n    " puts the first
    # entry on a new line so the closing ')' stays clean.
    extra_include_dirs_block = (
        "\n    " + "\n    ".join(extra_include_dirs)
        if extra_include_dirs
        else ""
    )
    ctx["extra_include_dirs_block"] = extra_include_dirs_block
    # gh-170: a `depends_on` component is linked AND its header is included, so
    # opaque fields of the dependency's types (e.g. `lfsr_state_t *`) compile
    # without a manual edit. Only deps with a real header are included (a bare
    # link target like `lo_core` is skipped). Each include is newline-prefixed
    # so it sits cleanly after the (possibly empty) perf include.
    # gh-432: method params' `header` keys (a capsule param's foreign type)
    # are included the same way when the header exists.
    _inc_root = root / "native" / "inc"
    ctx["depends_includes"] = "".join(
        "\n" + inc
        for inc in _dep_header_includes(_inc_root, C.dep_names(depends_on))
        + [
            f'#include "{h}"'
            for h in _param_headers_at_create(
                cfg, ctx["component"], init_params
            )
            if (_inc_root / h).exists()
        ]
    )

    def r(tmpl):
        return R.render(tmpl, ctx)

    comp = ctx["component"]

    # extra_link_on_core: propagates external includes to the OBJECT library
    # so that its header files can #include external library headers directly.
    if extra_link_libs:
        parts = "\n    ".join(extra_link_libs)
        ctx["extra_link_on_core"] = (
            f"target_link_libraries({comp}_core PUBLIC\n    {parts})\n"
        )
    else:
        ctx["extra_link_on_core"] = ""
    # extra_include_dirs_on_core: PUBLIC include dirs on the OBJECT library so
    # downstream consumers (Python ext, test, bench) inherit them transitively.
    if extra_include_dirs:
        parts = "\n    ".join(extra_include_dirs)
        ctx["extra_include_dirs_on_core"] = (
            f"target_include_directories({comp}_core PUBLIC\n    {parts})\n"
        )
    else:
        ctx["extra_include_dirs_on_core"] = ""

    print(f"just-makeit: adding component '{comp}' to project '{pkg}'")
    print()

    if perf and not C.is_perf(cfg):
        cfg.setdefault("project", {})["perf"] = "true"

    if perf:
        perf_h = root / "native" / "inc" / "jm_perf.h"
        if not perf_h.exists():
            _write(perf_h, r(R.JM_PERF_H))
        simd_h = root / "native" / "inc" / "jm_simd.h"
        if not simd_h.exists():
            _write(simd_h, R.JM_SIMD_H)

    core_h_tmpl = R.COMPONENT_CORE_H
    core_c_tmpl = R.COMPONENT_CORE_C
    ext_c_tmpl = R.COMPONENT_EXT_C
    test_c_tmpl = R.COMPONENT_TEST_C
    bench_c_tmpl = R.NO_STEP_BENCH_C if no_step else R.COMPONENT_BENCH_C
    pytest_tmpl = R.PYTEST_TEST_PURE if C.is_pytest(cfg) else R.PYTEST_TEST
    bench_py_tmpl = (
        R.COMPONENT_BENCH_PYTEST_BM
        if C.is_pytest_benchmark(cfg)
        else R.COMPONENT_BENCH_PY
    )
    init_py_tmpl = R.PACKAGE_INIT_PY

    # C headers. Object creation is create-only (the verb errors on a
    # duplicate name), so the sacred files are written fresh — never spliced.
    core_h_path = root / "native" / "inc" / comp / f"{comp}_core.h"
    _write(
        core_h_path,
        r(core_h_tmpl),
        "update" if core_h_path.exists() else "create",
    )
    if impl_body is not None and not no_step:
        from . import _impl as I

        h_path = root / "native" / "inc" / comp / f"{comp}_core.h"
        h_text = h_path.read_text(encoding="utf-8")
        h_text = I.patch_function_body(h_text, f"{comp}_step", impl_body)
        h_path.write_text(h_text, encoding="utf-8")

    # C sources (create-only — see above).
    core_c_path = root / "native" / "src" / comp / f"{comp}_core.c"
    _write(
        core_c_path,
        r(core_c_tmpl),
        "update" if core_c_path.exists() else "create",
    )
    _write(root / "native" / "src" / comp / f"{comp}_ext.c", r(ext_c_tmpl))

    build = C.build_system(cfg)

    if build == "cmake":
        _write(
            root / "native" / "src" / comp / "CMakeLists.txt",
            r(R.CMAKE_LISTS_COMPONENT),
        )

    # C test
    _write(root / "native" / "tests" / f"test_{comp}_core.c", r(test_c_tmpl))

    # C benchmark
    _write(
        root / "native" / "benchmarks" / f"bench_{comp}_core.c",
        r(bench_c_tmpl),
    )
    jm_bench_h = root / "native" / "benchmarks" / "jm_bench.h"
    if not jm_bench_h.exists():
        _write(jm_bench_h, R.JM_BENCH_H)

    # Python package — create __init__.py on first component; splice on subsequent ones
    init_py = root / "src" / pkg / "__init__.py"
    if not init_py.exists():
        _write(init_py, r(init_py_tmpl))
    else:
        _splice_init_py(init_py, comp, ctx["Component"])

    _write(root / "src" / pkg / f"{comp}.pyi", R.render_component_pyi(ctx))
    _write(root / "src" / pkg / "tests" / "__init__.py", R.TESTS_INIT_PY)
    _write(root / "src" / pkg / "tests" / f"test_{comp}.py", r(pytest_tmpl))

    # Python benchmark
    benchmarks_init = root / "src" / pkg / "benchmarks" / "__init__.py"
    if not benchmarks_init.exists():
        _write(benchmarks_init, "")
    _write(
        root / "src" / pkg / "benchmarks" / f"bench_{comp}.py",
        r(bench_py_tmpl),
    )

    # Benchmark history dir — dated snapshots committed to git
    gitkeep = root / "benchmarks" / "history" / ".gitkeep"
    if not gitkeep.exists():
        _write(gitkeep, "")

    if build == "cmake":
        # Write cmake/pkg.pc.in if the project predates v0.4
        pc_in = root / "cmake" / f"{pkg.replace('_', '-')}.pc.in"
        if not pc_in.exists():
            _write(pc_in, R.render(R.CMAKE_PC_IN, ctx))

        # Write or update the umbrella header
        umbrella = root / "native" / "inc" / f"{pkg}.h"
        include_line = f'#include "{comp}/{comp}_core.h"\n'
        if not umbrella.exists():
            _write(umbrella, R.render(R.UMBRELLA_H, ctx))
        umbrella_text = umbrella.read_text(encoding="utf-8")
        if include_line not in umbrella_text:
            # Insert before the closing #endif
            umbrella_text = umbrella_text.replace(
                "#ifdef __cplusplus\n}\n#endif\n\n#endif",
                f"{include_line}\n#ifdef __cplusplus\n}}\n#endif\n\n#endif",
            )
            umbrella.write_text(umbrella_text, encoding="utf-8")
            print(f"  update  {umbrella}")

        # Insert add_subdirectory + target_sources into the `# ── Components`
        # sentinel section — matches `_object.run`'s placement so a project
        # built via `jm new --object` and one built via `jm new` + `jm object`
        # have identical CMakeLists, and so `jm apply`'s aggregate reconcile
        # is a no-op on either.
        cmake_path = root / "CMakeLists.txt"
        if cmake_path.exists():
            cmake_text = cmake_path.read_text(encoding="utf-8")
            sub = f"add_subdirectory(native/src/{comp})\n"
            if sub not in cmake_text:
                obj_lines = ""
                if f"{pkg}_lib" in cmake_text:
                    for dep in C.dep_names(depends_on):
                        dep_name = dep[:-5] if dep.endswith("_core") else dep
                        obj_lines += (
                            f"target_sources({pkg}_lib PRIVATE "
                            f"$<TARGET_OBJECTS:{dep_name}_core>)\n"
                        )
                    obj_lines += (
                        f"target_sources({pkg}_lib PRIVATE "
                        f"$<TARGET_OBJECTS:{comp}_core>)\n"
                    )
                sentinel = "# ── Components"
                if sentinel in cmake_text:
                    idx = cmake_text.index(sentinel)
                    idx = cmake_text.index("\n", idx) + 1
                    cmake_text = (
                        cmake_text[:idx] + sub + obj_lines + cmake_text[idx:]
                    )
                else:
                    cmake_text += sub + obj_lines
                cmake_path.write_text(cmake_text, encoding="utf-8")
                print(f"  update  {cmake_path}")

        # compile_commands.json
        all_comps = C.components(cfg) + [comp]
        _write_compile_commands(root, all_comps, C.modules(cfg))

    else:
        # Patch TARGETS and C_TESTS lists, insert compile rules into Makefile
        mf_path = root / "Makefile"
        mf = mf_path.read_text(encoding="utf-8")
        target = f"src/{pkg}/{comp}$(EXT)"
        ctest = f"test_{comp}_core"
        mf = re.sub(r"^(TARGETS\s*:=.*)$", rf"\1 {target}", mf, flags=re.M)
        mf = re.sub(r"^(C_TESTS\s*:=.*)$", rf"\1 {ctest}", mf, flags=re.M)
        rules = R.render(R.MAKEFILE_SIMPLE_COMPONENT, ctx)
        mf = mf.replace("# ── Fixed targets", rules + "# ── Fixed targets")
        mf_path.write_text(mf, encoding="utf-8")
        print(f"  update  {mf_path}")

    # just-makeit.toml
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
        controllable_names_=controllable_names,
        extra_link_libs_=list(extra_link_libs),
        extra_include_dirs_=list(extra_include_dirs),
    )
    # gh-541/gh-544: persist the destructor contract so it survives the
    # manifest round-trip. `jm apply` replays through this path, and a key
    # apply silently drops is the second defect gh-519 shipped.
    C.set_destroy_spec(cfg, comp, destroy or {})
    C.save(root, cfg)
    print(f"  update  {cfg_path}")

    print()
    if _hint:
        print("Done!  make && make test")
