"""Split the suite into shards by test FILE, for Coverage's CI jobs (gh-2078).

``pytest --jm-shard=K/N`` keeps the tests whose file belongs to shard K of N
and deselects the rest. ``make coverage-shard COVERAGE_SHARD=K`` passes it,
one CI job per K, and ``make coverage-gate COVERAGE_FROM_SHARDS=1`` gates
once on the union of their data.

Which shard owns a file is a pure function of its path, relative to the
rootdir: a SHA-1 of the path, reduced mod N. That makes the split

- complete and disjoint by construction: every collected item has a file,
  and every file has exactly one owner in ``1..N``;
- free of registration: a test file added tomorrow is owned the moment it
  exists, and no other file moves;
- the same in every process: ``hash()`` is salted per interpreter, a digest
  is not, so N CI jobs and the xdist workers inside each agree;
- whole-file: a module- or class-scoped fixture runs in one shard only, never
  once per shard.

Balance is not tuned, it is measured: on the suite of 2026-10-08 the two
shards held 4304 and 4427 of 8731 worker-seconds (junit.xml of a full
Coverage run). A file heavy enough to unbalance that is a reason to make the
file cheaper, not to hand-place it.

``tests/conftest.py`` loads this module as a plugin (``pytest_plugins``), and
``tests/test_gh2078_coverage_shards.py`` runs it under ``-p _shard`` in a
child pytest, so the two read one implementation.

Examples
--------
>>> parse("2/3")
(2, 3)
>>> shard_of("tests/test_cli.py", 1)
1
>>> shard_of("tests/test_cli.py", 2) == shard_of("tests/test_cli.py", 2)
True
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

OPTION = "--jm-shard"


def parse(spec: str) -> "tuple[int, int]":
    """Read ``K/N`` into ``(K, N)``, refusing anything but ``1 <= K <= N``.

    Parameters
    ----------
    spec : str
        The option's value, e.g. ``"1/2"``.

    Returns
    -------
    tuple of int
        ``(index, count)``, both 1-based.

    Raises
    ------
    pytest.UsageError
        When *spec* is not two integers with ``1 <= K <= N``. An empty K is
        what ``make coverage-shard`` without ``COVERAGE_SHARD`` passes.

    Examples
    --------
    >>> parse("1/2")
    (1, 2)
    >>> parse("0/2")
    Traceback (most recent call last):
    ...
    pytest.UsageError: --jm-shard=0/2: want K/N with 1 <= K <= N
    """
    index, _, count = spec.partition("/")
    if index.isdigit() and count.isdigit():
        k, n = int(index), int(count)
        if 1 <= k <= n:
            return k, n
    raise pytest.UsageError(f"{OPTION}={spec}: want K/N with 1 <= K <= N")


def shard_of(relpath: str, count: int) -> int:
    """The 1-based shard of *count* that owns the test file at *relpath*.

    Parameters
    ----------
    relpath : str
        The file's path relative to the rootdir, with ``/`` separators, so
        the answer is the same on every platform and checkout location.
    count : int
        How many shards there are.

    Returns
    -------
    int
        A shard in ``1..count``.

    Examples
    --------
    >>> all(1 <= shard_of(f"tests/test_{i}.py", 3) <= 3 for i in range(50))
    True
    """
    digest = hashlib.sha1(relpath.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % count + 1


def item_file(item: pytest.Item) -> str:
    """The key :func:`shard_of` hashes for *item*: its file, rootdir-relative.

    ``os.path.relpath`` rather than ``Path.relative_to``, so a file outside
    the rootdir still gets a key (``../x``) rather than an exception.
    """
    rel = os.path.relpath(item.path, item.config.rootpath)
    return Path(rel).as_posix()


def split(
    items: "list[pytest.Item]", index: int, count: int
) -> "tuple[list[pytest.Item], list[pytest.Item]]":
    """Partition *items* into (kept by shard *index*, the rest), in order.

    Parameters
    ----------
    items : list of pytest.Item
        The collected items.
    index, count : int
        The shard, as :func:`parse` returns it.

    Returns
    -------
    tuple of list
        ``(keep, drop)``; together they are *items*, each in collection
        order.
    """
    keep, drop = [], []
    for item in items:
        owner = shard_of(item_file(item), count)
        (keep if owner == index else drop).append(item)
    return keep, drop


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--jm-shard``; unset, every collected item runs."""
    parser.addoption(
        OPTION,
        dest="jm_shard",
        default=None,
        metavar="K/N",
        help="run only the test files owned by shard K of N (gh-2078)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Refuse a malformed ``K/N`` before anything collects."""
    spec = config.getoption("jm_shard")
    if spec:
        parse(spec)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: "list[pytest.Item]"
) -> None:
    """Deselect the items another shard owns, and report them as deselected.

    ``trylast`` so the split sees the collection every other hook (``-k``,
    ``-m``) has already narrowed: a shard is a subset of THIS run, not of
    the files on disk.
    """
    spec = config.getoption("jm_shard")
    if not spec:
        return
    keep, drop = split(items, *parse(spec))
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep
