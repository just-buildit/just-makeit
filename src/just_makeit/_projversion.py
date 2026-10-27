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

**`apply` reports, and deliberately never writes.** From the tree alone jm
cannot know which side is stale, and for a real project the manifest is the
likelier one: a release bumps ``pyproject.toml``, and nothing in that flow
touches ``just-makeit.toml``. Rewriting an author-owned file from a stale
manifest on the next unrelated `apply` would be worse than the drift it fixed.

That is gh-442's answer to the identical question (a manifest ``default``
against the header's own ``@param`` doc), and it is followed here rather than
re-derived: name both values, name the file, let the author pick.

**`jm config version` writes** (gh-2069), through :func:`sync`. There jm does
not have to tell which side moved: the verb is the author declaring the new
value, so every copy follows it. Before this the verb wrote the manifest and
none of the copies, and `status --check` failed on the very next command.

One table, `_slots`, answers both directions: where each file keeps its copy,
for :func:`drift` to read and :func:`sync` to write. Each slot locates the
SPAN of the one value -- exactly one, or the file is not carrying a copy jm
understands -- so the reader and the writer cannot disagree about which text
is the version: a copy `drift` reports is one `sync` can rewrite, and the
write replaces the captured value, never the line around it.

The two TOML files are located through a parser rather than a regex alone,
because ``^version = `` matches under any table and a ``[tool.*]`` section
carrying one would be reported as the project's. The other three have no
parser worth the dependency and are anchored tightly instead — each on the
key or the function it belongs to, never on the bare number.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Callable, NamedTuple

from . import _config as C
from . import _textio

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


#: ``project(<name> ... VERSION x.y.z`` in the top CMakeLists: anchored on
#: the command, so no other ``VERSION`` keyword is read as the project's (a
#: ``write_basic_package_version_file`` one, say). The value is CMake's own
#: grammar, ``major[.minor[.patch[.tweak]]]`` integers, ended by whitespace
#: or the parenthesis: a value CMake rejects at configure time is not a
#: version this file can hold, so it is neither read nor written (gh-2069).
_CMAKE_RE = re.compile(
    r"^[ \t]*(?i:project)[ \t]*\([^)]*?\bVERSION\s+"
    r"(?P<ver>[0-9]+(?:\.[0-9]+){0,3})(?![^\s)])",
    re.M,
)

#: Doxygen's own key. Anchored at column 1, like the template writes it.
_DOXY_RE = re.compile(r"^PROJECT_NUMBER\s*=\s*(?P<ver>\S+)", re.M)

#: A ``version = "..."`` line in any table, single- or double-quoted, with
#: no escape in it. A CANDIDATE only: `_toml_span` asks the parser which one
#: is ``[project]``'s.
_TOML_KEY_RE = re.compile(
    r"""^[ \t]*version[ \t]*=[ \t]*(?P<q>["'])(?P<ver>[^"'\\\n]*)(?P=q)""",
    re.M,
)


def _lib_c_re(pkg: str) -> "re.Pattern[str]":
    """``<pkg>_version(void) { return "x.y.z"; }`` in the combined-library stub.

    Built per project rather than matched loosely: the file is the author's to
    extend, and a version string in something they added is not this one.
    """
    return re.compile(
        re.escape(f"{pkg}_version") + r"\s*\(\s*void\s*\)\s*\{\s*return\s+"
        r'"(?P<ver>[^"]*)"'
    )


#: The span of a file's one copy of the version in its text, or None.
Locate = Callable[[str], "tuple[int, int] | None"]


def _regex_span(pattern: "re.Pattern[str]") -> Locate:
    """A locator reading *pattern*'s ``ver`` group, from exactly one match.

    Two matches is a file jm does not understand: reading the first would
    report a value `sync` might then write into the wrong place.
    """

    def locate(text: str) -> "tuple[int, int] | None":
        spans = [m.span("ver") for m in pattern.finditer(text)]
        return spans[0] if len(spans) == 1 else None

    return locate


def _toml_span(text: str) -> "tuple[int, int] | None":
    """The span of ``[project] version``'s value, as the parser decides it.

    Each ``version = "..."`` line is a candidate. The one that is
    ``[project]``'s is the one whose value, changed, changes the parsed
    document in exactly ``[project] version`` and nowhere else -- so the
    parser, not a guess at TOML's table rules, says which line it is. None
    unless exactly one candidate does: a dotted ``project.version`` key, a
    multi-line or escaped string, or an unparseable file is not a copy this
    module can read or write.

    Examples
    --------
    >>> text = '[tool.x]\\nversion = "1"\\n[project]\\nversion = "0.1.0"\\n'
    >>> s, e = _toml_span(text)
    >>> text[s:e]
    '0.1.0'
    >>> _toml_span('[tool.x]\\nversion = "1"\\n') is None
    True
    """
    try:
        doc = tomllib.loads(text)
    except ValueError:  # tomllib.TOMLDecodeError
        return None
    proj = doc.get("project")
    if not isinstance(proj, dict) or not isinstance(proj.get("version"), str):
        return None
    hits = []
    for m in _TOML_KEY_RE.finditer(text):
        s, e = m.span("ver")
        # Derived from the candidate, so it differs from the value it
        # replaces wherever that value is.
        probe = text[s:e] + ".jm"
        try:
            got = tomllib.loads(text[:s] + probe + text[e:])
        except ValueError:
            continue
        if got == {**doc, "project": {**proj, "version": probe}}:
            hits.append((s, e))
    return hits[0] if len(hits) == 1 else None


class _Slot(NamedTuple):
    """Where one generated file keeps its copy of the version."""

    #: Project-relative posix path.
    rel: str
    #: What holds the value, for a message naming it.
    where: str
    locate: Locate
    #: How this file spells the manifest's version. Identity for every file
    #: that takes PEP 440 as written; the CMake copy takes the release segment
    #: (gh-2084). `drift` compares, and `sync` writes, in this spelling only.
    spell: Callable[[str], str] = lambda v: v


def _slots(cfg: dict) -> "list[_Slot]":
    """Every generated copy of the version: the one table `drift` reads and
    `sync` writes.

    A file a project does not have is simply absent from the tree -- a
    ``make`` project writes no ``CMakeLists.txt`` and no ``<pkg>_lib.c`` --
    so the set a project carries is the tree's answer, never a second list.
    """
    pkg = C.project_name(cfg)
    return [
        _Slot("pyproject.toml", "[project] version", _toml_span),
        _Slot("bootstrap.toml", "[project] version", _toml_span),
        _Slot(
            "CMakeLists.txt",
            "project(VERSION)",
            _regex_span(_CMAKE_RE),
            spell=C.cmake_version,
        ),
        _Slot("Doxyfile", "PROJECT_NUMBER", _regex_span(_DOXY_RE)),
        _Slot(
            f"native/src/{pkg}_lib.c",
            f"{pkg}_version()",
            _regex_span(_lib_c_re(pkg)),
        ),
    ]


#: A version literal, as PEP 440 requires one to begin. Anything else in a
#: version slot is a value the build DERIVES — doxygen's `$(VAR)`, CMake's
#: `@VAR@` or `${VAR}`, a make-style `$(VAR)` — and there is nothing to
#: compare it against.
#:
#: gh-1204. Deriving the version is the strongest fix for the drift this
#: module exists to catch: it cannot go stale, whereas syncing the number
#: postpones the next drift to the next release. The check reported it as
#: drift anyway, and it is a `!` finding, so a project that fixed the bug
#: properly got a permanently red gate whose only remedy was to go back to a
#: hardcoded number that will drift again — inverting the incentive the check
#: exists to create, and teaching the lesson `_createonly` warns about faster
#: than a self-healing finding would.
#:
#: A whitelist rather than a list of expansion syntaxes: the question is "is
#: this a version", and every way of writing "no, it is computed" answers it
#: the same. `_CMAKE_RE` already required a leading digit and so was right by
#: construction, and `_lib_c_re` requires a quoted literal and so skipped
#: `return PKG_VERSION;` by accident. This makes the three deliberate.
_VERSION_LITERAL = re.compile(r"^[0-9]")


class _Copy(NamedTuple):
    """A slot's copy as the tree holds it now."""

    slot: _Slot
    path: Path
    text: str
    span: "tuple[int, int]"

    @property
    def value(self) -> str:
        return self.text[self.span[0] : self.span[1]]


def _copies(root: Path, cfg: dict) -> "list[_Copy]":
    """Each slot's copy that the tree carries as a literal version.

    Missing files are skipped, and so is a file jm can find no copy in at
    all — an author who has rewritten `<pkg>_lib.c` past recognition, or a
    hand-maintained CMakeLists with no ``VERSION``, is not drifting, they
    are simply not carrying a copy. So is a copy the build DERIVES (gh-1204):
    it carries no value of its own to disagree with, or to overwrite.
    """
    out: "list[_Copy]" = []
    for slot in _slots(cfg):
        path = root / slot.rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        span = slot.locate(text)
        if span is None:
            continue
        copy = _Copy(slot, path, text, span)
        if _VERSION_LITERAL.match(copy.value):
            out.append(copy)
    return out


def drift(root: Path, cfg: dict) -> "list[VersionCopy]":
    """Generated copies of the version that disagree with the manifest.

    The copies are `_copies`': a missing file, one carrying no copy jm
    can find, and one whose copy the build derives are all skipped. A false
    negative here is fine; a false positive is a gate crying wolf on a file
    it does not understand.

    The PEP 723 app script is deliberately absent from the list it checks:
    `apply` rewrites it from the manifest, so it cannot disagree by the time
    anything reads it, and reporting a file that self-heals on the next
    command is how a gate teaches people to ignore it.
    """
    expected = C.project_version(cfg)
    if not expected:
        return []
    return [
        VersionCopy(c.slot.rel, c.value, expected)
        for c in _copies(root, cfg)
        if c.value != c.slot.spell(expected)
    ]


def sync(root: Path, cfg: dict) -> "tuple[list[str], list[str]]":
    """Write ``[project] version`` into every copy `drift` would report.

    gh-2069: what `jm config version` calls, because there the author has
    declared the value and jm knows which side moved. `apply` never calls
    it, for the reason the module docstring gives.

    Each write replaces the located value and nothing else, and is kept only
    if the file then reads back exactly *the version* through the same
    locator -- so a value its slot cannot hold is never written into it.
    CMake's copy is written in CMake's spelling, the release segment, so a
    pre-release manifest version is written as its release there (gh-2084);
    what cannot be spelled at all -- an epoch, a quote in a TOML string -- is
    left as it was and named in the second list.

    Returns
    -------
    (written, unwritable) : tuple of list of str
        Project-relative paths rewritten, and the messages naming each copy
        left behind -- which `drift` still reports.

    Examples
    --------
    >>> import tempfile
    >>> root = Path(tempfile.mkdtemp())
    >>> _ = (root / "Doxyfile").write_text("PROJECT_NUMBER = 0.1.0\\n")
    >>> cfg = {"project": {"name": "p", "version": "0.2.0"}}
    >>> sync(root, cfg)
    (['Doxyfile'], [])
    >>> (root / "Doxyfile").read_text()
    'PROJECT_NUMBER = 0.2.0\\n'
    >>> drift(root, cfg)
    []
    """
    manifest = C.project_version(cfg)
    written: "list[str]" = []
    unwritable: "list[str]" = []
    for c in _copies(root, cfg):
        version = c.slot.spell(manifest)
        if c.value == version:
            continue
        s, e = c.span
        new = c.text[:s] + version + c.text[e:]
        back = c.slot.locate(new)
        if back is None or new[back[0] : back[1]] != version:
            unwritable.append(
                f"{c.slot.rel}: its {c.slot.where} cannot hold "
                f"{version!r}, so it still says {c.value!r} and "
                "`jm status --check` reports the difference"
            )
            continue
        _textio.write_text(c.path, new)
        written.append(c.slot.rel)
    return written, unwritable
