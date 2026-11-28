"""Whether this interpreter is the Python a CI leg names (gh-2172).

A leg names its Python once, from the matrix's ``python-version``. A
``uv``-driven leg (ci.yml) reaches the suite with it only as
``UV_PYTHON``, which ``astral-sh/setup-uv`` exports; a ``setup-python``
leg (artifact.yml's smoke legs) only as the ``python`` on ``PATH``. A pin
that overrides it -- ``--python`` or ``-p`` on a ``uv run``, a uv default
that changes, a ``.python-version`` file, a second interpreter earlier on
``PATH`` -- runs every leg on one interpreter, and the job names and
``CI passed`` read exactly as before.

`mismatch` is the one answer, asked from two places:

* ``tests/conftest.py``, at the start of every pytest session on CI, with
  ``UV_PYTHON``: every ci.yml leg that runs jm's tests is checked, whatever
  files it collects;
* this file as a script, ``python tests/_legpython.py <python-version>``:
  each artifact.yml smoke leg's first step, for the interpreters its steps
  call (exit 1 naming both Pythons, 0 when they agree).
"""

from __future__ import annotations

import re
import sys
from typing import Optional, Tuple

_VERSION = re.compile(r"^(\d+)\.(\d+)")


def mismatch(want: str, running: "Tuple[int, ...]") -> Optional[str]:
    """Why this interpreter is not the leg's Python *want*, or None.

    Parameters
    ----------
    want : str
        The Python the leg names (``UV_PYTHON``, or the matrix's
        ``python-version``); empty off a leg.
    running : tuple of int
        ``sys.version_info`` of the interpreter being checked.

    Returns
    -------
    str or None
        A sentence naming both Pythons when *want* names one and this is
        another; None off a leg or when they agree. A *want* that is not a
        version is refused: it names no Python, so nothing could be checked
        against it.

    Examples
    --------
    >>> mismatch("", (3, 12, 1)) is None
    True
    >>> mismatch("3.12", (3, 12, 1)) is None
    True
    >>> print(mismatch("3.13", (3, 12, 1)))
    ... # doctest: +ELLIPSIS
    this leg names Python 3.13 but runs 3.12: ...
    """
    if not want:
        return None
    m = _VERSION.match(want)
    if not m:
        return f"{want!r} is not a Python version, so it names no Python"
    if tuple(running[:2]) == (int(m[1]), int(m[2])):
        return None
    return (
        f"this leg names Python {want} but runs {running[0]}.{running[1]}: "
        "a --python / -p pin, another uv default or another interpreter on "
        "PATH is overriding the leg's (gh-2172)"
    )


def main(argv: "list[str]") -> int:
    """``_legpython.py <want>``: exit 1 naming both Pythons on a mismatch.

    An empty *want* is refused here, unlike in `mismatch`: a step that asks
    names a leg, so an empty one is a matrix that lost its Python.
    """
    if len(argv) != 1 or not argv[0]:
        print("usage: _legpython.py <python-version>", file=sys.stderr)
        return 2
    why = mismatch(argv[0], tuple(sys.version_info))
    if why:
        print(f"error: {why}", file=sys.stderr)
        return 1
    print(f"_legpython: {sys.executable} is Python {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
