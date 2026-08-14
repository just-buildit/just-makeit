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
from ._docstring import max_out_prototypes, restore_max_out_prototypes
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
    # gh-965: a module object's binding lives in a per-object fragment
    # (`<mod>_ext_<obj>.c`, gh-729), and that fragment was not in the set this
    # command rebuilds. Everything downstream of here is member-level and
    # ADDITIVE — `_docsync` transplants docs and splices in missing bindings —
    # so nothing could rewrite the one function whose text a structural change
    # actually alters: `<Obj>_init`, which carries the constructor's `kwlist`.
    #
    # The result was that `jm add --state` on a module object left the kwlist
    # at its old shape. jm *said so*, loudly and on stderr, and gated
    # `status --check` on it (gh-612) — but the remedy it named ("reconcile the
    # manifest with the binding, or keep the hand-written constructor in an
    # _extra.c") is written for an author who hand-wrote that constructor, and
    # here jm wrote it. Measured: deleting the fragment and re-applying
    # produces the correct `kwlist[] = {"gain", "bias", NULL}`, which is what
    # this now does.
    #
    # `discard` only. That is the flag whose prompt already says "This discards
    # hand-written bodies", so a hand-written binding in the fragment is being
    # given up with the same warning and the same confirmation as a
    # hand-written `_core.c` body. Plain `jm regenerate` preserves, and is
    # unchanged — it does not touch the fragment at all.
    if discard and module:
        _frag = (
            root
            / "native"
            / "src"
            / C.module_paths(module).cname
            / f"{C.module_paths(module).cname}_ext_{component}.c"
        )
        if _frag.exists():
            paths.append(_frag)

    core_h = root / "native" / "inc" / component / f"{component}_core.h"
    core_c = root / "native" / "src" / component / f"{component}_core.c"
    preserved_h: dict[str, str] = {}
    preserved_c: dict[str, str] = {}
    # gh-903: the author owns every `*_max_out` signature (gh-761), and
    # `_apply._refresh_core_h_decls` protects it by reading the declaration
    # off the header. That protection cannot fire here: regenerate DELETES the
    # header first, so apply rebuilds it with jm's default and the contract is
    # gone. A prototype has no body, so the body-preserving machinery below
    # never covered it either — this is its declaration-level peer.
    preserved_max_out: dict[str, str] = {}
    if not discard:
        if core_h.exists():
            _h_text = core_h.read_text(encoding="utf-8")
            preserved_max_out = max_out_prototypes(_h_text)
            preserved_h = _extract_c_function_bodies(
                _h_text, require_static=False
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

    # gh-903: put the author's `*_max_out` declarations back, and re-derive
    # from them. The second apply is not belt-and-braces — the glue above was
    # generated against jm's default arity, so restoring the header alone
    # leaves a binding calling the restored prototype with the wrong number of
    # arguments. It runs only when a declaration actually changed, which is
    # never for a project that has not overridden one.
    if preserved_max_out and core_h.exists():
        restored, changed = restore_max_out_prototypes(
            core_h.read_text(encoding="utf-8"), preserved_max_out
        )
        if changed:
            core_h.write_text(restored, encoding="utf-8")
            for _name in changed:
                print(f"  keep author-owned prototype {_name}()")
            _apply.run(root)

    # Delete any pre-built extension module so cmake is forced to relink.
    for so in _stale_ext_modules(root, pkg, component, module):
        so.unlink()
        print(f"  remove  {so}  (stale build artefact; cmake will rebuild)")
