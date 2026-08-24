"""_projversion.py — the generated copies of ``[project] version``.

gh-1141. Six generated artefacts carry the project's version, all six rendered
from `_config.project_version` at scaffold time. Exactly **one** of them is
maintained afterwards: the PEP 723 app script, which is regenerated glue and
so picks up a bump on the next `apply`. The other five are create-only, and a
bump reaches none of them:

===========================================  ==========================
file                                          after a bump + ``jm apply``
===========================================  ==========================
``<name>.py`` (a ``--target pep723`` app)     rewritten
``pyproject.toml``                            unchanged
``bootstrap.toml``                            unchanged
``CMakeLists.txt`` (``project(... VERSION)``) unchanged
``Doxyfile`` (``PROJECT_NUMBER``)             unchanged
``native/src/<pkg>_lib.c`` (``<pkg>_version``) unchanged
===========================================  ==========================

The last row is the one with teeth. ``<pkg>_version()`` is a **C API** — a
consumer links the library and asks it what version it is, and is told the
version the project had on the day it was scaffolded, forever. The others are
build metadata a human tends to notice eventually; this one is a wrong answer
returned at runtime with nothing on screen.

**This module reports, and deliberately never writes.** jm cannot know which
side is stale, and for a real project the manifest is the likelier one: a
release bumps ``pyproject.toml``, and nothing in that flow touches
``just-makeit.toml``. Rewriting an author-owned file from a stale manifest on
the next unrelated `apply` would be worse than the drift it fixed.

That is gh-442's answer to the identical question (a manifest ``default``
against the header's own ``@param`` doc), and it is followed here rather than
re-derived: name both values, name the file, let the author pick. The fix is
manual and that is the point — picking the side is the part jm has no standing
to do.

The reading of the two TOML files goes through a parser rather than a regex,
because ``^version = `` matches under any table and a ``[tool.*]`` section
carrying one would be reported as the project's. The other three have no
parser worth the dependency and are anchored tightly instead — each on the
key or the function it belongs to, never on the bare number.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple

from . import _config as C

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.9/3.10 fall back to the backport
    import tomli as tomllib


class VersionCopy(NamedTuple):
    """One generated file's copy of the project version."""

    #: Project-relative posix path.
    rel: str
    #: What that file says.
    found: str
    #: What ``[project] version`` says.
    expected: str


#: ``project(<name>\n  VERSION x.y.z`` in the generated top CMakeLists.
_CMAKE_RE = re.compile(r"^\s*VERSION\s+(?P<ver>[0-9][^\s)]*)", re.M)

#: Doxygen's own key. Anchored at column 1, like the template writes it.
_DOXY_RE = re.compile(r"^PROJECT_NUMBER\s*=\s*(?P<ver>\S+)", re.M)


def _lib_c_re(pkg: str) -> "re.Pattern[str]":
    """``<pkg>_version(void) { return "x.y.z"; }`` in the combined-library stub.

    Built per project rather than matched loosely: the file is the author's to
    extend, and a version string in something they added is not this one.
    """
    return re.compile(
        re.escape(f"{pkg}_version") + r"\s*\(\s*void\s*\)\s*\{\s*return\s+"
        r'"(?P<ver>[^"]*)"'
    )


def _toml_version(path: Path) -> "str | None":
    """``[project] version`` from a TOML file, or None if absent/unreadable."""
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return None
    got = data.get("project", {}).get("version")
    return got if isinstance(got, str) else None


def _match(path: Path, pattern: "re.Pattern[str]") -> "str | None":
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    m = pattern.search(text)
    return m.group("ver") if m else None


def drift(root: Path, cfg: dict) -> "list[VersionCopy]":
    """Generated copies of the version that disagree with the manifest.

    Missing files are skipped, and so is a file jm can find no copy in at all
    — an author who has rewritten `<pkg>_lib.c` past recognition, or a
    hand-maintained CMakeLists with no ``VERSION`` line, is not drifting, they
    are simply not carrying a copy. A false negative here is fine; a false
    positive is a gate crying wolf on a file it does not understand.

    The PEP 723 app script is deliberately absent from the list it checks:
    `apply` rewrites it from the manifest, so it cannot disagree by the time
    anything reads it, and reporting a file that self-heals on the next
    command is how a gate teaches people to ignore it.
    """
    pkg = C.project_name(cfg)
    expected = C.project_version(cfg)
    if not expected:
        return []
    found: "list[VersionCopy]" = []
    checks: "list[tuple[str, object]]" = [
        ("pyproject.toml", None),
        ("bootstrap.toml", None),
        ("CMakeLists.txt", _CMAKE_RE),
        ("Doxyfile", _DOXY_RE),
        (f"native/src/{pkg}_lib.c", _lib_c_re(pkg)),
    ]
    for rel, pattern in checks:
        path = root / rel
        if not path.is_file():
            continue
        got = (
            _toml_version(path) if pattern is None else _match(path, pattern)  # type: ignore[arg-type]
        )
        if got is not None and got != expected:
            found.append(VersionCopy(rel, got, expected))
    return found
