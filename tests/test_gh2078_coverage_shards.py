"""gh-2078: Coverage runs as N shards, and together the shards ARE the suite.

Coverage took 41.7 min of a 45-min timeout as one job, so CI splits the suite
by test file across ``COVERAGE_SHARDS`` runners (``make coverage-shard
COVERAGE_SHARD=K``) and gates once on the union (``make coverage-gate
COVERAGE_FROM_SHARDS=1``). A split can go wrong silently in both directions:
a test owned by no shard never runs and nothing goes red, and a test owned
by two runs twice and only costs time. Neither shows in the percentage.

GATE: the shards partition the suite -- every test in exactly one, a file
added tomorrow included, with no list to register it in -- and the threshold
applies once, to the combined data of every shard.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest
import yaml

import _shard

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _recipe(*args: str) -> str:
    """What make would run for *args*, expanded, without running it."""
    proc = subprocess.run(
        ["make", "-nrR", "--no-print-directory", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture(scope="module")
def recipes() -> "dict[str, str]":
    """The three ways the Coverage gate runs, as make expands them."""
    return {
        "shard": _recipe("coverage-shard", "COVERAGE_SHARD=1"),
        "union": _recipe("coverage-gate", "COVERAGE_FROM_SHARDS=1"),
        "whole": _recipe("coverage-gate"),
    }


@pytest.fixture(scope="module")
def shards(recipes) -> int:
    """N, as the Makefile hands it to ``--jm-shard`` -- not restated here."""
    m = re.search(rf"{_shard.OPTION}=(\S+)", recipes["shard"])
    assert m, f"`make coverage-shard` passes no {_shard.OPTION}"
    index, count = _shard.parse(m.group(1))
    assert index == 1 and count >= 2, m.group(0)
    return count


def _owners(nodeids_by_shard: "dict[int, list[str]]") -> "dict[str, list]":
    owners: "dict[str, list[int]]" = defaultdict(list)
    for k, ids in nodeids_by_shard.items():
        for nodeid in ids:
            owners[nodeid].append(k)
    return owners


def _assert_partition(full: "list[str]", by_shard: "dict[int, list[str]]"):
    """Every id of *full* in exactly one shard, a file never split."""
    owners = _owners(by_shard)
    nowhere = sorted(set(full) - set(owners))
    twice = sorted(i for i, ks in owners.items() if len(ks) > 1)
    extra = sorted(set(owners) - set(full))
    assert (nowhere, twice, extra) == ([], [], []), (
        f"run by no shard: {nowhere[:5]}; by two: {twice[:5]}; "
        f"by a shard but not the suite: {extra[:5]}"
    )
    files: "dict[str, set[int]]" = defaultdict(set)
    for nodeid, ks in owners.items():
        files[nodeid.split("::")[0]].update(ks)
    split = sorted(f for f, ks in files.items() if len(ks) > 1)
    assert split == [], f"files split across shards: {split}"


def test_the_shards_partition_this_run(request, shards):
    """The real suite: what this session collected, split N ways.

    ``session.items`` is the whole collection on an unsharded run, and the
    shard's own subset on a sharded one -- a partition either way. The
    plugin must be the one this session loaded, or CI's ``--jm-shard`` is
    an unknown option, or worse, a different split.
    """
    plugin = request.config.pluginmanager.get_plugin("_shard")
    assert plugin is _shard, "tests/conftest.py does not load _shard"
    items = request.session.items
    assert request.node in items
    by_shard = {
        k: [i.nodeid for i in _shard.split(items, k, shards)[0]]
        for k in range(1, shards + 1)
    }
    _assert_partition([i.nodeid for i in items], by_shard)


# A tree of the shapes the suite has: a plain file, a parametrized test, a
# class, a file one directory down. Enough files that a split which is not
# a pure function of the path has a file to disagree about.
_TREE = {
    f"tests/test_mod{i}.py": (
        "import pytest\n\n"
        "def test_one():\n    pass\n\n"
        "@pytest.mark.parametrize('x', [1, 2, 3])\n"
        "def test_many(x):\n    pass\n\n"
        "class TestGroup:\n    def test_member(self):\n        pass\n"
    )
    for i in range(12)
}
_TREE["tests/deeper/test_nested.py"] = "def test_nested():\n    pass\n"


def _collect(tree: Path, *args: str, seed: str) -> "list[str]":
    """Node ids a child pytest collects under *tree*, with ``-p _shard``.

    A CHILD, and one per shard with its own ``PYTHONHASHSEED``: CI runs each
    shard on its own runner, so an owner computed from anything salted per
    process (``hash()``) would disagree between them, and only separate
    processes can show it. Plugin autoload is off, so the split is this
    plugin's alone.
    """
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(ROOT / "tests"), os.environ.get("PYTHONPATH", "")]
        ),
        "PYTHONHASHSEED": seed,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "_shard",
            "-p",
            "no:cacheprovider",
            "-c",
            str(tree / "pytest.ini"),
            "--rootdir",
            str(tree),
            *args,
        ],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode in (0, 5), proc.stdout + proc.stderr
    return [line for line in proc.stdout.splitlines() if "::" in line]


def _split_by_children(tree: Path, n: int) -> "dict[int, list[str]]":
    return {
        k: _collect(tree, f"{_shard.OPTION}={k}/{n}", seed=str(k))
        for k in range(1, n + 1)
    }


def test_a_new_file_lands_in_one_shard_and_moves_no_other(tmp_path, shards):
    """End to end, the option as CI passes it, before and after a new file.

    No list names a test file, so the file added here is owned the moment
    it exists; and since ownership is per path, adding it moves no other
    file between shards.
    """
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    for rel, body in _TREE.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    full = _collect(tmp_path, seed="0")
    assert len(full) == 5 * 12 + 1, full
    before = _split_by_children(tmp_path, shards)
    _assert_partition(full, before)

    new = tmp_path / "tests" / "test_added_tomorrow.py"
    new.write_text("def test_new():\n    pass\n", encoding="utf-8")
    after = _split_by_children(tmp_path, shards)
    _assert_partition(full + ["tests/test_added_tomorrow.py::test_new"], after)
    was, now = _owners(before), _owners(after)
    moved = sorted(nodeid for nodeid in was if was[nodeid] != now[nodeid])
    assert moved == [], f"a new file moved others between shards: {moved}"


def test_the_threshold_applies_once_to_the_union(recipes, tmp_path):
    """A shard is half a suite: its percentage means nothing, so no shard is
    gated; the union is, once; and plain ``coverage-gate`` stays the whole
    suite in one run, as ``make gates`` and a developer run it.
    """
    assert "fail-under" not in recipes["shard"], recipes["shard"]
    union = recipes["union"]
    assert "coverage combine" in union and "pytest" not in union, union
    assert union.count("--fail-under=") == 1, union
    whole = recipes["whole"]
    assert "--cov-fail-under=" in whole, whole
    assert _shard.OPTION not in whole, whole

    # One shard's data alone can clear the threshold, so the gate counts.
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / ".coverage").write_bytes(b"")
    proc = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "coverage-gate",
            "COVERAGE_FROM_SHARDS=1",
            f"COVERAGE_SHARD_ROOT={tmp_path}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode != 0, proc.stdout
    assert "1 shard data files" in proc.stdout, proc.stdout + proc.stderr


def _python(job: dict) -> "tuple[int, ...]":
    for step in job["steps"]:
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
            return tuple(
                int(p) for p in step["with"]["python-version"].split(".")
            )
    raise AssertionError("no setup-uv step")


def test_ci_runs_every_shard_and_gates_after_them(shards, recipes):
    """ci.yml runs shards 1..N -- the Makefile's N -- then the union gate.

    Both skip together (a version bump, a tested tree) and both run on a
    docs-only diff; ``CI passed`` waits on both, so a failed or cancelled
    shard is red even though the gate after it is the job that counts.
    Python 3.14 is where coverage.py's default core is sys.monitoring with
    branch coverage, which is most of what makes a shard fit.
    """
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    shard, gate = jobs["coverage-shard"], jobs["coverage"]
    assert shard["strategy"]["matrix"]["shard"] == list(range(1, shards + 1))
    runs = [s.get("run", "") for s in shard["steps"]]
    assert "make coverage-shard COVERAGE_SHARD=${{ matrix.shard }}" in runs
    runs = [s.get("run", "") for s in gate["steps"]]
    assert "make coverage-gate COVERAGE_FROM_SHARDS=1" in runs

    assert "coverage-shard" in gate["needs"]
    passed = jobs["ci-passed"]["needs"]
    assert {"coverage-shard", "coverage"} <= set(passed), passed
    for job in (shard, gate):
        assert "needs.changes.outputs.src == 'true'" in job["if"], job["if"]
        assert "outputs.code" not in job["if"], job["if"]
        assert _python(job) >= (3, 14), job["name"]

    # The artifact carries the directory the Makefile writes and reads.
    root = re.search(r'mkdir -p "([^"]+)/1"', recipes["shard"]).group(1)
    upload = next(
        s for s in shard["steps"] if "upload-artifact" in str(s.get("uses"))
    )
    download = next(
        s for s in gate["steps"] if "download-artifact" in str(s.get("uses"))
    )
    assert upload["with"]["path"].rstrip("/") == root
    assert download["with"]["path"].rstrip("/") == root
    assert download["with"].get("merge-multiple") is True
