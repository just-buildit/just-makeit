"""
_regenerate.py — `just-makeit regenerate <component>`.

The deliberate-refresh half of the sacred/glue contract. ``jm apply``
never overwrites a component's sacred ``_core.c`` (and preserves the inline
``step()`` body inside ``_core.h``), so to get a clean, fresh scaffold the
user removes the component's files and lets apply recreate them. This verb
does exactly that — ``rm`` every file the component owns, then ``jm apply``
— behind a single confirmation (skippable with ``--force``).

Unlike ``jm remove``, the manifest is left untouched: the component stays
declared, only its generated source is rebuilt.

Always ``git stash`` (or commit) first — regeneration discards hand-written
bodies in ``_core.c`` and any edits to the regenerated files.
"""

from __future__ import annotations

import sysconfig
import sys
from pathlib import Path

from . import _config as C
from ._remove import _confirm, _object_paths, _rm


def _stale_ext_modules(
    root: Path, pkg: str, component: str, module: str | None
) -> list[Path]:
    """Return pre-built extension-module files for *component*.

    When the source is rebuilt from the manifest the compiled .so/.pyd is
    stale.  Deleting it guarantees cmake relinks unconditionally — avoiding
    platform-specific mtime-comparison edge cases (e.g. macOS APFS + GNU Make
    1-second resolution on the build artefact that was produced by an earlier
    cmake run and may be seen as 'newer' than the freshly-regenerated sources
    on some runners).
    """
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    if module:
        # Module objects share a single .so named after the module.
        so = root / "src" / pkg / module / f"{module}{suffix}"
    else:
        so = root / "src" / pkg / f"{component}{suffix}"
    return [so] if so.exists() else []


def run(root: Path, component: str, force: bool = False) -> None:
    cfg_path = root / C.FILENAME
    if not cfg_path.exists():
        print(
            f"error: no {C.FILENAME} found in {root}.\n"
            "Run 'just-makeit new' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = C.load(root)
    if component not in C.components(cfg):
        known = ", ".join(C.components(cfg)) or "(none)"
        print(
            f"error: '{component}' is not a component in {C.FILENAME}.\n"
            f"Known components: {known}.",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = C.project_name(cfg)
    module = C.component_module(cfg, component)
    paths = [
        p for p in _object_paths(root, pkg, component, module) if p.exists()
    ]

    if paths:
        print(
            f"just-makeit: regenerate '{component}' — the following are "
            f"deleted and rebuilt from {C.FILENAME}:"
        )
        for p in paths:
            print(f"  {p}{'/' if p.is_dir() else ''}")
        print()
        print(
            "This discards hand-written bodies (e.g. in _core.c). "
            "git stash or commit first."
        )
        if not _confirm(f"Regenerate '{component}'?", force):
            print("aborted.")
            return
        for p in paths:
            _rm(p)
        print()
    else:
        print(
            f"just-makeit: '{component}' has no materialized files; "
            f"applying {C.FILENAME} to create them."
        )

    from . import _apply

    _apply.run(root)

    # Delete any pre-built extension module so cmake is forced to relink.
    for so in _stale_ext_modules(root, pkg, component, module):
        so.unlink()
        print(f"  remove  {so}  (stale build artefact; cmake will rebuild)")
