"""_enumc.py — one emitter for "validate a choice string to its enum int".

gh-1026. The issue counted **four** places re-spelling the same C. Scanning
for the shape rather than trusting the list found **ten**, which is the usual
result and the reason the gate scans:

| site                                    | symbols                       |
| --------------------------------------- | ----------------------------- |
| module-function param                   | bare                          |
| handle create-arg                       | bare                          |
| composer serializer param               | bare                          |
| composer source `tp_init` field         | bare                          |
| composer source property setter         | bare                          |
| composer segment property setter        | bare                          |
| composer decoded-getter / from-dict     | bare                          |
| object property setter                  | ``_enum_index_<Component>``   |
| object method param                     | ``_enum_index_<Component>``   |
| (+ the shared lookup body, already one) | —                             |

The lookup *body* was already shared — three of them imported one constant —
but the **tables** and the **call sites** were not, which is the half that
actually drifted. gh-1021 gave method parameters the property path's message:

    ValueError: invalid kind 'nope' (choices: none, rs, conv)

while the module-function path — the same feature on a different surface —
still said only ``invalid sample_type 'nope'``. One manifest, two wordings of
one refusal, decided by whether the enum was declared on a function or on a
method. That is a user-visible inconsistency created by the duplication, not
by anyone's decision.

So this file owns three things and every face calls it:

* :data:`INDEX_FN` — the lookup, and the "order is the C int" contract;
* :func:`symbols` — how the pair is named in a given namespace;
* :func:`render_tables` / :func:`validate_c` — the tables and the call site.

**On the namespace.** A module's ``_ext.c`` ``#include``s every object's
fragment into one translation unit, and a view (gh-504) adds another type over
the same component, so two types there may reference the same ``[[enum]]``.
Module-level ``function`` enums (gh-353) already own the bare ``_enum_index`` /
``_enum_<name>`` spellings. Passing an empty *prefix* keeps those bare names;
passing the type name namespaces them. One parameter, both conventions, and
no third one can appear by accident.

**The fifth spelling is deliberately not here.** An ``init_param``'s
``type = "enum:<name>"`` flattens through ``C.resolve_enum_type`` to
``string_enum:a,b,c`` and emits an inline ``strcmp`` if/else chain in
``_context/_state`` — no table, no lookup, and by then the enum's *name* is
gone, so it cannot name the choices even if it wanted to. Folding it in means
either teaching ``init_params`` the ``enum`` key or unflattening a type string
after the fact; both are a feature, not a de-duplication, and doing one of them
inside this change would hide it. It is filed separately.
"""

from __future__ import annotations

#: The shared string-enum → index lookup, as C.
#:
#: Order **is** the C int — the ``[[enum]]`` SSOT contract, which is why that
#: list is append-only. Every face indexes the same tables the same way, so a
#: value's integer meaning cannot differ between a property, a method
#: parameter, a module function and a handle constructor.
_INDEX_FN_TEMPLATE = "\n".join(
    [
        "/* String-enum tables — order is the C int (the [[enum]] SSOT). */",
        "static int",
        "{fn}(const char *const *tab, const char *s)",
        "{{",
        "    for (int i = 0; tab[i]; i++)",
        "        if (strcmp(tab[i], s) == 0)",
        "            return i;",
        "    return -1;",
        "}}",
        "",
    ]
)

#: The un-namespaced lookup, for the three faces that own the bare symbol.
INDEX_FN = _INDEX_FN_TEMPLATE.format(fn="_enum_index")


def symbols(prefix: str, name: str) -> tuple[str, str]:
    """``(index_fn, table)`` for one enum in the *prefix* namespace.

    An empty *prefix* is the bare convention that module functions, handles
    and composers all use; a type name is the object-scoped one gh-519
    introduced for properties and gh-1021 extended to method parameters.

    Examples
    --------
    >>> symbols("", "kind")
    ('_enum_index', '_enum_kind')
    >>> symbols("Acq", "kind")
    ('_enum_index_Acq', '_enum_Acq_kind')
    """
    if not prefix:
        return ("_enum_index", f"_enum_{name}")
    return (f"_enum_index_{prefix}", f"_enum_{prefix}_{name}")


def render_tables(
    used: list[str],
    enums: dict[str, list[str]],
    *,
    prefix: str = "",
    include_string_h: bool = False,
) -> str:
    """The lookup plus one ``static const char *const`` table per enum.

    Parameters
    ----------
    used : list of str
        Enum names, in first-reference order. Each face decides its own set —
        a handle scans its create-args and getters, a composer its source and
        segment tables, a module its functions — and only the *set* differs,
        which is why that is the parameter rather than the manifest.
    enums : dict
        The ``[[enum]]`` registry: name → ordered choices.
    prefix : str, optional
        Symbol namespace; see :func:`symbols`.
    include_string_h : bool, optional
        Emit an explicit ``#include <string.h>`` above the block. True for the
        object-scoped fragment, which is spliced into a file that may not have
        pulled it in; ``Python.h`` already does, but the block then stands on
        its own wherever it lands.

    Returns
    -------
    str
        C source. An empty *used* still emits the lookup, matching what every
        face did before this was shared — a caller that wants nothing emitted
        for an enum-free component checks that before calling.
    """
    index_fn, _ = symbols(prefix, "")
    parts: list[str] = []
    if include_string_h:
        parts += [
            "/* gh-519: strcmp for the enum lookup below. Python.h already",
            " * pulls in <string.h>, but the include is explicit so the block",
            " * stands on its own wherever it is spliced. */",
            "#include <string.h>",
            "",
        ]
    parts.append(_INDEX_FN_TEMPLATE.format(fn=index_fn))
    for name in used:
        _, table = symbols(prefix, name)
        items = "".join(f'    "{v}",\n' for v in enums.get(name, []))
        parts.append(f"static const char *const {table}[] = {{")
        parts.append(items + "    NULL,")
        parts.append("};")
        parts.append("")
    return "\n".join(parts)


def choices_suffix(ename: str, enums: "dict[str, list[str]] | None") -> str:
    """`` (choices: a, b, c)`` for the refusal message, or ``""``.

    gh-1026 ask 1, and the reason this function exists rather than a literal at
    each site: three of the four faces named no choices, so the same manifest
    produced two wordings of one refusal depending on which surface the enum
    was declared on.

    Empty when the registry is absent — the ``jm bind`` reflection path has no
    manifest to read ``[[enum]]`` from, and a suffix reading ``(choices: )``
    would be worse than none.

    Examples
    --------
    >>> choices_suffix("kind", {"kind": ["none", "rs"]})
    ' (choices: none, rs)'
    >>> choices_suffix("kind", None)
    ''
    >>> choices_suffix("kind", {"kind": []})
    ''
    """
    joined = ", ".join((enums or {}).get(ename, []))
    return f" (choices: {joined})" if joined else ""


def validate_c(
    pname: str,
    ename: str,
    enums: "dict[str, list[str]] | None",
    *,
    prefix: str = "",
    src: str = "",
    result: str = "",
    fail: str = "return NULL;",
    cleanup: str = "",
    indent: str = "    ",
) -> str:
    """The call site: look the choice up, raise naming the choices, or pass on.

    One emitter for what were four near-identical blocks, so the refusal a
    caller meets does not depend on which surface the enum was declared on.

    Parameters
    ----------
    pname : str
        The name in the message — the *declared* name, which the caller sees.
    src : str, optional
        The C expression holding the choice string, when it is not *pname*.
        A property setter has already pulled it out as ``s``/``_s``, and the
        message must still name the field rather than the local: the two are
        separate questions and were separate literals at every site.
    ename : str
        The ``[[enum]]`` name, used to pick the table and the choices.
    enums : dict or None
        The registry. ``None`` drops the choices suffix — see
        :func:`choices_suffix`.
    prefix : str, optional
        Symbol namespace; see :func:`symbols`.
    result : str, optional
        Name of the ``int`` local to declare. Defaults to ``_arg_<pname>``,
        which three faces already used; the composer's serializer spells it
        ``_e_<pname>`` and passes that.
    fail : str, optional
        The statement that leaves the wrapper. ``return NULL;`` in a
        ``PyCFunction``; ``return -1;`` in an ``initproc``, where a hard-coded
        ``NULL`` compiles and reports *success*.
    cleanup : str, optional
        Statements to run before *fail* — releasing arrays or path objects
        acquired earlier in the same parse block. Appended on the message line,
        as each face already did.
    indent : str, optional
        Leading whitespace for the block.

    Returns
    -------
    str
        C source, with no trailing newline.
    """
    index_fn, table = symbols(prefix, ename)
    var = result or f"_arg_{pname}"
    expr = src or pname
    suffix = choices_suffix(ename, enums)
    return (
        f"{indent}int {var} = {index_fn}({table}, {expr});\n"
        f"{indent}if ({var} < 0) {{\n"
        f"{indent}    PyErr_Format(PyExc_ValueError,\n"
        f"{indent}        \"invalid {pname} '%s'{suffix}\","
        f" {expr});{cleanup}\n"
        f"{indent}    {fail}\n"
        f"{indent}}}"
    )
