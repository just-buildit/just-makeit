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


def core_include(comp: str, owner: Owner) -> str:
    """How an ``#include`` spells *comp*'s ``_core.h``.

    >>> core_include("gain", {"project": {"name": "my_proj", "schema": "8"}})
    'my_proj/gain/gain_core.h'
    """
    return include(f"{core_dir(comp)}/{comp}_core.h", owner)


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
    return rel(f"{core_dir(comp)}/{comp}_core.h", owner)


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
    return path(root, f"{core_dir(comp)}/{comp}_core.h", owner)


def core_dir(comp: str) -> str:
    """*comp*'s directory below the header root, POSIX-spelled.

    gh-2423. THE answer to "where does this component live in the C tree":
    its ``_core.h`` is :func:`core_rel`'s ``<header root>/<core_dir>/``,
    and in the mirrored layout its generated test and benchmark sit at the
    same ``<core_dir>`` below their own trees (:func:`native_rel`). A
    component's header does not follow its module today -- an object of
    ``[module."dsp.filters"]`` keeps ``native/inc/<pkg>/fir/`` -- so neither
    does anything that mirrors it; the day it does, it moves here and every
    mirror moves with it.

    >>> core_dir("fir")
    'fir'
    """
    return comp


#: gh-2423: ``[project] layout``. ``flat`` (the default) writes every
#: generated native test and benchmark directly in its tree; ``mirrored``
#: writes each one under the tree at its component's :func:`core_dir`, the
#: directory its header has below ``native/inc/<pkg>/``.
LAYOUTS = ("flat", "mirrored")

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


def native_dir(tree: str, comp: str, owner: Owner) -> str:
    """The directory *comp*'s file in *tree* sits in, project-relative.

    >>> native_dir(TESTS_DIR, "fir", {"project": {"name": "p"}})
    'native/tests'
    >>> o = {"project": {"name": "p", "layout": "mirrored"}}
    >>> native_dir(TESTS_DIR, "fir", o)
    'native/tests/fir'
    """
    if layout(owner) == "mirrored":
        return f"{tree}/{core_dir(comp)}"
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
    found = (
        base.rglob("*.c") if layout(owner) == "mirrored" else base.glob("*.c")
    )
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


def relocations(owner: Owner) -> "list[tuple[str, str]]":
    """``(old, new)`` for every generated native file, from where the OTHER
    layout puts it to where *owner*'s does (gh-2423).

    Read as renames by :mod:`_createonly` (gh-1472): `apply` does not create
    a file at *new* beside one at *old*, `upgrade` and `apply` move it --
    the author's bytes come along -- and `status` names *old* until it is
    gone. Both directions from one table: switching back moves them back.

    >>> o = {"project": {"name": "p", "layout": "mirrored"}, "fir": {}}
    >>> relocations(o)[0]
    ('native/tests/test_fir_core.c', 'native/tests/fir/test_fir_core.c')
    """
    cfg = manifest(owner)
    here = layout(cfg)
    other = {
        **cfg,
        "project": {
            **(cfg.get("project") or {}),
            "layout": next(v for v in LAYOUTS if v != here),
        },
    }
    return [
        (native_rel(kind, name, other), native_rel(kind, name, cfg))
        for name in native_names(cfg)
        for kind in NATIVE_FILES
    ]


#: gh-2423: the per-component slots :func:`native_slots` fills, which
#: `render()` refuses a template using without.
NATIVE_SLOTS = (
    "native_test_c",
    "native_symbols_c",
    "native_bench_c",
    "native_bench_inc",
    "native_test_inc",
    "native_test_inc_make",
)


def native_slots(owner: Owner, comp: str) -> "dict[str, str]":
    """The template slots for *comp*'s generated native test and benchmark.

    Each path is project-relative (a CMake template leads it with
    ``${CMAKE_SOURCE_DIR}/``). ``<<native_bench_inc>>`` is the benchmark
    harness's directory, always the tree's root. ``<<native_test_inc>>`` /
    ``<<native_test_inc_make>>`` add the test harness's directory to the C
    test's include path in the mirrored layout only: a flat test sits beside
    ``jm_test.h`` and a quoted include finds it there, so the flat render is
    the one jm has always written.

    >>> s = native_slots({"project": {"name": "p"}}, "fir")
    >>> s["native_test_c"], s["native_test_inc"]
    ('native/tests/test_fir_core.c', '')
    >>> s = native_slots({"project": {"name": "p", "layout": "mirrored"}},
    ...                  "fir")
    >>> s["native_bench_c"], s["native_test_inc_make"]
    ('native/benchmarks/fir/bench_fir_core.c', ' -Inative/tests')
    """
    nested = layout(owner) == "mirrored"
    return {
        "native_test_c": native_rel("test", comp, owner),
        "native_symbols_c": native_rel("symbols", comp, owner),
        "native_bench_c": native_rel("bench", comp, owner),
        "native_bench_inc": BENCH_DIR,
        "native_test_inc": (
            f"\n    ${{CMAKE_SOURCE_DIR}}/{TESTS_DIR}" if nested else ""
        ),
        "native_test_inc_make": f" -I{TESTS_DIR}" if nested else "",
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
