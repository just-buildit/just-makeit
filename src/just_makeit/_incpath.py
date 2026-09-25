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

_CFG_CACHE: "dict[tuple[str, int], dict]" = {}


def _cfg(owner: Owner) -> dict:
    """The manifest *owner* stands for."""
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
        manifest = d / "just-makeit.toml"
        if manifest.is_file():
            break
    else:
        # No manifest above: not a project that has declared a schema, which
        # `C.schema_version` reads as schema 1 -- the legacy layout, the only
        # one there was before a manifest could say otherwise.
        return {}
    key = (str(manifest), manifest.stat().st_mtime_ns)
    if key not in _CFG_CACHE:
        from . import _config as C

        _CFG_CACHE[key] = C.load(manifest.parent)
    return _CFG_CACHE[key]


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

    return C.schema_version(_cfg(owner)) >= PREFIXED_SCHEMA


def prefixed_owner(pkg: str) -> dict:
    """An owner for package *pkg* in the prefixed layout.

    For `jm upgrade` (gh-1583): while it moves a project, the manifest still
    says the old schema, so what the NEW spellings are is asked of this
    owner -- the layout's schema stays a fact only this module states.

    >>> prefix(prefixed_owner("p"))
    'p/'
    """
    return {"project": {"name": pkg, "schema": str(PREFIXED_SCHEMA)}}


def _pkg(owner: Owner) -> str:
    from . import _config as C

    return C.project_name(_cfg(owner))


def prefix(owner: Owner) -> str:
    """What every ``#include`` of *owner*'s own headers starts with.

    >>> prefix({"project": {"name": "my_proj", "schema": "7"}})
    ''
    >>> prefix({"project": {"name": "my_proj", "schema": "8"}})
    'my_proj/'
    """
    return f"{_pkg(owner)}/" if prefixed(owner) else ""


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
    return include(f"{comp}/{comp}_core.h", owner)


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
    return rel(f"{comp}/{comp}_core.h", owner)


def rel_glob(pattern: str) -> str:
    """A project-relative glob for headers *pattern* matches, in ANY project.

    For a path table that cannot know the package or the layout
    (`_createonly`'s rules): it matches the header at the ``-I`` root and
    under a package directory alike. fnmatch's ``*`` crosses ``/``, so a
    pattern that already starts with one needs nothing; a bare name gets a
    leading ``*`` that is empty for the old layout and ``<pkg>/`` for the
    new one.

    >>> rel_glob("*/*_core.h")
    'native/inc/*/*_core.h'
    >>> rel_glob("clib_common.h")
    'native/inc/*clib_common.h'
    """
    star = "" if pattern.startswith("*") else "*"
    return f"{INC_DIR}/{star}{pattern}"


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
    return path(root, f"{comp}/{comp}_core.h", owner)


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
