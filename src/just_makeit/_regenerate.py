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

An object in a ``no_generate`` module is refused, before anything is
deleted: ``jm apply`` writes none of that module, so nothing would rebuild
what this deletes (:func:`refuse_hand_written`, gh-2087).
"""

from __future__ import annotations

from . import _textio

import sysconfig
import sys
from pathlib import Path

from . import _config as C
from . import _incpath as INC
from ._docstring import max_out_prototypes, restore_max_out_prototypes
from ._keys import METHOD_KEYS, OBJECT_KEYS
from ._linkcheck import binding_sources
from ._object import _extract_c_function_bodies, _restore_c_function_bodies
from ._remove import _confirm, _object_paths, _rm
from ._report import Refusal


def deleted_impl_sources(
    root: Path, cfg: dict, component: str, paths: "list[Path]"
) -> "list[str]":
    """Each ``*_impl_file`` of *component* that names a file in *paths*.

    gh-1867. A rebuild deletes *paths* and has `apply` write the component
    again, and `apply` lifts a body from every ``*_impl_file`` as it does
    -- from a file the delete has just removed, when one names a file
    inside the component. That refusal came after the delete, which lost
    the component; `_undo` now puts it back, but the refusal would still
    say "file not found" of a file that is there. Asked here, before
    anything is deleted, it can say what is wrong.

    The keys are read from `_keys`: every ``*impl_file`` key an object
    table or one of its methods may carry, so a new one is covered.

    Returns
    -------
    list of str
        ``<table>.<key> = '<value>'`` for each: the object's own keys,
        then each method's, in the order the manifest lists the methods.

    Examples
    --------
    >>> root = Path("/p")
    >>> cfg = {"o": {"impl_file": "native/src/o/o_core.c::o_step",
    ...              "methods": [{"name": "m", "impl_file": "ref.c::k"}]}}
    >>> deleted_impl_sources(root, cfg, "o", [root / "native/src/o"])
    ["o.impl_file = 'native/src/o/o_core.c::o_step'"]
    >>> deleted_impl_sources(root, cfg, "o", [root / "ref.c"])
    ["o.methods.m.impl_file = 'ref.c::k'"]
    """
    doomed = [p.resolve() for p in paths]
    tables = [(component, cfg.get(component) or {}, OBJECT_KEYS)] + [
        (f"{component}.methods.{m.get('name', '?')}", m, METHOD_KEYS)
        for m in C.methods(cfg, component)
    ]
    found = []
    for label, table, keys in tables:
        for key in sorted(k for k in keys if k.endswith("impl_file")):
            ref = table.get(key)
            if not isinstance(ref, str):
                continue
            src = (root / ref.partition("::")[0]).resolve()
            if any(src == d or d in src.parents for d in doomed):
                found.append(f"{label}.{key} = {ref!r}")
    return found


def refuse_hand_written(cfg: dict, component: str, action: str) -> None:
    """Refuse to rebuild *component* when `apply` would not write it back.

    A rebuild deletes the component's files -- its ``_core.c``, ``_core.h``,
    CMakeLists, binding fragment, C test and bench -- and has ``jm apply``
    write them again from the manifest. An object in a ``no_generate``
    module is the author's by declaration, and ``apply`` writes none of it,
    so on one the delete was the whole of the rebuild (gh-2087): ``jm add
    --state`` removed ``_core.c`` with the author's edits in it, and the
    rest, and exited 0; ``apply`` then had nothing to do.

    :func:`run` calls this first, so every rebuild passes it before
    anything is deleted -- ``jm regenerate`` with or without ``--discard``,
    and any caller added later. A caller that edits the manifest calls it
    as well, before it asks or saves, so a refusal leaves the tree exactly
    as it found it. ``tests/test_gh2087_no_generate_rebuild_refused.py``
    reads the callers of :func:`run` from the source and holds each to that.

    Parameters
    ----------
    cfg : dict
        The loaded manifest.
    component : str
        The component the command would rebuild.
    action : str
        What the command was asked to do, as the refusal opens with it:
        ``"add state (y) to 'o'"``.

    Raises
    ------
    Refusal
        When *component* belongs to a ``no_generate`` module. It names the
        route instead: edit the C by hand.

    Examples
    --------
    >>> cfg = {"module": {"mod": {"objects": ["o"], "no_generate": True}}}
    >>> try:
    ...     refuse_hand_written(cfg, "o", "regenerate 'o'")
    ... except Refusal as exc:
    ...     print(str(exc).split(":")[0])
    cannot regenerate 'o'

    An object `apply` writes, standalone or in a generated module, passes:

    >>> refuse_hand_written({"o": {}}, "o", "regenerate 'o'")
    >>> cfg = {"module": {"mod": {"objects": ["o"]}}}
    >>> refuse_hand_written(cfg, "o", "regenerate 'o'")
    """
    module = C.component_module(cfg, component)
    if module is None or not C.is_no_generate_module(cfg, module):
        return
    raise Refusal(
        f"cannot {action}: module '{module}' is `no_generate`, so"
        f" '{component}' is yours and `jm apply` writes none of it. A"
        " rebuild deletes its files -- _core.c with your edits, the header,"
        " binding and tests -- and nothing writes them back. Edit the C by"
        " hand instead."
    )


def _stale_ext_modules(
    root: Path, cfg: dict, pkg: str, component: str, module: str | None
) -> list[Path]:
    """Return pre-built extension-module files for *component*.

    When the source is rebuilt from the manifest the compiled .so/.pyd is
    stale.  Deleting it guarantees cmake relinks unconditionally — avoiding
    platform-specific mtime-comparison edge cases (e.g. macOS APFS + GNU Make
    1-second resolution on the build artefact that was produced by an earlier
    cmake run and may be seen as 'newer' than the freshly-regenerated sources
    on some runners).

    gh-983: the module's three names are all different here and this used the
    dotted id for both halves. The directory is the ``pypath`` (``dsp/filters``)
    — or the gh-523 ``package`` override, which aims it at a sibling package
    entirely — and the file is named for the ``leaf`` (``filters``), since
    that is what CMake's ``OUTPUT_NAME`` sets. So the path it built existed for
    no project, it found nothing to delete, and the guarantee this function is
    the whole point of quietly did not hold.
    """
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    if module:
        # Module objects share a single .so named after the module's leaf.
        mp = C.module_paths(module)
        out_pkg = C.module_package_resolved(cfg, module)
        so = root / "src" / pkg / out_pkg / f"{mp.leaf}{suffix}"
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

    # gh-2087: before the first delete. A caller that edits the manifest
    # asks this itself too, before it saves.
    refuse_hand_written(cfg, component, f"rebuild '{component}'")

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
    #
    # gh-2073: and every VIEW's fragment over the same core (gh-504). A view
    # without its own `init_params` takes the parent's (`C.view_init_params`),
    # so a state change moves its `kwlist` too, and `apply` re-scaffolds its
    # `create_fn` in the rebuilt core at the new arity. Only the object's
    # fragment was deleted here, so the view's `<View>_init` kept the old
    # kwlist and called the new `create_fn` with the old arguments: KWARGS
    # drift in `status`, and a module that did not compile. The set is
    # `_linkcheck.binding_sources`, the one list of "the fragments that call
    # into this core", as it is for the link-check table.
    if discard and module:
        paths += [
            p for p in binding_sources(root, cfg, component) if p.exists()
        ]

    # gh-1867: before anything is deleted. `_undo` would put the files back
    # after `apply`'s "file not found", but this can say why.
    _lost = deleted_impl_sources(root, cfg, component, paths)
    if _lost:
        raise Refusal(
            f"cannot regenerate '{component}': {'; '.join(_lost)} names a"
            " file regenerate deletes, so the rebuild could not read the"
            " body back from it. Point it at a file outside the"
            " component, or remove the key -- without --discard,"
            " regenerate keeps the hand-written bodies in _core.c/_core.h"
            " itself."
        )

    core_h = INC.core_h(root, component)
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
        _textio.write_text(core_h, restored)
        print(f"  restore hand-written bodies in {core_h}")
    if preserved_c and core_c.exists():
        restored = _restore_c_function_bodies(
            core_c.read_text(encoding="utf-8"),
            preserved_c,
            require_static=False,
        )
        _textio.write_text(core_c, restored)
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
            _textio.write_text(core_h, restored)
            for _name in changed:
                print(f"  keep author-owned prototype {_name}()")
            _apply.run(root)

    # Delete any pre-built extension module so cmake is forced to relink.
    for so in _stale_ext_modules(root, cfg, pkg, component, module):
        so.unlink()
        print(f"  remove  {so}  (stale build artefact; cmake will rebuild)")
