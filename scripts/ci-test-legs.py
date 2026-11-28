#!/usr/bin/env python3
"""Which CI legs a run owes, on the Pythons jm declares (gh-2125, gh-2626).

The Pythons jm supports are declared ONCE: the ``Programming Language ::
Python :: 3.N`` classifiers in ``pyproject.toml`` (:func:`pythons`). No
workflow lists them. Every matrix that runs "the supported Pythons" is a
GitHub matrix with no ``python-version`` list of its own, and this fills
it in: promoting a Python is adding its classifier, and nothing else.

ci.yml declares its ``test`` matrix as the JSON ``MATRIX`` of the
``toolchain`` job's ``legs`` step: a GitHub matrix (axes, ``exclude``,
``include``) WITHOUT a ``python-version`` axis, which :func:`resolve` adds
from the classifiers. That step runs this, as ``make ci-test-legs``, and the
``test`` job reads what it prints with ``fromJSON``. It expands the matrix
the way GitHub does and keeps the legs this run owes:

a plain ``pull_request`` (scope ``pr``)
    The cheap legs only (gh-2832): an OS named in ``PR_OS`` (the Linux
    ones), and of those, an OS named in ``TRIM`` runs only its floor and
    its newest Python, plus the middle one if the OS is also named in
    ``MIDDLE``. Every PR lands through a train (``make train``), which
    cancels the PR's own run and tests the combined tree once, so the
    macOS and Windows legs on a PR cost the most and gated nothing the
    train does not gate again; nine per-PR runs held a release's macOS
    smoke legs an hour in the free plan's ~20-job pool. A PR that lands
    without a train is given the full matrix by a dispatch.
a ``train/*`` pull request (``HEAD_REF``)
    Every leg: it is what lands.
a ``push`` whose tree its PR already tested (``TESTED=true``, scope
``tested``)
    Every leg a plain PR skips, and nothing else. gh-1801 lets such a push
    skip what its PR ran; it may not skip what its PR did not run, or the
    trimmed Pythons would be tested only by the nightly, which gates no
    release. So ``CI passed`` on every ``main`` commit -- what
    ``release.yml`` reads -- still certifies the whole matrix on that tree.
    A train's push owes nothing its train did not run, but which kind of PR
    a push merged is not known here yet (the org standard's #249), so
    every tested push runs the superset (gh-2832 fork 3).
anything else (scope ``full``)
    Every leg: a push to ``main`` the PR did not test, the nightly, a
    dispatch.

A plain PR's legs and a tested push's legs partition the matrix: neither
overlaps the other, and together they are all of it.

A leg that is about ONE Python rather than all of them names it by its
place, never by number, so it moves with the classifiers (:data:`TOKENS`):
``oldest``, ``middle`` (index ``len // 2``, the ``MIDDLE`` trim's choice),
``newest``, and ``next`` (the newest's minor plus one: the pre-release leg,
gh-2179). :func:`resolve` replaces each in an ``include`` or ``exclude``
entry, and in every ``LEGS_<NAME>`` matrix.

Inputs come through the environment, as in ``changes.yml``, so the test can
run exactly what the workflow runs:

``MATRIX``
    The declaration, JSON. Optional: without it, no ``matrix=`` line.
``TRIM``
    Space-separated ``os`` values a PR runs at the edges only.
``MIDDLE``
    Space-separated ``os`` values (a subset of ``TRIM``) that also keep
    the middle released Python: the one at index ``len // 2`` of that OS's
    sorted released Pythons, so it moves with the matrix.
``PR_OS``
    Space-separated ``os`` values a plain PR runs at all (gh-2832). Unset,
    every OS: the planner's own examples, and artifact.yml, which passes no
    ``EVENT``.
``EVENT``
    ``github.event_name``.
``HEAD_REF``
    ``github.head_ref``: a ``train/*`` PR is planned as a full run.
``TESTED``
    ``changes.yml``'s ``tested`` output: ``true`` only on such a push.
``LEGS_<NAME>``
    Another job's legs, JSON: a matrix whose Pythons are tokens (or none).
    Resolved and expanded, never trimmed by Python, and kept by OS the way
    the matrix is (`owed_named`): a plain PR keeps the ``PR_OS`` legs, a
    tested push the rest, any other run all of them. ci.yml's ``examples``
    jobs, its ``install-deps-smoke`` and ``consumer-smoke`` are planned
    this way (gh-2626, gh-2832), and artifact.yml's pre-publish smoke legs.

Prints ``matrix={"include": [...]}`` for ``$GITHUB_OUTPUT``, or ``matrix=``
when nothing is owed: GitHub refuses an empty matrix, so the ``test`` job
skips on an empty output instead. Then ``scope=`` (``pr``, ``tested`` or
``full``), then a ``<name>=`` line per ``LEGS_<NAME>``, in name order, empty
when it owes none. With the argument ``pythons`` it prints the declared
Pythons instead, one line, oldest first (``make ci-pythons``).

Examples
--------
>>> m = {"os": ["linux", "mac"], "python-version": ["3.9", "3.10", "3.11"],
...      "exclude": [{"os": "mac", "python-version": "3.9"}]}
>>> pairs = lambda legs: [(x["os"], x["python-version"]) for x in legs]
>>> pairs(expand(m))
[('linux', '3.9'), ('linux', '3.10'), ('linux', '3.11'), ('mac', '3.10'), \
('mac', '3.11')]
>>> m["python-version"].append("3.12")
>>> pairs(owed(m, ["mac"], [], "pull_request", ""))
[('linux', '3.9'), ('linux', '3.10'), ('linux', '3.11'), ('linux', '3.12'), \
('mac', '3.10'), ('mac', '3.12')]
>>> pairs(owed(m, ["mac"], [], "push", "true"))
[('mac', '3.11')]
>>> pairs(owed(m, ["linux"], ["linux"], "pull_request", ""))[:3]
[('linux', '3.9'), ('linux', '3.11'), ('linux', '3.12')]
>>> len(owed(m, ["mac"], [], "schedule", "")) == len(expand(m))
True

With ``PR_OS`` a plain PR runs those OSes alone, a ``train/*`` PR all of it,
and the push a plain PR's tree lands with the rest:

>>> pairs(owed(m, ["mac"], [], "pull_request", "", "fix/x", ["linux"]))
[('linux', '3.9'), ('linux', '3.10'), ('linux', '3.11'), ('linux', '3.12')]
>>> len(owed(m, ["mac"], [], "pull_request", "", "train/1-2", ["linux"]))
7
>>> pairs(owed(m, ["mac"], [], "push", "true", "", ["linux"]))
[('mac', '3.10'), ('mac', '3.11'), ('mac', '3.12')]

The tokens, against a declared list:

>>> pys = ["3.9", "3.10", "3.11", "3.12"]
>>> d = {"os": ["linux"], "include": [
...     {"os": "linux", "python-version": "next", "prerelease": True}]}
>>> pairs(expand(resolve(d, pys)))
[('linux', '3.9'), ('linux', '3.10'), ('linux', '3.11'), ('linux', '3.12'), \
('linux', '3.13')]
>>> ex = {"include": [{"os": "linux", "python-version": "middle"},
...                   {"os": "linux", "python-version": "newest"}]}
>>> pairs(expand(resolve(ex, pys)))
[('linux', '3.11'), ('linux', '3.12')]
"""

from __future__ import annotations

import itertools
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

Leg = Dict[str, Any]

#: The keys of a GitHub matrix that are not axes.
FILTERS = ("include", "exclude")

#: Where the supported Pythons are declared: this repository's own.
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

#: The classifier naming one supported Python.
CLASSIFIER = re.compile(r"^Programming Language :: Python :: (3\.\d+)$")

#: A Python named by its place among the declared ones.
TOKENS = ("oldest", "middle", "newest", "next")


def _version(leg: Leg) -> "tuple[int, ...]":
    """A leg's Python as an int tuple, so ``3.10`` sorts above ``3.9``."""
    return tuple(int(p) for p in str(leg["python-version"]).split("."))


def pythons(path: Path = PYPROJECT) -> "List[str]":
    """The Pythons jm supports: its ``3.N`` classifiers, oldest first.

    Parameters
    ----------
    path : Path
        The ``pyproject.toml`` to read; this repository's by default.

    Returns
    -------
    list of str
        Each ``3.N`` a ``Programming Language :: Python :: 3.N``
        classifier names, sorted by version (``3.10`` after ``3.9``).

    Raises
    ------
    ValueError
        When there is none: a matrix filled from nothing would run nothing
        and read as a pass.
    """
    try:
        import tomllib
    except ImportError:  # 3.9 / 3.10: the backport pyproject requires
        import tomli as tomllib  # type: ignore[no-redefine]
    with open(path, "rb") as f:
        meta = tomllib.load(f).get("project") or {}
    found = [
        m.group(1)
        for c in meta.get("classifiers") or []
        for m in [CLASSIFIER.match(str(c))]
        if m
    ]
    if not found:
        raise ValueError(
            f"no `Programming Language :: Python :: 3.N` in {path}"
        )
    return sorted(found, key=lambda v: _version({"python-version": v}))


def middle_of(ranked: "List[Any]") -> Any:
    """The middle of *ranked* (sorted oldest first): index ``len // 2``.

    The one definition of "the middle Python", for the ``middle`` token
    (over the classifiers) and the ``MIDDLE`` trim (over one OS's released
    legs). They are the same Python on an OS that runs every declared one,
    as ubuntu-latest does; the verb gate's leg is marked by the token and
    kept by the trim, and tests/test_gh2057_verb_leaves_what_apply_writes.py
    fails if the two ever part.
    """
    return ranked[len(ranked) // 2]


def _place(token: str, pys: "List[str]") -> str:
    """The Python *token* names among *pys*; any other value as it is."""
    if token not in TOKENS:
        return token
    if token == "next":
        major, minor = pys[-1].split(".")
        return f"{major}.{int(minor) + 1}"
    return {
        "oldest": pys[0],
        "middle": middle_of(pys),
        "newest": pys[-1],
    }[token]


def resolve(matrix: "dict[str, Any]", pys: "List[str]") -> "dict[str, Any]":
    """*matrix* on the declared Pythons *pys*.

    A matrix with axes gains the ``python-version`` axis *pys*; one that
    already has that axis is REFUSED, since a second list of the supported
    Pythons is what drifts from the first. An ``include``-only matrix (no
    axes) is one job's named legs and gains none. Every ``python-version``
    in an ``include`` or ``exclude`` entry that is a :data:`TOKENS` word is
    replaced by the Python it names.

    Raises
    ------
    ValueError
        When *matrix* declares its own ``python-version`` axis.
    """
    if "python-version" in matrix:
        raise ValueError(
            "a CI matrix lists its own python-version axis; the supported "
            "Pythons are the pyproject classifiers alone (gh-2626)"
        )
    out = dict(matrix)
    if any(k not in FILTERS for k in matrix):
        out["python-version"] = list(pys)
    for key in FILTERS:
        if key in matrix:
            out[key] = [
                dict(
                    e,
                    **(
                        {"python-version": _place(e["python-version"], pys)}
                        if "python-version" in e
                        else {}
                    ),
                )
                for e in matrix[key]
            ]
    return out


def expand(matrix: "dict[str, Any]") -> "List[Leg]":
    """The legs GitHub runs for *matrix*, in its order.

    The cross product of the axes, less every combination an ``exclude``
    entry matches (all of the entry's pairs equal; an entry may name a
    subset of the axes), then each ``include`` entry: merged into every
    combination whose own axis values it does not contradict, or appended
    as a leg of its own when it fits none -- GitHub's documented rule.

    Parameters
    ----------
    matrix : dict
        A ``strategy.matrix`` mapping, as JSON would decode it.

    Returns
    -------
    list of dict
        One mapping per leg, every key the leg's ``matrix`` context has.
    """
    axes = {k: v for k, v in matrix.items() if k not in FILTERS}
    legs = (
        [
            dict(zip(axes, values))
            for values in itertools.product(*axes.values())
        ]
        if axes
        else []
    )
    legs = [
        leg
        for leg in legs
        if not any(
            all(leg.get(k) == v for k, v in ex.items())
            for ex in matrix.get("exclude", [])
        )
    ]
    original = [dict(leg) for leg in legs]
    for inc in matrix.get("include", []):
        fits = [
            leg
            for leg, orig in zip(legs, original)
            if all(orig[k] == v for k, v in inc.items() if k in orig)
        ]
        for leg in fits:
            leg.update(inc)
        if not fits:
            legs.append(dict(inc))
    return legs


def trimmed(
    legs: "List[Leg]", trim: "List[str]", middle: "List[str]"
) -> "List[Leg]":
    """The legs a PR skips: each *trim* OS's Pythons between its edges.

    The edges are that OS's oldest and newest released Python among
    *legs*, so they move with the matrix: promoting a Python makes it the
    new newest, and raising the floor moves the oldest. An OS also in
    *middle* keeps its released Pythons' :func:`middle_of` as well. A ``prerelease``
    leg is never trimmed and never an edge.
    """
    out = []
    for name in trim:
        mine = [x for x in legs if x["os"] == name and not x.get("prerelease")]
        if not mine:
            continue
        edges = {_version(min(mine, key=_version))}
        edges.add(_version(max(mine, key=_version)))
        if name in middle:
            edges.add(_version(middle_of(sorted(mine, key=_version))))
        out += [x for x in mine if _version(x) not in edges]
    return out


#: The head-ref prefix of the PR ``make train`` opens: what lands.
TRAIN_PREFIX = "train/"


def scope(event: str, tested: str, head_ref: str = "") -> str:
    """Which run this is: ``pr``, ``tested`` or ``full``.

    ``pr`` is a pull request that is not a train (gh-2832); ``tested`` a
    push whose tree its PR already tested; ``full`` everything else,
    including a ``train/*`` PR.

    Examples
    --------
    >>> scope("pull_request", "", "fix/gh-1")
    'pr'
    >>> scope("pull_request", "", "train/2812-2815")
    'full'
    >>> scope("push", "true"), scope("schedule", "")
    ('tested', 'full')
    """
    if tested == "true":
        return "tested"
    if event == "pull_request" and not head_ref.startswith(TRAIN_PREFIX):
        return "pr"
    return "full"


def _on_pr(leg: Leg, pr_os: "List[str] | None") -> bool:
    """Whether a plain PR runs *leg*'s OS at all."""
    return pr_os is None or leg.get("os") in pr_os


def owed(
    matrix: "dict[str, Any]",
    trim: "List[str]",
    middle: "List[str]",
    event: str,
    tested: str,
    head_ref: str = "",
    pr_os: "List[str] | None" = None,
) -> "List[Leg]":
    """The legs a run on *event* owes; see the module docstring."""
    legs = expand(matrix)
    skip = trimmed(legs, trim, middle)
    pr = [x for x in legs if x not in skip and _on_pr(x, pr_os)]
    run = scope(event, tested, head_ref)
    if run == "tested":
        return [x for x in legs if x not in pr]
    if run == "pr":
        return pr
    return legs


def owed_named(
    legs: "List[Leg]", run: str, pr_os: "List[str] | None"
) -> "List[Leg]":
    """The legs of a ``LEGS_<NAME>`` job a *run* (a `scope`) owes.

    By OS alone, as the matrix is kept: a plain PR its ``PR_OS`` legs, a
    tested push the others, any other run all. Without *pr_os* (no
    ``PR_OS``) every run owes them all.

    Examples
    --------
    >>> legs = [{"os": "linux"}, {"os": "mac"}]
    >>> owed_named(legs, "pr", ["linux"])
    [{'os': 'linux'}]
    >>> owed_named(legs, "tested", ["linux"])
    [{'os': 'mac'}]
    >>> owed_named(legs, "tested", None) == legs
    True
    """
    if pr_os is None or run == "full":
        return legs
    on_pr = [x for x in legs if _on_pr(x, pr_os)]
    if run == "pr":
        return on_pr
    return [x for x in legs if x not in on_pr]


def _names(legs: "List[Leg]") -> str:
    return ", ".join(
        " ".join(str(x[k]) for k in ("name", "os", "python-version") if k in x)
        for x in legs
    )


def main(argv: "List[str] | None" = None) -> int:
    """Read the environment, print the plan for ``$GITHUB_OUTPUT``."""
    pys = pythons()
    if (sys.argv[1:] if argv is None else argv) == ["pythons"]:
        print(" ".join(pys))
        return 0
    event = os.environ.get("EVENT", "")
    tested = os.environ.get("TESTED", "")
    head_ref = os.environ.get("HEAD_REF", "")
    pr_os = os.environ["PR_OS"].split() if "PR_OS" in os.environ else None
    run = scope(event, tested, head_ref)
    if "MATRIX" in os.environ:
        legs = owed(
            resolve(json.loads(os.environ["MATRIX"]), pys),
            os.environ.get("TRIM", "").split(),
            os.environ.get("MIDDLE", "").split(),
            event,
            tested,
            head_ref,
            pr_os,
        )
        value = json.dumps({"include": legs}) if legs else ""
        print(f"matrix={value}")
        print(
            f"ci-test-legs: {len(legs)} leg(s): {_names(legs)}",
            file=sys.stderr,
        )
        print(f"scope={run}")
    for var in sorted(k for k in os.environ if k.startswith("LEGS_")):
        legs = owed_named(
            expand(resolve(json.loads(os.environ[var]), pys)), run, pr_os
        )
        name = var[len("LEGS_") :].lower()
        value = json.dumps({"include": legs}) if legs else ""
        print(f"{name}={value}")
        print(f"ci-test-legs: {name}: {_names(legs)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
