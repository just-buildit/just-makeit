"""Version pins jm hands to a project it generates, declared once.

gh-2411. pytest 9.2.0 (2026-10-27) dropped ``Node._nodeid``, which
pytest-benchmark 5.3.0, its latest release, still reads in
``fixture.py``. Both resolve unpinned wherever jm installs them, so every
benchmark run died at setup: ``'Function' object has no attribute '_nodeid'``.

`PYTEST` is the requirement every generated surface installs pytest by: the
Makefile's ``ensure pytest`` line, the CI's ``pip install`` and the hint
``jm bench`` prints. jm's own ``Makefile`` and ``pyproject.toml`` carry the
same literal (they cannot import this), and
``tests/test_gh2411_pytest_cap.py`` holds all of them to it.

REMOVE WHEN pytest-benchmark publishes a release that supports pytest 9.2
(check ``pip index versions pytest-benchmark`` and its changelog). Then drop
the cap in every place the gate names, and the gate's expectation with it.

Examples
--------
>>> PYTEST
'pytest<9.2'
>>> PYTEST_SHELL
"'pytest<9.2'"
"""

from __future__ import annotations

#: The pytest requirement. See the module docstring for when to remove it.
PYTEST = "pytest<9.2"

#: The same requirement quoted for a POSIX shell, where ``<`` redirects.
PYTEST_SHELL = f"'{PYTEST}'"

#: ... and for ``cmd.exe`` and YAML ``run:`` lines, which take double quotes.
PYTEST_DQ = f'"{PYTEST}"'
