"""_modpath — render slots for a (possibly dotted) module id.

A dotted module name (``dsp.filters``) nests the extension under
``src/<pkg>/dsp/filters/`` and imports as ``pkg.dsp.filters``. The single name
plays three roles that nesting splits apart (C identifier, filesystem segment,
Python import path), so the templates take role-specific slots:

- ``module``             the cname (``dsp_filters``) — CMake target / struct ids
- ``module_leaf``        the leaf (``filters``) — ``PyInit_`` / ``.m_name`` /
                         ``from .<leaf> import``
- ``module_pypath``      the package path (``dsp/filters``) — output directory
- ``module_output_name`` ``\\n    OUTPUT_NAME <leaf>`` only when nested
- ``module_tp``          the fully-qualified ``tp_name`` prefix

For a dotless id every slot equals today's value (and ``module_output_name`` is
empty), so flat modules render byte-for-byte unchanged.
"""

from __future__ import annotations

from .. import _config as C


def make_module_ctx(
    module_id: str, pkg: str = "", package: str = "", doc: str = ""
) -> dict[str, str]:
    """Render slots for *module_id* (dotted ids nest; dotless ones don't).

    *package* is the optional ``[module.X] package`` override (gh-523): the
    package directory the ``.so`` / ``.pyi`` land in when the module lives
    inside a sibling package rather than one named after itself. It replaces
    ``module_pypath`` only — the C identifiers, the ``PyInit_`` leaf and the
    ``.so`` basename are all still the module's own, since the extension keeps
    its own name inside the shared package. Empty (the default) leaves every
    slot exactly as before, so unpackaged modules render byte-identically."""
    mp = C.module_paths(module_id)
    # gh-645: the two doc faces, from the one manifest string. A module has no
    # header to derive from -- both of these files are wholly jm-generated --
    # so `[module.X] doc` is the only place an author can say what it is for.
    # Absent, each falls back to exactly what it rendered before.
    Module_t = "".join(w.title() for w in mp.cname.split("_"))
    docstring_py, doc_c = module_doc_faces(doc)
    doc_c = doc_c or f'"{Module_t} module."'
    nested = bool(mp.parents)
    return {
        "module": mp.cname,
        "module_leaf": mp.leaf,
        "module_pypath": package or mp.pypath,
        "module_output_name": (
            f"\n    OUTPUT_NAME {mp.leaf}" if nested else ""
        ),
        # tp_name stays bare (cname) for flat modules — matching today — and
        # becomes the full dotted import path when nested.
        "module_tp": f"{pkg}.{mp.id}" if (nested and pkg) else mp.cname,
        "module_docstring_py": docstring_py,
        "module_doc_c": doc_c,
    }


def module_doc_faces(doc: str) -> "tuple[str, str]":
    """``[module.X] doc`` as its two faces: a Python docstring and a C literal.

    ``("", "")`` when there is no doc, so each caller keeps its own fallback
    -- a plain module's ``m_doc`` has always said ``"<Module> module."``, and
    a ``kind`` module's has always been ``NULL``. The text is laid out by
    `_docstring.authored_doc_lines`, the one rule a manifest ``doc`` follows
    on every face (gh-1493); this used ``str.strip()``, which kept the indent
    of an indented TOML table's continuation lines. gh-1499 made it the one
    reader for the ``kind``-bearing modules too, which had no module doc on
    either face.

    Examples
    --------
    >>> module_doc_faces("")
    ('', '')
    >>> py, c = module_doc_faces('''Gain control.
    ...     Second line.''')
    >>> py
    '\"\"\"Gain control.\\nSecond line.\"\"\"\\n\\n'
    >>> print(c)
    "Gain control.\\n"
         "Second line.\\n"
    """
    from .._docstring import authored_doc_lines

    text = "\n".join(authored_doc_lines(doc))
    if not text:
        return "", ""
    return _py_docstring(text), _c_doc_literal(text)


def module_docstring_lines(cfg: dict, module: str) -> "list[str]":
    """A ``kind`` module's docstring as ``.pyi`` lines, blank line included.

    ``[]`` when the module declares no ``doc``, so a stub that never had one
    is byte-identical. The handle, capsule and composer stubs all splice
    this in ahead of their imports (gh-1499).

    Examples
    --------
    >>> module_docstring_lines({"module": {"m": {"doc": "One.\\nTwo."}}}, "m")
    ['\"\"\"One.', 'Two.\"\"\"', '']
    >>> module_docstring_lines({"module": {"m": {}}}, "m")
    []
    """
    py, _ = module_doc_faces(C.module_doc(cfg, module))
    return py.rstrip("\n").split("\n") + [""] if py else []


def module_m_doc(cfg: dict, module: str) -> str:
    """A ``kind`` module's ``m_doc``: its ``doc`` as a C literal, else ``NULL``.

    The runtime peer of `module_docstring_lines` (gh-1499). ``NULL`` is what
    every ``kind`` module emitted before, so one without a ``doc`` does not
    change.

    Examples
    --------
    >>> module_m_doc({"module": {"m": {}}}, "m")
    'NULL'
    >>> print(module_m_doc({"module": {"m": {"doc": "One."}}}, "m"))
    "One.\\n"
    """
    _, c = module_doc_faces(C.module_doc(cfg, module))
    return c or "NULL"


def _py_docstring(text: str) -> str:
    """Render *text* as a safe module-level docstring, with trailing blank line.

    The prose comes from TOML, so it can contain anything -- and two things
    would otherwise emit a generated file that does not parse: an embedded
    ``\"\"\"`` closes the docstring early, and a trailing ``"`` abuts the
    closing delimiter into ``\"\"\"\"``. Both are escaped rather than
    rejected: refusing an author's apostrophe-heavy sentence would be worse
    than quoting it.
    """
    body = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    if body.endswith('"'):
        body = body[:-1] + '\\"'
    return f'"""{body}"""\n\n'


def _c_doc_literal(text: str) -> str:
    """Render *text* as one C string literal, escaped and newline-joined.

    A module doc is prose a human wrote in TOML, so it may be several lines and
    may contain quotes or backslashes -- all of which are a compile error if
    pasted raw into ``.m_doc``.
    """
    # gh-1499: the one multi-line C docstring emitter; this was a second copy
    # of it, byte-for-byte the same output.
    from ._parse import _build_ml_doc

    return _build_ml_doc(text.splitlines() or [""])
