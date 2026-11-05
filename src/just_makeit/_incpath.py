"""Where a project's headers live, and how an ``#include`` spells them.

gh-1583. The one owner of the header layout. Two different things share the
prefix ``native/inc`` today, and the distinction is the point of this module:

- :data:`INC_DIR` is the ``-I`` directory: what the build's include path and
  the install rule name. It does not move.
- The **header root** is the directory a project's own headers are written
  into, and :func:`include` is how an ``#include`` spells one of them
  relative to :data:`INC_DIR`. Today the header root IS :data:`INC_DIR` and
  the spelling is bare (``gain/gain_core.h``). gh-1583's decision is to
  prefix it all the way -- headers under ``native/inc/<pkg>/``, included as
  ``<pkg>/gain/gain_core.h`` -- so an installed project's headers cannot
  collide with another's. A project is prefixed from manifest schema
  :data:`PREFIXED_SCHEMA` on (:func:`prefixed`): every path jm writes or
  reads, and every ``#include`` it emits, comes from here.

Every function takes an *owner*: a path inside the project, or its manifest
as a dict. Not a bare package name -- the name alone does not say which
layout the project is in.

A test (``tests/test_gh1583_include_layout.py``) refuses a hand-spelled
``native/inc`` anywhere else in jm's modules, and an ``#include`` of a
jm-generated header in a template that does not go through
``<<inc_prefix>>``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union

#: The ``-I`` directory, relative to a project root: every generated
#: ``#include`` resolves from here, in the build tree and (as ``include/``)
#: once installed. It stays put when headers move under the package.
INC_DIR = "native/inc"

#: The same directory as CMake names it in a generated ``CMakeLists.txt``.
CMAKE_INC = "${CMAKE_SOURCE_DIR}/" + INC_DIR

#: gh-1583: the manifest schema from which a project's headers live under
#: ``INC_DIR/<pkg>/`` and every ``#include`` of one is spelled ``<pkg>/...``.
#: The layout is a fact about each PROJECT, not a switch in jm: a project
#: scaffolded at an older schema keeps its layout until `jm upgrade` moves
#: it, so `apply` never writes the new layout beside the old one.
PREFIXED_SCHEMA = 8

#: A path inside the project (its root, or any file under it: the nearest
#: directory holding a manifest names it), or the project's manifest itself
#: as a dict. Never a bare package name: a name does not say which layout.
Owner = Union[Path, dict]

#: gh-2095: per manifest path, the key it was loaded under and the merged
#: manifest `C.load` built: see :func:`manifest`.
_CFG_CACHE: "dict[str, tuple[tuple, dict]]" = {}


def manifest(owner: Owner) -> dict:
    """The manifest *owner* stands for.

    Shared with :mod:`_csym` (gh-1591), whose functions take the same kind
    of owner: one answer to "which project is this", not two.

    A path owner is answered from a cache, because it is asked per include
    spelled and per symbol stemmed: 10,116 times in one `jm apply` of
    doppler, against ~130 ms for each `C.load` (measured 2026-10-08).

    gh-2095: it was keyed on the central manifest's mtime alone, but
    `C.load` merges `objects/*.toml` and `modules/*.toml` too (and reads
    `pyproject.toml` for an omitted version), so in one process a command
    that wrote only a fragment (`jm object`, `jm method` on a split
    layout) left every later reader -- `_csym`'s component test, a
    component's class name, the doc reader's methods -- on the manifest
    from before the write. What makes it stale is jm's OWN writes of TOML,
    so the key counts them (:func:`_textio.toml_writes`) rather than
    re-stamping every fragment per ask, which cost 5x here: a lookup stays
    one ``stat`` of the manifest and a dict hit. The manifest's own stamp
    stays in the key, so a hand edit of it is still seen. Inside
    `C.deferred_save`, where `C.load` serves the pending write rather than
    the disk, so does this.
    """
    if isinstance(owner, dict):
        return owner
    if isinstance(owner, str):
        raise TypeError(
            f"_incpath owner {owner!r} is a bare name: pass a path inside "
            "the project, or its manifest -- a name does not say which "
            "layout the project is in (gh-1583)"
        )
    if owner is None:
        raise TypeError(
            "_incpath owner is None: pass the project's root or manifest"
        )
    here = Path(owner).resolve()
    for d in (here, *here.parents):
        toml = d / "just-makeit.toml"
        if toml.is_file():
            break
    else:
        # No manifest above: not a project that has declared a schema, which
        # `C.schema_version` reads as schema 1 -- the legacy layout, the only
        # one there was before a manifest could say otherwise.
        return {}
    from . import _config as C
    from . import _textio

    root = toml.parent
    # `C.load` answers from the pending write inside a deferral (gh-764),
    # and every save there replaces it, so a disk stamp says nothing about
    # it. Served as is, not copied: like a cached manifest, it is shared
    # with every reader, and readers only read.
    if C._DEFERRED is not None:
        pending = C._DEFERRED.get(C._deferral_key(root))
        if pending is not None:
            return pending
    # Keyed BEFORE the load, so a write racing it leaves an older key and
    # the next ask reloads rather than trusting a newer one.
    st = toml.stat()
    key = (st.st_mtime_ns, st.st_size, _textio.toml_writes())
    hit = _CFG_CACHE.get(str(toml))
    if hit is not None and hit[0] == key:
        return hit[1]
    cfg = C.load(root)
    _CFG_CACHE[str(toml)] = (key, cfg)
    return cfg


def prefixed(owner: Owner) -> bool:
    """Whether *owner*'s headers are prefixed with its package (gh-1583).

    THE one answer: every path and spelling below asks it, and
    ``tests/test_gh1583_one_layout_question.py`` refuses the question being
    asked anywhere else.

    >>> prefixed({"project": {"name": "p", "schema": "7"}})
    False
    >>> prefixed({"project": {"name": "p", "schema": "8"}})
    True
    """
    from . import _config as C

    return C.schema_version(manifest(owner)) >= PREFIXED_SCHEMA


def prefixed_owner(pkg: str) -> dict:
    """An owner for package *pkg* in the prefixed layout.

    For `jm upgrade` (gh-1583): while it moves a project, the manifest still
    says the old schema, so what the NEW spellings are is asked of this
    owner -- the layout's schema stays a fact only this module states.

    >>> prefix(prefixed_owner("p"))
    'p/'
    """
    return {"project": {"name": pkg, "schema": str(PREFIXED_SCHEMA)}}


def layouts(pkg: str) -> "tuple[dict, dict]":
    """An owner for package *pkg* in each header layout: prefixed, then not.

    For a tree no manifest describes (gh-1895): `jm bind` on a bare
    ``native/`` has no schema to read, so where a header actually IS is the
    only evidence of the layout. The caller asks each owner where the header
    would be (:func:`core_h`) and keeps the ones that hold it -- the layout
    stays a fact only this module states, as :func:`prefixed_owner` keeps it
    for `jm upgrade`.

    >>> [core_rel("g", o) for o in layouts("p")]
    ['native/inc/p/g/g_core.h', 'native/inc/g/g_core.h']
    """
    return (
        prefixed_owner(pkg),
        {"project": {"name": pkg, "schema": str(PREFIXED_SCHEMA - 1)}},
    )


def _pkg(owner: Owner) -> str:
    from . import _config as C

    return C.project_name(manifest(owner))


def prefix(owner: Owner) -> str:
    """What every ``#include`` of *owner*'s own headers starts with.

    >>> prefix({"project": {"name": "my_proj", "schema": "7"}})
    ''
    >>> prefix({"project": {"name": "my_proj", "schema": "8"}})
    'my_proj/'
    """
    # One manifest for both questions: a path owner is a cache lookup that
    # stamps every fragment (gh-2095), and this is jm's most-asked one.
    cfg = manifest(owner)
    return f"{_pkg(cfg)}/" if prefixed(cfg) else ""


def include(name: str, owner: Owner) -> str:
    """How an ``#include`` spells *name*, one of *owner*'s own headers.

    *name* is relative to the header root: ``gain/gain_core.h``,
    ``clib_common.h``, ``my_proj.h``.

    >>> include("clib_common.h", {"project": {"name": "my_proj", "schema": "7"}})
    'clib_common.h'
    >>> include("clib_common.h", {"project": {"name": "my_proj", "schema": "8"}})
    'my_proj/clib_common.h'
    """
    return prefix(owner) + name


#: gh-2427: every header jm names after a component or a module's cname, by
#: kind, ``{}`` being the name. Each sits in the name's :func:`core_dir`, and
#: :func:`named_header` is the one spelling of where.
NAMED_HEADERS: "dict[str, str]" = {
    "core": "{}_core.h",
    "bridge": "{}_bridge.h",
    "procglobal": "{}_procglobal.h",
}


def named_header(kind: str, name: str, owner: Owner) -> str:
    """*name*'s header of *kind* (a :data:`NAMED_HEADERS` key), relative to
    the header root.

    >>> named_header("bridge", "dsp_mix", {"project": {"name": "p"}})
    'dsp_mix/dsp_mix_bridge.h'
    """
    return f"{core_dir(name, owner)}/{NAMED_HEADERS[kind].format(name)}"


def core_include(comp: str, owner: Owner) -> str:
    """How an ``#include`` spells *comp*'s ``_core.h``.

    >>> core_include("gain", {"project": {"name": "my_proj", "schema": "8"}})
    'my_proj/gain/gain_core.h'
    """
    return include(named_header("core", comp, owner), owner)


def rel(name: str, owner: Owner) -> str:
    """*name*'s path relative to the project root, POSIX-spelled.

    >>> rel("gain/gain_core.h", {"project": {"name": "my_proj", "schema": "7"}})
    'native/inc/gain/gain_core.h'
    """
    return f"{INC_DIR}/{include(name, owner)}"


def core_rel(comp: str, owner: Owner) -> str:
    """*comp*'s ``_core.h``, relative to the project root.

    >>> core_rel("gain", {"project": {"name": "my_proj", "schema": "8"}})
    'native/inc/my_proj/gain/gain_core.h'
    """
    return rel(named_header("core", comp, owner), owner)


def layout_free(rel_posix: str, owner: Owner) -> str:
    """*rel_posix* (project-relative) with the header layout taken out: a
    header's path below the ``-I`` root with any leading ``<pkg>/`` dropped,
    anything else unchanged. It names one file the same way in both
    layouts, so a replay rendered at one schema can be compared with a tree
    already moved to the other -- a tree whose headers sit under
    ``<pkg>/`` while its manifest still says an older schema (gh-1657).

    >>> o = {"project": {"name": "p"}}
    >>> layout_free("native/inc/p/fir/fir_core.h", o)
    'inc:fir/fir_core.h'
    >>> layout_free("native/inc/fir/fir_core.h", o)
    'inc:fir/fir_core.h'
    >>> layout_free("native/src/fir/fir_core.c", o)
    'native/src/fir/fir_core.c'

    An INSTALLED copy is the same header too: the ``-I`` root installs as
    ``<prefix>/include/<pkg>/``, and a prefix inside the project (``cmake
    --install build --prefix install``) holds jm's own headers again.

    >>> layout_free("install/include/p/fir/fir_core.h", o)
    'inc:fir/fir_core.h'
    """
    head = INC_DIR + "/"
    pkg = _pkg(owner) + "/"
    if rel_posix.startswith(head):
        rest = rel_posix[len(head) :]
        return "inc:" + (rest[len(pkg) :] if rest.startswith(pkg) else rest)
    installed = "include/" + pkg
    at = rel_posix.find("/" + installed)
    if rel_posix.startswith(installed) or at >= 0:
        cut = len(installed) if at < 0 else at + 1 + len(installed)
        return "inc:" + rel_posix[cut:]
    return rel_posix


def inc_dir(root: Path) -> Path:
    """The ``-I`` directory of the project at *root*."""
    return Path(root) / INC_DIR


def resolve(root: Path, spelling: str) -> Path:
    """The file ``#include "<spelling>"`` finds through the ``-I`` directory
    of the project at *root* -- *spelling* as written, by jm or the author.

    The one join onto the ``-I`` root outside this module (gh-2427): a
    component's own header is placed by :func:`core_h`, never by joining a
    name here, and ``tests/test_gh2427_nested_layout.py`` refuses that.

    >>> resolve(Path("p"), "p/fir/fir_core.h").as_posix()
    'p/native/inc/p/fir/fir_core.h'
    """
    return inc_dir(root) / spelling


def header_root(root: Path, owner: "Owner | None" = None) -> Path:
    """The directory the project at *root* writes its own headers into.

    *owner* defaults to *root* itself; pass the package name when *root* is
    a tree whose manifest is not written yet.
    """
    return inc_dir(root) / prefix(root if owner is None else owner)


def path(root: Path, name: str, owner: "Owner | None" = None) -> Path:
    """*name*, one of the project's own headers, as a path under *root*."""
    return header_root(root, owner) / name


def core_h(root: Path, comp: str, owner: "Owner | None" = None) -> Path:
    """*comp*'s ``_core.h`` under the project at *root*."""
    return path(
        root,
        named_header("core", comp, root if owner is None else owner),
        owner,
    )


def core_dir(comp: str, owner: Owner) -> str:
    """*comp*'s directory below the header root, POSIX-spelled.

    gh-2423. THE answer to "where does this component live in the C tree":
    its ``_core.h`` is :func:`core_rel`'s ``<header root>/<core_dir>/``,
    and in the mirrored and nested layouts its generated test and benchmark
    sit at the same ``<core_dir>`` below their own trees (:func:`native_rel`).

    gh-2427: in the ``nested`` layout it follows the component's module --
    ``<module package>/<comp>``, the package being
    :func:`_config.module_package_resolved`, the one answer to where the
    module's Python lives -- so the C header tree and the Python package
    read as one path. *comp* may be a module's cname as well as an object:
    a function-bearing module's own core, a composer's bridge, and the
    collocated object (which IS the module's core) all land in the module's
    directory by the same rule. Everywhere else, and for a standalone
    component, it is the component's own name.

    >>> core_dir("fir", {"project": {"name": "p"}})
    'fir'
    >>> o = {"project": {"name": "p", "layout": "nested"},
    ...      "module": {"rx.ss.burst": {"objects": ["fir"]}}, "fir": {}}
    >>> core_dir("fir", o)
    'rx/ss/burst/fir'
    >>> core_dir("rx_ss_burst", o)
    'rx/ss/burst/rx_ss_burst'
    """
    cfg = manifest(owner)
    if layout(cfg) != "nested":
        return comp
    package = _module_package_of(cfg, comp)
    return f"{package}/{comp}" if package else comp


def _module_package_of(cfg: dict, name: str) -> str:
    """The resolved package of the module *name* belongs to: the module
    listing it as an object, else the module whose cname it is; ``""`` for
    a standalone component (gh-2427)."""
    from . import _config as C

    module = C.module_of(cfg, name)
    if not module:
        module = next(
            (m for m in C.modules(cfg) if C.module_paths(m).cname == name),
            "",
        )
    return C.module_package_resolved(cfg, module) if module else ""


#: gh-2459: the tree a project's C sources sit in, project-relative. Each
#: component (or a module's cname) has its directory below it --
#: :func:`src_dir` is the one spelling of where -- and jm's own files that
#: are no component's (the ``<pkg>_lib.c`` stub, apps, a ``c_dep``) sit at
#: fixed places below it (:func:`lib_c_rel`, :data:`APP_DIR`,
#: :func:`c_dep_path`).
SRC_DIR = "native/src"

#: The directory ``jm app`` writes a C executable's ``main()`` into.
APP_DIR = f"{SRC_DIR}/app"


def _src_owner(owner: Owner) -> Owner:
    """*owner*, refused when it cannot say which project it is.

    The source directory does not depend on the layout yet, so nothing is
    loaded: this only holds every caller to passing a real owner, the one
    gh-2459 part 2 reads the layout from.
    """
    if owner is None or isinstance(owner, str):
        raise TypeError(
            f"_incpath owner {owner!r}: pass the project's root or its"
            " manifest (gh-2459)"
        )
    return owner


def src_dir(name: str, owner: Owner) -> str:
    """*name*'s C source directory, project-relative and POSIX-spelled.

    gh-2459. THE answer to "where is this component's C": its
    ``_core.c``, ``_ext.c``, CMakeLists and every file beside them sit
    here, and the root ``CMakeLists.txt`` wires it by this path
    (:func:`subdir_line`). *name* is a component or a module's cname.

    >>> src_dir("fir", {"project": {"name": "p"}})
    'native/src/fir'
    """
    _src_owner(owner)
    return f"{SRC_DIR}/{name}"


def src_path(root: Path, name: str, owner: "Owner | None" = None) -> Path:
    """:func:`src_dir` under *root*; *owner* defaults to *root*.

    ``None`` here is not a missing owner but "the project at *root*": a
    path inside the project names it, so *root* is always an owner to fall
    back on. :func:`src_dir` has no *root* to fall back on, so it refuses
    ``None`` rather than guess which project is meant.

    >>> src_path(Path("p"), "fir", {"project": {"name": "p"}}).as_posix()
    'p/native/src/fir'
    """
    return Path(root) / src_dir(name, root if owner is None else owner)


def src_rel(name: str, file: str, owner: Owner) -> str:
    """*file* in *name*'s source directory, project-relative.

    >>> src_rel("fir", "fir_core.c", {"project": {"name": "p"}})
    'native/src/fir/fir_core.c'
    """
    return f"{src_dir(name, owner)}/{file}"


def src_root(root: Path) -> Path:
    """The source tree of the project at *root*: what a scan over every
    source directory starts from. A component's own directory is
    :func:`src_path`, never a name joined onto this."""
    return Path(root) / SRC_DIR


def lib_c_rel(pkg: str) -> str:
    """The ``<pkg>_lib.c`` stub `jm new` writes, project-relative.

    >>> lib_c_rel("p")
    'native/src/p_lib.c'
    """
    return f"{SRC_DIR}/{pkg}_lib.c"


def app_path(root: Path) -> Path:
    """The directory ``jm app`` writes C executables into, under *root*."""
    return Path(root) / APP_DIR


def c_dep_path(root: Path, dep: str) -> Path:
    """A vendored ``c_dep`` directory *dep*, under *root*'s source tree.

    A ``c_dep`` is the author's C, not a component, so it never moves with
    a layout.
    """
    return src_root(root) / dep


def subdir_line(name: str, owner: Owner) -> str:
    """The root ``CMakeLists.txt`` line wiring *name*'s source directory.

    >>> subdir_line("fir", {"project": {"name": "p"}})
    'add_subdirectory(native/src/fir)\\n'
    """
    return f"add_subdirectory({src_dir(name, owner)})\n"


#: The regex for one :func:`subdir_line` (without its newline), its group
#: the name: what every reader of the root wiring matches.
SUBDIR_RE = rf"add_subdirectory\({re.escape(SRC_DIR)}/(\w+)\)"


#: gh-2423: ``[project] layout``. ``flat`` (the default) writes every
#: generated native test and benchmark directly in its tree; ``mirrored``
#: writes each one under the tree at its component's :func:`core_dir`, the
#: directory its header has below ``native/inc/<pkg>/``. gh-2427: ``nested``
#: mirrors the same way, and moves that directory itself under the
#: component's module path (:func:`core_dir`).
LAYOUTS = ("flat", "mirrored", "nested")

#: The trees jm writes a component's C test and C benchmark into. Each holds
#: its harness header at its root, in every layout: a nested file finds it
#: through the include directory the build adds (:func:`native_slots`).
TESTS_DIR = "native/tests"
BENCH_DIR = "native/benchmarks"

#: Each tree's shared harness, written once per project at the tree's root.
HARNESS = {TESTS_DIR: "jm_test.h", BENCH_DIR: "jm_bench.h"}

#: Every native file jm generates per component, by kind: its tree and its
#: file name, ``{}`` being the component (or a module's cname). The ONE
#: table every writer, reader, remover and build rule asks, through
#: :func:`native_rel`; ``tests/test_gh2423_mirrored_layout.py`` refuses a
#: spelling of one anywhere else in jm.
NATIVE_FILES: "dict[str, tuple[str, str]]" = {
    "test": (TESTS_DIR, "test_{}_core.c"),
    "symbols": (TESTS_DIR, "test_{}_symbols.c"),
    "bench": (BENCH_DIR, "bench_{}_core.c"),
}


def layout(owner: Owner) -> str:
    """*owner*'s ``[project] layout`` (gh-2423): ``flat`` unless declared.

    An unknown value is refused rather than read as the default: a typo
    would otherwise write the flat tree the author declared away.

    >>> layout({"project": {"name": "p"}})
    'flat'
    >>> layout({"project": {"name": "p", "layout": "mirrored"}})
    'mirrored'
    """
    raw = (manifest(owner).get("project") or {}).get("layout", LAYOUTS[0])
    if raw not in LAYOUTS:
        from . import _config as C

        C._refuse(
            [
                f"[project] layout {raw!r} is not one jm writes -- it is"
                f" one of {', '.join(repr(v) for v in LAYOUTS)} (gh-2423)"
            ]
        )
    return raw


def mirrors(owner: Owner) -> bool:
    """Whether *owner*'s generated native tests and benchmarks sit under
    their component's :func:`core_dir` -- every layout but ``flat``.

    >>> mirrors({"project": {"name": "p", "layout": "nested"}})
    True
    """
    return layout(owner) != "flat"


def native_dir(tree: str, comp: str, owner: Owner) -> str:
    """The directory *comp*'s file in *tree* sits in, project-relative.

    >>> native_dir(TESTS_DIR, "fir", {"project": {"name": "p"}})
    'native/tests'
    >>> o = {"project": {"name": "p", "layout": "mirrored"}}
    >>> native_dir(TESTS_DIR, "fir", o)
    'native/tests/fir'
    """
    if mirrors(owner):
        return f"{tree}/{core_dir(comp, owner)}"
    return tree


def native_rel(kind: str, comp: str, owner: Owner) -> str:
    """*comp*'s generated native file of *kind* (a :data:`NATIVE_FILES`
    key), project-relative and POSIX-spelled.

    >>> native_rel("bench", "fir", {"project": {"name": "p"}})
    'native/benchmarks/bench_fir_core.c'
    >>> o = {"project": {"name": "p", "layout": "mirrored"}}
    >>> native_rel("symbols", "fir", o)
    'native/tests/fir/test_fir_symbols.c'
    """
    tree, name = NATIVE_FILES[kind]
    return f"{native_dir(tree, comp, owner)}/{name.format(comp)}"


def native_path(
    root: Path, kind: str, comp: str, owner: "Owner | None" = None
) -> Path:
    """:func:`native_rel` under *root*; *owner* defaults to *root*."""
    return Path(root) / native_rel(
        kind, comp, root if owner is None else owner
    )


def harness_rel(tree: str) -> str:
    """*tree*'s shared harness header, project-relative.

    >>> harness_rel(BENCH_DIR)
    'native/benchmarks/jm_bench.h'
    """
    return f"{tree}/{HARNESS[tree]}"


def native_sources(root: Path, tree: str, owner: Owner) -> "list[Path]":
    """The ``.c`` files under *tree* where *owner*'s layout puts jm's files.

    Its root alone when flat -- a hand-made subdirectory is not where jm
    looks for its own files -- and every depth when mirrored. Sorted.
    """
    base = Path(root) / tree
    if not base.is_dir():
        return []
    found = base.rglob("*.c") if mirrors(owner) else base.glob("*.c")
    return sorted(p for p in found if p.is_file())


def native_names(cfg: dict) -> "list[str]":
    """Every name jm generates native files for: each component, then each
    module's cname (a function-bearing module's own test and benchmark,
    gh-1034). A collocated object shares its module's cname; once only."""
    from . import _config as C

    names = list(C.components(cfg))
    for mod in C.modules(cfg):
        cname = C.module_paths(mod).cname
        if cname not in names:
            names.append(cname)
    return names


def _with_layout(cfg: dict, value: str) -> dict:
    """*cfg* with its ``[project] layout`` replaced by *value*."""
    return {**cfg, "project": {**(cfg.get("project") or {}), "layout": value}}


def _project_root(owner: Owner) -> "Path | None":
    """The project root a path *owner* sits in; ``None`` for a manifest."""
    if isinstance(owner, dict):
        return None
    here = Path(owner).resolve()
    for d in (here, *here.parents):
        if (d / "just-makeit.toml").is_file():
            return d
    return None


def relocations(owner: Owner) -> "list[tuple[str, str]]":
    """``(old, new)`` for every generated native file and header, from
    where each OTHER layout puts it -- or where jm's own records say it
    last put it (:func:`_recorded_places`: a former ``package``, never a
    file matched by its shape) -- to where *owner*'s does (gh-2423,
    gh-2427).

    Read as renames by :mod:`_createonly` (gh-1472): `apply` does not create
    a file at *new* beside one at *old*, `upgrade` and `apply` move it --
    the author's bytes come along -- and `status` names *old* until it is
    gone. Every direction from one table: switching back moves them back.

    gh-2427: a header moves when its :func:`core_dir` does (into or out of
    the ``nested`` layout). Each header jm names after the component
    (:data:`NAMED_HEADERS`) moves, and -- when *owner* is a path, so the
    tree can be read -- every other file directly in the old directory too:
    it is the component's directory, and an author's header beside the core
    belongs with it. Files only, never a subdirectory, so an object of
    ``fir.sub`` moving ``fir/`` to ``fir/sub/fir/`` does not move a
    directory into itself; and nothing but the named headers is read from
    an old directory that is an ancestor of some component's new one,
    because what else sits there is not this component's.

    >>> o = {"project": {"name": "p", "layout": "mirrored"}, "fir": {}}
    >>> relocations(o)[0]
    ('native/tests/test_fir_core.c', 'native/tests/fir/test_fir_core.c')
    >>> o = {"project": {"name": "p", "layout": "nested", "schema": "8"},
    ...      "module": {"dsp": {"objects": ["fir"]}}, "fir": {}}
    >>> ("native/inc/p/fir/fir_core.h", "native/inc/p/dsp/fir/fir_core.h") in (
    ...     relocations(o))
    True
    """
    cfg = manifest(owner)
    here = layout(cfg)
    root = _project_root(owner)
    names = native_names(cfg)
    new_dirs = {core_dir(name, cfg) for name in names}
    others = [_with_layout(cfg, v) for v in LAYOUTS if v != here]
    seen_dirs, seen_files, weak_dirs = _recorded_places(root, cfg, names)
    out: "list[tuple[str, str]]" = []

    def add(pair: "tuple[str, str]") -> None:
        if pair[0] != pair[1] and pair not in out:
            out.append(pair)

    for name in names:
        for kind, (_tree, fname) in NATIVE_FILES.items():
            new = native_rel(kind, name, cfg)
            for other in others:
                add((native_rel(kind, name, other), new))
            for old in sorted(seen_files.get(name, ())):
                if old.rsplit("/", 1)[-1] == fname.format(name):
                    add((old, new))
        new_dir = core_dir(name, cfg)
        old_dirs = [core_dir(name, other) for other in others]
        old_dirs += sorted(seen_dirs.get(name, ()))
        for old_dir in dict.fromkeys(old_dirs):
            if old_dir == new_dir:
                continue
            files = {f.format(name) for f in NAMED_HEADERS.values()}
            old_abs = None if root is None else path(root, old_dir, cfg)
            if (
                old_abs is not None
                and old_abs.is_dir()
                and not any(d.startswith(old_dir + "/") for d in new_dirs)
            ):
                files |= {f.name for f in old_abs.iterdir() if f.is_file()}
            for f in sorted(files):
                add((rel(f"{old_dir}/{f}", cfg), rel(f"{new_dir}/{f}", cfg)))
        # A place only the sacred core's first include names moves jm's
        # named headers and nothing else, and only when the header there
        # carries this component's own guard: anything less is not jm's.
        for old_dir in sorted(weak_dirs.get(name, ())):
            if old_dir == new_dir or old_dir in old_dirs:
                continue
            if _guarded(root, cfg, old_dir, name):
                for f in sorted(
                    f.format(name) for f in NAMED_HEADERS.values()
                ):
                    add(
                        (
                            rel(f"{old_dir}/{f}", cfg),
                            rel(f"{new_dir}/{f}", cfg),
                        )
                    )
    return out


def _guarded(root: "Path | None", cfg: dict, d: str, name: str) -> bool:
    """Whether ``<d>/<name>_core.h`` under *root*'s header root opens with
    *name*'s own include guard, ``<STEM>_CORE_H`` (`_csym.upper`)."""
    from . import _csym

    if root is None:
        return False
    h = path(root, f"{d}/{NAMED_HEADERS['core'].format(name)}", cfg)
    if not h.is_file():
        return False
    m = _csym._GUARD_LINE.search(
        h.read_text(encoding="utf-8", errors="replace")
    )
    return bool(m) and m.group(1) == _csym.upper(cfg, name)


def _recorded_places(
    root: "Path | None", cfg: dict, names: "list[str]"
) -> "tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]":
    """Where jm last PUT each component's files, as its own records say
    (gh-2427).

    A component's directory moves for more reasons than a layout switch:
    under ``nested`` a module's ``package`` decides it too, and once the
    manifest's ``package`` changes nothing in the manifest says where the
    old one put the header, test and bench. Those places are not guessed by
    matching file names in the tree -- a vendored copy of another jm
    project has exactly jm's shapes, and is not this project's. They are
    read from the files jm wrote naming them, which still name them until
    the run that moves them re-renders:

    - ``native/src/<name>/CMakeLists.txt``, jm's render: the sources of
      jm's own ``test_<name>_core`` / ``bench_<name>_core`` targets, whose
      directory below its tree, when there is one, is the component's
      directory in the mirroring layout that wrote it (the include
      directory there says the same, so it is not read twice);
    - under ``nested`` only, ``native/src/<name>/<name>_core.c``, scaffolded
      including its own header first: that first ``#include``, when it
      names ``<name>_core.h``. The file is sacred, so this is the author's
      content and a weaker word: returned apart, as the third map. In flat
      and mirrored a package moves nothing, so it is never read there.

    Only paths spelled for *name* itself, by its own files. Returns
    ``({name: {header dir}}, {name: {project-relative native file}},
    {name: {header dir known only from the core's first include}})``, each
    header dir below the header root; all empty for a manifest owner, and
    for a project whose headers are not under its package yet (an
    unprefixed header root may hold a half-upgraded ``<pkg>/`` tree,
    gh-1583: `upgrade` moves that).
    """
    import re

    dirs: "dict[str, set[str]]" = {}
    files: "dict[str, set[str]]" = {}
    weak: "dict[str, set[str]]" = {}
    if root is None or not prefixed(cfg):
        return dirs, files, weak
    nested = layout(cfg) == "nested"
    pre = re.escape(prefix(cfg))
    src = Path(root) / "native" / "src"
    for name in names:
        n = re.escape(name)
        here = src / name
        cmake = here / "CMakeLists.txt"
        text = cmake.read_text(encoding="utf-8") if cmake.is_file() else ""
        kinds = "|".join(
            re.escape(f.format(name)) for _t, f in NATIVE_FILES.values()
        )
        for m in re.finditer(
            r"\$\{CMAKE_SOURCE_DIR\}/((?:"
            + "|".join(re.escape(t) for t in HARNESS)
            + rf")(?:/[\w.-]+)*/(?:{kinds}))(?![\w.])",
            text,
        ):
            files.setdefault(name, set()).add(m.group(1))
            # A test or bench below its tree's root was written by a
            # mirroring layout, at the component's directory then.
            sub = m.group(1).split("/", 2)[2].rsplit("/", 1)
            if (
                len(sub) == 2
                and (sub[0] + "/").endswith(f"/{name}/")
                or (len(sub) == 2 and sub[0] == name)
            ):
                dirs.setdefault(name, set()).add(sub[0])
        # The sacred core's first include is the AUTHOR's content, not a
        # record jm owns: read only under nested, where a package can move a
        # module core whose CMakeLists names no include directory, and kept
        # apart -- :func:`relocations` trusts it for the named headers only,
        # behind the component's own guard.
        core = here / f"{name}_core.c"
        if nested and core.is_file():
            first = re.search(
                r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"',
                core.read_text(encoding="utf-8", errors="replace"),
                re.M,
            )
            got = first and re.fullmatch(
                rf"{pre}((?:[\w.-]+/)*{n})/{n}_core\.h", first.group(1)
            )
            if got:
                weak.setdefault(name, set()).add(got.group(1))
    return dirs, files, weak


#: gh-2423: the per-component slots :func:`native_slots` fills, which
#: `render()` refuses a template using without.
NATIVE_SLOTS = (
    "native_test_c",
    "native_symbols_c",
    "native_bench_c",
    "native_bench_inc",
    "native_test_inc",
    "native_test_inc_object",
    "native_test_inc_make",
    "core_include",
    "core_dir",
    "src_dir",
)


def _cmake_continuation(column: int) -> str:
    """The test harness's directory as one more ``PRIVATE`` include,
    aligned under the first at *column* (gh-2429)."""
    return "\n" + " " * column + f"${{CMAKE_SOURCE_DIR}}/{TESTS_DIR}"


def native_slots(owner: Owner, comp: str) -> "dict[str, str]":
    """The template slots for *comp*'s generated native test and benchmark.

    Each path is project-relative (a CMake template leads it with
    ``${CMAKE_SOURCE_DIR}/``). ``<<native_bench_inc>>`` is the benchmark
    harness's directory, always the tree's root. ``<<native_test_inc>>`` /
    ``<<native_test_inc_make>>`` add the test harness's directory to the C
    test's include path in the mirrored layout only: a flat test sits beside
    ``jm_test.h`` and a quoted include finds it there, so the flat render is
    the one jm has always written. ``<<native_test_inc_object>>`` is the
    same line for the object-core template, whose ``PRIVATE`` sits further
    right: each is aligned under the include it follows.

    >>> s = native_slots({"project": {"name": "p"}}, "fir")
    >>> s["native_test_c"], s["native_test_inc"]
    ('native/tests/test_fir_core.c', '')
    >>> s = native_slots({"project": {"name": "p", "layout": "mirrored"}},
    ...                  "fir")
    >>> s["native_bench_c"], s["native_test_inc_make"]
    ('native/benchmarks/fir/bench_fir_core.c', ' -Inative/tests')
    """
    nested = mirrors(owner)
    return {
        "native_test_c": native_rel("test", comp, owner),
        "native_symbols_c": native_rel("symbols", comp, owner),
        "native_bench_c": native_rel("bench", comp, owner),
        "native_bench_inc": BENCH_DIR,
        # Aligned under the `${CMAKE_SOURCE_DIR}` that follows `PRIVATE`.
        "native_test_inc": _cmake_continuation(12) if nested else "",
        "native_test_inc_object": _cmake_continuation(35) if nested else "",
        "native_test_inc_make": f" -I{TESTS_DIR}" if nested else "",
        # gh-2427: how an ``#include`` spells the component's ``_core.h``,
        # and its directory below the header root (a CMake template leads
        # it with ``native/inc/<<inc_prefix>>``).
        "core_include": core_include(comp, owner),
        "core_dir": core_dir(comp, owner),
        # gh-2459: the component's C source directory, project-relative.
        "src_dir": src_dir(comp, owner),
    }


#: The template slots whose value depends on which layout a project is in.
#: `render()` refuses a template that uses one when its context lacks it.
PROJECT_SLOTS = ("inc_prefix",)


def layout_slots(ctx: dict) -> "dict[str, str]":
    """The layout slot every project shares, filled under *ctx* by `render()`.

    Only ``<<inc_dir>>``: the per-project slots (:data:`PROJECT_SLOTS`) come
    from :func:`ctx_slots`, and `render()` refuses a template needing one
    that its context does not carry.
    """
    return {"inc_dir": INC_DIR}


def ctx_slots(owner: Owner) -> "dict[str, str]":
    """The layout's template slots for a render of *owner*'s files.

    ``<<inc_prefix>>`` leads every ``#include`` of a jm-generated header.

    ``jm_simd.h`` and ``jm_perf.h`` keep ONE include guard in every layout:
    they define fixed-name inline functions and macros each ``_core.h``
    calls, so a per-package guard would define them twice in a translation
    unit that includes two packages (gh-1583; version skew is gh-1606).

    >>> ctx_slots({"project": {"name": "p", "schema": "8"}})
    {'inc_dir': 'native/inc', 'inc_prefix': 'p/'}
    """
    return {"inc_dir": INC_DIR, "inc_prefix": prefix(owner)}
