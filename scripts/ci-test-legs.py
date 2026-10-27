#!/usr/bin/env python3
"""Which ``test`` legs a ci.yml run owes (gh-2125).

ci.yml declares its ``test`` matrix ONCE, as the JSON ``MATRIX`` of the
``toolchain`` job's ``legs`` step: a GitHub matrix (axes, ``exclude``,
``include``). That step runs this, as ``make ci-test-legs``, and the ``test``
job reads what it prints with ``fromJSON``. It expands the matrix the way
GitHub does and keeps the legs this run owes:

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

Inputs come through the environment, as in ``changes.yml``, so the test can
run exactly what the workflow runs:

``MATRIX``
    The declaration, JSON.
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

Prints ``matrix={"include": [...]}`` for ``$GITHUB_OUTPUT``, or ``matrix=``
when nothing is owed: GitHub refuses an empty matrix, so the ``test`` job
skips on an empty output instead.

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
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from typing import Any, Dict, List

Leg = Dict[str, Any]

#: The keys of a GitHub matrix that are not axes.
FILTERS = ("include", "exclude")


def _version(leg: Leg) -> "tuple[int, ...]":
    """A leg's Python as an int tuple, so ``3.10`` sorts above ``3.9``."""
    return tuple(int(p) for p in str(leg["python-version"]).split("."))


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
    legs = [
        dict(zip(axes, values)) for values in itertools.product(*axes.values())
    ]
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


def main() -> int:
    """Read the environment, print ``matrix=...`` for ``$GITHUB_OUTPUT``."""
    legs = owed(
        json.loads(os.environ["MATRIX"]),
        os.environ.get("TRIM", "").split(),
        os.environ.get("MIDDLE", "").split(),
        os.environ.get("EVENT", ""),
        os.environ.get("TESTED", ""),
    )
    value = json.dumps({"include": legs}) if legs else ""
    print(f"matrix={value}")
    names = ", ".join(f"{x['os']} {x['python-version']}" for x in legs)
    print(f"ci-test-legs: {len(legs)} leg(s): {names}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
