"""Which CMake target names are already claimed, and by whom (gh-1046).

A CMake target name is **global**. Two ``add_executable`` calls with the same
name are a hard configure error — not a shadow, not an override:

    CMake Error at CMakeLists.txt:647 (add_executable):
      add_executable cannot create target "bench_util_core" because another
      target with the same name already exists.

jm emits target names from two places, and both got the question wrong in the
same way: they asked what the *manifest* claims and never what the project's
own ``CMakeLists.txt`` already declares.

- gh-1034 gave a function-only module a ``test_``/``bench_<cname>_core`` pair.
  Applied to a project that had hand-registered those targets — which is what
  every consumer did *because* jm did not generate them — the configure fails.
  So the feature that removes the need for the workaround collides with the
  workaround instead of replacing it, and only projects that worked around the
  gap are broken by closing it.
- ``jm app --name X`` suffixes its target when ``X`` collides, using
  :func:`claimed`. That set was derived purely from the manifest, so a
  hand-written ``add_executable(myapp ...)`` was invisible and ``jm app
  --name myapp`` emitted a second one beside it.

Both are answered here rather than twice, because they are one question and a
second copy is what let the manifest-derived list go stale in the first place
(it listed ``test_<comp>_core`` and not ``bench_<comp>_core``, which jm has
emitted beside it all along).
"""

from __future__ import annotations

import re
from pathlib import Path

from . import _config as C

#: ``add_executable(<name> ...)`` at the start of a line. Deliberately
#: lexical and deliberately narrow: this asks "is this exact name spelled
#: here", not "what would CMake do with this file". Inferring reachability
#: through ``if()`` blocks, variables or ``include()`` would be a model of
#: CMake living in jm, tracking CMake forever — and a name spelled inside a
#: branch jm cannot evaluate is still a name that may collide, so reading it
#: as taken is the safe direction.
_ADD_EXECUTABLE_RE = re.compile(
    r"^[ \t]*add_executable[ \t]*\(\s*([A-Za-z0-9_]+)", re.M
)

#: The sentinels bounding jm's own app block in the root ``CMakeLists.txt``.
#:
#: Defined here rather than in `_app`, which imports them, because this module
#: is the one that has to know which regions of a project's CMakeLists jm owns
#: — and a second copy of a marker string is a marker that eventually does not
#: match. `_app` appends a box-drawing rule after each, so both are matched by
#: PREFIX and the rule never has to be repeated.
APP_CMAKE_SENTINEL = "# ── App ──"
APP_CMAKE_END = "# ── App end ──"


def _without_jm_app_block(text: str) -> str:
    """*text* with jm's own app block removed.

    The block is jm's to rewrite — ``_app._splice_cmake`` replaces it in place
    on every run — so the target inside it is not a claim by the project. Left
    in, a second ``jm app`` over an unchanged scaffold would find its own
    previous target, believe the name taken and suffix it, turning an
    idempotent command into ``myapp_app`` and then ``myapp_app_app``.
    """
    out, i = [], 0
    while True:
        start = text.find(APP_CMAKE_SENTINEL, i)
        if start == -1:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:start])
        end = text.find(APP_CMAKE_END, start)
        if end == -1:  # unterminated: drop the remainder, it is jm's
            return "".join(out)
        nl = text.find("\n", end)
        i = len(text) if nl == -1 else nl + 1


def declared_in_cmake(root: Path) -> frozenset[str]:
    """Executable targets the PROJECT declares in its own root CMakeLists.

    Scoped to the **root** file on purpose. jm owns every
    ``native/src/<name>/CMakeLists.txt`` and writes its own targets there, so
    scanning those would have jm find its own emission, read it as a claim by
    someone else, and stop emitting it — the target would vanish on the second
    apply. What is left is the file the project writes in, minus the one
    region of it jm rewrites.

    Parameters
    ----------
    root : Path
        The project root. A missing ``CMakeLists.txt`` yields the empty set.

    Returns
    -------
    frozenset of str
        Target names, in no particular order.

    Examples
    --------
    >>> import tempfile, pathlib
    >>> d = pathlib.Path(tempfile.mkdtemp())
    >>> _ = (d / "CMakeLists.txt").write_text(
    ...     "add_executable(bench_util_core native/benchmarks/b.c)\\n"
    ...     "target_link_libraries(bench_util_core PRIVATE util_core)\\n")
    >>> sorted(declared_in_cmake(d))
    ['bench_util_core']
    >>> sorted(declared_in_cmake(d / "nope"))
    []
    """
    cmake = root / "CMakeLists.txt"
    if not cmake.exists():
        return frozenset()
    text = _without_jm_app_block(cmake.read_text(encoding="utf-8"))
    return frozenset(_ADD_EXECUTABLE_RE.findall(text))


def from_manifest(cfg: dict) -> frozenset[str]:
    """Target names jm itself will emit for *cfg*.

    Every module ext target (its cname) and, when the module declares free
    functions, the gh-1034 ``test_``/``bench_<cname>_core`` pair; every
    component's Python ext target, its ``<comp>_core`` OBJECT lib and the
    ``test_``/``bench_<comp>_core`` pair beside it; and the top-level
    ``<pkg>_lib`` / ``<pkg>_lib_static`` aggregates (gh-184).

    ``bench_<comp>_core`` was absent from this list while jm emitted it, which
    is the reason the list now lives beside the CMake scan rather than apart
    from it: a hand-maintained roster of what jm generates goes out of date
    silently, and the only symptom is a collision nobody predicted.
    """
    pkg = C.project_name(cfg).replace("-", "_")
    reserved = {f"{pkg}_lib", f"{pkg}_lib_static"}
    for mod in C.modules(cfg):
        cname = C.module_paths(mod).cname
        reserved.add(cname)
        if C.module_functions(cfg, mod):
            reserved |= {f"test_{cname}_core", f"bench_{cname}_core"}
    for comp in C.components(cfg):
        reserved |= {
            comp,
            f"{comp}_core",
            f"test_{comp}_core",
            f"bench_{comp}_core",
        }
    return frozenset(reserved)


def claimed(cfg: dict, root: Path | None = None) -> frozenset[str]:
    """Every target name a NEW one must not reuse.

    The union of what jm will emit for *cfg* and what the project already
    declares itself. Callers choosing a fresh name (``jm app``) want this;
    a caller deciding whether to emit one of jm's OWN targets wants
    :func:`declared_in_cmake` alone, since the manifest half necessarily
    contains the very name it is asking about.
    """
    names = from_manifest(cfg)
    return names | declared_in_cmake(root) if root is not None else names
