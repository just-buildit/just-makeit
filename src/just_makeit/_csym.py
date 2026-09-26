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


def property_getter(stem_: str, prop: str) -> str:
    """The derived getter of property *prop* on the component whose stem is
    *stem_*: ``<stem>_get_<prop>`` -- what a plain property's binding calls,
    and the name EVERY property's docstring is read from (gh-1670).

    >>> property_getter("dp_fir", "num_taps")
    'dp_fir_get_num_taps'
    """
    return f"{stem_}_get_{prop}"


def property_getters(cfg: dict) -> "dict[str, str]":
    """``{getter: component stem}`` for each property of each component in
    *cfg*, under the stem *cfg* derives (:func:`property_getter`).

    A derived name whether or not jm declares it (gh-1670). A plain
    property's getter is in the render; a ``field = true`` (or ``expr``,
    ``buf_field``, capsule) property's is not -- the binding reads the
    field -- yet its docstring is still looked up at this name, so an author
    documents it by declaring the getter in the sacred header. A replay's
    declarations alone would leave that one out of :func:`renames`, and
    `jm upgrade` would leave it unprefixed while the doc lookup asked for
    the prefixed spelling.

    >>> property_getters({"project": {"name": "p", "c_prefix": "dp"},
    ...                   "fir": {"properties": [{"name": "num_taps",
    ...                                           "field": True}]}})
    {'dp_fir_get_num_taps': 'dp_fir'}
    """
    from . import _config as C

    out = {}
    for comp in C.components(cfg):
        st = stem(cfg, comp)
        for prop in (cfg.get(comp) or {}).get("properties", []) or []:
            if isinstance(prop, dict) and prop.get("name"):
                out[property_getter(st, str(prop["name"]))] = st
    return out


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
    declares it. Every property's getter is here too, declared or not
    (:func:`property_getters`, gh-1670).
    """
    stems = sources(cfg)
    out = {}
    # Headers AND sources: a varargs method's binder (`<stem>_<name>` in
    # its own create-only `_core.c`, called from the binding) is derived
    # and declared in no header. Duplicates stay header-only -- there a
    # declaration and its definition would count twice.
    found = set(_derived_by_dir(tree, cfg)) | set(property_getters(cfg))
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


def _derivation(name: str, cfg: dict) -> str:
    """What *name* is derived from, in the manifest's words: a component and
    its method, a module function, or the component or module alone."""
    from . import _config as C

    src = _source_of(name, sources(cfg))
    if src is None:
        return "the manifest"
    cased, st = src
    source = cased.lower()
    rest = name[len(st) :].lstrip("_")
    for mod in C.modules(cfg):
        if source in {f["name"] for f in C.module_functions(cfg, mod)}:
            return f"module `{mod}`'s function `{source}`"
        if source == C.module_paths(mod).cname:
            return f"module `{mod}`"
    methods = {
        m.get("name") for m in (cfg.get(source) or {}).get("methods", [])
    }
    if rest in methods:
        return f"component `{source}`'s method `{rest}`"
    return f"component `{source}`"


def _line_of(text: str, name: str) -> int:
    """The line where *text* (C) declares *name* at file scope: the first
    code occurrence :func:`declared` would have read it from."""
    from ._docsync import _code_mask

    mask = _code_mask(text)
    word = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
    # Count lines in the string that matched: `_file_scope` keeps every line
    # but shortens a preprocessor one to "\n", so its offsets are not the
    # mask's.
    for where in (_file_scope(mask), mask):
        m = word.search(where)
        if m:
            return where.count("\n", 0, m.start()) + 1
    return 1


#: The derived names whose files are a component's own: its state type and
#: lifecycle. What an author-written derived name beside them is owned by.
_LIFECYCLE = ("state_t", "create", "destroy", "reset")


def collisions(root: Path, tree: Path, cfg: dict) -> "list[str]":
    """Why *cfg*'s ``c_prefix`` cannot be applied to the tree at *root*: each
    prefixed name jm would derive (a :func:`renames` value) that an author
    C file under *root* already DECLARES (:func:`declared`) while not being
    one of that name's OWNING files (gh-1657).

    A name's owning files are the ones jm's own render (*tree*, the replay)
    declares or defines it in -- a component's ``_core.h`` / ``_core.c``, a
    module's header, a module function's ``.c`` -- read from the render,
    never listed. So a tree an older jm half-moved (its C respelled, its
    manifest not) declares the new names only where jm does, and is not a
    collision; an author header that already has ``dp_syncword_find`` is.

    Refused rather than respelled, because the respell cannot tell the two
    functions apart: it turns every call to ``ber_theory_ser`` into
    ``dp_ber_theory_ser``, including the one inside the author's own
    ``dp_ber_theory_ser`` wrapper, which then calls itself. The one detector
    `apply` and `jm upgrade` both ask, before either writes.

    The OLD spelling too (gh-1661): a file with its own ``static crc16``
    beside module ``wfm``'s jm function ``crc16`` would have it renamed to
    ``dp_crc16`` -- the respell is keyed by name, not by function -- and the
    tree the upgrade left would then collide. Only when a prefix renames:
    with none, the two ``crc16`` never meet, exactly as before.
    """
    from . import _config as C
    from . import _function
    from . import _upgrade

    if prefix(cfg) is None:
        return []
    # gh-1653's map: plus `<stem>_step_batch`, which `JM_DEFINE_STEPS`
    # pastes and the upgrade respells. The bare stem is not here: it is
    # respelled only as the macro's argument, so no identifier `lo` moves.
    names = with_macro_names(renames(tree, cfg))
    new = set(names.values())
    if not new:
        return []
    owning: "dict[str, set[str]]" = {}
    for f in sorted(tree.rglob("*")):
        if f.suffix not in _upgrade._C_SUFFIXES or not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for n in declared(text) & new:
            # Keyed without the header layout: upgrade asks before it moves
            # the headers, so the replay and the tree may disagree on it.
            owning.setdefault(n, set()).add(
                INC.layout_free(f.relative_to(tree).as_posix(), cfg)
            )
    # `<stem>_step_batch` is the author's, beside the `<stem>_step` (sacred
    # header) and `<stem>_steps` (`_core.c`) jm renders -- no render
    # declares it, so it is owned where those siblings are.
    for n in new:
        if n.endswith("_step_batch") and n not in owning:
            base = n[: -len("_batch")]
            sibling = owning.get(base, set()) | owning.get(base + "s", set())
            if sibling:
                owning[n] = sibling
    # gh-1670: a property getter no render declares (a `field = true`
    # property's) is the author's, written to document the property -- in
    # the component's own files, where its `<stem>_state_t` and lifecycle are.
    for n, st in property_getters(cfg).items():
        if n in new and n not in owning:
            owning[n] = set().union(
                *(owning.get(f"{st}_{s}", set()) for s in _LIFECYCLE)
            )
    # An `*_impl_file = "path::fn"` names the file jm lifts a body FROM: its
    # `fn` is jm's, by the manifest's own word, and gh-1653 respells the
    # `::fn` with it -- so that file owns the name.
    for comp in C.components(cfg):
        section = cfg.get(comp) or {}
        for key in IMPL_KEYS:
            ref = section.get(f"{key}_file")
            if not isinstance(ref, str) or "::" not in ref:
                continue
            path, _, fn = ref.partition("::")
            # Either spelling: before the upgrade `::lo_reset`, after it
            # `::zz_lo_reset` -- the same function.
            owned = names.get(fn, fn if fn in new else None)
            if owned:
                owning.setdefault(owned, set()).add(
                    INC.layout_free(Path(path).as_posix(), cfg)
                )
    # A module function's home is a manifest choice (`inline`,
    # `functions_in_core`), so the replay shows only where it is NOW; the
    # tree may still hold jm's own copy where it was. All its homes own it.
    for mod in C.modules(cfg):
        cname = C.module_paths(mod).cname
        for f in C.module_functions(cfg, mod):
            homes = _function.homes(cfg, cname, f["name"])
            owning.setdefault(stem(cfg, f["name"]), set()).update(
                INC.layout_free(h, cfg) for h in homes
            )
    out = []
    # Every C file of the project, not `_author_files`: ownership is the
    # replay's answer, per name, not `_createonly`'s per file. (Until gh-1659
    # that classification also claimed every author header beside the
    # umbrella -- doppler's `dp_syncword.h`, the collision this was filed
    # for, gh-1657.)
    files = _upgrade._project_files(
        root, lambda p: p.suffix in _upgrade._C_SUFFIXES
    )
    # gh-1669: a stem passed to a macro that pastes it into a derived name
    # AND one that is not -- no spelling of the argument is right, so the
    # upgrade must not guess and `apply` must not pass it.
    macros = project_macros(root)
    stems = macro_stems(cfg)
    for p in files:
        rel = p.relative_to(root).as_posix()
        text = p.read_text(encoding="utf-8", errors="replace")
        for at, end, why, _label in pasted_stems(text, names, stems, macros):
            if end is None:
                out.append(f"{rel}:{text.count(chr(10), 0, at) + 1}: {why}")
        where = INC.layout_free(rel, cfg)
        found = declared(text)
        for n in sorted(found & new):
            if where in owning.get(n, set()):
                continue
            out.append(
                f"{rel}:{_line_of(text, n)} already declares `{n}`, the name"
                f" [project] c_prefix = {prefix(cfg)!r} derives from "
                f"{_derivation(n, cfg)} -- two different C symbols would"
                " become one. Rename yours, or choose another c_prefix"
            )
        # An old name is owned where its new spelling is: a sacred file
        # still spelling it bare is jm's, awaiting the respell.
        for old in sorted(found & names.keys()):
            if where in owning.get(names[old], set()):
                continue
            out.append(
                f"{rel}:{_line_of(text, old)} declares its own `{old}`, which"
                f" shares its name with {_derivation(names[old], cfg)} --"
                f" [project] c_prefix = {prefix(cfg)!r} renames that to"
                f" `{names[old]}`, and `jm upgrade` cannot tell your calls"
                " from jm's. Rename yours, or make it `static` under another"
                " name"
            )
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
        rule = _createonly.classify(p.relative_to(root).as_posix(), root)
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


# -- Is this identifier a REFERENCE to a derived name? (gh-1668, gh-1669) --
#
# The one question the respell (`respell_c`) and the refusal (`_old_in`)
# both ask of an identifier spelled like a derived name. The rename map is
# keyed by spelling, but C scopes a name: a struct member, a parameter or a
# local spelled `frame_bits` is not the function `frame_bits`, and renaming
# one is an API break nobody asked for (gh-1668). The other way, a derived
# name need not be spelled at all: an author macro pasting `pfx##_state_bytes`
# makes its ARGUMENT the stem (gh-1669).
#
# Read from tokens of the code mask with the least context C needs to say
# which is which -- the tokens either side, the enclosing bracket, the kind
# of brace. Not a C parser: what it cannot place it calls a reference, which
# is what every identifier was before.

#: A token of the code mask: an identifier, a number, the operators the rules
#: read (`->`, `##`, `...`), a line splice (dropped), a newline (kept only
#: where it ends a directive), or any other single character.
_TOKEN = re.compile(r"[A-Za-z_]\w*|\d[\w.]*|->|##|\.\.\.|\\[ \t]*\n|\n|\S")
_IDENT = re.compile(r"[A-Za-z_]\w*\Z")

#: Declaration specifiers that name no type: `const fir_cfg_t` in a
#: prototype is a TYPE use of `fir_cfg_t`, not a parameter named it.
_QUALS = frozenset(
    "const volatile restrict __restrict __restrict__ static extern inline"
    " __inline __inline__ register auto _Thread_local thread_local".split()
)
#: Words after which a name is an EXPRESSION or a type being declared,
#: never a variable: `return frame_bits;`, `typedef ... fir_state_t;`.
_NOT_SPECIFIER = frozenset(
    "return sizeof case goto else do if while switch for _Alignof alignof"
    " typeof __typeof__ typedef define undef ifdef ifndef elif"
    " defined".split()
)
_TAG_KW = frozenset(("struct", "union", "enum"))
#: What may follow a declarator's name, and what may precede its specifiers
#: (``\n``: the end of a preprocessor directive).
_DECL_END = frozenset((";", ",", "[", "=", ")", ":"))
_DECL_START = frozenset(("(", ",", ";", "{", "}", "\n"))


class _Tokens:
    """*text*'s code tokens, and for each the bracket it sits in.

    ``toks`` is ``[(token, start, end)]`` read from the code mask, so the
    offsets are *text*'s; ``inner[i]`` is the index of the innermost open
    ``(`` / ``{`` / ``[`` at token *i* (-1: file scope) and ``close[k]`` the
    index of the bracket closing the one at *k*. A newline survives only
    where it ends a preprocessor directive -- and one inside a block comment
    does not, since the comment is a single space to the preprocessor:
    doppler's multi-line macros carry exactly such comments (gh-1669).
    """

    def __init__(self, text: str) -> None:
        from ._docsync import _MASK_RE, _code_mask

        self.mask = mask = _code_mask(text)
        comments = [
            m.span() for m in _MASK_RE.finditer(text) if m.group("block")
        ]
        toks: "list[tuple[str, int, int]]" = []
        opened: "list[int]" = []  # where each directive began, in toks
        directive, line_start = False, True
        for m in _TOKEN.finditer(mask):
            t = m.group(0)
            if t.startswith("\\"):
                continue
            if t == "\n":
                if any(a <= m.start() < b for a, b in comments):
                    continue
                if directive:
                    toks.append(("\n", m.start(), m.end()))
                directive, line_start = False, True
                continue
            if t == "#" and line_start:
                directive = True
                opened.append(len(toks))
            line_start = False
            toks.append((t, m.start(), m.end()))
        self.toks = toks
        self.inner = [-1] * len(toks)
        self.close: "dict[int, int]" = {}
        stack: "list[int]" = []
        begun = -1
        starts = set(opened)
        for i, (t, _a, _b) in enumerate(toks):
            self.inner[i] = stack[-1] if stack else -1
            if i in starts:
                begun = i
            if t in "({[":
                stack.append(i)
            elif t in ")}]" and stack:
                self.close[stack.pop()] = i
            elif t == "\n":
                # A directive's brackets end with it: an unbalanced
                # `#define BEGIN {` must not swallow the rest of the file.
                while stack and stack[-1] > begun:
                    stack.pop()

    def text(self, i: int) -> str:
        """Token *i*, or ``;`` past either end: a snippet (a manifest
        ``type``) is a whole statement."""
        return self.toks[i][0] if 0 <= i < len(self.toks) else ";"

    def is_ident(self, i: int) -> bool:
        return bool(_IDENT.match(self.text(i)))

    def brace_kind(self, o: int) -> str:
        """``record`` (a struct/union body, whose declarators are MEMBERS),
        ``linkage`` (``extern "C" {``, which is file scope) or ``block``."""
        if self.text(o - 1) in _TAG_KW or (
            self.is_ident(o - 1) and self.text(o - 2) in _TAG_KW
        ):
            return "record"
        if re.search(
            r'\bextern\s*"[^"\n]*"\s*$', self.mask[: self.toks[o][1]]
        ):
            return "linkage"
        return "block"

    def _in_body(self, o: int) -> bool:
        """Whether bracket *o* is inside a function body (a block brace)."""
        while o != -1:
            if self.text(o) == "{" and self.brace_kind(o) == "block":
                return True
            o = self.inner[o]
        return False

    def declarator(
        self, i: int
    ) -> "tuple[str, tuple[int, int] | None] | None":
        """``(kind, scope)`` when token *i* is the NAME a declaration declares
        -- a ``member``, a ``param``, or a ``variable`` (a local, or at file
        scope) -- else None. *scope* is the token range over which the name
        then means that variable: a variable's to the end of its block (or
        file), a parameter's its function's body, a member's or a
        prototype parameter's nowhere.

        A declarator is ``<specifiers> [*...] NAME`` followed by one of
        ``; , [ = ) :``, its specifiers starting a statement or a parameter;
        ``void (*NAME)(...)`` too. A ``typedef`` declares a TYPE (derived:
        a reference), a name after ``struct`` is a tag (a reference), and a
        parenthesised list inside a function body is a CALL's arguments
        (``f (n * FIR_BATCH)``), never a parameter list.
        """
        if self.text(i + 1) not in _DECL_END:
            return None
        j, ptr, start = i - 1, False, i
        while self.text(j) in ("*", "const", "volatile", "restrict"):
            ptr = ptr or self.text(j) == "*"
            j -= 1
        if self.text(j) == "(" and self.text(i + 1) == ")":
            # `void (*name)(int)`: a function-pointer declarator.
            after = self.close.get(j)
            if not ptr or after is None or self.text(after + 1) not in "([":
                return None
            start, j = j, j - 1
        run = []
        while j >= 0 and self.is_ident(j):
            run.append(self.text(j))
            j -= 1
        if not run or run[0] in _TAG_KW:
            return None
        if any(t in _NOT_SPECIFIER for t in run):
            return None
        if not ptr and all(t in _QUALS for t in run):
            return None
        if j >= 0 and self.text(j) not in _DECL_START:
            return None
        end = len(self.toks)
        o = self.inner[start]
        if o == -1:
            return "variable", (i + 1, end)
        if self.text(o) == "{":
            if self.brace_kind(o) == "record":
                return "member", None
            return "variable", (i + 1, self.close.get(o, end))
        if self.text(o) != "(":
            return None
        if self.text(o - 1) == "for":
            return "variable", (i + 1, self.close.get(self.inner[o], end))
        if self.text(o - 1) != ")" and not (
            self.is_ident(o - 1) and self.text(o - 1) not in _NOT_SPECIFIER
        ):
            return None
        if self._in_body(o):
            return None
        c = self.close.get(o)
        if c is not None and self.text(c + 1) == "{":
            return "param", (c + 1, self.close.get(c + 1, end))
        return "param", None


def references(text: str, names) -> "list[tuple[int, int, str]]":
    """``(start, end, name)`` for each occurrence in *text* (C) of a name in
    *names* that REFERS to it -- the one classifier `jm upgrade`'s respell
    and `apply`'s refusal share (gh-1668), so what one rewrites the other
    reports, and nothing else.

    Code only (gh-1382), whole identifier, case-sensitive. NOT a reference:

    - a member access or designated initializer (``.x``, ``->x``, ``.x =``);
    - a declarator (:meth:`_Tokens.declarator`) -- a struct/union member, a
      parameter, a local or other variable -- and every use of that
      variable within its scope.

    Everything else is: a call, ``&x``, a function-pointer use, a function's
    own declaration or definition, a type use, a ``typedef``, a
    preprocessor name.

    >>> t = '''struct lay { size_t frame_bits; };
    ... size_t frame_bits (const struct lay *l);
    ... int describe (const uint8_t *frame_bits, size_t n);
    ... size_t
    ... use (struct lay *l, size_t (*fp) (const struct lay *))
    ... {
    ...   fp = frame_bits;
    ...   struct lay k = { .frame_bits = 1 };
    ...   return l->frame_bits + frame_bits (&k) + k.frame_bits;
    ... }
    ... void g (void) { size_t frame_bits = 3; frame_bits++; }'''
    >>> [t.count("\\n", 0, a) + 1 for a, _b, _n in references(t, {"frame_bits"})]
    [2, 7, 9]
    """
    from ._docsync import _code_mask

    if not names or not old_names_pattern(names).search(_code_mask(text)):
        return []
    tk = _Tokens(text)
    shadow: "dict[str, list[tuple[int, int]]]" = {}
    refs = []
    for i, (name, _a, _b) in enumerate(tk.toks):
        if name not in names or tk.text(i - 1) in (".", "->"):
            continue
        decl = tk.declarator(i)
        if decl is None:
            refs.append(i)
        elif decl[1] is not None:
            shadow.setdefault(name, []).append(decl[1])
    return [
        (tk.toks[i][1], tk.toks[i][2], tk.text(i))
        for i in refs
        if not any(lo <= i <= hi for lo, hi in shadow.get(tk.text(i), ()))
    ]


# -- A stem passed to a macro that pastes it (gh-1669) ----------------------

#: ``{macro: {parameter position: pastes}}`` (:func:`paste_macros`).
Macros = "dict[str, dict[int, frozenset]]"


def _macro_defs(text: str):
    """``(name, params, body)`` for each function-like ``#define`` in *text*
    (C), the body as tokens (:class:`_Tokens`: comments, strings and line
    splices already gone)."""
    tk = _Tokens(text)
    t = tk.toks
    for i in range(len(t) - 3):
        if t[i][0] != "#" or t[i + 1][0] != "define" or not tk.is_ident(i + 2):
            continue
        # Function-like only when `(` touches the name.
        if t[i + 3][0] != "(" or t[i + 3][1] != t[i + 2][2]:
            continue
        c = tk.close.get(i + 3)
        if c is None:
            continue
        params = [p[0] for p in t[i + 4 : c] if p[0] not in (",", "...")]
        k = c + 1
        while k < len(t) and t[k][0] != "\n":
            k += 1
        yield t[i + 2][0], params, [p[0] for p in t[c + 1 : k]]


def _call_arg(body: "list[str]", k: int) -> "tuple[str, int] | None":
    """``(callee, position)`` when ``body[k]`` is a WHOLE argument of a call
    ``callee(...)`` in a macro body, else None."""
    if k == 0 or body[k - 1] not in ("(", ","):
        return None
    if body[k + 1 : k + 2] not in ([")"], [","]):
        return None
    depth, pos = 0, 0
    for j in range(k - 1, -1, -1):
        t = body[j]
        if t in ")]}":
            depth += 1
        elif t in "([{":
            if depth:
                depth -= 1
                continue
            if t == "(" and j and _IDENT.match(body[j - 1]):
                return body[j - 1], pos
            return None
        elif t == "," and not depth:
            pos += 1
    return None


def _body_uses(params: "list[str]", body: "list[str]"):
    """Per parameter position: its pastes -- ``(pre, post)`` around it in a
    ``##`` chain, None when another parameter shares the chain, ``("",
    "")`` for a plain use -- and the ``(callee, position)`` calls it is
    passed to whole, to be followed. ``#param`` (a string) is neither."""
    index = {p: k for k, p in enumerate(params)}
    direct: "dict[int, set]" = {k: set() for k in range(len(params))}
    passed: "dict[int, set]" = {k: set() for k in range(len(params))}
    k = 0
    while k < len(body):
        if body[k + 1 : k + 2] == ["##"]:
            chain, m = [body[k]], k + 1
            while body[m : m + 1] == ["##"] and m + 1 < len(body):
                chain.append(body[m + 1])
                m += 2
            for at, part in enumerate(chain):
                if part not in index:
                    continue
                rest = chain[:at] + chain[at + 1 :]
                if any(r in index or not re.match(r"\w+\Z", r) for r in rest):
                    direct[index[part]].add(None)
                else:
                    direct[index[part]].add(
                        ("".join(chain[:at]), "".join(chain[at + 1 :]))
                    )
            k = m
            continue
        if body[k] in index and not (k and body[k - 1] == "#"):
            call = _call_arg(body, k)
            if call is None:
                direct[index[body[k]]].add(("", ""))
            else:
                passed[index[body[k]]].add(call)
        k += 1
    return direct, passed


def paste_macros(texts) -> Macros:
    """``{macro: {parameter position: pastes}}`` for every function-like
    macro in *texts* (C) that token-pastes a parameter, followed through the
    macros it passes the parameter on to: `JM_DEFINE_STEPS` hands ``fn`` to
    `JM_DEFINE_STEPS_EX`, which pastes ``fn##_steps``.

    A paste is ``(pre, post)``: the argument ``x`` there yields the
    identifier ``pre + x + post``; ``("", "")`` is a plain use of the
    argument, and None a paste this cannot spell (another parameter in it).

    >>> paste_macros(['#define T(pfx, a) pfx##_state_bytes (a)'])
    {'T': {0: frozenset({('', '_state_bytes')})}}
    """
    direct: "dict[str, dict[int, set]]" = {}
    passed: "dict[str, dict[int, set]]" = {}
    for text in texts:
        for name, params, body in _macro_defs(text):
            d, p = _body_uses(params, body)
            for k in d:
                direct.setdefault(name, {}).setdefault(k, set()).update(d[k])
                passed.setdefault(name, {}).setdefault(k, set()).update(p[k])

    def follow(name: str, k: int, seen: frozenset) -> set:
        if (name, k) in seen:
            return set()
        if k not in direct.get(name, {}):
            # A function, or a macro no file here defines: a plain use.
            return {("", "")}
        out = set(direct[name][k])
        for callee, pos in passed[name][k]:
            out |= follow(callee, pos, seen | {(name, k)})
        return out

    table: "dict[str, dict[int, frozenset]]" = {}
    for name in direct:
        for k in direct[name]:
            forms = follow(name, k, frozenset())
            if forms - {("", "")}:
                table.setdefault(name, {})[k] = frozenset(forms)
    return table


_JM_MACROS: "dict[str, dict[int, frozenset]]" = {}


def jm_macros() -> Macros:
    """:func:`paste_macros` of jm's own ``jm_perf.h`` -- `JM_DEFINE_STEPS`
    and the macros it calls -- whether or not the tree at hand carries a
    copy: a manifest body calls the macro with no header beside it."""
    if not _JM_MACROS:
        from . import _render

        _JM_MACROS.update(paste_macros([_render.JM_PERF_H]))
    return _JM_MACROS


def project_macros(root: Path) -> Macros:
    """:func:`paste_macros` over every C/C++ file `jm upgrade` respells under
    *root* (the walk that finds doppler's ``native/tests/dp_state_test.h``),
    and jm's own (:func:`jm_macros`)."""
    from . import _upgrade

    files = _upgrade._project_files(
        root, lambda p: p.suffix in _upgrade._C_SUFFIXES
    )
    found = paste_macros(
        p.read_text(encoding="utf-8", errors="replace") for p in files
    )
    for name, positions in jm_macros().items():
        for k, forms in positions.items():
            mine = found.setdefault(name, {})
            mine[k] = mine.get(k, frozenset()) | forms
    return found


def pasted_stems(
    text: str,
    names: "dict[str, str]",
    stems: "dict[str, str]",
    macros: Macros,
):
    """Each stem *text* (C) passes to a macro in *macros* that pastes it into
    an old name in *names* (gh-1669), as ``(start, end, new, label)``.

    The argument is respelled -- ``viterbi`` to ``dp_viterbi`` -- only when
    EVERY identifier the macro makes of it is an old name whose new spelling
    is the same paste of the new stem. When it also makes one that is not
    (a plain use of the stem, an author name beside the derived ones), no
    spelling of the argument is right, and it is yielded with ``end`` None
    and a reason in place of ``new``: :func:`collisions` refuses it, naming
    the call. A call none of whose pastes is derived is the author's own,
    and is not yielded at all.

    >>> m = paste_macros(['#define T(p, a) p##_bytes (a) + p##_mine (a)'])
    >>> [(a, b, n) for a, b, n, _l in pasted_stems(
    ...     "T (fir, s);", {"fir_bytes": "p_fir_bytes"}, {"fir": "p_fir"}, m)]
    ... # doctest: +ELLIPSIS
    [(3, None, '`T(fir, ...)` passes the stem `fir` to a macro that pastes ...')]
    """
    if not macros or not stems:
        return
    from ._docsync import _code_mask

    call = re.compile(
        r"(?<![\w])(?:" + "|".join(map(re.escape, macros)) + r")\s*\("
    )
    if not call.search(_code_mask(text)):
        return
    tk = _Tokens(text)
    for i, (t, _a, _b) in enumerate(tk.toks):
        if t not in macros or tk.text(i + 1) != "(":
            continue
        if tk.text(i - 1) == "define":
            continue
        c = tk.close.get(i + 1)
        if c is None:
            continue
        args: "list[list[int]]" = [[]]
        for k in range(i + 2, c):
            if tk.text(k) == "," and tk.inner[k] == i + 1:
                args.append([])
            else:
                args[-1].append(k)
        for pos, forms in sorted(macros[t].items()):
            if pos >= len(args) or len(args[pos]) != 1:
                continue
            k = args[pos][0]
            arg = tk.text(k)
            if arg not in stems:
                continue
            made = {f: None if f is None else f[0] + arg + f[1] for f in forms}
            if not any(s in names for s in made.values()):
                continue
            label = f"{t}({'..., ' if pos else ''}{arg}, ...)"
            new = stems[arg]
            wrong = sorted(
                "a paste it cannot spell" if f is None else f"`{s}`"
                for f, s in made.items()
                if f is None or names.get(s) != f[0] + new + f[1]
            )
            a, b = tk.toks[k][1], tk.toks[k][2]
            if not wrong:
                yield a, b, new, label
                continue
            derived = sorted(f"`{s}`" for s in made.values() if s in names)
            yield (
                a,
                None,
                (
                    f"`{label}` passes the stem `{arg}` to a macro that pastes"
                    f" it into {', '.join(derived)} -- derived, which c_prefix"
                    f" renames -- and into {', '.join(wrong)}, which it does"
                    " not; no one spelling of the argument names both. Split"
                    " the macro, or spell those names out"
                ),
                label,
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


#: The step and lifecycle body keys (`_keys`' `*impl`), each of which has a
#: ``<key>_file = "path::fn"`` companion that lifts the body from a file.
IMPL_KEYS = ("impl", "create_impl", "reset_impl", "destroy_impl")

#: The manifest keys whose string value is a C EXPRESSION or statement jm
#: splices into generated code verbatim (gh-1666), each found at its render
#: site: a module function's `out_size` (`_render`, the binding's `_dim`); a
#: property's `expr` (`_context/_methods`, `_handle`, `_borrow`); a method's
#: `count_default` (`_context/_methods._count_default_parts`); an object's
#: `init_post_parse` (`_context/_state`); a handle `create_post`'s `arg`
#: and its `when` guard (`_handle`). Any of them can call a derived function -- doppler's
#: ``out_size = "kaiser_num_taps(...) | 1"`` -- or name a derived type.
#: `default` is not here: an enum param's default is a choice STRING, a bare
#: word the respell would read as an identifier.
C_EXPR_KEYS = (
    "out_size",
    "expr",
    "count_default",
    "init_post_parse",
    "arg",
    "when",
)

#: The manifest keys whose value is a free-form C TYPE -- never checked
#: against `_types`, so it can name a sibling's derived type (gh-1653's
#: `type`, gh-1666's siblings): a capsule property's `capsule_type`, an
#: init-param's `c_type`, a container property's `value_type`, a codec's
#: `entry_type` (default ``<stem>_<param>_t``), a composer generator's
#: `state_type` (default ``<stem>_state_t``) and source `struct`, a handle
#: module's `handle_type`, and a method's `record_dtype`.
C_TYPE_KEYS = (
    "type",
    "capsule_type",
    "c_type",
    "value_type",
    "entry_type",
    "state_type",
    "struct",
    "handle_type",
    "record_dtype",
)

#: The manifest keys whose string value is C that jm copies into the project's
#: C verbatim: the bodies (:data:`IMPL_KEYS`, gh-1653), the expressions
#: (:data:`C_EXPR_KEYS`, gh-1666) and the types (:data:`C_TYPE_KEYS`). THE
#: one set both `jm upgrade`'s respell (:func:`respell_manifest`) and
#: `apply`'s refusal (:func:`unrenamed_all`) read, so they cannot disagree.
#: Author-NAMED keys (`fn`, `create_fn`, `out_len_fn`, ...) are not here: jm
#: never prefixes what the author named.
MANIFEST_C_KEYS = IMPL_KEYS + C_EXPR_KEYS + C_TYPE_KEYS


def _toml_string(q: int) -> str:
    """A TOML string of any of its four forms, as a pattern whose quote is
    group *q* and content group *q* + 1 -- one spelling of "a string value"
    for every place that rewrites one in place (gh-1583, gh-1653)."""
    return rf"(\"\"\"|'''|\"|')((?:(?!\{q})[\s\S])*?)\{q}"


#: Any TOML string value, captured whole so a rewrite replaces it in place and
#: never re-serialises the file. `_upgrade._TOML_STRING` IS this (gh-1583's
#: header respell), so the two readers of "a manifest string" cannot drift.
TOML_STRING = re.compile(_toml_string(1))


def _manifest_c_value(keys: "tuple[str, ...]") -> "re.Pattern":
    """A manifest ``<key> = <string>`` for one of *keys*: group 3 is the
    opening quote, 4 the content -- :data:`TOML_STRING` behind a key."""
    return re.compile(
        r"(?<![\w-])(" + "|".join(keys) + r")(\s*=\s*)" + _toml_string(3)
    )


#: :func:`_manifest_c_value` over every C-bearing key.
MANIFEST_C_VALUE = _manifest_c_value(MANIFEST_C_KEYS)

#: One ``<key> = <string>`` pair of a ``replace`` table (gh-1656): the key a
#: TOML string (groups 1-2) or a bare key (group 3), the value a TOML string
#: (groups 4-5). BOTH sides are author C: `apply` substitutes each key's text
#: in the ``impl`` body with its value (`_impl.apply_replacements`).
_REPLACE_PAIR = (
    r"[ \t]*(?:"
    + _toml_string(1)
    + r"|([A-Za-z0-9_-]+))[ \t]*=[ \t]*"
    + _toml_string(4)
)
_REPLACE_INLINE = re.compile(r"(?<![\w.-])replace[ \t]*=[ \t]*\{")
_REPLACE_NEXT = re.compile(r"\s*,")
_REPLACE_HEADER = re.compile(
    r"^[ \t]*\[[ \t]*([^\[\]\n]*?)\.replace[ \t]*\][^\n]*$", re.M
)
#: A table header, ``[name]`` (group 2) or ``[[name]]`` (group 1 is ``[``).
_HEADER = re.compile(r"^[ \t]*\[(\[?)[ \t]*([^\[\]\n]*?)[ \t]*\]", re.M)


def _section_at(text: str, at: int) -> str:
    """The table *at* sits in: the last ``[name]`` header before it, or
    ``""`` at the top level or under an array-of-tables header."""
    name = ""
    for m in _HEADER.finditer(text, 0, at):
        name = "" if m.group(1) else m.group(2)
    return name


def replace_pairs(
    text: str,
) -> "list[tuple[str, tuple[int, int], tuple[int, int]]]":
    """``(table, key span, value span)`` for each pair of each ``replace``
    table in *text* (a manifest or fragment), each span the CONTENT of its
    string -- the inline ``replace = { "<old>" = "<new>" }`` form and a
    ``[<table>.replace]`` table alike (gh-1656). *table* is the one the
    ``replace`` belongs to (``lo`` for both forms).

    A value is C spliced into the ``impl`` body, so it names derived symbols
    as the body does; a key is text matched AGAINST that body. Neither sits
    behind a key :data:`MANIFEST_C_VALUE` knows.

    >>> t = '[lo]\\nreplace = { "G(s)" = "lo_get(s)", N = "n" }\\n'
    >>> [(s, t[slice(*k)], t[slice(*v)]) for s, k, v in replace_pairs(t)]
    [('lo', 'G(s)', 'lo_get(s)'), ('lo', 'N', 'n')]
    >>> t = '[lo.replace]\\n"G(s)" = "lo_get(s)"\\n[lo.x]\\ny = "z"\\n'
    >>> [(s, t[slice(*k)], t[slice(*v)]) for s, k, v in replace_pairs(t)]
    [('lo', 'G(s)', 'lo_get(s)')]
    """
    pair = re.compile(_REPLACE_PAIR)
    out = []

    def one(table: str, m: "re.Match") -> None:
        out.append((table, m.span(2 if m.group(1) else 3), m.span(5)))

    for head in _REPLACE_INLINE.finditer(text):
        table = _section_at(text, head.start())
        at = head.end()
        while (m := pair.match(text, at)) is not None:
            one(table, m)
            sep = _REPLACE_NEXT.match(text, m.end())
            if sep is None:
                break
            at = sep.end()
    line = re.compile(r"^" + _REPLACE_PAIR, re.M)
    for head in _REPLACE_HEADER.finditer(text):
        end = _HEADER.search(text, head.end())
        stop = end.start() if end else len(text)
        for m in line.finditer(text, head.end(), stop):
            one(head.group(1), m)
    return out


#: An ``impl_file = "path::..."`` value, group 2 the path: the body a
#: component's ``replace`` keys are matched against, when it is a file.
_IMPL_FILE_PATH = re.compile(r"(?<![\w-])impl_file\s*=\s*([\"'])([^\"'\n]*)::")


def unfollowed_bodies(
    text: str, root: Path, followed: "set[Path]"
) -> "set[str]":
    """The tables in *text* whose ``impl`` body is lifted from a file the
    upgrade does NOT respell (not in *followed*, :func:`walked`): their
    ``replace`` keys are matched against the old spelling still, so they
    keep it.

    >>> import tempfile
    >>> d = Path(tempfile.mkdtemp())
    >>> t = '[lo]\\nimpl_file = "../v/old.c::lo_k"\\n[hi]\\nimpl = "x;"\\n'
    >>> unfollowed_bodies(t, d, set())
    {'lo'}
    """
    return {
        _section_at(text, m.start())
        for m in _IMPL_FILE_PATH.finditer(text)
        if (root / m.group(2)).resolve() not in followed
    }


def manifest_c_spans(
    text: str,
    keys: "tuple[str, ...]" = MANIFEST_C_KEYS,
    frozen: "set[str] | frozenset[str]" = frozenset(),
) -> "list[tuple[int, int]]":
    """``(start, end)`` of every C-bearing string in *text* (a manifest or
    fragment), ascending: each *keys* value (:func:`_manifest_c_value`), and
    every ``replace`` value and key (:func:`replace_pairs`) -- except the
    keys of a table in *frozen* (:func:`unfollowed_bodies`), whose body did
    not move. THE reading both the respell (:func:`respell_manifest_c`) and
    the refusal (:func:`unrenamed_all`) take, so what one rewrites the other
    reports.

    >>> t = '[lo]\\nimpl = "a;"\\nreplace = { "b" = "c" }\\n'
    >>> [t[a:b] for a, b in manifest_c_spans(t)]
    ['a;', 'b', 'c']
    >>> [t[a:b] for a, b in manifest_c_spans(t, frozen={"lo"})]
    ['a;', 'c']
    """
    found = {m.span(4) for m in _manifest_c_value(keys).finditer(text)}
    for table, key, value in replace_pairs(text):
        found.add(value)
        if table not in frozen:
            found.add(key)
    return sorted(found)


def respell_manifest_c(
    text: str,
    respell,
    keys: "tuple[str, ...]" = MANIFEST_C_KEYS,
    frozen: "set[str] | frozenset[str]" = frozenset(),
) -> str:
    """*text* (a manifest or fragment) with *respell* (C text to C text)
    applied to each *keys* value, and to each ``replace`` key and value
    (gh-1656) -- :func:`manifest_c_spans` -- in place: the file is never
    re-serialised, and nothing outside those strings moves.

    THE one visit of the manifest's C-bearing values, for every respell `jm
    upgrade` makes to C (gh-1647): jm renders a header body FROM these
    strings, so a respell that rewrote the header and not its source is
    undone by the next `apply` -- the two never converge. The `c_prefix`
    respell passes :func:`respell_c` over every C-bearing key; the complex
    respell (gh-1248) passes its own over the bodies (:data:`IMPL_KEYS`),
    the only values that reach C verbatim -- a ``type`` is validated to the
    ``_Complex`` spelling before anything renders, and `apply` refuses the
    old one. ``tests/test_gh1647_one_manifest_walker.py`` requires every C
    respell in `_upgrade` to come through here.

    A ``replace`` table is visited by every respell, whatever *keys*: its
    values are spliced into the ``impl`` body and its keys matched against
    it, so they move when the body does -- a key not at all when the body
    is lifted from a file this upgrade leaves alone (*frozen*).

    >>> print(respell_manifest_c('impl = "x;"\\ncreate_fn = "x"\\n',
    ...                          lambda c: c.replace("x", "y")), end="")
    impl = "y;"
    create_fn = "x"
    """
    for a, b in reversed(manifest_c_spans(text, keys, frozen)):
        text = text[:a] + respell(text[a:b]) + text[b:]
    return text


#: A ``<impl>_file = "path::fn"`` value: group 3 the path, 4 the function.
#: The FILE is C the upgrade's walk respells, so when it is in that walk its
#: ``fn`` moves with it -- a derived name renamed in the file and not here
#: is a body `apply` can no longer find. A path outside the walk, or a
#: ``path::N:M`` line range, names nothing the upgrade moved.
IMPL_FILE_VALUE = re.compile(
    r"(?<![\w-])((?:"
    + "|".join(IMPL_KEYS)
    + r")_file\s*=\s*)(\"|')([^\"'\n]*)::([A-Za-z_]\w*)\2"
)


def walked(root: Path) -> "set[Path]":
    """Every C/C++ file `jm upgrade` respells under *root*, resolved -- what an
    ``*_impl_file`` path must be for its function name to follow."""
    from . import _upgrade

    files = _upgrade._project_files(
        root, lambda p: p.suffix in _upgrade._C_SUFFIXES
    )
    return {p.resolve() for p in files}


def _impl_file_fns(text: str, root: Path, followed: "set[Path]"):
    """``(match, fn)`` for each ``*_impl_file`` value in *text* whose file is
    in *followed* -- one reading for the respell and for the refusal."""
    for m in IMPL_FILE_VALUE.finditer(text):
        if (root / m.group(3)).resolve() in followed:
            yield m, m.group(4)


def macro_stems(cfg: dict) -> "dict[str, str]":
    """``{bare name: stem}`` for every source name the prefix changes -- what
    a `JM_DEFINE_STEPS` first argument respells from and to.

    >>> macro_stems({"project": {"name": "p", "c_prefix": "p"}, "fir": {}})
    {'fir': 'p_fir'}
    """
    return {n: s for n, s in sources(cfg).items() if n != s}


def with_macro_names(names: "dict[str, str]") -> "dict[str, str]":
    """*names* plus ``<stem>_step_batch`` for each ``<stem>_step`` in it.

    jm never declares ``step_batch`` -- the author writes it, and
    `JM_DEFINE_STEPS` pastes the name -- so no render carries it, yet it
    must move with ``<stem>_step`` (gh-1653).

    >>> with_macro_names({"fir_step": "p_fir_step"})["fir_step_batch"]
    'p_fir_step_batch'
    """
    out = dict(names)
    for old, new in names.items():
        if old.endswith("_step"):
            out.setdefault(old + "_batch", new + "_batch")
    return out


def _hits(
    text: str,
    names: "dict[str, str]",
    stems: "dict[str, str]",
    macros: "Macros | None",
) -> "dict[int, tuple[int, str, str]]":
    """``{start: (end, new spelling, label)}`` for each identifier in *text*
    (C) that refers to an old name: :func:`references` of *names*, and
    :func:`pasted_stems` of *stems* through *macros* (None: jm's own). The
    one answer :func:`respell_c` rewrites and :func:`_old_in` reports."""
    out: "dict[int, tuple[int, str, str]]" = {}
    table = jm_macros() if macros is None else macros
    for a, b, new, label in pasted_stems(text, names, stems, table):
        if b is not None:
            out[a] = (b, new, label)
    for a, b, name in references(text, names):
        out.setdefault(a, (b, names[name], name))
    return out


def respell_c(
    text: str,
    names: "dict[str, str]",
    stems: "dict[str, str]",
    macros: "Macros | None" = None,
) -> str:
    """*text* (C) with every REFERENCE (:func:`references`) to an old name in
    *names*, and every stem in *stems* passed to a macro in *macros* that
    pastes it (:func:`pasted_stems`; None: jm's own `JM_DEFINE_STEPS`),
    respelled -- in CODE only (gh-1382), whole identifiers, case-sensitive.
    The one respell `jm upgrade` applies to a C file and to a manifest's
    C-bearing value alike.

    >>> print(respell_c(
    ...     "/* fir_create */ JM_DEFINE_STEPS (fir, fir_state_t, float)",
    ...     {"fir_state_t": "p_fir_state_t", "fir_step": "p_fir_step",
    ...      "fir_steps": "p_fir_steps",
    ...      "fir_step_batch": "p_fir_step_batch"}, {"fir": "p_fir"}))
    /* fir_create */ JM_DEFINE_STEPS (p_fir, p_fir_state_t, float)
    """
    if not names and not stems:
        return text
    for a, (b, new, _label) in sorted(
        _hits(text, names, stems, macros).items(), reverse=True
    ):
        text = text[:a] + new + text[b:]
    return text


def respell_manifest(
    text: str,
    names: "dict[str, str]",
    stems: "dict[str, str]",
    root: "Path | None" = None,
    followed: "set[Path]" = frozenset(),
    macros: "Macros | None" = None,
) -> str:
    """*text* (a manifest or fragment) with each :data:`MANIFEST_C_KEYS`
    value and each ``replace`` value and key (:func:`manifest_c_spans`, a
    key only where its body moved: :func:`unfollowed_bodies`) respelled by
    :func:`respell_c`, and each ``*_impl_file``'s ``fn``
    whose file (resolved against *root*) is in *followed*, in place;
    nothing else moves.

    >>> t = 'create_fn = "fir_open"\\ntype = "fir_state_t *"\\n'
    >>> print(respell_manifest(t, {"fir_state_t": "p_fir_state_t"}, {}), end="")
    create_fn = "fir_open"
    type = "p_fir_state_t *"
    """

    frozen = (
        frozenset()
        if root is None
        else unfollowed_bodies(text, root, followed)
    )
    text = respell_manifest_c(
        text,
        lambda c: respell_c(c, names, stems, macros),
        frozen=frozen,
    )
    if root is None:
        return text
    hits = {m.start(): fn for m, fn in _impl_file_fns(text, root, followed)}

    def lifted(m: "re.Match") -> str:
        fn = hits.get(m.start())
        if fn not in names:
            return m.group(0)
        head = m.group(1) + m.group(2) + m.group(3) + "::"
        return head + names[fn] + m.group(2)

    return IMPL_FILE_VALUE.sub(lifted, text)


def _manifest_files(root: Path) -> "list[Path]":
    """The manifest and the fragments it includes -- gh-1583's list."""
    from . import _upgrade

    main = root / "just-makeit.toml"
    return ([main] if main.is_file() else []) + _upgrade._manifest_fragments(
        root
    )


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
    return _unrenamed(root, names, {})


def _old_in(
    text: str,
    names: "dict[str, str]",
    stems: "dict[str, str]",
    macros: "Macros | None" = None,
) -> "list[str]":
    """The old names *text* (C) still spells in code -- what :func:`respell_c`
    would change, read from the same :func:`_hits`. One question, asked the
    way the respell answers it."""
    return sorted(
        {label for _b, _n, label in _hits(text, names, stems, macros).values()}
    )


def unrenamed_all(root: Path, cfg: dict, tree: Path) -> "dict[str, list[str]]":
    """:func:`unrenamed` over everything `jm upgrade` respells (gh-1653): the
    author's C -- including a stem passed to a pasting macro, jm's
    `JM_DEFINE_STEPS` or the project's own (gh-1669), and
    ``<stem>_step_batch`` -- AND the manifest's C-bearing values
    (:data:`MANIFEST_C_KEYS`), so `apply` refuses exactly what the upgrade
    it names would fix."""
    return _unrenamed(
        root, with_macro_names(renames(tree, cfg)), macro_stems(cfg), cfg
    )


def _unrenamed(root, names, stems, cfg=None) -> "dict[str, list[str]]":
    if not names and not stems:
        return {}
    macros = project_macros(root) if stems else {}
    out = {}
    for p in _author_files(root):
        found = _old_in(
            p.read_text(encoding="utf-8", errors="replace"),
            names,
            stems,
            macros,
        )
        if found:
            out[p.relative_to(root).as_posix()] = found
    if cfg is not None:
        followed = walked(root)
        for p in _manifest_files(root):
            text = p.read_text(encoding="utf-8")
            frozen = unfollowed_bodies(text, root, followed)
            found = sorted(
                {
                    n
                    for a, b in manifest_c_spans(text, frozen=frozen)
                    for n in _old_in(text[a:b], names, stems, macros)
                }
                | {
                    fn
                    for _m, fn in _impl_file_fns(text, root, followed)
                    if fn in names
                }
            )
            if found:
                out[p.relative_to(root).as_posix()] = found
    return out


def slots(owner: INC.Owner, name: str) -> "dict[str, str]":
    """The template slots for a render of *name*'s files in *owner*'s project.

    >>> slots({"project": {"name": "p"}}, "fir")
    {'csym': 'fir', 'CSYM': 'FIR'}
    """
    return {"csym": stem(owner, name), "CSYM": upper(owner, name)}
