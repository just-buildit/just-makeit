"""_pythons.py -- the Python versions a generated project supports.

gh-2629. A generated project's supported Pythons live in ONE place, its own
``pyproject.toml``: the ``Programming Language :: Python :: 3.N`` trove
classifiers under ``[project] classifiers``. Everything else that names them
is rendered from there.

- ``jm new`` writes the classifiers, and ``requires-python``, from jm's OWN
  installed metadata (:func:`jm_supported`, :func:`jm_requires_python`): a
  new project starts out supporting what jm supports, and from then on the
  list is the project's.
- The GitHub CI ``jm ci`` writes takes its ``python-version`` matrix from
  that list (:func:`ci_versions`), and every ``jm apply`` rewrites the list
  in an existing ``.github/workflows/ci.yml`` to match it
  (:func:`sync_ci`). The CI stays static YAML -- nothing parses
  ``pyproject.toml`` at run time. Because `status` replays `apply`, a CI
  whose list disagrees with the classifiers reads as STALE until then.
- A PEP 723 app script declares the project's ``requires-python``
  (:func:`requires_python`, gh-2631); it is regenerated glue, so `apply`
  keeps it current.

**A project with no version classifiers** -- one scaffolded before this, or
one whose author removed them -- is left alone: ``pyproject.toml`` is the
author's file (`_createonly` classifies it ``AUTHOR``), and jm writes into
an author file only through a verb that declares the value. Its CI follows
jm's own list instead, and the command that renders it says once, in one
line, to add the classifiers (:func:`fallback_note`).

The CI write follows `_projversion`'s rule: it locates the SPAN of the one
list, from exactly one match, and replaces that value and nothing around it.
A workflow with no ``python-version: [...]`` line, or more than one, is not
a file jm understands and is never written.
"""

from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

from . import _textio

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.9/3.10 fall back to the backport
    import tomli as tomllib

#: The classifier prefix a version is spelled after.
CLASSIFIER = "Programming Language :: Python :: "

#: Where `jm ci` writes the GitHub workflow, and `apply` keeps its list.
CI_REL = ".github/workflows/ci.yml"

_VERSION_RE = re.compile(r"^" + re.escape(CLASSIFIER) + r"(3\.\d+)$")

#: The matrix's one ``python-version: [...]`` flow list, value captured.
_CI_RE = re.compile(
    r"^[ \t]*python-version:[ \t]*(?P<list>\[[^\]\n]*\])[ \t]*$", re.M
)


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
    return versions(metadata.metadata("just-makeit").get_all("Classifier"))


def jm_requires_python() -> str:
    """jm's own ``Requires-Python``, which a new project starts with."""
    return str(metadata.metadata("just-makeit")["Requires-Python"])


def classifier_lines(vs: "list[str]") -> str:
    """The TOML array body a new ``pyproject.toml`` lists *vs* in.

    >>> print(classifier_lines(["3.9", "3.10"]))
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    """
    return "\n".join(f'    "{CLASSIFIER}{v}",' for v in vs)


def declared(root: Path) -> "list[str]":
    """The versions the project's ``pyproject.toml`` classifiers declare.

    Empty when there is no ``pyproject.toml``, it does not parse, or it
    declares no ``3.N`` classifier -- each of which means the project has
    not said, never that it supports nothing.
    """
    try:
        with (root / "pyproject.toml").open("rb") as f:
            doc = tomllib.load(f)
    except (OSError, ValueError):
        return []
    proj = doc.get("project")
    cls = proj.get("classifiers") if isinstance(proj, dict) else None
    if not isinstance(cls, list):
        return []
    return versions([c for c in cls if isinstance(c, str)])


def requires_python(root: Path) -> str:
    """The project's ``[project] requires-python``, or jm's own.

    gh-2631: what a generated PEP 723 app script declares, so the script
    a project ships asks for the interpreters its ``pyproject.toml`` does.
    jm's own floor stands in only while the project has not said -- no
    ``pyproject.toml``, one that does not parse, or no string there.

    Examples
    --------
    >>> import tempfile
    >>> root = Path(tempfile.mkdtemp())
    >>> _ = (root / "pyproject.toml").write_text(
    ...     '[project]\\nrequires-python = ">=3.11"\\n'
    ... )
    >>> requires_python(root)
    '>=3.11'
    """
    try:
        with (root / "pyproject.toml").open("rb") as f:
            doc = tomllib.load(f)
    except (OSError, ValueError):
        return jm_requires_python()
    proj = doc.get("project")
    req = proj.get("requires-python") if isinstance(proj, dict) else None
    return req if isinstance(req, str) else jm_requires_python()


def ci_versions(root: Path) -> "tuple[list[str], bool]":
    """The versions the project's CI tests, and whether they are jm's.

    The project's own classifiers when it declares any; jm's list
    otherwise, with the second item True so the caller can say so.
    """
    own = declared(root)
    return (own, False) if own else (jm_supported(), True)


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


def _ci_span(text: str) -> "tuple[int, int] | None":
    """The span of the workflow's one ``python-version`` list, or None.

    >>> t = 'matrix:\\n  python-version: ["3.9"]\\n'
    >>> s, e = _ci_span(t)
    >>> t[s:e]
    '["3.9"]'
    >>> _ci_span(t + t) is None
    True
    """
    spans = [m.span("list") for m in _CI_RE.finditer(text)]
    return spans[0] if len(spans) == 1 else None


def sync_ci(root: Path) -> "tuple[bool, bool]":
    """Rewrite the CI's ``python-version`` list from the classifiers.

    Writes only the captured list, and only when it differs. A project
    with no ``.github/workflows/ci.yml``, or one whose workflow does not
    carry exactly one such list, is not touched.

    Returns
    -------
    (written, fallback) : tuple of bool
        Whether the file was rewritten, and whether the project carries a
        list jm keeps that follows jm's own versions for want of
        classifiers -- the case :func:`fallback_note` is for.

    Examples
    --------
    >>> import tempfile
    >>> root = Path(tempfile.mkdtemp())
    >>> _ = (root / "pyproject.toml").write_text(
    ...     '[project]\\nclassifiers = ["' + CLASSIFIER + '3.12"]\\n'
    ... )
    >>> ci = root / CI_REL
    >>> ci.parent.mkdir(parents=True)
    >>> _ = ci.write_text('    python-version: ["3.9", "3.12"]\\n')
    >>> sync_ci(root)
    (True, False)
    >>> ci.read_text()
    '    python-version: ["3.12"]\\n'
    """
    path = root / CI_REL
    if not path.is_file():
        return False, False
    text = path.read_text(encoding="utf-8")
    span = _ci_span(text)
    if span is None:
        return False, False
    vs, fallback = ci_versions(root)
    want = yaml_list(vs)
    s, e = span
    if text[s:e] == want:
        return False, fallback
    _textio.write_text(path, text[:s] + want + text[e:])
    return True, fallback
