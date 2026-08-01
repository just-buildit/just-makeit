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
empty), so flat modules render byte-for-byte unchanged — the same zero-churn
gate as ``make_platform_ctx``.
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
    _doc = (doc or "").strip()
    docstring_py = _py_docstring(_doc) if _doc else ""
    doc_c = _c_doc_literal(_doc) if _doc else f'"{Module_t} module."'
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
    out = []
    for i, line in enumerate(text.splitlines() or [""]):
        esc = line.replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'"{esc}\\n"' if i else f'"{esc}\\n"')
    return "\n     ".join(out)
