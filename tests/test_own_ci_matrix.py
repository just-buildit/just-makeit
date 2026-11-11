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
matrices. gh-2626 made the classifiers the ONLY list: the planner fills
every matrix from them, and a workflow names a Python by its place among
them (`oldest`, `middle`, `newest`, `next`), never a list of numbers. The
tests here hold the planner to the classifiers, refuse a job that names
more than one Python, and hold the example builds to the newest Python.

Every parser below RAISES rather than returning empty. A parser that quietly
finds nothing makes every assertion in the file vacuously true, which is how a
gate ends up passing for months over a thing it stopped being able to see.

GATE: every Python in jm's CI matrix runs on macOS in a run that gates a
      release (a PR, or the push that lands it), all but at most the
      requires-python floor; a PR runs macOS and arm Linux at their oldest and newest only, and
      ubuntu-latest at those and the middle Python.
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
RUNNER = ROOT / "scripts" / "ci-runner.py"
ARTIFACT_YML = ROOT / ".github" / "workflows" / "artifact.yml"
WORKFLOWS = ROOT / ".github" / "workflows"
PYPROJECT = ROOT / "pyproject.toml"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LEGS = _load("ci_test_legs", PLANNER)
#: The org standard's runner picker, vendored (just-buildit.github.io#203).
PICK = _load("ci_runner", RUNNER)

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
    """The matrix the `legs` step runs: its MATRIX, on the classifiers'
    Pythons, as the planner fills it in (gh-2626)."""
    env = _legs_step().get("env") or {}
    try:
        raw = json.loads(env["MATRIX"])
    except (KeyError, ValueError) as exc:
        raise AssertionError(
            f"the `legs` step has no JSON MATRIX ({exc!r})"
        ) from exc
    return LEGS.resolve(raw, LEGS.pythons())


def _trim() -> "list[str]":
    """The OSes a PR runs at their oldest and newest Python only."""
    return str((_legs_step().get("env") or {}).get("TRIM", "")).split()


def _middle() -> "list[str]":
    """The trimmed OSes whose PR legs also keep the middle Python."""
    return str((_legs_step().get("env") or {}).get("MIDDLE", "")).split()


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
    return LEGS.owed(_declared(), _trim(), _middle(), event, tested)


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
    """Each artifact.yml smoke job's FULL Pythons, by job.

    ci.yml calls the workflow `quick` on a PR, and `release.yml` calls it
    full before it publishes, so the full legs are what the wheel PyPI
    gets was tried on. Both come from the `legs` job (gh-2626); this holds
    each job to reading its output.
    """
    jobs = yaml.safe_load(ARTIFACT_YML.read_text(encoding="utf-8"))["jobs"]
    win = " ".join(str(jobs["smoke-windows"]["strategy"]["matrix"]).split())
    assert win == (
        "${{ fromJSON(inputs.quick && needs.legs.outputs.windows_quick"
        " || needs.legs.outputs.windows) }}"
    ), win
    legs = _artifact_legs()
    return {
        "smoke": _artifact_pythons(_artifact_matrix(False)),
        "smoke-windows": _artifact_pythons(legs["windows"]),
    }


def test_the_planner_reads_the_classifiers(
    capsys: pytest.CaptureFixture,
) -> None:
    """gh-2626: the planner's Pythons are the classifiers, read here by an
    independent parser, and `make ci-pythons` prints exactly them."""
    want = sorted(_classifiers(), key=_key)
    assert LEGS.pythons() == want
    assert LEGS.main(["pythons"]) == 0
    assert capsys.readouterr().out.split() == want


def test_a_matrix_listing_its_own_pythons_is_refused() -> None:
    """A second list of the supported Pythons is what drifts from the
    first, so the planner refuses a matrix that brings one."""
    with pytest.raises(ValueError, match="classifiers alone"):
        LEGS.resolve({"os": ["x"], "python-version": ["3.9"]}, ["3.9"])


def test_the_classifiers_are_the_released_matrix() -> None:
    """gh-2179: what PyPI says jm supports is what CI tests.

    Both directions. A classifier the matrix does not run is an untested
    promise; a released Python the matrix runs with no classifier is the
    half-done promotion the pre-release leg's expiry step asks for.
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
            if os_ in _middle():
                want.add(sorted(have, key=_key)[len(have) // 2])
            assert _released(pr, os_) == want, (os_, sorted(pr))
        kept = {(o, p) for o, p in full if o not in _trim()}
        assert kept <= pr, f"a PR skips {sorted(kept - pr)}"
        assert pr <= full

    def test_the_pr_legs_are_exactly_the_trimmed_set(self) -> None:
        """gh-2125 + the Linux trim: the PR's legs, written out. Adding a
        leg to the PR set (a wider TRIM edge, a MIDDLE os) fails here until
        this table is changed on purpose, with the reason in the PR. The
        Pythons are the classifiers' places, read by this file's own
        parser, so promoting one moves the table by itself (gh-2626)."""
        pys = sorted(_classifiers(), key=_key)
        floor, newest = pys[0], pys[-1]
        middle = pys[len(pys) // 2]
        mac_floor = min(_released(_full(), "macos-latest"), key=_key)
        assert _pairs(_owed("pull_request", "")) == {
            ("ubuntu-latest", floor),
            ("ubuntu-latest", middle),
            ("ubuntu-latest", newest),
            ("ubuntu-24.04-arm", floor),
            ("ubuntu-24.04-arm", newest),
            ("macos-latest", mac_floor),
            ("macos-latest", newest),
        }
        assert len(_owed("pull_request", "")) == 7

    def test_middle_is_a_trimmed_os(self) -> None:
        """A MIDDLE os that is not trimmed would do nothing, silently."""
        assert _middle() and set(_middle()) <= set(_trim())

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
            "${{ steps.runners.outputs.matrix }}"
        )
        assert step["run"] == 'make -s ci-test-legs >> "$GITHUB_OUTPUT"'
        assert _runners_step()["env"]["LEGS"] == (
            "${{ steps.legs.outputs.matrix }}"
        )
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
        assert "ubuntu-latest" in oses
        assert pys == sorted(_classifiers(), key=_key) and len(pys) > 1

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
        assert LEGS.main([]) == 0
        line = capsys.readouterr().out.splitlines()[0]
        assert line.startswith("matrix=")
        got = json.loads(line[len("matrix=") :])["include"]
        assert got == _owed(event, tested) and got

        monkeypatch.setenv("TRIM", "")
        monkeypatch.setenv("EVENT", "push")
        monkeypatch.setenv("TESTED", "true")
        LEGS.main([])
        assert capsys.readouterr().out.splitlines()[0] == "matrix="


# ---------------------------------------------------------------------------
# just-buildit.github.io#203: which runner each macOS leg gets.


def _step(job: dict, sid: str, where: str) -> dict:
    steps = [s for s in job.get("steps") or [] if s.get("id") == sid]
    assert len(steps) == 1, f"{where} has no single `{sid}` step"
    return steps[0]


def _runners_step() -> dict:
    """The toolchain job's `runners` step: `make ci-runner`."""
    return _step(_jobs()["toolchain"], "runners", "ci.yml's toolchain job")


def _artifact_plan(sid: str) -> dict:
    jobs = yaml.safe_load(ARTIFACT_YML.read_text(encoding="utf-8"))["jobs"]
    return _step(jobs["plan"], sid, "artifact.yml's plan job")


def _artifact_legs() -> "dict[str, dict]":
    """What artifact.yml's `legs` job prints, by output name: each
    LEGS_<NAME> of its step, through the planner, in include form."""
    jobs = yaml.safe_load(ARTIFACT_YML.read_text(encoding="utf-8"))["jobs"]
    step = _step(jobs["legs"], "legs", "artifact.yml's legs job")
    assert step["run"] == 'make -s ci-test-legs >> "$GITHUB_OUTPUT"'
    out = {
        k[len("LEGS_") :].lower(): {
            "include": LEGS.expand(LEGS.resolve(json.loads(v), LEGS.pythons()))
        }
        for k, v in (step.get("env") or {}).items()
        if k.startswith("LEGS_")
    }
    assert {"smoke", "quick", "windows", "windows_quick"} <= set(out), out
    for name in out:
        assert jobs["legs"]["outputs"][name] == (
            "${{ steps.legs.outputs.%s }}" % name
        )
    return out


def _artifact_matrix(quick: bool) -> dict:
    """artifact.yml's smoke legs, in include form, for one value of the
    `quick` input: the `runners` step's LEGS, `quick && .. || ..`, each
    side one of the `legs` job's outputs."""
    expr = " ".join(str(_artifact_plan("runners")["env"]["LEGS"]).split())
    assert expr == (
        "${{ inputs.quick && needs.legs.outputs.quick"
        " || needs.legs.outputs.smoke }}"
    ), expr
    return _artifact_legs()["quick" if quick else "smoke"]


def _artifact_pythons(matrix: dict) -> "list[str]":
    """The Pythons an include-form smoke matrix runs, first-seen order."""
    out: "list[str]" = []
    for leg in matrix["include"]:
        if leg["python-version"] not in out:
            out.append(leg["python-version"])
    return out


def _plan(
    step: dict,
    event: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    fetch=None,
    runners: "dict | None" = None,
    **env: str,
) -> "dict[str, list[dict]]":
    """What a `legs` step and its `runners` step hand GitHub: `make
    ci-test-legs`, then, with EXAMPLES when the runners step declares it,
    `make ci-runner` -- both in-process."""
    for name, value in (step.get("env") or {}).items():
        if not str(value).startswith("${{"):
            monkeypatch.setenv(name, str(value))
    monkeypatch.setenv("EVENT", event)
    monkeypatch.setenv("TESTED", "")
    assert LEGS.main([]) == 0
    lines = capsys.readouterr().out.splitlines()
    # What the runners step hands `make ci-runner`: the outputs its env
    # reads from the `legs` step (`LEGS`, and `EXAMPLES` when it has it).
    reads = {str(v): k for k, v in ((runners or {}).get("env") or {}).items()}
    names = {"matrix"} | {
        n for n in ("examples",) if "${{ steps.legs.outputs.%s }}" % n in reads
    }
    out, _ = PICK.pick(
        [x for x in lines if x.split("=", 1)[0] in names],
        ("runs_on", "os"),
        {
            "GITHUB_EVENT_NAME": event,
            "GITHUB_REPOSITORY": "just-buildit/just-makeit",
            **env,
        },
        fetch,
    )
    plan = {}
    for line in out:
        name, _, value = line.partition("=")
        plan[name] = json.loads(value)["include"]
    return plan


MAC = ["self-hosted", "macOS", "ARM64"]


def _one_idle_mac(path: str) -> dict:
    """The org runner API with one idle self-hosted Mac, for `pick`."""
    if "/actions/runs" in path:
        return {"workflow_runs": []}
    if "/runner-groups?" in path:
        return {
            "runner_groups": [
                {
                    "id": 1,
                    "visibility": "all",
                    "allows_public_repositories": True,
                }
            ]
        }
    return {
        "runners": [
            {
                "status": "online",
                "busy": False,
                "runner_group_id": 1,
                "labels": [{"name": x} for x in MAC],
            }
        ]
    }


class TestEachMacosLegsRunner:
    """Every macOS leg that can use the org's own Mac asks `make ci-runner`
    for its runner; the rest are named, with their reason."""

    #: Jobs that stay on a stock hosted runner on purpose: they test
    #: installing onto one, which a prepared VM would not.
    STOCK = {"install-deps-smoke", "consumer-smoke"}

    def _macos_jobs(self) -> "dict[tuple[str, str], dict]":
        out = {}
        for path in (CI_YML, ARTIFACT_YML):
            text = yaml.safe_load(path.read_text(encoding="utf-8"))
            for name, job in text["jobs"].items():
                if (
                    "macos" in json.dumps(job.get("strategy") or {})
                    or ("macos" in str(job.get("runs-on")))
                    or name in ("test", "examples", "smoke")
                ):
                    out[(path.name, name)] = job
        return out

    def test_the_jobs_read_runs_on_from_ci_runner(self) -> None:
        jobs = self._macos_jobs()
        routed = {k for k, j in jobs.items() if k[1] not in self.STOCK}
        assert routed == {
            ("ci.yml", "test"),
            ("ci.yml", "examples"),
            ("artifact.yml", "smoke"),
        }, sorted(routed)
        for key in routed:
            assert jobs[key]["runs-on"] == "${{ matrix.runs_on }}", key
        assert {k[1] for k in jobs} >= self.STOCK

    def test_the_steps_pass_the_opt_in_and_the_token(self) -> None:
        for step, own in (
            (_runners_step(), CI_YML),
            (_artifact_plan("runners"), ARTIFACT_YML),
        ):
            # A runner group restricted to selected workflows is matched
            # against the file whose jobs the plan feeds: its own.
            want = f"CI_RUNNER_WORKFLOW=.github/workflows/{own.name}"
            assert want in step["run"], step["run"]
            env = step["env"]
            assert env["JB_SELF_HOSTED_MACOS"] == (
                "${{ vars.JB_SELF_HOSTED_MACOS }}"
            )
            assert env["JB_RUNNER_READ_TOKEN"] == (
                "${{ secrets.JB_RUNNER_READ_TOKEN }}"
            )
            assert env["GITHUB_TOKEN"] == "${{ github.token }}"
            assert "make -s ci-runner" in step["run"]
        # One call shares the Macs out over both jobs, the test legs first.
        run = _runners_step()["run"]
        assert run.index('"$LEGS"') < run.index('"$EXAMPLES"'), run

    def test_the_token_reaches_ci_runner_alone(self) -> None:
        """The planner steps never see a token; each planning job reads
        only what it needs."""
        art = yaml.safe_load(ARTIFACT_YML.read_text(encoding="utf-8"))
        step = _legs_step()
        env = " ".join(str(v) for v in (step.get("env") or {}).values())
        assert "secrets." not in env and "github.token" not in env
        assert "ci-runner" not in step["run"]
        # artifact.yml is reached by workflow_call and workflow_run, where
        # canonical's self-hosted-check allows only a plain checkout before
        # `make ci-runner`: so no planner step there at all.
        plan = art["jobs"]["plan"]["steps"]
        assert [x.get("uses", x.get("id")) for x in plan] == [
            plan[0]["uses"],
            "runners",
        ] and plan[0]["uses"].startswith("actions/checkout@")
        for job in (_jobs()["toolchain"], art["jobs"]["plan"]):
            assert job["permissions"] == {
                "contents": "read",
                "actions": "read",
            }

    def test_release_hands_artifact_the_token(self) -> None:
        rel = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "release.yml").read_text(
                encoding="utf-8"
            )
        )
        assert rel["jobs"]["artifact-smoke"]["secrets"] == {
            "JB_RUNNER_READ_TOKEN": "${{ secrets.JB_RUNNER_READ_TOKEN }}"
        }

    @pytest.mark.parametrize("event", ["pull_request", "push", "schedule"])
    def test_without_a_token_every_leg_stays_hosted(
        self,
        event: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """The default until the owner opts in, and any repo or fork PR
        without the secret: each leg's runner is its own `os`."""
        plan = _plan(
            _legs_step(),
            event,
            monkeypatch,
            capsys,
            runners=_runners_step(),
            JB_SELF_HOSTED_MACOS="true",
        )
        assert set(plan) == {"matrix", "examples"}
        legs = plan["matrix"] + plan["examples"]
        assert legs and all(x["runs_on"] == x["os"] for x in legs)
        assert plan["matrix"] == [
            dict(x, runs_on=x["os"]) for x in _owed(event, "")
        ]

    def test_an_idle_mac_goes_to_a_test_leg_first(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _plan(
            _legs_step(),
            "push",
            monkeypatch,
            capsys,
            _one_idle_mac,
            runners=_runners_step(),
            JB_SELF_HOSTED_MACOS="true",
            JB_RUNNER_READ_TOKEN="t",
            GITHUB_TOKEN="g",
        )
        got = [
            (job, x["os"], x["runs_on"])
            for job in ("matrix", "examples")
            for x in plan[job]
            if x["runs_on"] == MAC
        ]
        assert len(got) == 1 and got[0][:2] == ("matrix", "macos-latest")
        assert all(
            x["runs_on"] == "macos-latest"
            for x in plan["examples"]
            if x["os"] == "macos-latest"
        )

    @pytest.mark.parametrize("quick", [True, False])
    def test_the_smoke_plan_is_the_matrix_it_replaced(
        self, quick: bool
    ) -> None:
        """Same legs as the static matrix it replaced: quick is one Linux
        leg, at the middle Python; full is every os on every Python (a
        complete grid, so a promotion cannot reach one os only), the Linux
        floor leg on ubuntu-24.04 (setup-python has no 3.9 for Ubuntu
        26.04). The Pythons are the classifiers, read by this file."""
        pys = sorted(_classifiers(), key=_key)
        m = _artifact_matrix(quick)
        out, _ = PICK.pick(
            ["matrix=" + json.dumps(m)],
            ("runs_on", "os"),
            {"GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": "o/r"},
        )
        legs = json.loads(out[0].partition("=")[2])["include"]
        got = [(x["os"], x["python-version"], x["runs_on"]) for x in legs]
        if quick:
            middle = pys[len(pys) // 2]
            assert got == [("ubuntu-latest", middle, "ubuntu-latest")]
            return
        oses = sorted({x["os"] for x in legs})
        assert oses == ["macos-latest", "ubuntu-latest"]
        want = {
            (
                os_,
                py,
                "ubuntu-24.04"
                if (os_, py) == ("ubuntu-latest", pys[0])
                else os_,
            )
            for os_ in oses
            for py in pys
        }
        assert set(got) == want and len(got) == len(want)


# ---------------------------------------------------------------------------
# gh-2626: the Pythons are the classifiers alone, and the example builds
# compile against the newest of them.

#: A Python version as a workflow spells one: a `3.N` standing alone, so
#: `v10.3.3`, `ubuntu-24.04` and `@v7.0.1` are not read as one.
_PY_LITERAL = re.compile(r"(?<![\w.])3\.\d{1,2}(?![\w.])")


def _named_pythons(node: object) -> "set[str]":
    """Every Python *node* (a parsed workflow job) names, outside `run:`.

    A `run:` script is shell, and builds things with versions of their own
    (`cmake_minimum_required(VERSION 3.16)`); every other value -- an
    `env`, a `with:`, a matrix, an `if:` -- is where a workflow names a
    Python. An unquoted `3.12` parses as a float, so a number counts too.
    """
    if isinstance(node, dict):
        return {
            p for k, v in node.items() if k != "run" for p in _named_pythons(v)
        }
    if isinstance(node, list):
        return {p for v in node for p in _named_pythons(v)}
    if isinstance(node, (str, float)):
        return set(_PY_LITERAL.findall(str(node)))
    return set()


def _workflow_jobs() -> "dict[tuple[str, str], dict]":
    """Every job of every workflow, by (file, job)."""
    out = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for name, job in (doc.get("jobs") or {}).items():
            out[(path.name, name)] = job
    if not out:
        raise AssertionError(f"no workflow job under {WORKFLOWS}")
    return out


def test_no_workflow_job_lists_pythons() -> None:
    """The rule: the supported Pythons are the pyproject classifiers, and
    a CI job names at most ONE Python by number -- a setup pin, a single
    leg's condition, an `exclude` cell. Two in one job is a list of them,
    which drifts from the classifiers; name a Python by its place instead
    (`oldest`, `middle`, `newest`, `next`), which `make ci-test-legs`
    resolves. Registration-free: every job of every workflow."""
    many = {
        f"{f}:{job}": sorted(pys, key=_key)
        for (f, job), body in _workflow_jobs().items()
        for pys in [_named_pythons(body)]
        if len(pys) > 1
    }
    assert not many, (
        f"these jobs list Pythons of their own: {many}. The classifiers "
        "in pyproject.toml are the one list (gh-2626)"
    )


def test_the_python_scan_is_armed() -> None:
    """It sees a pin, a list in each shape a workflow could write one, and
    nothing in a `run:` script or an action's version."""
    assert any(_named_pythons(j) for j in _workflow_jobs().values())
    for shape in (
        {"strategy": {"matrix": {"python-version": ["3.9", "3.15"]}}},
        {
            "env": {
                "M": '{"include": [{"python-version": "3.9"}, '
                '{"python-version": "3.15"}]}'
            }
        },
        {
            "strategy": {
                "matrix": {
                    "include": [
                        {"python-version": 3.12},
                        {"python-version": "3.15"},
                    ]
                }
            }
        },
    ):
        assert len(_named_pythons(shape)) == 2, shape
    assert not _named_pythons(
        {"run": "cmake 3.16; python3.12", "uses": "a/b@v10.3.3"}
    )


def _ci_legs(name: str) -> "list[dict]":
    """The `legs` step's LEGS_<NAME>, as the planner resolves it."""
    env = _legs_step().get("env") or {}
    key = f"LEGS_{name.upper()}"
    assert key in env, f"the `legs` step plans no {key}"
    return LEGS.expand(LEGS.resolve(json.loads(env[key]), LEGS.pythons()))


class TestTheExamplesBuildOnTheNewestPython:
    """gh-2626: the jobs that build the bundled examples -- generated
    projects whose C extensions compile against Python's C API -- ran 3.12
    alone, so no generated extension was ever compiled against the newest
    Python jm declares. Each now runs it too, derived from the classifiers,
    so a promotion reaches the examples with no edit to the workflow."""

    #: Each examples job, and the toolchain output it reads its legs from.
    JOBS = {"examples": "examples", "examples-windows": "examples_windows"}

    def test_linux_and_windows_build_at_the_newest(self) -> None:
        newest = max(_classifiers(), key=_key)
        assert ("ubuntu-latest", newest) in _pairs(_ci_legs("examples"))
        assert ("windows-latest", newest) in _pairs(
            _ci_legs("examples_windows")
        )

    def test_every_os_keeps_its_middle_leg(self) -> None:
        """The legs the examples ran before gh-2626, at the middle
        Python (3.12 then), are all still there."""
        pys = sorted(_classifiers(), key=_key)
        middle = pys[len(pys) // 2]
        posix = {"ubuntu-latest", "macos-latest", "ubuntu-24.04-arm"}
        for name, oses in (
            ("examples", posix),
            ("examples_windows", {"windows-latest"}),
        ):
            assert {(o, middle) for o in oses} <= _pairs(_ci_legs(name))

    def test_every_leg_is_a_declared_python(self) -> None:
        for name in self.JOBS.values():
            pys = {p for _, p in _pairs(_ci_legs(name))}
            assert pys <= _classifiers(), (name, sorted(pys, key=_key))

    def test_gate_a_shrinks_on_one_leg(self) -> None:
        """Gate A's ratchet may only shrink where it was recorded: one
        ubuntu-latest leg, not every Python on it."""
        marked = [x for x in _ci_legs("examples") if x.get("shrink")]
        assert len(marked) == 1 and marked[0]["os"] == "ubuntu-latest"
        steps = [
            s
            for s in _jobs()["examples"]["steps"]
            if "JM_GATE_A_SHRINK" in (s.get("env") or {})
        ]
        assert len(steps) == 1
        assert steps[0]["env"]["JM_GATE_A_SHRINK"] == (
            "${{ matrix.shrink && '1' || '' }}"
        )

    def test_the_planner_prints_them(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """What `make ci-test-legs` prints is what the tests above read."""
        for name, value in (_legs_step().get("env") or {}).items():
            monkeypatch.setenv(name, str(value))
        monkeypatch.setenv("EVENT", "pull_request")
        assert LEGS.main([]) == 0
        lines = dict(
            x.split("=", 1) for x in capsys.readouterr().out.splitlines()
        )
        for out in self.JOBS.values():
            assert json.loads(lines[out])["include"] == _ci_legs(out)

    def test_each_job_runs_its_planned_legs(self) -> None:
        jobs = _jobs()
        tool = jobs["toolchain"]["outputs"]
        assert tool["examples"] == "${{ steps.runners.outputs.examples }}"
        assert _runners_step()["env"]["EXAMPLES"] == (
            "${{ steps.legs.outputs.examples }}"
        )
        assert tool["examples_windows"] == (
            "${{ steps.legs.outputs.examples_windows }}"
        )
        for job, out in self.JOBS.items():
            body = jobs[job]
            assert "toolchain" in body["needs"], job
            assert body["strategy"]["matrix"] == (
                "${{ fromJSON(needs.toolchain.outputs.%s) }}" % out
            ), job
            pins = [
                s["with"]["python-version"]
                for s in body["steps"]
                if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")
            ]
            assert pins == ["${{ matrix.python-version }}"], (job, pins)
            assert job in jobs["ci-passed"]["needs"], job
