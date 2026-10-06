"""The oldest Python jm supports, read once from `requires-python` (gh-1931).

`pyproject.toml` declares the floor, and three gates hold other things to
it: no test imports a module the floor lacks (`test_stdlib_floor.py`), CI's
matrix tests it (`test_own_ci_matrix.py`), and `install.sh` accepts it
(`test_gh1916_install_sh_floor.py`). Each had read the line itself, two of
them with a private `_floor()` -- one returning ``(3, 9)``, the other
``"3.9"`` -- and the third by importing one of those from a test module.
Two readers of one line are a pair that drifts, and these already
disagreed about what a floor is.

`tests/test_gh1931_shared_test_helpers.py` refuses a test that reads
`requires-python` itself.
"""

from __future__ import annotations

from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

#: The key the floor is declared under.
KEY = "requires-python"


def python_floor() -> "tuple[int, ...]":
    """`requires-python = ">=3.9"` in jm's `pyproject.toml`, as ``(3, 9)``.

    A tuple of ints, so it compares with ``sys.version_info[:2]`` and with
    another version tuple directly; ``".".join(map(str, ...))`` spells it.

    Raises
    ------
    AssertionError
        When the file has no ``requires-python = ">=X.Y"`` line. A gate
        whose floor came back empty would hold nothing to it, so this
        fails the caller rather than answer.

    Examples
    --------
    >>> floor = python_floor()
    >>> len(floor) >= 2 and all(isinstance(p, int) for p in floor)
    True
    """
    for line in PYPROJECT.read_text(encoding="utf-8").splitlines():
        if line.startswith(KEY):
            ver = line.split(">=")[1].strip().strip('"').strip("'")
            return tuple(int(p) for p in ver.split("."))
    raise AssertionError(f"no `{KEY}` line in {PYPROJECT}")
