"""
_libwiring.py — which component cores reach the project's combined C library.

A generated project ships two combined C libraries built from the same
sources, ``lib<pkg>.so`` and ``lib<pkg>.a``, and a component contributes to
them only through an explicit line in the *root* ``CMakeLists.txt``::

    target_sources(<pkg>_lib PRIVATE $<TARGET_OBJECTS:<X>_core>)

Nothing about building the component implies that line. Its OBJECT library
compiles, the Python extension links it directly, the C test links it
directly, and the whole Python suite passes with the symbol in no shipped
library at all. The only observer is a C consumer, who gets ``undefined
reference`` — which is why gh-981 survived for as long as it did, and why
this file holds the **writer and the reader together**.

They are the same fact asked in two directions:

- :func:`cmake_core_wiring` — the one emitter (gh-981). Three copies of it
  used to exist and each shipped a different subset, so which of a project's
  cores reached which library came down to the order the generators ran in.
- :func:`unwired` — the one detector (gh-984). A project could not find out
  it was affected: ``jm status --check`` exited 0 over a tree whose
  components were in no library.

Both build the line through :func:`wiring_line`, so a detector that looks for
something the emitter does not write is not expressible here. Splitting them
across two modules is what would make that possible, and this bug class has
already cost one round of exactly that.
"""

from __future__ import annotations

from . import _textio

import re
from pathlib import Path
from typing import NamedTuple

from . import _config as C


COMPONENTS_SENTINEL = "# ── Components"
MODULES_SENTINEL = "# ── Modules"

# gh-988: the READERS tolerate leading whitespace; the writer and the stripper
# do not. That asymmetry is deliberate and it is the whole fix.
#
# A project may declare a core inside a conditional — indented, as cmake style
# requires — and doppler does. Anchored at column 1, `dangling` then reported
# the root's perfectly good wiring as naming a core that does not exist, and
# `apply` DELETED it: the exact failure gh-981 was filed about, caused by the
# fix for it.
#
# Reading generously is safe: a core found is one more thing that must be
# wired, and one fewer line that looks orphaned.
#
# `_WIRING` stays anchored at column 1, but honestly: that is defence in
# depth, NOT a behaviour. The stripper only ever removes a line whose core is
# unknown, so anchoring it changes no outcome that the reader has not already
# decided — measured, by widening it and watching every test stay green. It is
# kept because jm writes these lines only at column 1, so restricting deletion
# to lines shaped like jm's own bounds the blast radius if the reader is ever
# incomplete again. A test asserting it would be decoration; the property that
# earns a test is the reader finding everything, which two now pin.
# gh-1311: `OBJECT` in this pattern is LOAD-BEARING, not incidental spelling.
#
# A `header_only` component declares `add_library(<X>_core INTERFACE)` — it has
# no `.c` file, so an OBJECT library would be a hard CMake *configure* error.
# This detector therefore does not see it, and that is the correct answer
# rather than a gap: `$<TARGET_OBJECTS:>` on an INTERFACE target is a configure
# error too, so such a core MUST NOT be wired into `lib<pkg>.so`. Asked as
# *does this contribute an out-of-line symbol*, an INTERFACE library honestly
# answers no.
#
# So gh-1311 needed no change here at all, in either direction: `unwired` does
# not report a header-only core as missing, and `cmake_core_wiring` is not
# asked to emit a line for one. Widening this to `(OBJECT|INTERFACE)` would
# manufacture the bug — every header-only component would be reported unwired,
# and `apply` would "fix" it by writing a line that fails configure.
#
# Deriving the answer from the tree (gh-988) is what makes a new component
# KIND free here; a manifest table of which cores are wired would have needed
# a row for it.
_DECLARES_CORE = re.compile(r"^[ \t]*add_library\(\s*(\w+)\s+OBJECT\b", re.M)
_DECLARES_LIB = re.compile(
    r"^[ \t]*add_library\(\s*(\w+_lib(?:_static)?)\s", re.M
)
_WIRING = re.compile(
    r"^target_sources\((\w+) PRIVATE \$<TARGET_OBJECTS:(\w+)>\)", re.M
)
# The generous counterpart, for asking "does this core reach the library at
# all" (gh-988). Indentation allowed, because a conditional wiring block is
# what a platform-gated core looks like in a hand-written CMakeLists.
# gh-1338: whitespace RUNS, not a single space. A hand-written block aligns
# its arguments -- `target_sources(doppler_lib        PRIVATE ...)` -- and the
# generous reader existed precisely to see hand-written wiring, so matching
# only jm's own single-space spelling made it blind to the case it was added
# for. jm's writer still emits one space; this only widens what is READ.
_WIRING_ANY = re.compile(
    r"^[ \t]*target_sources\(\s*(\w+)\s+PRIVATE\s+"
    r"\$<TARGET_OBJECTS:(\w+)>\s*\)",
    re.M,
)
# gh-991: `add_library(NAME SHARED|STATIC …)` — the other way objects reach a
# library, and the only way doppler's second library gets its two cores.
# MODULE is excluded on purpose (see `shipped_cores`): counting a Python
# extension would answer "shipped" for every core, forever.
_DECLARES_SHIPPED_LIB = re.compile(
    r"^[ \t]*add_library\(\s*(\w+)\s+(?:SHARED|STATIC)\b([^)]*)\)", re.M
)
_TARGET_OBJECTS = re.compile(r"\$<TARGET_OBJECTS:(\w+)>")


def wiring_line(target: str, core: str) -> str:
    """The one spelling of a combined-library wiring line.

    Every writer and every reader in jm goes through this. It is a
    one-line function on purpose: the emitter and the detector agreeing on
    the exact string is the entire property this module exists to hold.
    """
    return f"target_sources({target} PRIVATE $<TARGET_OBJECTS:{core}>)\n"


# ── Reading the tree ─────────────────────────────────────────────────────────


def component_core_libs(root: Path, comp: str) -> list[str]:
    """The OBJECT libraries ``native/src/<comp>/CMakeLists.txt`` declares.

    The root CMakeLists folds each of these into the project's combined C
    library, so this is what decides whether a component contributes any
    out-of-line symbol to ``lib<pkg>.a`` / ``.so`` at all.

    Derived from the component's own generated file rather than from the
    manifest (gh-981). A ``kind = "capsule"``/``"handle"``/``"composer"``
    module owns no core — its kernels live in a ``depends_on`` component —
    and a module whose leaf name is also one of its objects has that object's
    ``add_library`` in place of its own. Reading the file gets all three
    right without a table of which module kinds have a core, and covers a
    ``no_generate`` module's hand-written CMakeLists for free.

    Returns an empty list when the component has no CMakeLists (the `make`
    build backend, or a component not yet written).
    """
    path = root / "native" / "src" / comp / "CMakeLists.txt"
    if not path.exists():
        return []
    return _DECLARES_CORE.findall(path.read_text(encoding="utf-8"))


def declared_cores(root: Path) -> dict[str, str]:
    """Every ``<X>_core`` OBJECT library the project declares, mapped to the
    ``native/src`` directory that declares it.

    The whole-tree form of :func:`component_core_libs`, and the reason the
    detector needs no manifest: a component that exists on disk and builds a
    core is exactly what must reach a library, whether or not the manifest
    has caught up with it.
    """
    found: dict[str, str] = {}
    src = root / "native" / "src"
    if not src.is_dir():
        return found
    # gh-988: every depth, not just `native/src/*/`. A hand-owned `c_dep` is
    # free to nest, and a core this misses is one whose correct wiring `apply`
    # DELETES — the same failure as the indented declaration, reached by a
    # different route. One level was never a decision, only the shape jm's own
    # scaffolds happen to have; deriving from the tree means asking the tree.
    for cmake in sorted(src.rglob("CMakeLists.txt")):
        text = cmake.read_text(encoding="utf-8")
        for core in _DECLARES_CORE.findall(text):
            found[core] = cmake.parent.relative_to(src).as_posix()
    return found


def shipped_cores(root: Path) -> set[str]:
    """Cores that reach *some* shared or static library the project builds.

    gh-991, and the fourth reader gap of the gh-988 family. Two assumptions
    were baked in and both are wrong for a real project:

    - **`<pkg>_lib{,_static}` are not the only libraries.** doppler builds a
      second pair, `doppler_stream{,_static}`, and two cores live there and
      nowhere else. They ship; the check called them unwired.
    - **``target_sources`` is not the only way to fold objects in.** Those two
      arrive as ``add_library(NAME SHARED $<TARGET_OBJECTS:X> …)`` arguments,
      which the wiring scan does not read at all.

    ``MODULE`` is deliberately excluded, and that exclusion is the whole
    reason this cannot simply be "any target": a Python extension is a
    ``MODULE``, every core is linked into one, and counting those would make
    the check answer "yes" for every component forever — which is precisely
    the gh-981 state, a symbol reachable from Python and from no C consumer.

    ``OBJECT`` is excluded for the same reason one step earlier: an OBJECT
    library is what is being *placed*, not somewhere to place it.
    """
    shipped: set[str] = set()
    files = [root / "CMakeLists.txt"]
    if (root / "native").is_dir():
        files += sorted((root / "native").rglob("CMakeLists.txt"))
    for path in files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _DECLARES_SHIPPED_LIB.finditer(text):
            shipped |= set(_TARGET_OBJECTS.findall(m.group(2)))
    return shipped


def wired_pairs(root: Path) -> "set[tuple[str, str]]":
    """Every ``(target, core)`` the project wires, in ANY of its CMakeLists.

    gh-988's "read generously" scan, lifted out of :func:`unwired` so the
    writer can ask the same question. cmake does not care which file said a
    core reaches a library, so neither may this module: the detector has
    honoured that since gh-988 and the emitter had not, which is gh-1338.
    """
    pairs: set[tuple[str, str]] = set()
    files = [root / "CMakeLists.txt"]
    native = root / "native"
    if native.is_dir():
        files += sorted(native.rglob("CMakeLists.txt"))
    for path in files:
        if path.is_file():
            pairs |= set(_WIRING_ANY.findall(path.read_text(encoding="utf-8")))
    return pairs


def externally_wired(root: Path) -> "set[str]":
    """Cores some file OTHER than the root CMakeLists already wires.

    gh-1338. This is the precise predicate for *the project wires this
    itself, outside jm's block* -- and it is deliberately not "wired
    anywhere": a core wired only in the root is jm's own line, which the
    emitter must still own and re-emit.

    Why it matters. A component may declare its core inside a platform
    guard and fold it into both libraries there, which is what doppler does
    for a POSIX-only timing core -- specifically to keep the conditional out
    of the jm-managed region. jm then emitted its own UNGUARDED pair into the
    root, which is redundant where the guard holds and fatal where it does
    not: cmake resolves ``$<TARGET_OBJECTS:>`` at CONFIGURE time, so a
    missing target kills the whole generate step before anything compiles.

    Hand-guarding jm's pair is not available as a workaround -- the next
    `apply` re-emits the canonical unguarded pair and relocates the
    hand-written one, leaving both, and `status` then reports the file STALE
    until it is reverted.

    Note this reads the REAL tree, so it says nothing about whether the core
    *should* be wired -- only that the project has already said where. The
    "is it in a library at all" question stays :func:`unwired`'s.
    """
    root_cmake = root / "CMakeLists.txt"
    root_pairs: set[tuple[str, str]] = set()
    if root_cmake.is_file():
        root_pairs = set(
            _WIRING_ANY.findall(root_cmake.read_text(encoding="utf-8"))
        )
    return {core for _t, core in wired_pairs(root) - root_pairs}


def _is_project_core(lib: str) -> bool:
    """Whether a link item names one of the project's own cores."""
    return "::" not in lib and lib.endswith("_core")


#: A link item that is an object library's OBJECTS rather than a target.
_OBJECTS_ITEM = re.compile(r"^\s*\$<TARGET_OBJECTS:(\w+)>\s*$")


def _is_project_objects(lib: str, objects: "frozenset[str]") -> bool:
    """Whether a link item is object code this project builds.

    gh-1613: :func:`_is_project_core` recognised it by jm's ``_core`` naming
    alone, and doppler names one ``dp_interrupt_obj`` and links it as
    ``$<TARGET_OBJECTS:dp_interrupt_obj>``. Its objects reached
    ``libdoppler.so`` twice -- ``multiple definition of dp_interrupt_*`` --
    and the bare name on the archive is a target in no export set, a CMake
    GENERATE error. ``$<TARGET_OBJECTS:>`` can only name a target of this
    build, so it is always the project's; a bare name is, when the tree
    declares it (*objects*, from :func:`declared_cores`).
    """
    return (
        _is_project_core(lib)
        or bool(_OBJECTS_ITEM.match(lib))
        or lib in objects
    )


def combined_link_c(
    libs: "list[str]",
    header_only: bool,
    objects: "frozenset[str]" = frozenset(),
) -> str:
    """Restate a core's external libraries on both combined libraries.

    The root folds a core into ``lib<pkg>`` by its objects alone
    (:func:`wiring_line`), and ``$<TARGET_OBJECTS:>`` is objects, not a link
    edge: the core's ``extra_link_libs`` usage requirement is dropped on the
    floor (gh-1572). The shared library was then linked with those symbols
    undefined -- refused on macOS and Windows, accepted on Linux, where a C
    consumer of the ``.so`` failed instead -- and the archive's link
    interface did not carry them for its consumers either.

    The fix is doppler's, for the same shape with ``Threads::Threads``
    (doppler ``CMakeLists.txt``, "Threads::Threads is PUBLIC on the
    ARCHIVE"): restate the dependency on each library, ``PRIVATE`` on the
    shared one, which resolves it at link time, and ``PUBLIC`` on the
    archive, which cannot and must hand it to whoever links it. The
    installed config's ``find_dependency`` for the package that defines the
    target is the other half (``JM_FIND_DEPENDENCIES``, set in the root).

    Emitted into the COMPONENT's CMakeLists, beside the core's own link
    line, rather than into the root: that file is regenerated from the
    manifest, so the link follows the core when its ``extra_link_libs``
    change and leaves with it when it is removed, with no root line for
    ``remove`` or ``status`` to learn. CMake 3.13+ (the root requires 3.16)
    lets a directory link a target another directory declared.

    *libs* is the ``extra_link_libs`` list, never the core's full link
    line, which also names ``depends_on`` cores. Even so, an in-project core
    is dropped from it: ``extra_link_libs`` may name one (kitchen_sink's
    module links ``cjson_core``, a ``[project] c_deps`` OBJECT library). The
    root already folds its objects in, so the shared library would get them
    twice, and the archive would export a target that is in no export set --
    a CMake GENERATE error. A core is recognised by jm's own naming, the
    convention :func:`dep_core_libs` normalises to: an un-namespaced
    ``<x>_core``. An imported package target carries ``::``, and a bare
    library name (``m``, ``fftw3``) does not end in ``_core``.
    ``if(TARGET ...)`` because the ``make`` backend and a root predating the
    combined library declare none. A header-only core is never folded in
    (see ``_DECLARES_CORE``), so it needs nothing.

    Examples
    --------
    >>> print(combined_link_c(["doppler::doppler-static"], False), end="")
    ... # doctest: +ELLIPSIS
    if(TARGET ${PROJECT_NAME}_lib)
      target_link_libraries(${PROJECT_NAME}_lib PRIVATE
          doppler::doppler-static)
      target_include_directories(${PROJECT_NAME}_lib INTERFACE
    ...
    endif()
    if(TARGET ${PROJECT_NAME}_lib_static)
      target_link_libraries(${PROJECT_NAME}_lib_static PUBLIC
          doppler::doppler-static)
    endif()
    >>> combined_link_c([], False), combined_link_c(["x"], True)
    ('', '')
    >>> combined_link_c(["cjson_core"], False)
    ''
    >>> combined_link_c(["$<TARGET_OBJECTS:dp_obj>"], False)
    ''
    >>> combined_link_c(["dp_obj"], False, frozenset({"dp_obj"}))
    ''
    """
    libs = [lib for lib in libs if not _is_project_objects(lib, objects)]
    if not libs or header_only:
        return ""
    joined = "\n      ".join(libs)
    shared = (
        "if(TARGET ${PROJECT_NAME}_lib)\n"
        "  target_link_libraries(${PROJECT_NAME}_lib PRIVATE\n"
        f"      {joined})\n"
        + _compile_usage_c("${PROJECT_NAME}_lib", libs)
        + "endif()\n"
    )
    static = (
        "if(TARGET ${PROJECT_NAME}_lib_static)\n"
        "  target_link_libraries(${PROJECT_NAME}_lib_static PUBLIC\n"
        f"      {joined})\n"
        "endif()\n"
    )
    return shared + static


#: An imported target's name, and nothing else: no expression, variable,
#: path or flag (gh-1576).
_IMPORTED_TARGET = re.compile(r"[A-Za-z0-9_.+-]+(?:::[A-Za-z0-9_.+-]+)+")

#: The three properties that carry a target's COMPILE usage requirements --
#: what `$<COMPILE_ONLY:>` passes on. A dependency's header needs all three:
#: measured (gh-1576) with a header that `#error`s without its define, include
#: dirs alone failed, and definitions alone failed for a `pkg_check_modules`
#: target, which carries `-D` as an OPTION.
_COMPILE_USAGE = (
    ("target_include_directories", "INTERFACE_INCLUDE_DIRECTORIES"),
    ("target_compile_definitions", "INTERFACE_COMPILE_DEFINITIONS"),
    ("target_compile_options", "INTERFACE_COMPILE_OPTIONS"),
)


def _compile_usage_c(target: str, libs: "list[str]") -> str:
    """Hand *libs*' compile usage to *target*'s consumers, without the link.

    The shared library links a dependency PRIVATE: it resolves the symbols
    itself, and re-linking a static dependency into every consumer would
    risk a second copy of its state. But the project's own headers may
    include the dependency's, so a consumer compiling them needs its include
    dirs, definitions and options (gh-1576). ``$<COMPILE_ONLY:>`` is exactly
    this and needs CMake 3.27; the root requires 3.16, so it is spelled out
    per property. Measured in clean containers on CMake 3.16.3 and 3.28.3:
    identical to ``$<COMPILE_ONLY:>`` on every consumer face.

    Only an item that IS an imported-target name (``Ns::name``: a package's
    target, ``PkgConfig::X``) is read -- a full match, because
    ``extra_link_libs`` allows generator expressions, and
    ``$<$<PLATFORM_ID:Linux>:Ns::x>`` is empty elsewhere, which
    ``$<TARGET_EXISTS:>`` refuses just as it refuses a path. anything else in ``extra_link_libs`` is a
    library file, a ``${VAR}`` holding one, or a flag, none of which has
    usage requirements -- and a path inside ``$<TARGET_EXISTS:>`` is a hard
    configure error ("requires a non-empty valid target name"), measured on
    3.16 and 3.28. ``$<TARGET_EXISTS:>`` still guards the rest, for a
    ``::`` name the consumer never defines. The expressions are exported as
    written and evaluated on the consumer's machine, where
    ``find_dependency`` has defined the target, so nothing absolute reaches
    the export.

    Examples
    --------
    >>> _compile_usage_c("L", ["m", "${LIB}", "/p/libz.a", "-lfoo",
    ...                        "$<$<PLATFORM_ID:Linux>:x::y>"])
    ''
    >>> print(_compile_usage_c("L", ["x::y", "m"]), end="")
      target_include_directories(L INTERFACE
          $<$<TARGET_EXISTS:x::y>:$<TARGET_PROPERTY:x::y,INTERFACE_INCLUDE_DIRECTORIES>>)
      target_compile_definitions(L INTERFACE
          $<$<TARGET_EXISTS:x::y>:$<TARGET_PROPERTY:x::y,INTERFACE_COMPILE_DEFINITIONS>>)
      target_compile_options(L INTERFACE
          $<$<TARGET_EXISTS:x::y>:$<TARGET_PROPERTY:x::y,INTERFACE_COMPILE_OPTIONS>>)
    """
    libs = [lib for lib in libs if _IMPORTED_TARGET.fullmatch(lib)]
    if not libs:
        return ""
    return "".join(
        f"  {fn}({target} INTERFACE\n"
        + "\n".join(
            f"      $<$<TARGET_EXISTS:{lib}>:$<TARGET_PROPERTY:{lib},{prop}>>"
            for lib in libs
        )
        + ")\n"
        for fn, prop in _COMPILE_USAGE
    )


def lib_targets(cmake_text: str, pkg: str) -> list[str]:
    """The combined C library targets the root CMakeLists declares.

    Read back out of the file rather than assumed: the `make` build backend
    declares none, and a project scaffolded before the combined library
    existed has only some of them. A project that declares none has nothing
    for a core to be missing from, and every question here answers empty.
    """
    # gh-1600: exactly lib<pkg>'s two targets. `startswith(pkg)` also read an
    # ADDITIONAL library's `<pkg>_<name>_lib` as lib<pkg>, and every core jm
    # wires would then have been folded into it too -- the table of which
    # library a core belongs to is `[project.libraries]`, not the name.
    main = (f"{pkg}_lib", f"{pkg}_lib_static")
    return [t for t in _DECLARES_LIB.findall(cmake_text) if t in main]


def folded_pairs(root: Path) -> "set[tuple[str, str]]":
    """Every ``(library target, core)`` the project folds objects into, by
    either spelling: a ``target_sources`` line (:func:`wired_pairs`) or an
    ``add_library(NAME SHARED|STATIC $<TARGET_OBJECTS:x> ...)`` argument
    (gh-991, doppler's own). :func:`shipped_cores` asks the second spelling
    WHETHER; gh-1600's exclusivity rule needs WHICH library.
    """
    pairs = set(wired_pairs(root))
    files = [root / "CMakeLists.txt"]
    if (root / "native").is_dir():
        files += sorted((root / "native").rglob("CMakeLists.txt"))
    for path in files:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in _DECLARES_SHIPPED_LIB.finditer(text):
                pairs |= {
                    (m.group(1), c)
                    for c in _TARGET_OBJECTS.findall(m.group(2))
                }
    return pairs


#: jm's own wiring in the root: the ``target_sources`` lines it writes
#: directly under an ``add_subdirectory(native/src/X)`` it manages -- the runs
#: `apply` rebuilds from the replay (`_apply._SUBDIR_BLOCK`).
_JM_SUBDIR_RUN = re.compile(
    r"^add_subdirectory\(native/src/\w+\)[ \t]*\n"
    r"((?:^target_sources\(\w+ PRIVATE \$<TARGET_OBJECTS:\w+>\)[ \t]*\n)*)",
    re.M,
)


def _jm_wired_pairs(root: Path) -> "set[tuple[str, str]]":
    """The ``(target, core)`` pairs jm's own root wiring states -- which the
    next `apply` rewrites, so they are not the project's claim on a core."""
    path = root / "CMakeLists.txt"
    if not path.is_file():
        return set()
    runs = "".join(
        m.group(1)
        for m in _JM_SUBDIR_RUN.finditer(path.read_text(encoding="utf-8"))
    )
    return set(_WIRING.findall(runs))


def library_cores(cfg: dict) -> "set[str]":
    """Every core an additional library (gh-1600) claims -- none of which jm
    folds into lib<pkg>."""
    if not (cfg.get("project") or {}).get("libraries"):
        return set()
    return {c for lib in C.project_libraries(cfg) for c in lib.cores}


def library_tree_errors(root: Path, cfg: dict) -> "list[str]":
    """What in the REAL tree stops ``[project.libraries]`` building (gh-1600).

    A library names OBJECT libraries by target, so each must be one the tree
    declares -- ``$<TARGET_OBJECTS:>`` on a missing target is a CMake
    configure error (gh-988). And a core belongs to ONE library: one the
    project also folds into lib<pkg> would put its objects in a consumer's
    link twice, so it is refused here rather than left to the linker.
    """
    libs = C.project_libraries(cfg)
    if not libs:
        return []
    pkg = C.project_name(cfg)
    declared = declared_cores(root)
    main = {f"{pkg}_lib", f"{pkg}_lib_static"}
    # jm's own lines are not counted: a core jm wired into lib<pkg> before it
    # was claimed is unwired by the very apply this runs in (the replay
    # skips claimed cores). Counting them refused that apply forever.
    folded = folded_pairs(root) - _jm_wired_pairs(root)
    out: "list[str]" = []
    for lib in libs:
        where = f"[project.libraries.{lib.name}]"
        for core in lib.cores:
            if core not in declared:
                out.append(
                    f"{where}: `{core}` is not an OBJECT library the tree"
                    " declares (add_library(<name> OBJECT ...) under"
                    " native/src)"
                )
            both = sorted(t for t, c in folded if c == core and t in main)
            if both:
                out.append(
                    f"{where}: `{core}` is also folded into "
                    + ", ".join(both)
                    + "; a core belongs to one library -- remove that"
                    " line, or drop it from this library"
                )
    return out


def _cmake_quote(text: str) -> str:
    return (
        '"'
        + text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
        + '"'
    )


def libraries_cmake(cfg: dict) -> str:
    """The ``[project.libraries]`` section of the managed install block.

    Each additional library's two targets, folded from its cores by
    ``add_library`` arguments (the spelling :func:`shipped_cores` and
    :func:`folded_pairs` read), linked PUBLIC to lib<pkg>'s matching form --
    the CMake face of its ``.pc``'s ``Requires: <pkg>``, and what lets a
    cross-library call resolve on Mach-O -- and appended to
    ``JM_LIBRARIES``, the one list every per-library packaging rule below
    runs over. A library that declares ``platforms`` does all of that only
    there. Empty for a project that declares none.
    """
    from . import _modplatforms

    pkg = C.project_name(cfg)
    out: "list[str]" = []
    for lib in C.project_libraries(cfg):
        objs = " ".join(f"$<TARGET_OBJECTS:{c}>" for c in lib.cores)
        body = (
            f"add_library({lib.target}_lib SHARED {objs})\n"
            f"add_library({lib.target}_lib_static STATIC {objs})\n"
            f"target_link_libraries({lib.target}_lib PUBLIC {pkg}_lib)\n"
            f"target_link_libraries({lib.target}_lib_static PUBLIC"
            f" {pkg}_lib_static)\n"
            f"set(JM_LIBRARY_{lib.target}_DESCRIPTION"
            f" {_cmake_quote(lib.description)})\n"
            f'list(APPEND JM_LIBRARIES "{lib.target}:{lib.name}")\n'
        )
        if lib.platforms:
            test = _modplatforms.platform_test(lib.platforms)
            body = (
                f"if({test})\n"
                + "".join(f"  {ln}\n" for ln in body.splitlines())
                + "endif()\n"
            )
        out.append(body)
    return "".join(out)


def dep_core_libs(depends_on: list) -> list[str]:
    """``<name>_core`` for every entry of a component's ``depends_on``.

    gh-130: a caller may write ``depends_on = ["lo_core"]`` or ``["lo"]`` and
    mean the same OBJECT library, so a trailing ``_core`` is stripped before
    it is re-appended rather than doubled into ``lo_core_core``.
    """
    return [
        f"{dep[:-5] if dep.endswith('_core') else dep}_core"
        for dep in C.dep_names(depends_on)
    ]


# ── Writing it ───────────────────────────────────────────────────────────────


def cmake_core_wiring(
    cmake_text: str,
    pkg: str,
    cores: list[str],
    already_wired: "set[str] | None" = None,
) -> str:
    """``target_sources`` lines folding each core in *cores* into every
    combined C library target the root CMakeLists declares.

    Lines already present in *cmake_text* are skipped, so every caller is
    idempotent and a second generator touching the same component adds only
    what the first left out.

    gh-1338: *already_wired* is :func:`externally_wired` -- cores the project
    folds in from somewhere other than the root. jm emits nothing for those.
    Its line would duplicate the project's where the project's works, and
    would be a dangling ``$<TARGET_OBJECTS:>`` (a CONFIGURE error, not a link
    error) where the project's is inside a platform guard that is false.

    Default ``None`` rather than an empty set so a caller that cannot reach
    the tree keeps the old behaviour explicitly, instead of silently
    claiming nothing is externally wired.
    """
    skip = already_wired or set()
    lines = ""
    for core in cores:
        if core in skip:
            continue
        for target in lib_targets(cmake_text, pkg):
            line = wiring_line(target, core)
            if line not in cmake_text and line not in lines:
                lines += line
    return lines


def splice_cmake_component(
    root: Path,
    pkg: str,
    comp: str,
    cores: list[str],
    sentinel: str = COMPONENTS_SENTINEL,
) -> None:
    """Wire *comp* into the root CMakeLists: ``add_subdirectory`` under
    *sentinel*, and :func:`cmake_core_wiring` for *cores* directly beneath it.

    The two halves are independent — a module named after one of its objects
    may already have the ``add_subdirectory`` from the ``jm module`` step
    while its ``target_sources`` lines are still missing — so each is checked
    on its own.

    Keeping the wiring adjacent to the ``add_subdirectory`` is what lets
    ``_apply._SUBDIR_BLOCK`` lift the whole block as a unit when it
    reconciles a real project against a fresh replay.
    """
    cmake_path = root / "CMakeLists.txt"
    if not cmake_path.exists():
        return
    text = cmake_path.read_text(encoding="utf-8")
    original = text
    sub = f"add_subdirectory(native/src/{comp})\n"
    if sub not in text:
        if sentinel in text:
            idx = text.index("\n", text.index(sentinel)) + 1
            text = text[:idx] + sub + text[idx:]
        else:
            text += sub
    # gh-1600: a core an additional library claims is never folded into
    # lib<pkg> too. Asked of the REAL project: under `apply` this runs in a
    # temp replay whose manifest carries no `[project.libraries]` (the
    # replay's `_object._DOC_ROOT_OVERRIDE`, as gh-1046's check reads it).
    from . import _object

    real = _object._DOC_ROOT_OVERRIDE or root
    skip = externally_wired(root) | library_cores(C.load(real))
    wiring = cmake_core_wiring(text, pkg, cores, skip)
    if wiring:
        idx = text.index("\n", text.index(sub)) + 1
        text = text[:idx] + wiring + text[idx:]
    if text != original:
        _textio.write_text(cmake_path, text)
        print(f"  update  {cmake_path}")


# ── Detecting it (gh-984) ────────────────────────────────────────────────────


class Unwired(NamedTuple):
    """A component whose core reaches at least one combined library short.

    *targets* names the libraries it is missing from, so a project caught
    mid-way — gh-981's shape, where the shared library had it and the static
    archive did not — reads as the partial state it is rather than as an
    all-or-nothing failure.
    """

    core: str
    component: str
    targets: tuple[str, ...]


class Dangling(NamedTuple):
    """A wiring line naming a ``_core`` no component on disk declares."""

    core: str
    targets: tuple[str, ...]


def unwired(root: Path, cfg: dict) -> list[Unwired]:
    """Components whose OBJECT library is folded into no combined library.

    The gh-981 finding, asked of a real tree. Unlike everything else in
    ``status``, this needs no replay: it compares the project against
    *itself*, so it holds on a tree jm could not re-render. A component that
    declares a core and is named in no ``target_sources`` line is shipping a
    public header whose symbols are in no library — the exact state doppler
    was in for nine functions.

    gh-988: "named in no ``target_sources`` line" means **anywhere in the
    project**, not only in the root. jm writes its own wiring into the root,
    but a project may wire a core from the component's own CMakeLists — and
    doppler deliberately does, for a POSIX-only core, precisely to keep a
    conditional out of the jm-managed block. Reading only the root called five
    correctly-shipped cores unwired, which would gate a green project's CI.

    The scan is the same "read generously" rule as :func:`declared_cores`: the
    question is whether the symbol reaches the library, and cmake does not
    care which file said so.
    """
    cmake_path = root / "CMakeLists.txt"
    if not cmake_path.exists():
        return []
    text = cmake_path.read_text(encoding="utf-8")
    targets = lib_targets(text, C.project_name(cfg))
    if not targets:
        return []
    wired = wired_pairs(root)
    shipped = shipped_cores(root)
    found = []
    for core, comp in sorted(declared_cores(root).items()):
        # gh-991: a core that reaches SOME shared/static library is shipped,
        # and there is nothing for a reader to do about which one. The finding
        # is "the symbols are in no library"; `<pkg>_lib` is where jm would put
        # them, not the only place they may legitimately be.
        if core in shipped:
            continue
        missing = tuple(t for t in targets if (t, core) not in wired)
        if missing:
            found.append(Unwired(core, comp, missing))
    return found


def dangling(root: Path, cfg: dict) -> list[Dangling]:
    """Wiring lines naming a ``_core`` no component declares.

    The mirror of :func:`unwired`, and a strictly worse failure: CMake
    rejects a ``$<TARGET_OBJECTS:>`` naming a target that does not exist, and
    does so at **configure** time. A project in this state does not build at
    all, so it is reported next to the unwired ones rather than left for
    cmake to phrase.
    """
    cmake_path = root / "CMakeLists.txt"
    if not cmake_path.exists():
        return []
    text = cmake_path.read_text(encoding="utf-8")
    cores = declared_cores(root)
    per_core: dict[str, list[str]] = {}
    for target, core in _WIRING.findall(text):
        if core not in cores:
            per_core.setdefault(core, []).append(target)
    return [
        Dangling(core, tuple(targets))
        for core, targets in sorted(per_core.items())
    ]
