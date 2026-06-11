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


def make_module_ctx(module_id: str, pkg: str = "") -> dict[str, str]:
    """Render slots for *module_id* (dotted ids nest; dotless ones don't)."""
    mp = C.module_paths(module_id)
    nested = bool(mp.parents)
    return {
        "module": mp.cname,
        "module_leaf": mp.leaf,
        "module_pypath": mp.pypath,
        "module_output_name": (
            f"\n    OUTPUT_NAME {mp.leaf}" if nested else ""
        ),
        # tp_name stays bare (cname) for flat modules — matching today — and
        # becomes the full dotted import path when nested.
        "module_tp": f"{pkg}.{mp.id}" if (nested and pkg) else mp.cname,
    }
