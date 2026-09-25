"""The C stem jm derives a component's, module's or function's symbols from.

gh-1591, phase 1 of 3. The one owner of the question "what does a symbol jm
names *for* the author start with". Two different things share a component's
name today, and the distinction is the point of this module:

- the **file stem** -- ``native/src/<comp>/<comp>_core.c``, the
  ``<comp>_core`` CMake target, the ``<comp>/`` header directory. Files are
  namespaced by the package directory (gh-1583) and never move for this.
- the **symbol stem** (:func:`stem`) -- ``<stem>_create``,
  ``<stem>_state_t``, the ``<STEM>_CORE_H`` guard, a method's
  ``<stem>_<name>``. Every C identifier jm derives from a name, as opposed
  to one the author named (``fn =``, ``create_fn``, ``status_fn``, ...),
  which is never touched.

Today the two are equal: :func:`stem` returns the name it is given, and every
project renders byte-identically to before this module existed.

Phase 2 adds ``[project] c_prefix`` and reads it here and nowhere else, so
two installed jm packages that share a component name stop colliding at link
time and in one translation unit (the shared include guard silently drops
the second header today). Its rules, stated here so they land in this file:

- a name that already starts with ``<prefix>_`` is used as is (doppler's
  ``dp_tlm`` stays ``dp_tlm_create``, not ``dp_dp_tlm_create``);
- ``apply`` refuses a manifest whose derived symbol set has a duplicate --
  ``x`` and ``dp_x`` both deriving ``dp_x_create`` -- checked over the whole
  set, so a component, a method and a module function that happen to derive
  one name are caught by the same check.

Every function takes an *owner*, exactly as :mod:`_incpath` does: a path
inside the project, or its manifest as a dict -- never a bare package name,
because a name does not say whether the project declares a prefix.

The template slots are ``<<csym>>`` / ``<<CSYM>>``, set wherever
``<<component>>`` / ``<<module>>`` are (:func:`slots`); `render()` refuses a
template that uses one without it, through the same check that guards
``<<inc_prefix>>``. ``tests/test_gh1591_one_symbol_stem.py`` refuses a
template that derives a symbol from ``<<component>>``, and ratchets the
Python sites that still spell one by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import _incpath as INC

#: The template slots whose value depends on the project's symbol stem.
#: `render()` refuses a template that uses one when its context lacks it --
#: the same check, and the same loop, that guards `_incpath.PROJECT_SLOTS`.
SLOTS = ("csym", "CSYM")


def prefix(owner: INC.Owner) -> "str | None":
    """*owner*'s ``[project] c_prefix``, or None (gh-1591 phase 2).

    >>> prefix({"project": {"c_prefix": "dp"}}), prefix({"project": {}})
    ('dp', None)
    """
    from . import _config as C

    return C.c_prefix(INC.manifest(owner))


def stem(owner: INC.Owner, name: str) -> str:
    """The C stem jm derives *name*'s symbols from, in *owner*'s project.

    *name* is a component, a module (its C name, ``cname``) or a module
    function. Without ``[project] c_prefix`` it is *name* itself; with one it
    is ``<prefix>_<name>`` -- unless *name* already starts with
    ``<prefix>_``, which is used as is: doppler's ``dp_tlm`` stays
    ``dp_tlm_create``, not ``dp_dp_tlm_create``. The collision that rule can
    open -- ``x`` and ``dp_x`` both deriving ``dp_x_create`` -- is refused
    over the whole derived set by :func:`duplicates`.

    >>> stem({"project": {"name": "p"}}, "fir")
    'fir'
    >>> stem({"project": {"c_prefix": "dp"}}, "fir")
    'dp_fir'
    >>> stem({"project": {"c_prefix": "dp"}}, "dp_tlm")
    'dp_tlm'
    """
    p = prefix(owner)
    if p is None or name.startswith(f"{p}_"):
        return name
    return f"{p}_{name}"


def upper(owner: INC.Owner, name: str) -> str:
    """:func:`stem` in upper case: an include guard's or ``#define``'s.

    >>> upper({"project": {"name": "p"}}, "fir")
    'FIR'
    """
    return stem(owner, name).upper()


def create_name(stem_: str, create_fn: "str | None" = None) -> str:
    """The constructor an object's faces call: the declared *create_fn*, else
    ``<stem>_create`` -- the one spelling of the default (gh-1328, gh-1591).

    Takes a stem already derived (:func:`stem`, or a render context's
    ``csym``) so a context-only caller and a manifest caller share one rule:
    ``C.object_create_name`` is this over the manifest.

    >>> create_name("fir"), create_name("fir", "fir_open")
    ('fir_create', 'fir_open')
    """
    return create_fn or f"{stem_}_create"


def ctx_create_name(ctx: dict) -> str:
    """:func:`create_name` for a render context: its ``create_name`` slot
    when the state context set one, else the default from its ``csym``.

    >>> ctx_create_name({"csym": "fir"})
    'fir_create'
    """
    declared = ctx.get("create_name")
    return str(declared) if declared else create_name(str(ctx["csym"]))


def sources(cfg: dict) -> "dict[str, str]":
    """Every name jm derives C symbols from in *cfg*, mapped to its stem:
    each component, each module's C name, each module function.

    >>> sources({"project": {"name": "p", "c_prefix": "dp"},
    ...          "fir": {}, "dp_tlm": {}})
    {'fir': 'dp_fir', 'dp_tlm': 'dp_tlm'}
    """
    from . import _config as C

    names = list(C.components(cfg))
    for mod in C.modules(cfg):
        names.append(C.module_paths(mod).cname)
        names += [f["name"] for f in C.module_functions(cfg, mod)]
    return {n: stem(cfg, n) for n in dict.fromkeys(names)}


def _file_scope(mask: str) -> str:
    """*mask* (comments and strings already blanked) with every brace body
    and preprocessor line blanked too: what is left is file scope, where a
    header declares its functions and types. An ``extern "C" { ... }``
    block IS file scope -- every jm header wraps its declarations in one --
    so its braces are transparent.

    >>> _file_scope('extern " " {\\nint f(void);\\nint g(void) { h(); }\\n}')
    'extern " "  \\nint f(void);\\nint g(void)         \\n '
    """
    out = []
    # One entry per open brace: True for a body (its content is blanked),
    # False for an `extern "C"` linkage block (its content is file scope).
    stack: "list[bool]" = []
    seen = ""
    for line in mask.splitlines(keepends=True):
        if not any(stack) and line.lstrip().startswith("#"):
            out.append("\n")
            continue
        buf = []
        for ch in line:
            inside = any(stack)
            if ch == "{":
                linkage = not inside and re.search(
                    r'\bextern\s*"[^"\n]*"\s*$', seen + "".join(buf)
                )
                stack.append(not linkage)
                buf.append(" ")
            elif ch == "}":
                if stack:
                    stack.pop()
                buf.append(" ")
            else:
                buf.append(ch if not inside or ch == "\n" else " ")
        text = "".join(buf)
        out.append(text)
        seen = (seen + text)[-80:]
    return "".join(out)


_CALLED = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_TYPEDEF = re.compile(r"\btypedef\b[^;]*?\b([A-Za-z_]\w*)\s*;")
_GUARD = re.compile(r"^\s*#\s*(?:ifndef|define)\s+([A-Za-z_]\w*)", re.M)


def declared(text: str) -> "set[str]":
    """The identifiers a C header declares at file scope: functions (a name
    followed by ``(``), ``typedef`` names, and macros it ``#define``s or
    guards with ``#ifndef``. Comments and strings never count.

    >>> sorted(declared('''#ifndef P_FIR_CORE_H
    ... #define P_FIR_CORE_H
    ... /* fir_create() in a comment */
    ... typedef struct { int n; } p_fir_state_t;
    ... p_fir_state_t *p_fir_create(void);
    ... static inline int p_fir_step(p_fir_state_t *s) { return g(s->n); }
    ... #endif'''))
    ['P_FIR_CORE_H', 'p_fir_create', 'p_fir_state_t', 'p_fir_step']
    """
    from ._docsync import _code_mask

    mask = _code_mask(text)
    scope = _file_scope(mask)
    return (
        set(_CALLED.findall(scope))
        | set(_TYPEDEF.findall(scope))
        | set(_GUARD.findall(mask))
    )


def _source_of(name: str, stems: "dict[str, str]") -> "tuple[str, str] | None":
    """The ``(source, stem)`` *name* derives from -- the longest stem it
    starts with at an identifier boundary, in either case -- or None."""
    best = None
    for src, st in stems.items():
        for s, cased in ((st, src), (st.upper(), src.upper())):
            if (name == s or name.startswith(s + "_")) and (
                best is None or len(s) > len(best[1])
            ):
                best = (cased, s)
    return best


def _derived_by_dir(tree: Path, cfg: dict) -> "dict[str, set[str]]":
    """Every derived identifier the headers under *tree* declare, mapped to
    the header directories (component / module) that declare it."""
    stems = sources(cfg)
    out: "dict[str, set[str]]" = {}
    root = INC.header_root(tree, cfg)
    for h in sorted(root.rglob("*.h")):
        where = h.parent.relative_to(root).as_posix() or "."
        for name in declared(h.read_text(encoding="utf-8", errors="replace")):
            if _source_of(name, stems) is not None:
                out.setdefault(name, set()).add(where)
    return out


def duplicates(tree: Path, cfg: dict) -> "list[str]":
    """Why *cfg*'s derived symbols cannot all exist in one library: each
    derived identifier that the headers of two different components or
    modules under *tree* (jm's render of the project) both declare.

    Read from the render, not re-derived from the manifest, so every shape
    that names a symbol -- lifecycle, methods, accessors, module functions,
    process_global, guards -- is covered by the one set of rules that
    produces it. Asked only of a prefixed project: the ``<p>_`` rule is what
    can turn two different names into one (``x`` and ``dp_x``).
    """
    if prefix(cfg) is None:
        return []
    out = []
    for name, dirs in sorted(_derived_by_dir(tree, cfg).items()):
        if len(dirs) > 1:
            out.append(
                f"the C symbol `{name}` is derived twice, in "
                + " and ".join(f"`{d}`" for d in sorted(dirs))
                + " -- rename one of them"
            )
    return out


def renames(tree: Path, cfg: dict) -> "dict[str, str]":
    """``{unprefixed: prefixed}`` for every derived identifier the headers
    under *tree* (jm's render of the project) declare: what a project that
    adds ``c_prefix`` has to respell in the C it wrote.

    Case-sensitive and derived only: ``fir_state_t`` and ``FIR_CORE_H`` are
    here; an author's own ``FIR_STATE_MAGIC`` is not, because jm never
    declares it.
    """
    stems = sources(cfg)
    out = {}
    # Headers AND sources: a varargs method's binder (`<stem>_<name>` in
    # its own create-only `_core.c`, called from the binding) is derived
    # and declared in no header. Duplicates stay header-only -- there a
    # declaration and its definition would count twice.
    found = set(_derived_by_dir(tree, cfg))
    for c in sorted((tree / "native").rglob("*.c")):
        found |= {
            n
            for n in declared(c.read_text(encoding="utf-8", errors="replace"))
            if _source_of(n, stems) is not None
        }
    for name in found:
        src = _source_of(name, stems)
        if src is None:
            continue
        cased, st = src
        old = cased + name[len(st) :]
        if old != name:
            out[old] = name
    return out


def _author_files(root: Path) -> "list[Path]":
    """The project's C/C++ files whose content is the author's:
    ``_createonly``'s AUTHOR and PARTIAL kinds, and anything it does not
    classify (a module function's ``.c``, a hand-written file). What jm
    rewrites whole -- JM, RECONCILED, DERIVED -- is not asked.

    Walked by gh-1583's ``_upgrade._project_files`` -- the walk `jm upgrade`
    respells over -- so the refusal never names a file the upgrade it points
    to would not fix: a nested project's, a build tree's."""
    from . import _createonly
    from . import _upgrade

    def mine(p: Path) -> bool:
        if p.suffix not in _upgrade._C_SUFFIXES:
            return False
        rule = _createonly.classify(p.relative_to(root).as_posix())
        return rule is None or rule.kind in (
            _createonly.AUTHOR,
            _createonly.PARTIAL,
        )

    return _upgrade._project_files(root, mine)


def old_names_pattern(names: "dict[str, str]") -> "re.Pattern":
    """One regex matching any OLD name in *names* as a whole identifier,
    case-sensitive -- the one matcher the refusal (:func:`unrenamed`) and
    `jm upgrade`'s respell share, so what one reports the other rewrites.

    Whole identifier is what makes the respell idempotent: ``fir_create``
    cannot match inside ``zz_fir_create``, because ``_`` is an identifier
    character.

    >>> p = old_names_pattern({"fir_create": "zz_fir_create"})
    >>> [m.group(0) for m in p.finditer("zz_fir_create fir_create FIR_CREATE")]
    ['fir_create']
    """
    return re.compile(
        r"(?<![A-Za-z0-9_])("
        + "|".join(map(re.escape, sorted(names, key=len, reverse=True)))
        + r")(?![A-Za-z0-9_])"
    )


_GUARD_LINE = re.compile(r"^\s*#\s*ifndef\s+([A-Za-z_]\w*)_CORE_H\b", re.M)


def stray_prefixes(root: Path, cfg: dict) -> "dict[str, str]":
    """``{component: the prefix its header already carries}`` wherever that
    differs from what *cfg* declares -- a ``c_prefix`` CHANGED (``a`` to
    ``b``) or REMOVED after the tree was prefixed.

    Read from each component's include guard in the real tree, the one line
    jm always derives: ``#ifndef A_FIR_CORE_H`` under ``c_prefix = "b"``
    says ``a``. Neither case is migrated (gh-1591 phase 3 moves a bare tree
    onto a prefix, nothing else), so both are refused rather than half-done.
    """
    from . import _config as C

    want = prefix(cfg)
    out = {}
    hroot = INC.header_root(root, cfg)
    for comp in C.components(cfg):
        h = hroot / comp / f"{comp}_core.h"
        if not h.is_file():
            continue
        m = _GUARD_LINE.search(h.read_text(encoding="utf-8", errors="replace"))
        if not m:
            continue
        guard, name = m.group(1), comp.upper()
        expect = upper(cfg, comp)
        if guard == expect or guard == name:
            continue
        if guard.endswith("_" + name):
            had = guard[: -len(name) - 1].lower()
            if had != (want or ""):
                out[comp] = had
    return out


def unrenamed(root: Path, names: "dict[str, str]") -> "dict[str, list[str]]":
    """``{file: [old names]}`` for the author's C under *root* that still
    spells a name in *names* (from :func:`renames`) -- in code, whole
    identifiers only, case-sensitive. Phase 3's ``jm upgrade`` respells
    exactly these; until then ``apply`` refuses on them.

    >>> import tempfile
    >>> d = Path(tempfile.mkdtemp())
    >>> (d / "native/src/acc").mkdir(parents=True)
    >>> _ = (d / "native/src/acc/acc_core.c").write_text(
    ...     "#define ACC_STATE_MAGIC 7 /* acc_create */\\n"
    ...     "acc_state_t *acc_create(void) { return 0; }\\n")
    >>> unrenamed(d, {"acc_create": "dp_acc_create",
    ...               "acc_state_t": "dp_acc_state_t"})
    {'native/src/acc/acc_core.c': ['acc_create', 'acc_state_t']}
    """
    from ._docsync import _code_mask

    if not names:
        return {}
    pat = old_names_pattern(names)
    out = {}
    for p in _author_files(root):
        mask = _code_mask(p.read_text(encoding="utf-8", errors="replace"))
        found = sorted(set(pat.findall(mask)))
        if found:
            out[p.relative_to(root).as_posix()] = found
    return out


def slots(owner: INC.Owner, name: str) -> "dict[str, str]":
    """The template slots for a render of *name*'s files in *owner*'s project.

    >>> slots({"project": {"name": "p"}}, "fir")
    {'csym': 'fir', 'CSYM': 'FIR'}
    """
    return {"csym": stem(owner, name), "CSYM": upper(owner, name)}
