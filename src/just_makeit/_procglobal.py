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


# ── Declaring it: `process_global = true` ─────────────────────────────────────
#
# What the detector above reports, a component can answer. The declaration
# says "this core's file-scope state is one-per-process", and jm then makes
# that true across every module linking it.
#
# **jm cannot do this with no project-side C, and gh-1117 hoped it could.**
# The state is the author's, declared in their `_core.c`, reached by their own
# code on every access. Nothing jm generates can allocate it or route reads
# through a pointer it does not own. So the split is:
#
#   the author writes   two accessors -- hand out the address, adopt another's
#   jm writes           the cross-module rendezvous, in every PyInit, once,
#                       identically, and the header that declares the pair
#
# That is still the whole point. The rendezvous is the part that is easy to
# get subtly wrong and impossible to notice: doppler#976 passed every test it
# had, because the only setter and the only exercised wait happened to live in
# the same `.so`.


class ProcGlobalRefusal(ValueError):
    """A ``process_global`` declaration jm cannot honour."""


def is_process_global(cfg: dict, comp: str) -> bool:
    """Whether *comp* declares ``process_global = true``."""
    section = cfg.get(comp)
    if not isinstance(section, dict):
        return False
    return str(section.get("process_global", "")).lower() in ("true", "1")


def process_globals(cfg: dict) -> "list[str]":
    """Components declaring ``process_global``, in manifest order."""
    return [c for c in C.components(cfg) if is_process_global(cfg, c)]


def owner_module(cfg: dict, comp: str) -> "str | None":
    """The module whose ``.so`` holds the ONE copy of *comp*'s state.

    Every other module linking the core adopts a pointer to it, so exactly one
    has to own the storage, and it must be one a reader can name: the
    rendezvous is an ordinary ``import``.

    A component is owned by the object module that lists it, or -- when no
    module does -- by its own standalone ``.so``. A ``kind``-bearing module
    (handle / capsule / composer) is never an owner: it has no ``objects``, so
    the component whose core it links lives somewhere else and that somewhere
    else is the answer.

    ``None`` only for a name that is not a component of this project at all;
    every declared component either belongs to a module or is standalone, so
    for anything `process_globals` returns this is a ``str``.
    """
    for mod in C.modules(cfg):
        if comp in C.module_objects(cfg, mod):
            return mod
    if isinstance(cfg.get(comp), dict):
        return comp
    return None


def import_path(cfg: dict, module: str) -> str:
    """The dotted name of the EXTENSION MODULE an adopter must import.

    Not the package (gh-1134). jm's standard layout for an object module is
    ``<pkg>/<mod>/<mod>.so`` behind a generated re-exporting ``__init__.py``,
    so ``<pkg>.<mod>`` is that ``__init__.py`` — which re-exports the declared
    class names and nothing else — while the capsule is published on the
    ``.so``'s own module object in its ``PyInit_``. Importing the package
    found no attribute and **every adopting module failed at import**.

    The two are the same string only when a module's ``.so`` *is* the package
    module, which is exactly the flat shape gh-1117's compiled test built for
    itself. A project laid out the way `jm apply` lays one out is not that
    shape, and the end-to-end test beside it asserted on generated TEXT, so
    the text was self-consistent and wrong about the layout.

    A **standalone** object is genuinely flat: its ``.so`` lands in the
    package root, so ``<pkg>.<comp>`` already named the extension.
    """
    pkg = C.project_name(cfg)
    if module not in (cfg.get("module") or {}):
        return f"{pkg}.{module}"
    mp = C.module_paths(module)
    # `[module.X] package` moves the `.so` into a sibling package, and the
    # CMake output dir follows it -- so the import path has to as well.
    where = (C.module_package(cfg, module) or mp.pypath).replace("/", ".")
    return f"{pkg}.{where}.{mp.leaf}"


def capsule_name(cfg: dict, comp: str) -> str:
    """The capsule's name, checked on unwrap so a mismatch cannot pass.

    Project-qualified: two jm projects in one process must not hand each
    other a pointer because both happened to call a component ``filter``.
    """
    return f"{C.project_name(cfg)}.{comp}._jm_procglobal"


def hand_written_adopters(cfg: dict) -> "list[tuple[str, str]]":
    """``(component, module)`` pairs jm cannot generate an adopt into.

    A `no_generate` module gets an ``add_subdirectory`` line and nothing else
    — its binding is hand-written, so there is no generated ``PyInit_`` to put
    a rendezvous in. Every OTHER module still shares one state correctly; this
    one keeps its own copy until its author adds the adopt themselves, which
    is a defect they can fix in a file they already own.

    Reported rather than refused (gh-1128). The refusal that used to cover
    this case named an escape hatch that did not exist: `validate` runs before
    anything is written, so the ``<comp>_procglobal.h`` it told the author to
    read was never generated on a project in this state.
    """
    out = []
    linkers = module_cores(cfg)
    for comp in process_globals(cfg):
        owner = owner_module(cfg, comp)
        core = f"{comp}_core"
        for mod, cores in sorted(linkers.items()):
            if (
                core in cores
                and mod != owner
                and C.is_no_generate_module(cfg, mod)
            ):
                out.append((comp, mod))
    return out


def validate(cfg: dict) -> None:
    """Refuse a ``process_global`` jm cannot make true **at all**.

    Only the OWNER being ``no_generate`` reaches that bar. jm writes no
    ``PyInit_`` there, so nothing publishes the capsule and no adopter in the
    project can work — there is no edit to another module that helps, and
    generating the rest would produce a project where the feature is declared
    and inert everywhere.

    An **adopter** being ``no_generate`` is a different situation and is not
    refused (gh-1128): every other module still shares one state, and the one
    that does not is fixable in a binding its author already writes.
    `hand_written_adopters` reports those, and the generated header carries
    the three names such a binding needs.

    Raises
    ------
    ProcGlobalRefusal
        With the reason and what to change. A declaration that generated
        nothing and said nothing would be gh-1118 in a new place: a key read,
        accepted, and silently doing nothing.
    """
    linkers = module_cores(cfg)
    for comp in process_globals(cfg):
        owner = owner_module(cfg, comp)
        if owner is None or not C.is_no_generate_module(cfg, owner):
            continue
        if f"{comp}_core" not in linkers.get(owner, ()):
            continue
        raise ProcGlobalRefusal(
            f"component '{comp}': its owning module '{owner}' is"
            f" `no_generate`, so jm writes no PyInit_ there and nothing"
            f" publishes the shared state — every other module would adopt"
            f" from a module that never offers it. Drop `no_generate` on"
            f" '{owner}', move '{comp}' to a module jm generates, or publish"
            f" the capsule from '{owner}'s hand-written binding:"
            f" `{comp}_procglobal.h` carries the name to publish it under."
        )


# ── Emitting it ───────────────────────────────────────────────────────────────


#: The contract, as ``(declaration, what it is for)``. ONE source, rendered
#: into two places: the published ``<comp>_procglobal.h`` a C test or bench
#: includes, and a block-scope declaration inside the rendezvous itself.
#:
#: The block-scope copy is why the five `PyInit_` emitters need no include
#: splice of their own. Ten splice points across five generators is ten
#: chances for one to be missed, and a missed one fails at COMPILE time in a
#: user's project rather than in jm's suite. C has declared functions at block
#: scope since C89, scoped to exactly where they are called, which is a
#: narrower blast radius than a file-level include besides.
#:
#: This is not gh-998's second copy: that rule is about two AUTHORED copies
#: drifting. Both of these come from the tuple below, so they cannot disagree.
_CONTRACT = (
    (
        "void *{comp}_state_ptr(void);",
        "The address of this `.so`'s state — the OWNER module publishes it.",
    ),
    (
        "void {comp}_state_adopt(void *shared);",
        "Point this `.so` at the owner's state — every OTHER module calls it.",
    ),
)


def contract_decls(comp: str, indent: int = 0) -> "list[str]":
    """The two prototypes for *comp*, indented for their splice site."""
    pad = " " * indent
    return [pad + d.format(comp=comp) for d, _ in _CONTRACT]


def header_name(comp: str) -> str:
    """Relative path of the generated contract header for *comp*."""
    return f"{comp}/{comp}_procglobal.h"


def render_header(cfg: dict, comp: str, declared: "bool | None" = None) -> str:
    """The generated ``<comp>_procglobal.h``, or ``""`` when not declared.

    *declared* overrides the manifest lookup for callers that already hold
    the answer. The scaffold writers are exactly that case and they need it:
    they run BEFORE `_config.add_component` puts the key into ``cfg``, so
    asking the manifest there returns False and the header is never written
    — for the one component that declared it.

    The two accessors are the AUTHOR's to implement and jm's to declare, so
    they are published in a header rather than left as ``extern`` lines inside
    the generated ``_ext.c``. That is gh-998's rule, and its reason applies
    unchanged: a C test or benchmark that wants to assert the state really is
    shared can only reach a signature jm owns by writing a second copy of it.

    One call decides whether this file exists, and every writer of it is
    gated on that call: the two scaffold writers here, and — since gh-1140 —
    `_apply._render_procglobal_headers`, which re-renders it from the whole
    manifest on every `apply` so that `status` compares it and a stale owner
    macro cannot outlive the release that fixed it.
    """
    if not (is_process_global(cfg, comp) if declared is None else declared):
        return ""
    guard = f"{comp.upper()}_PROCGLOBAL_H"
    decls = "\n\n".join(
        f"/* {why} */\n{decl.format(comp=comp)}" for decl, why in _CONTRACT
    )
    # gh-1128: the three names a HAND-WRITTEN binding needs to join the
    # rendezvous. They are jm's invention and appeared only inside another
    # module's generated C, so "add the rendezvous to your own binding" was
    # not actionable -- the author would have had to reverse engineer all
    # three from a different module's output. A `no_generate` module is
    # exactly the case that advice is for, so the names ship with the
    # contract that advice points at.
    up = comp.upper()
    owner = owner_module(cfg, comp)
    names = (
        "/*\n"
        " * The rendezvous, for a binding jm does NOT generate (a\n"
        " * `no_generate` module). To ADOPT, in your PyInit_ once the module\n"
        " * object exists (error handling omitted -- every pointer here can\n"
        " * be NULL):\n"
        " *\n"
        f" *     PyObject *own = PyImport_ImportModule({up}_PG_OWNER);\n"
        f" *     PyObject *cap = PyObject_GetAttrString(own, {up}_PG_ATTR);\n"
        f" *     {comp}_state_adopt(\n"
        f" *         PyCapsule_GetPointer(cap, {up}_PG_CAPSULE));\n"
        " *\n"
        " * To PUBLISH, when this module owns the state:\n"
        " *\n"
        f" *     PyModule_AddObject(m, {up}_PG_ATTR,\n"
        f" *         PyCapsule_New({comp}_state_ptr(), {up}_PG_CAPSULE,\n"
        " *                       NULL));\n"
        " */\n"
        f'#define {up}_PG_OWNER   "'
        + (import_path(cfg, owner) if owner else "")
        + '"\n'
        f'#define {up}_PG_ATTR    "_jm_pg_{comp}"\n'
        f'#define {up}_PG_CAPSULE "{capsule_name(cfg, comp)}"\n'
    )
    return f"""/*
 * {comp}_procglobal.h — generated by just-makeit (gh-1117). DO NOT EDIT.
 *
 * `{comp}` declares `process_global = true`: its file-scope state is
 * one-per-PROCESS, not one-per-`.so`. CPython imports extensions RTLD_LOCAL
 * and jm links this core statically into every module that needs it, so
 * without a rendezvous each `.so` would hold its own copy — a flag set
 * through one module is not the flag another module reads.
 *
 * jm generates that rendezvous into every module's PyInit. It cannot
 * generate the two functions below: the state is yours, declared in
 * {comp}_core.c and reached by your own code on every access, so nothing
 * generated can allocate it or route reads through a pointer it does not
 * own.
 *
 * Implement them in {comp}_core.c, holding the state behind one pointer:
 *
 *     static {comp}_state_t  g_own;
 *     static {comp}_state_t *g_cur = &g_own;
 *
 *     void *{comp}_state_ptr(void)  {{ return (void *)g_cur; }}
 *     void  {comp}_state_adopt(void *shared)
 *     {{ if (shared) g_cur = ({comp}_state_t *)shared; }}
 *
 * and read through `g_cur` everywhere else. Adoption happens at import,
 * before any of your code runs, so nothing has to be thread-safe here.
 */
#ifndef {guard}
#define {guard}

{decls}

{names}
#endif /* {guard} */
"""


def rendezvous_c(cfg: dict, module: str, *, var: str = "m") -> str:
    """The ``PyInit_`` block unifying every process-global core *module* links.

    ``""`` when the module links none, which is every module in almost every
    project — so each of jm's five ``PyInit_`` emitters splices this
    unconditionally and generates byte-identical output to before unless the
    feature is actually in use.

    *var* names the module object in scope, because the emitters do not agree
    on it and one of them does not create it at all until this returns
    something.

    The owner publishes a named `PyCapsule`; everyone else imports the owner
    and adopts the pointer out of it. Two properties are worth stating, both
    of which were verified against a real pair of compiled ``.so`` files
    rather than reasoned about:

    - **Import order does not matter.** An adopter imported first pulls the
      owner in itself, so a user who never names the owning module still gets
      one shared state.
    - **The capsule's name is checked on unwrap**, and is project-qualified,
      so two jm projects in one process cannot hand each other a pointer
      because both happen to have a component called ``filter``.
    """
    lines: list[str] = []
    for core in module_cores(cfg).get(module, ()):
        comp = core[: -len("_core")] if core.endswith("_core") else core
        if not is_process_global(cfg, comp):
            continue
        owner = owner_module(cfg, comp)
        name = capsule_name(cfg, comp)
        if owner == module:
            lines.append(
                f"    /* gh-1117: this module OWNS {comp}'s"
                f" process-global state. */\n"
                f"    {{\n"
                + "".join(d + "\n" for d in contract_decls(comp, 8))
                + f"        PyObject *_pg = PyCapsule_New({comp}_state_ptr(),"
                f' "{name}", NULL);\n'
                f"        if (!_pg) {{ Py_DECREF({var}); return NULL; }}\n"
                f'        if (PyModule_AddObject({var}, "_jm_pg_{comp}",'
                f" _pg) < 0) {{\n"
                f"            Py_DECREF(_pg); Py_DECREF({var});"
                f" return NULL;\n"
                f"        }}\n"
                f"    }}\n"
            )
        else:
            lines.append(
                f"    /* gh-1117: adopt {comp}'s process-global state from"
                f" its owner. */\n"
                f"    {{\n"
                + "".join(d + "\n" for d in contract_decls(comp, 8))
                + f"        PyObject *_own ="
                f' PyImport_ImportModule("{import_path(cfg, owner)}");\n'
                f"        if (!_own) {{ Py_DECREF({var}); return NULL; }}\n"
                f"        PyObject *_pg = PyObject_GetAttrString(_own,"
                f' "_jm_pg_{comp}");\n'
                f"        Py_DECREF(_own);\n"
                f"        if (!_pg) {{ Py_DECREF({var}); return NULL; }}\n"
                f"        void *_p = PyCapsule_GetPointer(_pg,"
                f' "{name}");\n'
                f"        Py_DECREF(_pg);\n"
                f"        if (!_p) {{ Py_DECREF({var}); return NULL; }}\n"
                f"        {comp}_state_adopt(_p);\n"
                f"    }}\n"
            )
    return "".join(lines)
