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

By default, hand-written bodies in ``_core.c``/``_core.h`` (create/destroy/
reset, ``step()``, getters/setters, method implementations — anything past
the boilerplate ``*_steps`` dispatch loop) are lifted before the sacred files
are deleted and spliced back into the freshly regenerated ones (gh-267),
reusing the same by-name extract/restore machinery ``jm apply`` already uses
to preserve hand-patched module ``_ext.c`` glue. Pass ``--discard`` for a
truly clean reset back to the template scaffold. Always ``git stash`` (or
commit) first regardless — the splice is best-effort text matching, not a
guarantee.
"""

from __future__ import annotations

import sysconfig
import sys
from pathlib import Path

from . import _config as C
from ._object import _extract_c_function_bodies, _restore_c_function_bodies
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


def run(
    root: Path, component: str, force: bool = False, discard: bool = False
) -> None:
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
        p
        for p in _object_paths(root, cfg, pkg, component, module)
        if p.exists()
    ]

    core_h = root / "native" / "inc" / component / f"{component}_core.h"
    core_c = root / "native" / "src" / component / f"{component}_core.c"
    preserved_h: dict[str, str] = {}
    preserved_c: dict[str, str] = {}
    if not discard:
        if core_h.exists():
            preserved_h = _extract_c_function_bodies(
                core_h.read_text(encoding="utf-8"), require_static=False
            )
        if core_c.exists():
            preserved_c = _extract_c_function_bodies(
                core_c.read_text(encoding="utf-8"), require_static=False
            )

    if paths:
        print(
            f"just-makeit: regenerate '{component}' — the following are "
            f"deleted and rebuilt from {C.FILENAME}:"
        )
        for p in paths:
            print(f"  {p}{'/' if p.is_dir() else ''}")
        print()
        if discard:
            print(
                "This discards hand-written bodies (e.g. in _core.c). "
                "git stash or commit first."
            )
        else:
            print(
                "Hand-written bodies in _core.c/_core.h are lifted and "
                "spliced back in afterward (--discard skips this). "
                "git stash or commit first regardless."
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

    if preserved_h and core_h.exists():
        restored = _restore_c_function_bodies(
            core_h.read_text(encoding="utf-8"),
            preserved_h,
            require_static=False,
        )
        core_h.write_text(restored, encoding="utf-8")
        print(f"  restore hand-written bodies in {core_h}")
    if preserved_c and core_c.exists():
        restored = _restore_c_function_bodies(
            core_c.read_text(encoding="utf-8"),
            preserved_c,
            require_static=False,
        )
        core_c.write_text(restored, encoding="utf-8")
        print(f"  restore hand-written bodies in {core_c}")

    # Delete any pre-built extension module so cmake is forced to relink.
    for so in _stale_ext_modules(root, pkg, component, module):
        so.unlink()
        print(f"  remove  {so}  (stale build artefact; cmake will rebuild)")
