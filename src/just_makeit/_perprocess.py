"""State that lives for one jm process, and the one way to start it over.

A real ``jm`` command is a process: whatever jm remembers across one
command -- which advisories it has already printed, which manifest and
header it has already read -- starts empty and dies with it. Within that
one process the memory is the point: ``jm apply`` loads the manifest for the
real tree and again for its temp scaffold, and a reader should hear each
warning once.

The in-process runner (`_example.run_cli`, gh-1374) runs many commands in
one Python process, so it must give each the empty state a child had. It
did that for one cache by name (gh-2095) and missed three warning memos
(gh-2244): once a warning printed in a pytest worker, every later call in
that worker skipped it, and a test asserting the warning was ABSENT passed
whether or not jm would have printed it -- gh-2115's ``out_cols`` check was
proven blind that way.

So such state is declared here, where it is made, and :func:`fresh` empties
all of it: nobody keeps a list of what to clear. A module writes::

    _SEEN: "set[str]" = per_process(set())

``tests/test_gh2244_per_process_state.py`` finds every module-level value a
function in the package mutates or rebinds, and every ``functools`` cache,
and fails on one that is neither declared here nor classified there as state
that is the same in every process (a cache of jm's own install) or scoped to
one command.
"""

from __future__ import annotations

from typing import Any, TypeVar

_T = TypeVar("_T")

#: Every container :func:`per_process` has declared, in import order. A
#: module not yet imported has declared nothing, and needs nothing cleared:
#: importing it makes its state empty.
_REGISTRY: "list[Any]" = []


def per_process(state: _T) -> _T:
    """Declare *state* as living for one jm process, and return it.

    Parameters
    ----------
    state
        An empty container with a ``clear()`` method -- a ``set``, a
        ``dict``, a ``list`` -- or a ``functools`` cache, with
        ``cache_clear()``. :func:`fresh` calls that method, so the object
        keeps its identity and every module holding it sees it emptied. A
        cache is declared as a decorator, above ``@functools.cache``.

    Returns
    -------
    The same object, so the declaration is the assignment.

    Examples
    --------
    >>> said = per_process(set())
    >>> said.add("x")
    >>> fresh()
    >>> said
    set()
    """
    _REGISTRY.append(state)
    return state


def fresh() -> None:
    """Empty every container :func:`per_process` declared.

    What a new ``jm`` process starts with. Called by the in-process runner
    before each command; jm itself never calls it, because within one
    command the memory is wanted.
    """
    for state in _REGISTRY:
        (getattr(state, "cache_clear", None) or state.clear)()
