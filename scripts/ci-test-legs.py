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

``pull_request``
    Every leg, except that an OS named in ``TRIM`` runs only its floor and
    its newest Python, plus the middle one if the OS is also named in
    ``MIDDLE``. macOS bounded PR throughput: its runner pool is a quarter
    of the org's, and its five legs were ~100 of a run's ~340 job-minutes.
    The free plan's ~20 concurrent jobs then bounded it for the Linux legs
    too (queue waits averaged ~400s on every runner label), so they are
    trimmed the same way.
a ``push`` whose tree its PR already tested (``TESTED=true``)
    Exactly the legs that PR trimmed, and nothing else. gh-1801 lets such a
    push skip what its PR ran; it may not skip what its PR did not run, or
    the trimmed Pythons would be tested only by the nightly, which gates no
    release. So ``CI passed`` on every ``main`` commit -- what
    ``release.yml`` reads -- still certifies the whole matrix on that tree,
    from the PR's legs plus these.
anything else
    Every leg: a push to ``main`` the PR did not test, the nightly, a
    dispatch.

The PR legs and a tested push's legs partition the matrix: neither
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
``EVENT``
    ``github.event_name``.
``TESTED``
    ``changes.yml``'s ``tested`` output: ``true`` only on such a push.
``LEGS_<NAME>``
    Another job's legs, JSON: a matrix whose Pythons are tokens. Printed,
    resolved and expanded but never trimmed, as ``<name>=`` (lower case):
    every event owes them. ci.yml's ``examples`` jobs are planned this way
    (gh-2626), and artifact.yml's pre-publish smoke legs.

Prints ``matrix={"include": [...]}`` for ``$GITHUB_OUTPUT``, or ``matrix=``
when nothing is owed: GitHub refuses an empty matrix, so the ``test`` job
skips on an empty output instead. Then a ``<name>=`` line per
``LEGS_<NAME>``, in name order. With the argument ``pythons`` it prints the
declared Pythons instead, one line, oldest first (``make ci-pythons``).

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


def _place(token: str, pys: "List[str]") -> str:
    """The Python *token* names among *pys*; any other value as it is."""
    if token not in TOKENS:
        return token
    if token == "next":
        major, minor = pys[-1].split(".")
        return f"{major}.{int(minor) + 1}"
    return {
        "oldest": pys[0],
        "middle": pys[len(pys) // 2],
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
    *middle* keeps the Python at index ``len // 2`` of its sorted released
    Pythons as well. A ``prerelease``
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
            ranked = sorted(mine, key=_version)
            edges.add(_version(ranked[len(ranked) // 2]))
        out += [x for x in mine if _version(x) not in edges]
    return out


def owed(
    matrix: "dict[str, Any]",
    trim: "List[str]",
    middle: "List[str]",
    event: str,
    tested: str,
) -> "List[Leg]":
    """The legs a run on *event* owes; see the module docstring."""
    legs = expand(matrix)
    skip = trimmed(legs, trim, middle)
    if tested == "true":
        return skip
    if event == "pull_request":
        return [x for x in legs if x not in skip]
    return legs


def _names(legs: "List[Leg]") -> str:
    return ", ".join(f"{x['os']} {x['python-version']}" for x in legs)


def main(argv: "List[str] | None" = None) -> int:
    """Read the environment, print the plan for ``$GITHUB_OUTPUT``."""
    pys = pythons()
    if (sys.argv[1:] if argv is None else argv) == ["pythons"]:
        print(" ".join(pys))
        return 0
    if "MATRIX" in os.environ:
        legs = owed(
            resolve(json.loads(os.environ["MATRIX"]), pys),
            os.environ.get("TRIM", "").split(),
            os.environ.get("MIDDLE", "").split(),
            os.environ.get("EVENT", ""),
            os.environ.get("TESTED", ""),
        )
        value = json.dumps({"include": legs}) if legs else ""
        print(f"matrix={value}")
        print(
            f"ci-test-legs: {len(legs)} leg(s): {_names(legs)}",
            file=sys.stderr,
        )
    for var in sorted(k for k in os.environ if k.startswith("LEGS_")):
        legs = expand(resolve(json.loads(os.environ[var]), pys))
        name = var[len("LEGS_") :].lower()
        print(f"{name}={json.dumps({'include': legs})}")
        print(f"ci-test-legs: {name}: {_names(legs)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
