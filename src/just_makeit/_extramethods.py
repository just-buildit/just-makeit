"""A hand-written method the manifest registers: its row, prototype and stub.

gh-1190 gave a ``kind = "composer"`` module ``[[module.X.extra_methods]]``: the
C function is the author's, written in an ``*_extra.c`` jm includes and never
touches, and the manifest row is what gives it a ``PyMethodDef`` entry and a
``.pyi`` member. gh-1997 is the same gap on an ordinary object: a module
object's ``<mod>_ext_<obj>_extra.c`` could hold the function, but once the
fragment is jm's (``fragment = "generated"``) its method table is rendered
whole from the manifest, so the row registering the function had nowhere to
survive -- and ``jm adopt`` rightly refused the flip.

The object key is the composer's, row for row, so this module is the ONE
emitter both read. A second copy for objects is how the two would come to
disagree about a flag's calling convention or a doc's escaping; the composer
renders through here byte-for-byte as it did before it was lifted.

What jm owns and what the author owns
-------------------------------------
- jm: the ``PyMethodDef`` row, a forward prototype above every table that
  names the function (the hand-written file is included AFTER the generated
  types, so it can use them), the ``#include`` of that file, and the stub.
- The author: the function, with exactly the signature its ``flags`` imply
  (:func:`params`). ``self`` is ``PyObject *``, the type CPython calls every
  method with; cast it to the object's struct inside the body.

The include is emitted whenever a row is declared, whether or not the file
exists yet (gh-1516): the row's ``fn`` is defined nowhere else, so without
the file the build fails either way, and ``No such file`` names the fix where
an undefined function named only the symbol.
"""

from __future__ import annotations

import re
from pathlib import Path

from ._docstring import authored_c_doc, authored_doc_lines, authored_docstring

#: The calling convention a row gets when it declares none -- the common
#: shape of a hand-written accessor, and the composer's default since gh-1190.
DEFAULT_FLAGS = "METH_NOARGS"


def flags(row: dict) -> str:
    """The ``ml_flags`` a row registers its function with.

    >>> flags({"name": "f", "fn": "F_f"})
    'METH_NOARGS'
    >>> flags({"name": "f", "fn": "F_f", "flags": "METH_O"})
    'METH_O'
    """
    return str(row.get("flags") or DEFAULT_FLAGS)


def params(flags: str) -> str:
    """The C parameter list CPython calls a method with, given its *flags*.

    Only the calling-convention bits change the signature; ``METH_CLASS`` /
    ``METH_STATIC`` / ``METH_COEXIST`` change what ``self`` is, not its C
    type, so they fall through to the two-pointer form.

    >>> params("METH_NOARGS")
    'PyObject *, PyObject *'
    >>> params("METH_VARARGS | METH_KEYWORDS")
    'PyObject *, PyObject *, PyObject *'
    >>> params("METH_FASTCALL")
    'PyObject *, PyObject *const *, Py_ssize_t'
    """
    bits = {b.strip() for b in flags.split("|")}
    if "METH_FASTCALL" in bits:
        if "METH_METHOD" in bits:
            return (
                "PyObject *, PyTypeObject *, PyObject *const *, Py_ssize_t,"
                " PyObject *"
            )
        if "METH_KEYWORDS" in bits:
            return "PyObject *, PyObject *const *, Py_ssize_t, PyObject *"
        return "PyObject *, PyObject *const *, Py_ssize_t"
    if "METH_KEYWORDS" in bits:
        return "PyObject *, PyObject *, PyObject *"
    return "PyObject *, PyObject *"


def method_def_row(row: dict) -> str:
    """One ``PyMethodDef`` entry for *row*, newline-terminated.

    The double cast is what lets one spelling serve every calling
    convention: a ``METH_KEYWORDS`` or ``METH_FASTCALL`` function is not a
    ``PyCFunction``, and casting through ``void (*)(void)`` is the conversion
    C99 6.3.2.3p8 defines and ``-Wcast-function-type`` accepts. The doc is the
    authored text, laid out as written (gh-1499), or ``NULL``.

    >>> print(method_def_row({"name": "n", "fn": "F_n", "doc": "N."}), end="")
        {"n", (PyCFunction)(void (*)(void))F_n,
         METH_NOARGS, "N.\\n"},
    """
    doc_c = authored_c_doc(str(row.get("doc") or ""))
    return (
        f'    {{"{row["name"]}", (PyCFunction)(void (*)(void)){row["fn"]},\n'
        f"     {flags(row)}, {doc_c}}},\n"
    )


def prototypes(rows: "list[dict]", hook: str) -> str:
    """Forward prototypes for every row's ``fn``, or ``""`` for no rows.

    The rows sit in a ``PyMethodDef`` table rendered ABOVE the ``#include``
    of *hook* -- the hand-written file has to come after the generated types
    so its bodies can use them -- so without a declaration the table names a
    function its compiler has not seen yet (gh-1516). The signature follows
    from ``flags``, so jm can write it; ``static`` matches a definition
    written with or without the keyword. A function two rows share is
    declared once.

    >>> print(prototypes([{"name": "n", "fn": "F_n"}], "f_ext_extra.c"))
    <BLANKLINE>
    /* extra_methods (gh-1190): defined in the hand-written f_ext_extra.c,
     * included after the types; declared here for the method tables. */
    static PyObject *F_n(PyObject *, PyObject *);
    <BLANKLINE>
    """
    seen: list[str] = []
    lines = []
    for row in rows:
        fn = row["fn"]
        if fn in seen:
            continue
        seen.append(fn)
        lines.append(f"static PyObject *{fn}({params(flags(row))});")
    if not lines:
        return ""
    return (
        f"\n/* extra_methods (gh-1190): defined in the hand-written {hook},"
        "\n * included after the types; declared here for the method tables."
        " */\n" + "\n".join(lines) + "\n"
    )


def pyi_member(row: dict) -> "list[str]":
    """The ``.pyi`` lines for *row*'s method, at class-body indent.

    ``args`` and ``returns`` are raw Python: the whole point of the escape
    hatch is that jm does not know the shape, so it does not try to derive
    one. No ``returns`` reads as ``None``, and no ``doc`` as ``...``.

    >>> pyi_member({"name": "n", "fn": "F_n", "args": "k: int",
    ...             "returns": "float", "doc": "N."})
    ['    def n(self, k: int) -> float:', '        \"\"\"N.\"\"\"']
    >>> pyi_member({"name": "n", "fn": "F_n"})
    ['    def n(self) -> None:', '        ...']
    """
    sig = str(row.get("args") or "")
    sig = f", {sig}" if sig else ""
    returns = row.get("returns") or "None"
    out = [f"    def {row['name']}(self{sig}) -> {returns}:"]
    # gh-1499: the whole `doc`, as written.
    doc = authored_doc_lines(str(row.get("doc") or ""))
    out.extend(authored_docstring(doc, 8) if doc else ["        ..."])
    return out


_C_IDENT = re.compile(r"[A-Za-z_]\w*")


def manifest_errors(root: Path, cfg: dict) -> "list[str]":
    """Every ``[[<obj>.extra_methods]]`` row jm cannot render, with why.

    An object's rows only (gh-1997); a composer's belong to its module. Each
    refusal is a binding that would not do what the row says, asked before
    anything is written rather than left to the compiler or the import:

    - a row with no ``name`` or no ``fn``, or an ``fn`` that is not a C
      identifier -- there is no row, or no prototype, to render;
    - a ``name`` two rows share, or one a member jm generates already holds
      (:func:`._builtins.reserved_python_members`, and the built-ins
      :func:`._builtins.absorbable_members` names) -- two ``PyMethodDef``
      rows under one key, the second silently shadowing the first;
    - a ``name`` a ``[[<obj>.methods]]`` entry declares, unless that entry is
      ``manual_stub`` -- a manual stub emits no row, and this is exactly the
      row it lacked;
    - an ``fn`` two rows give different ``flags`` -- two prototypes of one
      function;
    - an ``fn`` the object's ``_core.h`` declares -- a core C function, not
      the CPython wrapper a row registers, so jm's prototype for it would
      conflict with the header's.

    Examples
    --------
    >>> cfg = {"o": {"state": [], "extra_methods": [
    ...     {"name": "reset", "fn": "O_r"}, {"name": "hand"}]}}
    >>> for e in manifest_errors(Path("no-such-project"), cfg):
    ...     print(e.splitlines()[0])
    [[o.extra_methods]] 'reset': the name is already a built-in method.
    [[o.extra_methods]] 'hand': a row needs both `name` and `fn`.
    """
    from . import _builtins
    from . import _config as C
    from . import _incpath as INC
    from ._linkcheck import _header_functions

    errors: list[str] = []
    for comp in C.components(cfg):
        rows = C.extra_methods(cfg, comp)
        if not rows:
            continue
        where = f"[[{comp}.extra_methods]]"
        generated = dict.fromkeys(
            _builtins.absorbable_members(cfg, comp), "a built-in method"
        )
        for n, (holder, _hint) in _builtins.reserved_python_members(
            cfg, comp
        ).items():
            generated[n] = holder
        declared = {str(m.get("name")): m for m in C.methods(cfg, comp)}
        header = INC.core_h(root, comp, cfg)
        core_fns = set(
            _header_functions(header.read_text(encoding="utf-8"))
            if header.is_file()
            else ()
        )
        seen: set[str] = set()
        signature_of: dict[str, str] = {}
        for row in rows:
            name = str(row.get("name") or "")
            fn = str(row.get("fn") or "")
            label = f"{where} {name or '?'!r}"
            if not name or not fn:
                errors.append(f"{label}: a row needs both `name` and `fn`.")
                continue
            if not _C_IDENT.fullmatch(fn):
                errors.append(
                    f"{label}: fn = {fn!r} is not a C identifier -- it names"
                    " the function you write in the object's _extra.c."
                )
                continue
            if name in seen:
                errors.append(f"{label}: two rows are named {name!r}.")
            seen.add(name)
            if name in generated:
                errors.append(
                    f"{label}: the name is already {generated[name]}.\n"
                    "  jm generates that member, so a second row would"
                    " shadow it; rename the row."
                )
            elif name in declared and not declared[name].get("manual_stub"):
                errors.append(
                    f"{label}: [[{comp}.methods]] declares {name!r} too, and"
                    " jm generates its binding.\n"
                    "  Drop one: a `methods` entry is a wrapper jm writes, an"
                    " `extra_methods` row one you wrote."
                )
            sig = params(flags(row))
            if signature_of.setdefault(fn, sig) != sig:
                errors.append(
                    f"{label}: another row gives fn {fn!r} different flags,"
                    " so it would be declared twice with two signatures."
                )
            if fn in core_fns:
                errors.append(
                    f"{label}: fn {fn!r} is declared in {comp}_core.h -- a"
                    " core function, not a CPython method.\n"
                    f"  Write a wrapper `static PyObject *<name>({sig})` in"
                    " the object's _extra.c and name that."
                )
    return errors
