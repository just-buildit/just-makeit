"""_pythons.py -- the Python versions a generated project supports.

gh-2629. A generated project's supported Pythons live in ONE place, its own
``pyproject.toml``: the ``Programming Language :: Python :: 3.N`` trove
classifiers under ``[project] classifiers``, bounded by ``[project]
requires-python``. Everything else that names a version is rendered from
there.

- ``jm new`` writes the classifiers, and ``requires-python``, from jm's OWN
  installed metadata (:func:`jm_supported`, :func:`jm_requires_python`): a
  new project starts out supporting what jm supports, and from then on the
  list is the project's. The README's ``Python 3.N+`` line is the project's
  floor, read back from the ``pyproject.toml`` just written (:func:`floor`).
- The CI ``jm ci`` writes takes its Pythons from that list
  (:func:`ci_versions`): the GitHub ``python-version`` matrix is every
  classifier ``requires-python`` admits, and the Woodpecker image is the
  oldest of them. Every ``jm apply`` rewrites both in an existing workflow
  to match (:func:`sync_ci`). The CI stays static YAML -- nothing parses
  ``pyproject.toml`` at run time. Because `status` replays `apply`, a CI
  behind its classifiers reads as STALE until then.

**A classifier ``requires-python`` refuses** (``>=3.9,<3.14`` beside a
``3.15`` classifier) gets no CI leg: pip would refuse to install the project
on it, so the leg could only fail. jm says which, once per command, rather
than choosing which of the two declarations the author meant.

**A project with no version classifiers** -- one scaffolded before this, or
one whose author removed them -- is left alone: ``pyproject.toml`` is the
author's file (`_createonly` classifies it ``AUTHOR``), and jm writes into
an author file only through a verb that declares the value. Its CI follows
jm's own list instead, and the command that renders it says once, in one
line, to add the classifiers (:func:`fallback_note`).

The CI write follows `_projversion`'s rule: it locates the SPAN of the one
value, from exactly one match, and replaces that value and nothing around
it. A ``python-version`` list may be spelled as a YAML flow list on one line
or several, or as a block list; a workflow with no such list, or more than
one, is not a file whose list jm can name. It is never written, and
:func:`ci_plan` says so -- `apply` warns, and `status` reports it under
``CI PYTHONS`` -- rather than leaving a CI that no longer follows the
classifiers looking current.
"""

from __future__ import annotations

import functools
import re
import sys
from importlib import metadata
from pathlib import Path
from typing import Callable, NamedTuple

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from . import _textio

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.9/3.10 fall back to the backport
    import tomli as tomllib

#: The classifier prefix a version is spelled after.
CLASSIFIER = "Programming Language :: Python :: "

#: Where `jm ci` writes the GitHub workflow, and `apply` keeps its list.
CI_REL = ".github/workflows/ci.yml"

#: Where `jm ci --provider woodpecker` writes its pipeline.
WOODPECKER_REL = ".woodpecker.yml"

_VERSION_RE = re.compile(r"^" + re.escape(CLASSIFIER) + r"(3\.\d+)$")

#: A ``python-version:`` key, whatever its value; the value starts at ``v``.
_KEY_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:-[ \t]+)?python-version:[ \t]*(?P<v>)", re.M
)

#: A flow list -- one line or several, no nested brackets -- with an
#: optional trailing comment, starting where the key's value does.
_FLOW_RE = re.compile(r"\[[^\[\]]*\]")

#: One block-list item line under the key.
_ITEM_RE = re.compile(r"(?P<indent>[ \t]*)-[ \t]+(?P<item>\S[^#\n]*?)[ \t]*$")

#: The Woodpecker image's version: ``python:3.N`` before a ``-`` or the end.
_IMAGE_RE = re.compile(
    r"^[ \t]*image:[ \t]*python:(?P<v>3\.\d+)(?=[-\s]|$)", re.M
)

#: A minor version's latest patch, which the CI installs and so the one
#: ``requires-python`` is asked about: ``<3.14`` refuses 3.14 because it
#: refuses 3.14.N, and ``>=3.9.2`` admits 3.9 because 3.9's latest patch is
#: above it.
_LATEST_PATCH = 999


@functools.lru_cache(maxsize=None)
def _jm_metadata() -> "metadata.PackageMetadata":
    """jm's installed metadata, read once per process.

    Uncached, every read walks ``sys.path``; on 3.9 importlib does not
    cache that search, and under pytest (whose ``sys.path`` grows by an
    entry per test module that inserts ``src/``) one read cost ~0.6 s --
    four per ``jm new`` timed every 3.9 CI leg out (#2633). Installed
    metadata does not change within a process, so one read serves all.
    """
    return metadata.metadata("just-makeit")


def versions(classifiers: "list[str]") -> "list[str]":
    """The ``3.N`` versions *classifiers* declare, oldest first.

    Only the minor-version classifiers count: ``Python :: 3`` and
    ``Python :: 3 :: Only`` say nothing about which interpreters to test.

    >>> versions([CLASSIFIER + "3.10", CLASSIFIER + "3", CLASSIFIER + "3.9"])
    ['3.9', '3.10']
    """
    found = {m.group(1) for c in classifiers if (m := _VERSION_RE.match(c))}
    return sorted(found, key=lambda v: tuple(int(p) for p in v.split(".")))


def jm_supported() -> "list[str]":
    """The versions jm itself supports, from its installed metadata.

    The one source a new project's list is copied from; jm's source holds
    no list of its own.
    """
    return versions(_jm_metadata().get_all("Classifier") or [])


def jm_requires_python() -> str:
    """jm's own ``Requires-Python``, which a new project starts with."""
    return str(_jm_metadata()["Requires-Python"])


def classifier_lines(vs: "list[str]") -> str:
    """The TOML array body a new ``pyproject.toml`` lists *vs* in.

    >>> print(classifier_lines(["3.9", "3.10"]))
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    """
    return "\n".join(f'    "{CLASSIFIER}{v}",' for v in vs)


def project_table(root: Path) -> dict:
    """The ``[project]`` table of *root*'s ``pyproject.toml``.

    The one reader of that file here. Empty when there is no
    ``pyproject.toml``, it does not parse, or it has no ``[project]`` table
    -- each of which means the project has not said.
    """
    try:
        with (root / "pyproject.toml").open("rb") as f:
            doc = tomllib.load(f)
    except (OSError, ValueError):
        return {}
    proj = doc.get("project")
    return proj if isinstance(proj, dict) else {}


def declared(root: Path) -> "list[str]":
    """The versions the project's ``pyproject.toml`` classifiers declare.

    Empty when the project declares no ``3.N`` classifier -- which means
    the project has not said, never that it supports nothing.
    """
    cls = project_table(root).get("classifiers")
    if not isinstance(cls, list):
        return []
    return versions([c for c in cls if isinstance(c, str)])


def _spec(root: Path) -> "SpecifierSet | None":
    """The project's ``requires-python``, or None when it states none or
    states one that does not parse (it then constrains nothing jm can
    read)."""
    rp = project_table(root).get("requires-python")
    if not isinstance(rp, str):
        return None
    try:
        return SpecifierSet(rp)
    except InvalidSpecifier:
        return None


def admits(spec: "SpecifierSet | None", version: str) -> bool:
    """Whether *spec* admits ``3.N`` *version*, asked of its latest patch.

    >>> admits(SpecifierSet(">=3.9,<3.14"), "3.13")
    True
    >>> admits(SpecifierSet(">=3.9,<3.14"), "3.14")
    False
    >>> admits(SpecifierSet(">=3.9.2"), "3.9")
    True
    >>> admits(None, "3.8")
    True
    """
    return spec is None or spec.contains(f"{version}.{_LATEST_PATCH}")


def floor(root: Path) -> "str | None":
    """The oldest ``3.N`` the project's ``requires-python`` admits.

    None when it states no ``requires-python`` jm can read.

    >>> import tempfile
    >>> root = Path(tempfile.mkdtemp())
    >>> _ = (root / "pyproject.toml").write_text(
    ...     '[project]\\nrequires-python = ">=3.10"\\n'
    ... )
    >>> floor(root)
    '3.10'
    """
    spec = _spec(root)
    if spec is None:
        return None
    return next((f"3.{n}" for n in range(100) if admits(spec, f"3.{n}")), None)


class CiPythons(NamedTuple):
    """What a project's CI tests, and why.

    Attributes
    ----------
    versions : list of str
        The legs: the classifiers ``requires-python`` admits, oldest first.
    fallback : bool
        True when the project declares no classifiers and these are jm's.
    refused : list of str
        Classifiers ``requires-python`` refuses, which get no leg.
    """

    versions: "list[str]"
    fallback: bool
    refused: "list[str]"


def ci_versions(root: Path) -> CiPythons:
    """The versions the project's CI tests.

    The project's own classifiers when it declares any, jm's list
    otherwise; either way only those its ``requires-python`` admits.
    """
    own = declared(root)
    listed = own or jm_supported()
    spec = _spec(root)
    return CiPythons(
        versions=[v for v in listed if admits(spec, v)],
        fallback=not own,
        refused=[v for v in listed if not admits(spec, v)],
    )


def yaml_list(vs: "list[str]") -> str:
    """*vs* as the YAML flow list a matrix takes.

    >>> yaml_list(["3.9", "3.10"])
    '["3.9", "3.10"]'
    """
    return "[" + ", ".join(f'"{v}"' for v in vs) + "]"


def fallback_note() -> str:
    """The one line a project without classifiers is told."""
    return (
        "pyproject.toml declares no `Programming Language :: Python :: 3.N` "
        "classifiers, so the CI tests jm's own Python list; add the "
        "classifiers to choose your own"
    )


def refused_note(refused: "list[str]") -> str:
    """The one line a classifier ``requires-python`` refuses is named in."""
    return (
        "pyproject.toml classifies Python "
        + ", ".join(refused)
        + " but its requires-python refuses "
        + ("it" if len(refused) == 1 else "them")
        + ", so the CI has no leg for "
        + ("it" if len(refused) == 1 else "them")
        + "; drop the classifier or widen requires-python"
    )


def _quote(sample: str) -> str:
    """The quote character an existing item is spelled with, if any."""
    return sample[0] if sample[:1] in ("'", '"') else ""


#: Renders the versions into a list's own spelling.
_Render = Callable[["list[str]"], str]


def _list_span(text: str, at: int) -> "tuple[int, int, _Render] | None":
    r"""The list value starting at *at*: its span, and how to respell it.

    The renderer keeps the author's shape and quoting: a flow list on one
    line, a flow list one item per line, or a block list. None when the
    value at *at* is not a list (a scalar, an expression).

    >>> t = 'k:\n  python-version:\n    - "3.9"\n    - "3.10"\n  os: x\n'
    >>> s, e, r = _list_span(t, t.index("python-version:") + 15)
    >>> print(t[:s] + r(["3.11"]) + t[e:], end="")
    k:
      python-version:
        - "3.11"
      os: x
    """
    rest = text[at:]
    m = _FLOW_RE.match(rest)
    if m:
        body = m.group(0)[1:-1]
        items = [i.strip() for i in body.split(",") if i.strip()]
        q = _quote(items[0]) if items else '"'
        if "\n" not in body:
            return (
                at,
                at + m.end(),
                lambda vs: "[" + ", ".join(f"{q}{v}{q}" for v in vs) + "]",
            )
        # One item per line: keep the items' indent, and the closing
        # bracket's line, as the author wrote them.
        first = re.search(r"\n([ \t]*)\S", body)
        ind = first.group(1) if first else ""
        close = body[body.rfind("\n") + 1 :]
        return (
            at,
            at + m.end(),
            lambda vs: (
                "[" + "".join(f"\n{ind}{q}{v}{q}," for v in vs) + f"\n{close}]"
            ),
        )
    # A block list: nothing after the key but a comment, then `- item`
    # lines at one indent.
    eol = rest.find("\n")
    head = rest[:eol].strip() if eol != -1 else "?"
    if head and not head.startswith("#"):
        return None
    start = end = at + eol + 1
    q, indent = '"', None
    for line in text[start:].splitlines(keepends=True):
        im = _ITEM_RE.match(line.rstrip("\n"))
        if not im or (indent is not None and im.group("indent") != indent):
            break
        if indent is None:
            indent, q = im.group("indent"), _quote(im.group("item"))
        end += len(line)
    if indent is None:
        return None
    ind = indent
    return (
        start,
        end,
        lambda vs: "".join(f"{ind}- {q}{v}{q}\n" for v in vs),
    )


def _github_span(text: str) -> "tuple[int, int, _Render] | str":
    r"""The workflow's one ``python-version`` list, or why there is none.

    >>> t = 'matrix:\n  python-version: ["3.9"]\n'
    >>> s, e, r = _github_span(t)
    >>> t[s:e], r(["3.10", "3.11"])
    ('["3.9"]', '["3.10", "3.11"]')
    >>> _github_span(t + t)
    'it has 2 `python-version` lists'
    """
    spans = [
        sp
        for k in _KEY_RE.finditer(text)
        if (sp := _list_span(text, k.end("v"))) is not None
    ]
    if len(spans) == 1:
        return spans[0]
    if not spans:
        return "it has no `python-version` list"
    return f"it has {len(spans)} `python-version` lists"


def _woodpecker_span(text: str) -> "tuple[int, int, _Render] | str":
    r"""The pipeline's one ``image: python:3.N`` version, or why not.

    It runs one interpreter: the oldest leg, the one most likely to break
    and the one surely released.

    >>> t = "steps:\n  test:\n    image: python:3.12-bookworm\n"
    >>> s, e, r = _woodpecker_span(t)
    >>> t[s:e], r(["3.10", "3.11"])
    ('3.12', '3.10')
    """
    found = [m.span("v") for m in _IMAGE_RE.finditer(text)]
    if len(found) != 1:
        return f"it has {len(found)} `image: python:3.N` lines"
    s, e = found[0]
    return s, e, lambda vs: vs[0]


#: Every CI file jm keeps Pythons in: (path, locator). The ONE table both
#: the writer (:func:`sync_ci`) and the reader (`status`, through
#: :func:`ci_plan`) walk.
CI_FILES: "tuple[tuple[str, Callable[[str], object]], ...]" = (
    (CI_REL, _github_span),
    (WOODPECKER_REL, _woodpecker_span),
)


class CiPlan(NamedTuple):
    """What `apply` would do to one CI file.

    Attributes
    ----------
    rel : str
        The file, relative to the project root.
    text : str or None
        Its content after the rewrite; None when it is not written (already
        current, or jm cannot name its value).
    notes : list of str
        The lines to tell the author, once each.
    unlocated : str or None
        Why jm cannot name the file's Python value, when it cannot -- the
        finding `status` reports under ``CI PYTHONS``.
    """

    rel: str
    text: "str | None"
    notes: "list[str]"
    unlocated: "str | None"


def ci_plan(root: Path) -> "list[CiPlan]":
    """One :class:`CiPlan` per CI file the project carries.

    Reads only; :func:`sync_ci` writes what this plans.
    """
    present = [(rel, loc) for rel, loc in CI_FILES if (root / rel).is_file()]
    if not present:
        return []
    cp = ci_versions(root)
    notes = [fallback_note()] if cp.fallback else []
    if cp.refused:
        notes.append(refused_note(cp.refused))
    plans = []
    for rel, locate in present:
        text = (root / rel).read_text(encoding="utf-8")
        found = locate(text)
        if isinstance(found, str):
            why = (
                f"{rel}: {found}, so jm cannot keep its Pythons equal to "
                "pyproject.toml's classifiers; spell the one list jm "
                f"writes, or name {rel} in [project] status_allow"
            )
            plans.append(CiPlan(rel, None, [*notes, why], why))
            continue
        s, e, render = found  # type: ignore[misc]
        if not cp.versions:
            plans.append(CiPlan(rel, None, notes, None))
            continue
        want = render(cp.versions)
        new = None if text[s:e] == want else text[:s] + want + text[e:]
        plans.append(CiPlan(rel, new, notes, None))
    return plans


def sync_ci(root: Path, skip: "Callable[[str], bool]" = lambda rel: False):
    r"""Rewrite each CI file's Pythons from the classifiers.

    Writes only the located value, and only when it differs; a file whose
    value jm cannot name is not touched. *skip* names a file not to write
    (one ``status_allow`` exempts).

    Returns
    -------
    (written, notes) : tuple of list
        The files rewritten, relative to *root*, and the lines to tell the
        author, each once.

    Examples
    --------
    >>> import tempfile
    >>> root = Path(tempfile.mkdtemp())
    >>> _ = (root / "pyproject.toml").write_text(
    ...     '[project]\nclassifiers = ["' + CLASSIFIER + '3.12"]\n'
    ... )
    >>> ci = root / CI_REL
    >>> ci.parent.mkdir(parents=True)
    >>> _ = ci.write_text('    python-version: ["3.9", "3.12"]\n')
    >>> sync_ci(root)
    (['.github/workflows/ci.yml'], [])
    >>> ci.read_text()
    '    python-version: ["3.12"]\n'
    """
    written: "list[str]" = []
    notes: "list[str]" = []
    for plan in ci_plan(root):
        if skip(plan.rel):
            continue
        notes += [n for n in plan.notes if n not in notes]
        if plan.text is not None:
            _textio.write_text(root / plan.rel, plan.text)
            written.append(plan.rel)
    return written, notes
