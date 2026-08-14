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
_WIRING_ANY = re.compile(
    r"^[ \t]*target_sources\((\w+) PRIVATE \$<TARGET_OBJECTS:(\w+)>\)", re.M
)


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


def lib_targets(cmake_text: str, pkg: str) -> list[str]:
    """The combined C library targets the root CMakeLists declares.

    Read back out of the file rather than assumed: the `make` build backend
    declares none, and a project scaffolded before the combined library
    existed has only some of them. A project that declares none has nothing
    for a core to be missing from, and every question here answers empty.
    """
    return [t for t in _DECLARES_LIB.findall(cmake_text) if t.startswith(pkg)]


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


def cmake_core_wiring(cmake_text: str, pkg: str, cores: list[str]) -> str:
    """``target_sources`` lines folding each core in *cores* into every
    combined C library target the root CMakeLists declares.

    Lines already present in *cmake_text* are skipped, so every caller is
    idempotent and a second generator touching the same component adds only
    what the first left out.
    """
    lines = ""
    for core in cores:
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
    wiring = cmake_core_wiring(text, pkg, cores)
    if wiring:
        idx = text.index("\n", text.index(sub)) + 1
        text = text[:idx] + wiring + text[idx:]
    if text != original:
        cmake_path.write_text(text, encoding="utf-8")
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
    wired = {(t, c) for t, c in _WIRING_ANY.findall(text)}
    src = root / "native"
    if src.is_dir():
        for cmake in sorted(src.rglob("CMakeLists.txt")):
            wired |= set(
                _WIRING_ANY.findall(cmake.read_text(encoding="utf-8"))
            )
    found = []
    for core, comp in sorted(declared_cores(root).items()):
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
