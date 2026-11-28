"""Which directories below ``src/<pkg>/`` nobody accounts for (gh-2854).

A package directory jm writes is a standalone component's, a module's
package (or one of its parents, `_config.module_package_dirs`), or one of
jm's own (`_config.reserved_package_dirs`). Anything else is a package jm
started writing that `namespace_errors` does not reserve, so a component
or module of that name would be hidden by it. Both completeness gates --
`test_gh2854_parent_package` and every bundled example in `test_examples`
-- read this one answer.
"""

from __future__ import annotations

from pathlib import Path

from just_makeit import _config as C


def unaccounted(root: Path) -> "list[str]":
    """The directories below *root*'s ``src/<pkg>/`` that are neither a
    component's, a module's package, nor jm's own, sorted."""
    cfg = C.load(root)
    src = root / "src" / C.project_name(cfg)
    if not src.is_dir():
        return []
    owned = set(C.module_package_dirs(cfg)) | {
        c for c in C.components(cfg) if not C.module_of(cfg, c)
    }
    allowed = owned | set(C.reserved_package_dirs(cfg))
    written = {
        d.relative_to(src).as_posix()
        for d in src.rglob("*")
        if d.is_dir() and d.name != "__pycache__"
    }
    return sorted(written - allowed)
