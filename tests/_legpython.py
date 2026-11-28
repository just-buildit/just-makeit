"""Whether this interpreter is the Python a CI leg names (gh-2172).

A CI leg names its Python once, as ``UV_PYTHON``, which
``astral-sh/setup-uv`` exports from the leg's ``python-version``; every
test run reaches it only through ``uv run``, which honours it. A pin that
overrides it -- ``--python`` or ``-p`` on a ``uv run``, a uv default that
changes, a ``.python-version`` file -- runs every leg on one interpreter,
and the job names and ``CI passed`` read exactly as before.

`mismatch` is the one answer. ``tests/conftest.py`` asks it at the start
of EVERY pytest session, so each leg that runs jm's tests is checked --
the unit suite, the examples, the compiled tests, the goldens -- not only
the one that happens to collect a particular test file.
"""

from __future__ import annotations

import re
from typing import Mapping, Optional, Tuple

_VERSION = re.compile(r"^(\d+)\.(\d+)")


def mismatch(
    env: "Mapping[str, str]", running: "Tuple[int, ...]"
) -> Optional[str]:
    """Why this interpreter is not the leg's Python, or None.

    Parameters
    ----------
    env : mapping
        The environment, ``os.environ`` in use.
    running : tuple of int
        ``sys.version_info`` of the interpreter running the suite.

    Returns
    -------
    str or None
        A sentence naming both Pythons, when ``UV_PYTHON`` names one and
        this is another; None off a leg (no ``UV_PYTHON``) or when they
        agree. A ``UV_PYTHON`` that is not a version is refused: it names
        no Python, so nothing could be checked against it.

    Examples
    --------
    >>> mismatch({}, (3, 12, 1)) is None
    True
    >>> mismatch({"UV_PYTHON": "3.12"}, (3, 12, 1)) is None
    True
    >>> print(mismatch({"UV_PYTHON": "3.13"}, (3, 12, 1)))
    ... # doctest: +ELLIPSIS
    this leg names Python 3.13 but the suite runs 3.12: ...
    """
    want = env.get("UV_PYTHON", "")
    if not want:
        return None
    m = _VERSION.match(want)
    if not m:
        return f"UV_PYTHON={want!r} is not a version, so it names no Python"
    if tuple(running[:2]) == (int(m[1]), int(m[2])):
        return None
    return (
        f"this leg names Python {want} but the suite runs "
        f"{running[0]}.{running[1]}: a --python / -p pin or another uv "
        "default is overriding the leg's interpreter (gh-2172)"
    )
