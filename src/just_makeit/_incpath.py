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
  collide with another's. That is :data:`PREFIXED`, and flipping it is the
  whole of the layout change: every path jm writes or reads, and every
  ``#include`` it emits, comes from here.

Every function takes an *owner*: the package name, or a project root whose
manifest names it. The name is only read when :data:`PREFIXED` is on, so an
unprefixed layout never touches a manifest -- including a scratch tree that
has not written one yet.

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

#: gh-1583: headers live under ``INC_DIR/<pkg>/`` and every ``#include`` of
#: one is spelled ``<pkg>/...``. Off until the layout change lands.
PREFIXED = False

Owner = Union[str, Path]

_PKG_CACHE: "dict[tuple[str, int], str]" = {}


def _pkg(owner: Owner) -> str:
    """The package name *owner* stands for.

    A string is the name. A path is the project root, or any path inside the
    project: the nearest directory holding a manifest names it -- which is
    what lets a writer that knows only the file it writes (an umbrella
    header, a ``_core.c``) spell that project's includes.
    """
    if isinstance(owner, str):
        return owner
    here = Path(owner).resolve()
    for d in (here, *here.parents):
        manifest = d / "just-makeit.toml"
        if manifest.is_file():
            break
    else:
        raise FileNotFoundError(f"no just-makeit.toml at or above {owner}")
    key = (str(manifest), manifest.stat().st_mtime_ns)
    if key not in _PKG_CACHE:
        from . import _config as C

        _PKG_CACHE[key] = C.project_name(C.load(manifest.parent))
    return _PKG_CACHE[key]


def prefix(owner: Owner) -> str:
    """What every ``#include`` of *owner*'s own headers starts with.

    >>> prefix("my_proj")
    ''
    """
    return f"{_pkg(owner)}/" if PREFIXED else ""


def include(name: str, owner: Owner) -> str:
    """How an ``#include`` spells *name*, one of *owner*'s own headers.

    *name* is relative to the header root: ``gain/gain_core.h``,
    ``clib_common.h``, ``my_proj.h``.

    >>> include("clib_common.h", "my_proj")
    'clib_common.h'
    """
    return prefix(owner) + name


def core_include(comp: str, owner: Owner) -> str:
    """How an ``#include`` spells *comp*'s ``_core.h``.

    >>> core_include("gain", "my_proj")
    'gain/gain_core.h'
    """
    return include(f"{comp}/{comp}_core.h", owner)


def rel(name: str, owner: Owner) -> str:
    """*name*'s path relative to the project root, POSIX-spelled.

    >>> rel("gain/gain_core.h", "my_proj")
    'native/inc/gain/gain_core.h'
    """
    return f"{INC_DIR}/{include(name, owner)}"


def core_rel(comp: str, owner: Owner) -> str:
    """*comp*'s ``_core.h``, relative to the project root.

    >>> core_rel("gain", "my_proj")
    'native/inc/gain/gain_core.h'
    """
    return rel(f"{comp}/{comp}_core.h", owner)


def rel_glob(pattern: str) -> str:
    """A project-relative glob for headers *pattern* matches, in ANY project.

    For a path table that cannot know the package (`_createonly`'s rules):
    under :data:`PREFIXED` the package's directory is a wildcard.

    >>> rel_glob("*/*_core.h")
    'native/inc/*/*_core.h'
    """
    return f"{INC_DIR}/{'*/' if PREFIXED else ''}{pattern}"


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


def layout_slots(ctx: dict) -> "dict[str, str]":
    """The template slots the layout owns, for a render of *ctx*.

    ``<<inc_dir>>`` is :data:`INC_DIR`; ``<<inc_prefix>>`` leads every
    ``#include`` of a jm-generated header. The package is the context's
    ``project_underscore`` (or ``package``) when :data:`PREFIXED` needs one.
    """
    if not PREFIXED:
        return {"inc_dir": INC_DIR, "inc_prefix": ""}
    pkg = ctx.get("project_underscore") or ctx.get("package") or ""
    return {"inc_dir": INC_DIR, "inc_prefix": prefix(pkg) if pkg else ""}
