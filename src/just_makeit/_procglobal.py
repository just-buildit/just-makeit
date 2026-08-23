"""
_procglobal.py — which component cores reach more than one extension module.

CPython imports extensions ``RTLD_LOCAL``, and jm links a component's OBJECT
library **statically** into every ``.so`` that needs it. So a core linked into
three modules is three copies: three copies of every file-scope ``static`` in
it, in one process, none of them aware of the others.

For a pure kernel that is correct and desirable — it is why the OBJECT-library
wiring exists at all. For a primitive whose contract is *one per process* it
is silently wrong, and the silence is the whole problem. Measured in doppler
(gh-1117 / doppler#976): a ``volatile sig_atomic_t`` interrupt flag consulted
by every blocking wait, spread across three modules. Setting it from one
module left the waits in the other two spinning on a different variable, and
every test passed because the only setter and the only exercised wait happened
to live in the same ``.so``.

**This file reports linkage, and deliberately nothing more.** It does not read
the component's C to decide whether the core actually holds process-global
state. That would be a model of C — "is this file-scope ``static`` mutable, is
that array ``const``, is this a definition or a declaration" — living in jm,
where it would track C forever and be wrong quietly. jm owns the linkage fact
completely: it wrote the ``target_link_libraries`` line. It owns nothing about
the contents of a core, so it says nothing about them.

The consequence is that the report is *informational*: it lists a situation
that is usually fine, and cannot tell the reader which of their cores is the
exception. That is a real limitation and the reason it does not fail
``jm status --check``. What turns it into an answer is the project declaring
which core is process-global; the generator for that declaration will live
**here, beside this detector**, for the reason :mod:`._libwiring` gives for
holding its own pair together — a detector that looks for something its
emitter does not write is not expressible when they share a file.
"""

from __future__ import annotations

from typing import NamedTuple

from . import _config as C


class SharedCore(NamedTuple):
    """One component core linked into more than one extension module."""

    core: str
    #: Extension modules linking it, as a reader would name them — a
    #: standalone object by its component name, a module by its module id.
    modules: tuple[str, ...]


def _core(name: str) -> str:
    """``<name>_core``, tolerating a name that already carries the suffix."""
    return name if name.endswith("_core") else f"{name}_core"


def module_cores(cfg: dict) -> "dict[str, tuple[str, ...]]":
    """Map each extension module to the cores whose symbols land in its ``.so``.

    Three things produce an extension module, and all three are counted — a
    detector that knew about two of them would report a component shared
    between the third and either other as unshared, which is the failure mode
    this whole issue is an instance of:

    - a **standalone object**, which gets its own ``.so``;
    - an **object module** (``objects = [...]``), one ``.so`` for the group;
    - a ``kind``-bearing module (handle / capsule / composer), whose
      ``depends_on`` entries carry the linkage.

    A core reaches a module either by being that module's own, or through a
    ``{name = ..., link = true}`` dependency — the entry that puts
    ``<name>_core`` directly on the consuming target's
    ``target_link_libraries`` (gh-225). A dependency without ``link`` supplies
    headers and aggregate-library objects, not symbols in this ``.so``, so it
    is not linkage and is not counted.
    """
    out: dict[str, tuple[str, ...]] = {}
    owned: dict[str, str] = {}  # component -> the module that owns it
    for mod in C.modules(cfg):
        for obj in C.module_objects(cfg, mod):
            owned[obj] = mod

    def _for_components(comps: list) -> list[str]:
        cores: list[str] = []
        for comp in comps:
            for core in [_core(comp)] + C.dep_link_libs(
                C.depends_on_raw(cfg, comp)
            ):
                if core not in cores:
                    cores.append(core)
        return cores

    for comp in C.components(cfg):
        if comp in owned:
            continue  # counted under its module below
        if not isinstance(cfg.get(comp), dict):
            # `components` is "top-level keys that are not reserved", which on
            # a real manifest also catches array-of-table sections a reader
            # would never call components. Same guard `_keys` uses, and found
            # the same way: by running this over doppler rather than over a
            # fixture jm wrote itself.
            continue
        out[comp] = tuple(_for_components([comp]))

    for mod in C.modules(cfg):
        data = cfg.get("module", {}).get(mod) or {}
        if C.module_kind(cfg, mod):
            cores = C.dep_link_libs(data.get("depends_on", []))
        else:
            cores = _for_components(C.module_objects(cfg, mod))
        out[mod] = tuple(cores)
    return out


def shared_cores(cfg: dict) -> "list[SharedCore]":
    """Cores linked into two or more extension modules, in manifest order.

    Empty for a single-module project, which is most of them — so this costs
    one pass over the manifest and reports nothing at all in the common case.

    Examples
    --------
    >>> cfg = {"a": {}, "b": {"depends_on": [{"name": "a", "link": True}]}}
    >>> shared_cores(cfg)
    [SharedCore(core='a_core', modules=('a', 'b'))]
    >>> shared_cores({"a": {}, "b": {}})
    []
    """
    where: dict[str, list[str]] = {}
    for mod, cores in module_cores(cfg).items():
        for core in cores:
            where.setdefault(core, []).append(mod)
    return [
        SharedCore(core, tuple(mods))
        for core, mods in where.items()
        if len(mods) > 1
    ]
