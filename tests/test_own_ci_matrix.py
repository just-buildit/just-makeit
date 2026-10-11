"""jm's OWN CI matrix: what it declares, and what each run owes.

Not the matrix `jm ci` generates for a scaffolded project -- that is
`test_ci.py`. This is `.github/workflows/ci.yml` in this repo, which had no
gate at all.

It gained one because the matrix gained an `exclude`. Narrowing which OS runs
a Python version is a legitimate cost decision; **removing a version from the
matrix entirely while `requires-python` still promises it** is a silent lie,
and an `exclude` block is one line away from doing exactly that. The two
declarations -- `pyproject.toml`'s floor and this matrix -- have to agree in
both directions, and nothing was holding them.

gh-2125 made what a run owes depend on the event. The macOS legs bounded PR
throughput (5 of the pool's 20 runners, ~100 of a run's ~340 job-minutes), so
a PR runs macOS at its oldest and newest Python only. The legs it trims are
not left to the nightly, which gates no release: the push that lands the PR
runs exactly those, so `CI passed` on a `main` commit -- what `release.yml`
reads -- still certifies every leg on that tree. The matrix is therefore
declared once, as the JSON `MATRIX` of the toolchain job's `legs` step, and
`scripts/ci-test-legs.py` (`make ci-test-legs`) keeps each run's legs. This
file runs that script for every event the workflow sees, and holds the
workflow to running what it says.

gh-2179 promoted 3.15 when it went final, and the pre-release leg's expiry
step named three more places that list the supported Pythons: the
`pyproject.toml` classifiers and `artifact.yml`'s two pre-publish smoke
matrices. An error message is a note; the tests here hold all of them to
the matrix's released Pythons.

Every parser below RAISES rather than returning empty. A parser that quietly
finds nothing makes every assertion in the file vacuously true, which is how a
gate ends up passing for months over a thing it stopped being able to see.

GATE: every Python in jm's CI matrix runs on macOS in a run that gates a
      release (a PR, or the push that lands it), all but at most the
      requires-python floor; a PR runs macOS at its oldest and newest only.
"""

from __future__ import annotations

import doctest
import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml
from _pyfloor import python_floor

ROOT = Path(__file__).parent.parent
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
PLANNER = ROOT / "scripts" / "ci-test-legs.py"
ARTIFACT_YML = ROOT / ".github" / "workflows" / "artifact.yml"
PYPROJECT = ROOT / "pyproject.toml"


def _load_planner():
    spec = importlib.util.spec_from_file_location("ci_test_legs", PLANNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LEGS = _load_planner()

#: Every run that owes the whole matrix: a push its PR did not test
#: (``changes.yml`` writes ``tested=false``, or nothing when it exits before
#: asking), the nightly, a dispatch.
FULL_RUNS = [
    ("push", "false"),
    ("push", ""),
    ("schedule", ""),
    ("workflow_dispatch", ""),
]


def _jobs() -> dict:
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8")).get("jobs") or {}


def _legs_step() -> dict:
    """The toolchain job's `legs` step: the matrix's one declaration."""
    steps = [
        s
        for s in (_jobs().get("toolchain") or {}).get("steps", [])
        if s.get("id") == "legs"
    ]
    if len(steps) != 1:
        raise AssertionError(
            f"found {len(steps)} `legs` steps in {CI_YML}'s toolchain job, "
            "not one -- the matrix declaration moved, so every assertion in "
            "this file would have been vacuous"
        )
    return steps[0]


def _declared() -> dict:
    """The declared matrix, decoded from the `legs` step's MATRIX."""
    env = _legs_step().get("env") or {}
    try:
        return json.loads(env["MATRIX"])
    except (KeyError, ValueError) as exc:
        raise AssertionError(
            f"the `legs` step has no JSON MATRIX ({exc!r})"
        ) from exc


def _trim() -> "list[str]":
    """The OSes a PR runs at their oldest and newest Python only."""
    return str((_legs_step().get("env") or {}).get("TRIM", "")).split()


def _axes() -> tuple[list[str], list[str]]:
    """The `os` and `python-version` axis lists, as declared."""
    m = _declared()
    missing = {"os", "python-version"} - set(m)
    if missing:
        raise AssertionError(
            f"could not find the {sorted(missing)} matrix axis in {CI_YML} -- "
            "the declaration's shape moved, so every assertion in this file "
            "would have been vacuous"
        )
    return [str(o) for o in m["os"]], [str(p) for p in m["python-version"]]


def _excludes() -> set[tuple[str, str]]:
    """The `(os, python-version)` pairs the matrix excludes. May be empty --
    unlike the axes, an absent `exclude` is a legitimate state, so this is
    the one parser allowed to return nothing."""
    return {
        (str(e.get("os")), str(e.get("python-version")))
        for e in _declared().get("exclude", [])
    }


def _key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def _owed(event: str, tested: str) -> "list[dict]":
    """What the planner keeps for a run on *event*, from the step's own
    declaration -- the inputs the workflow hands it."""
    return LEGS.owed(_declared(), _trim(), event, tested)


def _pairs(legs: "list[dict]") -> "set[tuple[str, str]]":
    return {(str(x["os"]), str(x["python-version"])) for x in legs}


def _full() -> "set[tuple[str, str]]":
    """Every leg the declaration names, worked out here rather than by the
    planner: the axes' product, less the excludes, plus the includes."""
    oses, pys = _axes()
    m = _declared()
    out = {
        (o, p)
        for o in oses
        for p in pys
        if not any(
            e.get("os", o) == o and str(e.get("python-version", p)) == p
            for e in m.get("exclude", [])
        )
    }
    return out | {
        (i["os"], str(i["python-version"])) for i in m.get("include", [])
    }


def _released(pairs: "set[tuple[str, str]]", os_: str) -> "set[str]":
    """*os_*'s Pythons among *pairs*, less any pre-release include."""
    pre = {
        str(i["python-version"])
        for i in _declared().get("include", [])
        if i.get("prerelease")
    }
    return {p for o, p in pairs if o == os_ and p not in pre}


def test_the_matrix_floor_is_the_declared_floor() -> None:
    """`requires-python` promises a version; the matrix has to test it.

    Both directions. Raising the floor without dropping the leg leaves CI
    testing an interpreter the package refuses to install on; dropping the leg
    without raising the floor ships an untested promise.
    """
    _, pys = _axes()
    floor = python_floor()
    assert _key(min(pys, key=_key)) == floor, (
        f'`requires-python = ">={".".join(map(str, floor))}"` but the CI '
        f"matrix's oldest Python is {min(pys, key=_key)}"
    )


def test_every_supported_python_is_tested_on_some_os() -> None:
    """The gate the `exclude` block exists under.

    Narrowing a version to fewer OSes is a cost decision and allowed. Removing
    it from every OS is not -- that is an untested promise, and one more
    `exclude:` entry is all it takes.
    """
    oses, pys = _axes()
    excl = _excludes()
    orphaned = [p for p in pys if all((o, p) in excl for o in oses)]
    assert not orphaned, (
        f"these Python versions are excluded on EVERY os, so nothing tests "
        f"them: {orphaned}. Either restore a leg or raise `requires-python`."
    )


def test_every_os_still_runs_something() -> None:
    """The same argument along the other axis."""
    oses, pys = _axes()
    excl = _excludes()
    orphaned = [o for o in oses if all((o, p) in excl for p in pys)]
    assert not orphaned, f"these OSes run no leg at all: {orphaned}"


def test_an_exclude_names_a_real_cell() -> None:
    """An `exclude` for a pair the axes cannot produce is dead config -- it
    silently does nothing, and reads as coverage having been deliberately
    dropped when it never was."""
    oses, pys = _axes()
    bogus = [
        (o, p)
        for (o, p) in sorted(_excludes())
        if o not in oses or p not in pys
    ]
    assert not bogus, f"exclude names a cell not in the matrix: {bogus}"


def _released_pys() -> "set[str]":
    """Every Python the matrix tests as a RELEASE, on any OS."""
    return {p for o in _axes()[0] for p in _released(_full(), o)}


def _classifiers() -> "set[str]":
    """The `Programming Language :: Python :: 3.N` classifiers jm ships."""
    found = set(
        re.findall(
            r'"Programming Language :: Python :: (3\.\d+)"',
            PYPROJECT.read_text(encoding="utf-8"),
        )
    )
    if not found:
        raise AssertionError(
            f"no `Programming Language :: Python :: 3.N` classifier in "
            f"{PYPROJECT} -- the gate below would compare against nothing"
        )
    return found


def _artifact_pys() -> "dict[str, list[str]]":
    """Each artifact.yml job's FULL `python-version` list, by job.

    The list sits in a `fromJSON(inputs.quick && '[...]' || '[...]')`
    expression; the full one is the branch after `||`. ci.yml calls the
    workflow `quick` on a PR, and `release.yml` calls it full before it
    publishes, so the full list is what the wheel PyPI gets was tried on.
    """
    jobs = yaml.safe_load(ARTIFACT_YML.read_text(encoding="utf-8"))["jobs"]
    out = {}
    for name, job in jobs.items():
        expr = ((job.get("strategy") or {}).get("matrix") or {}).get(
            "python-version"
        )
        if expr is None:
            continue
        m = re.search(r"\|\|\s*'(\[[^']*\])'", str(expr))
        if not m:
            raise AssertionError(
                f"artifact.yml job {name!r}: no full python-version list in "
                f"{expr!r}"
            )
        out[name] = [str(v) for v in json.loads(m.group(1))]
    if not out:
        raise AssertionError(f"no python-version matrix in {ARTIFACT_YML}")
    return out


def test_the_classifiers_are_the_released_matrix() -> None:
    """gh-2179: what PyPI says jm supports is what CI tests.

    Both directions. A classifier the matrix does not run is an untested
    promise; a released Python the matrix runs with no classifier is the
    half-done promotion the pre-release leg's expiry step asks for, which
    the step's error message alone could not hold.
    """
    shipped, tested = _classifiers(), _released_pys()
    assert shipped == tested, (
        f"pyproject classifiers name {sorted(shipped - tested, key=_key)} "
        f"that no released matrix leg runs, and the matrix runs "
        f"{sorted(tested - shipped, key=_key)} that no classifier names"
    )


def test_the_prepublish_artifact_runs_the_released_pythons() -> None:
    """gh-2179: the wheel is smoke-tested on what the matrix promises.

    `smoke` (POSIX) runs every released Python; `smoke-windows` runs the
    bounds and the middle, so it must hold both bounds. A promotion that
    moved only ci.yml would publish a wheel never installed on the new
    Python.
    """
    lists, want = _artifact_pys(), _released_pys()
    edges = {min(want, key=_key), max(want, key=_key)}
    assert set(lists) == {"smoke", "smoke-windows"}, sorted(lists)
    assert set(lists["smoke"]) == want, (lists["smoke"], sorted(want))
    win = set(lists["smoke-windows"])
    assert edges <= win <= want, (sorted(win), sorted(edges))


class TestWhatEachRunOwes:
    """gh-2125: the legs each event's run keeps, from the real declaration."""

    @pytest.mark.parametrize("event, tested", FULL_RUNS)
    def test_a_full_run_owes_every_leg(self, event: str, tested: str) -> None:
        legs = _owed(event, tested)
        assert _pairs(legs) == _full() and len(legs) == len(_full()), (
            f"a {event} run (tested={tested!r}) must run the whole matrix; "
            f"it would skip {sorted(_full() - _pairs(legs))}"
        )

    def test_trim_names_an_os_the_matrix_runs(self) -> None:
        """A TRIM no leg has trims nothing, and every test below that loops
        over it would pass over an empty loop."""
        oses, _ = _axes()
        assert _trim(), "the `legs` step trims no OS"
        assert set(_trim()) <= set(oses), (_trim(), oses)

    def test_a_pr_runs_each_trimmed_os_at_its_edges(self) -> None:
        """The oldest and newest released Python, derived from the matrix,
        so promoting a Python or raising the floor moves them by itself."""
        pr, full = _pairs(_owed("pull_request", "")), _full()
        for os_ in _trim():
            have = _released(full, os_)
            want = {min(have, key=_key), max(have, key=_key)}
            assert _released(pr, os_) == want, (os_, sorted(pr))
        kept = {(o, p) for o, p in full if o not in _trim()}
        assert kept <= pr, f"a PR skips {sorted(kept - pr)}"
        assert pr <= full

    def test_a_tested_push_owes_exactly_what_its_pr_trimmed(self) -> None:
        """The PR and the push that lands it partition the matrix: no leg
        twice, none never. A tested push owing less is gh-2125's trap -- the
        trimmed Pythons tested only by the nightly, which gates no release."""
        pr = _owed("pull_request", "")
        landed = _owed("push", "true")
        assert not _pairs(pr) & _pairs(landed), "a leg runs twice"
        assert _pairs(pr) | _pairs(landed) == _full(), (
            f"no release-gating run tests "
            f"{sorted(_full() - _pairs(pr) - _pairs(landed))}"
        )
        assert len(pr) + len(landed) == len(_full())

    def test_every_python_runs_on_macos_before_a_release(self) -> None:
        """The issue's own gate: each Python in the matrix runs on every
        trimmed OS in a run whose `CI passed` gates a release -- a PR plus
        the push that lands it, or an untested push alone.

        The one exception allowed is the requires-python floor, and only by
        an `exclude`: on macOS that is 3.9, whose interpreter there is
        Apple's own build (see the `legs` step). At MOST the floor, so the
        exception lapses when the floor moves, and no other narrowing of a
        trimmed OS passes.
        """
        _, pys = _axes()
        floor = ".".join(map(str, python_floor()))
        paths = {
            "a PR, then the push that lands it": _pairs(
                _owed("pull_request", "") + _owed("push", "true")
            ),
            "an untested push": _pairs(_owed("push", "false")),
        }
        for os_ in _trim():
            for path, pairs in paths.items():
                missing = set(pys) - {floor} - _released(pairs, os_)
                assert not missing, (
                    f"{os_} never runs {sorted(missing, key=_key)} before a "
                    f"release on {path}"
                )


#: A GitHub `if:` built only from these runs here as Python. Anything
#: else -- a function call, another context -- fails the match, so the
#: evaluator below cannot quietly mis-read an expression it does not know.
_IF_TOKEN = re.compile(
    r"\s+|\(|\)|&&|\|\||==|!=|'[^']*'|needs\.[\w-]+\.outputs\.[\w-]+"
)


def _runs(job: str, outputs: "dict[str, str]") -> bool:
    """Evaluate *job*'s `if:` with these `needs.*.outputs` (default '')."""
    expr = " ".join(str(_jobs()[job]["if"]).split())
    assert not _IF_TOKEN.sub("", expr), f"{job}: unknown syntax in {expr!r}"
    py = expr.replace("&&", " and ").replace("||", " or ")
    py = re.sub(
        r"needs\.([\w-]+)\.outputs\.([\w-]+)",
        lambda m: repr(outputs.get(f"{m.group(1)}.{m.group(2)}", "")),
        py,
    )
    return bool(eval(py, {"__builtins__": {}}, {}))


class TestTheWorkflowRunsWhatThePlannerSays:
    """The planner is only the answer if the workflow asks it, uses what it
    says, and runs the job on the push that owes the trimmed legs."""

    def test_the_test_job_reads_the_planners_matrix(self) -> None:
        jobs = _jobs()
        step = _legs_step()
        assert jobs["test"]["strategy"]["matrix"] == (
            "${{ fromJSON(needs.toolchain.outputs.matrix) }}"
        )
        assert jobs["toolchain"]["outputs"]["matrix"] == (
            "${{ steps.legs.outputs.matrix }}"
        )
        assert step["run"] == 'make -s ci-test-legs >> "$GITHUB_OUTPUT"'
        assert step["env"]["EVENT"] == "${{ github.event_name }}"
        assert step["env"]["TESTED"] == "${{ needs.changes.outputs.tested }}"

    @pytest.mark.parametrize(
        "outputs, runs",
        [
            ({"changes.src": "true", "changes.code": "true"}, True),
            ({"changes.src": "true", "changes.code": "false"}, False),
            ({"changes.src": "false", "changes.tested": "true"}, True),
            ({"changes.src": "false", "changes.tested": "false"}, False),
        ],
        ids=["code", "docs-only", "tested-push", "bump"],
    )
    def test_the_legs_run_when_a_run_owes_them(
        self, outputs: "dict[str, str]", runs: bool
    ) -> None:
        """The toolchain job (which plans) and the test job (which runs)
        both run on a tested push, and neither on a bump or docs-only diff."""
        planned = dict(outputs, **{"toolchain.matrix": '{"include": [1]}'})
        assert _runs("toolchain", outputs) is runs
        assert _runs("test", planned) is runs

    def test_nothing_owed_skips_rather_than_fails(self) -> None:
        """GitHub refuses an empty matrix outright, so `matrix=` must skip."""
        owes = {"changes.src": "false", "changes.tested": "true"}
        assert _runs("test", dict(owes, **{"toolchain.matrix": ""})) is False

    def test_ci_passed_counts_the_owed_legs(self) -> None:
        """Under src=false the aggregator skips the RESULTS loop, so the
        legs a tested push owes reach it by name. Executed in
        test_ci_passed_aggregator.py; this holds the wiring."""
        env = _jobs()["ci-passed"]["env"]
        owed = env.get("OWED", "")
        for job in ("toolchain", "test"):
            assert f"needs.{job}.result" in owed, (job, owed)

    def test_a_leg_has_room_to_finish(self) -> None:
        """gh-2125: over 33 PR runs each macOS leg's median was 20-21 min,
        every version had 3-7 runs past 25, and 30 minutes cancelled #2096's
        3.12 leg at 85%. A cancel there reads as `CI passed` red with no
        failing test, so the ceiling sits clear of the measured tail."""
        assert _jobs()["test"]["timeout-minutes"] >= 40


class TestTheParsersAreArmed:
    """A scan that finds nothing must be proven able to find something."""

    def test_the_axes_are_actually_read(self) -> None:
        oses, pys = _axes()
        assert "ubuntu-latest" in oses and "3.9" in pys

    def test_the_exclude_parser_sees_the_live_one(self) -> None:
        """Pinned to the real entry: if the exclude is removed this fails, and
        the coverage tests above would otherwise go quietly vacuous the moment
        the block's shape changed."""
        assert ("macos-latest", "3.9") in _excludes()

    def test_a_moved_declaration_raises_rather_than_passing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The vacuity guard itself. Point the parser at a file with no matrix
        and it must raise, not return empty and let every test above pass."""
        empty = tmp_path / "ci.yml"
        empty.write_text("name: CI\njobs: {}\n", encoding="utf-8")
        monkeypatch.setattr("test_own_ci_matrix.CI_YML", empty)
        with pytest.raises(AssertionError, match="declaration moved"):
            _axes()

    def test_the_planners_examples_run(self) -> None:
        result = doctest.testmod(LEGS)
        assert result.attempted and not result.failed, result

    @pytest.mark.parametrize("event", ["pull_request", "push", "schedule"])
    def test_the_planner_prints_what_it_keeps(
        self,
        event: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """`main` -- what `make ci-test-legs` runs -- hands GitHub exactly
        the legs `owed` keeps, and an empty value when there are none."""
        env = _legs_step()["env"]
        tested = "true" if event == "push" else ""
        for name, value in dict(env, EVENT=event, TESTED=tested).items():
            monkeypatch.setenv(name, str(value))
        assert LEGS.main() == 0
        line = capsys.readouterr().out.strip()
        assert line.startswith("matrix=")
        got = json.loads(line[len("matrix=") :])["include"]
        assert got == _owed(event, tested) and got

        monkeypatch.setenv("TRIM", "")
        monkeypatch.setenv("EVENT", "push")
        monkeypatch.setenv("TESTED", "true")
        LEGS.main()
        assert capsys.readouterr().out == "matrix=\n"
