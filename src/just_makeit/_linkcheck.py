"""Every C function the binding calls must resolve at LINK time (gh-1361).

A shared object links with undefined symbols. So a function the generated
binding calls and nothing defines builds cleanly and fails only at import::

    ImportError: .../o.cpython-312-...so: undefined symbol: o_get_level

That is how gh-1303 hid: `jm property` declared accessors with no body,
``make`` exited 0, and only ``make test`` failed -- at import. Reading source
text cannot close the class either: a definition may come from ``_core.c``, a
sibling source, a header ``static inline``, or a macro a text scan cannot see
into (gh-1310).

So jm generates ``native/tests/test_<comp>_symbols.c``: a table taking the
address of every such function, compiled INTO the component's C test
executable. An executable resolves every reference it links, so a missing
definition fails ``make test`` at link time with the symbol's name, whatever
was supposed to produce it.

Three details, each load-bearing:

- **Function pointers, not ``void *``.** Converting a function pointer to an
  object pointer is not defined by ISO C and ``-Wpedantic`` warns; converting
  between function-pointer types is (C99 6.3.2.3p8), and the table is never
  called through.
- **External linkage plus ``used``.** Nothing else in its translation unit
  refers to the table, so a ``static`` one could be discarded before the
  linker looks -- the gate would then be only as strong as the optimiser
  allows.
- **A separate, jm-owned file.** The test ``test_<comp>_core.c`` is the
  author's (create-only); the table is added to the test executable's sources
  in the generated build files instead, so ``apply`` never edits a file the
  author owns and an existing project gets it with nothing to migrate.

The list is derived, never kept: every function the binding calls AND the
component's own header declares without defining (a body in the header is a
definition by construction, and a C99 ``inline`` one has no address to take
from another translation unit). The header half is what keeps
Python-API calls, and the ``PyObject *`` accessors that live in
``_ext_extra.c`` (the extension, not the core), out of a C executable that
could not link them.
"""

from __future__ import annotations

from . import _textio

import re
from pathlib import Path

from . import _config as C

#: ``name(`` -- a call or a declarator.
_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")


def symbols_file(root: Path, comp: str) -> Path:
    """Where *comp*'s table lives."""
    return root / "native" / "tests" / f"test_{comp}_symbols.c"


def _header_functions(header: str) -> "list[str]":
    """Names *header* puts before a ``(`` at file scope, in order.

    Every declaration and every definition, however it wraps -- jm renders
    ``steps()`` across several lines, which a one-line prototype matcher
    misses. Comments and strings are masked, function bodies blanked
    (gh-1362's rule), and preprocessor lines skipped, so nothing inside a
    body, a comment or a ``#define`` is read as a declaration. Anything else
    that matches (a macro invocation, say) is harmless: only names the
    binding also calls reach the table.
    """
    from ._docsync import _code_mask
    from ._init import _function_bodies_blanked

    text = _function_bodies_blanked(_code_mask(header))
    names: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for n in _CALL_RE.findall(line):
            if n not in names:
                names.append(n)
    return names


#: Words that take a parenthesis without being a function -- C keywords and
#: compiler built-ins. Measured on doppler: a header's ``sizeof(...)`` at file
#: scope and a binding's ``sizeof(...)`` put ``(jm_any_fn)sizeof`` in a table,
#: which does not compile.
_NOT_FUNCTIONS = frozenset(
    {
        "sizeof",
        "_Alignof",
        "alignof",
        "_Static_assert",
        "static_assert",
        "_Generic",
        "_Atomic",
        "typeof",
        "__typeof__",
        "__attribute__",
        "__declspec",
        "__has_attribute",
        "__builtin_expect",
        "defined",
        "return",
        "if",
        "while",
        "for",
        "switch",
        "do",
        "else",
        "case",
    }
)

#: ``#define NAME(`` -- a function-like macro, which has no address either.
_MACRO_RE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)\(", re.M)

#: ``name(...) {`` -- a definition, however its return type is laid out.
_DEFN_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{")


def _header_definitions(header: str) -> "set[str]":
    """Names *header* itself gives a body to.

    Excluded from the table, and correctly: a function defined in the header
    is defined by construction -- if the body were missing the header would
    not compile. Taking its address from another translation unit is also
    exactly what C99 does NOT promise for a plain ``inline`` definition,
    which emits no external symbol (measured: perf mode's ``JM_FORCEINLINE``
    ``step()`` linked the binding, where every call is inlined, and failed the
    table with `undefined reference to 'c_step'`).
    """
    from ._docsync import _code_mask

    return set(_DEFN_RE.findall(_code_mask(header)))


def bound_symbols(binding: str, header: str) -> "list[str]":
    """Functions *binding* calls that *header* declares and does not define.

    Examples
    --------
    >>> bound_symbols(
    ...     "static PyObject *W_get(W *s) {"
    ...     " return PyFloat_FromDouble(o_get_level(s->h)); }",
    ...     "double o_get_level(const o_state_t *state);\\n"
    ...     "void o_unused(o_state_t *state);\\n",
    ... )
    ['o_get_level']
    """
    from ._docsync import _code_mask

    called = set(_CALL_RE.findall(_code_mask(binding)))
    excluded = (
        _header_definitions(header)
        | set(_MACRO_RE.findall(header))
        | _NOT_FUNCTIONS
    )
    return [
        n
        for n in _header_functions(header)
        if n in called and n not in excluded
    ]


def binding_sources(root: Path, cfg: dict, comp: str) -> "list[Path]":
    """The generated binding file(s) that call into *comp*'s core."""
    mod = C.module_of(cfg, comp)
    if mod:
        cname = C.module_paths(mod).cname
        d = root / "native" / "src" / cname
        # gh-504: a view's fragment calls the parent's core too.
        frags = [comp] + [v["class_name"].lower() for v in C.views(cfg, comp)]
        return [d / f"{cname}_ext_{f}.c" for f in frags]
    return [root / "native" / "src" / comp / f"{comp}_ext.c"]


def render_symbols_c(comp: str, names: "list[str]") -> str:
    """The table for *comp*, as C."""
    rows = "".join(f"    (jm_any_fn){n},\n" for n in names) or "    0,\n"
    return (
        f"/* test_{comp}_symbols.c -- generated by just-makeit (gh-1361).\n"
        " *\n"
        " * The address of every function the Python binding calls. This\n"
        " * file is linked into the C test, so a function that is declared\n"
        " * and never defined fails the build here, naming it -- rather than\n"
        " * at import, where a shared object's undefined symbol surfaces.\n"
        " * Regenerated by `jm apply`; do not edit.\n"
        " */\n"
        f'#include "{comp}/{comp}_core.h"\n'
        "\n"
        "typedef void (*jm_any_fn)(void);\n"
        "\n"
        f"extern const jm_any_fn jm_bound_symbols_{comp}[];\n"
        "/* `used` keeps the compiler from dropping the table; `retain` keeps\n"
        " * the linker's --gc-sections from dropping it (measured: without it\n"
        " * a -Wl,--gc-sections build links a missing definition cleanly). On\n"
        " * Mach-O, `used` alone already marks it no-dead-strip. */\n"
        "#if defined(__has_attribute)\n"
        "#if __has_attribute(retain)\n"
        "__attribute__((used, retain))\n"
        "#elif __has_attribute(used)\n"
        "__attribute__((used))\n"
        "#endif\n"
        "#endif\n"
        f"const jm_any_fn jm_bound_symbols_{comp}[] = {{\n"
        f"{rows}"
        "};\n"
    )


def write(
    root: Path, cfg: dict, comp: str, binding_root: "Path | None" = None
) -> bool:
    """Write *comp*'s table from the files on disk; True when it changed.

    Computed from the tree it writes into -- the binding as it stands and the
    header as it stands -- so it describes what actually compiles.

    *binding_root* reads the binding from another tree. `status` needs it: its
    scratch copy deletes the module fragments `apply` does not rewrite
    (gh-767), so a fresh render stands in for them there, and a table derived
    from that stand-in would disagree with the real one about a hand-kept call
    the real fragment still makes.
    """
    header = root / "native" / "inc" / comp / f"{comp}_core.h"
    srcs = [
        p
        for p in binding_sources(binding_root or root, cfg, comp)
        if p.exists()
    ]
    # Always written, even before a binding exists: the build files name it,
    # and a source they name but cannot find does not configure.
    names = (
        bound_symbols(
            "".join(p.read_text(encoding="utf-8") for p in srcs),
            header.read_text(encoding="utf-8"),
        )
        if header.exists() and srcs
        else []
    )
    text = render_symbols_c(comp, names)
    out = symbols_file(root, comp)
    if out.exists() and out.read_text(encoding="utf-8") == text:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    _textio.write_text(out, text)
    return True


def refresh(root: Path, comp: str) -> bool:
    """:func:`write` with the manifest as saved on disk."""
    return write(root, C.load(root), comp)


def after(command):
    """Refresh the table once *command* (``run(root, object_name, ...)``) ends.

    For the verbs that inject a declaration into the header AFTER regenerating
    the binding -- ``jm method`` and ``jm property``. The binding writers'
    own hook runs before that injection, so on its own it would read a header
    that does not declare the new member yet and leave the table one member
    behind until the next `apply` (measured: `status --check` then failed on
    a project the verb had just written).
    """
    import functools

    @functools.wraps(command)
    def run(root, object_name, *args, **kwargs):
        result = command(root, object_name, *args, **kwargs)
        refresh(Path(root), object_name)
        return result

    return run
