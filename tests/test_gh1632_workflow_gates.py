"""gh-1632: a workflow check must be able to fail, and must gate something.

v0.90.0 failed before publish because the release's artifact smoke still
wrote the pre-schema-8 layout. Two things let that reach a tag:

- **No PR execution home.** `artifact.yml` ran only on a release, so three
  layout PRs changed what it asserts and merged green. `ci.yml` now calls it
  on every source change, and a job only gates anything if `CI passed` --
  the one required check -- waits on it. So every job in `ci.yml` must be
  in `ci-passed`'s `needs`, read from the file rather than listed here.
- **A check that passes on a missing file.** ``! grep X path`` inverts
  grep's exit 2 ("no such file") into success. The NCO step's header had
  moved and the step stayed green. A ``! grep`` on a path must be preceded,
  in the same step, by ``test -f`` of that path.

GATE: no workflow but ci.yml runs on a PR (gh-1643, a shrink-only set aside);
      ci.yml builds the docker image on every source change and publishes
      it only on a push; every ci.yml job but the aggregator and what runs
      after it feeds `CI passed`; ci.yml runs the artifact smoke from the wheel `make wheel`
      builds; no workflow step negates a grep of a file it has not proven
      exists, or negates any command, which bash -e would not stop on; no
      workflow or doc wires or describes a merge queue jm does not have; ci.yml
      has a nightly that never skips, in a concurrency group of its own; a
      docs-only diff skips every ci.yml job but lint, coverage and the strict
      docs build, which gates `CI passed`.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _jobs(name: str) -> dict:
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))["jobs"]


def _needs(job: dict) -> "list[str]":
    n = job.get("needs") or []
    return [n] if isinstance(n, str) else list(n)


def test_every_ci_job_feeds_ci_passed():
    jobs = _jobs("ci.yml")
    after = {k for k, j in jobs.items() if "ci-passed" in _needs(j)}
    fed = set(_needs(jobs["ci-passed"]))
    missing = sorted(set(jobs) - {"ci-passed"} - after - fed)
    assert missing == [], (
        "these ci.yml jobs gate nothing -- `CI passed` does not wait on "
        f"them: {missing}"
    )


def test_ci_runs_the_artifact_smoke_from_the_wheel_it_builds():
    jobs = _jobs("ci.yml")
    callers = [
        j
        for j in jobs.values()
        if j.get("uses") == "./.github/workflows/artifact.yml"
    ]
    assert len(callers) == 1, "ci.yml must call artifact.yml once"
    wheel = callers[0]["with"]["wheel-artifact"]
    builders = [
        j
        for k, j in jobs.items()
        if k in _needs(callers[0])
        and any(s.get("run") == "make wheel" for s in j.get("steps", []))
        and any(
            (s.get("with") or {}).get("name") == wheel
            for s in j.get("steps", [])
        )
    ]
    assert builders, (
        f"no job the smoke needs builds `{wheel}` with `make wheel`, the "
        "command the release builds with"
    )


# ── gh-1643: nothing that runs on a PR fails where no one must look ─────────
#
# docker.yml ran on PRs and on main with triggers of its own and fed no
# required check. #1602 renamed the `.pc` its smoke asks pkg-config for, and
# it was red on main for 9 hours while every PR merged green -- the gh-1089
# pattern again (14 red runs). Its PR `paths` also omitted docker.yml itself,
# so the PR fixing it never ran it. ci.yml now calls it; the rule that keeps
# the next workflow from repeating this is that `CI passed` is the only thing
# a PR is required to pass, so a workflow with its own PR trigger gates
# nothing. These may only shrink (gh-1782).
# Empty since gh-1782: docs.yml became deploy-only (#1810) and
# nco_tone_ci.yml was deleted, its PR run being the required `examples` job's
# test_example[nco_tone] and its doppler-drift schedule ci.yml's nightly.
_PR_WORKFLOWS_OUTSIDE_CI: "dict[str, str]" = {}


def _on(name: str) -> dict:
    doc = yaml.safe_load((WF / name).read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as the boolean True.
    on = doc.get(True, doc.get("on")) or {}
    if isinstance(on, (str, list)):
        on = {e: None for e in ([on] if isinstance(on, str) else on)}
    return on


def test_no_workflow_but_ci_runs_on_a_pull_request():
    pr = {
        wf.name
        for wf in WF.glob("*.yml")
        if {"pull_request", "pull_request_target"} & set(_on(wf.name))
    }
    assert "ci.yml" in pr, "the walk below must have something to find"
    extra = sorted(pr - {"ci.yml"} - set(_PR_WORKFLOWS_OUTSIDE_CI))
    assert extra == [], (
        f"{extra} run on a pull_request but `CI passed` -- the one required "
        "check -- cannot wait on them, so a red run merges. Call the "
        "workflow from a ci.yml job instead (see the `docker` job)"
    )
    stale = sorted(set(_PR_WORKFLOWS_OUTSIDE_CI) - pr)
    assert stale == [], f"fixed; drop from _PR_WORKFLOWS_OUTSIDE_CI: {stale}"


def test_main_has_a_nightly_full_run_of_its_own():
    """gh-1801 item 2: a nightly that never skips, in its own concurrency group.

    main skips every tree its PR tested, so the nightly is where drift no
    diff causes goes red. changes.yml must send `schedule` to src=true, and
    the run must not share main's group, or the next merge's pending run
    supersedes it and it never runs (gh-1763's mechanism).
    """
    doc = yaml.safe_load((WF / "ci.yml").read_text(encoding="utf-8"))
    assert _on("ci.yml").get("schedule"), "ci.yml has no nightly"
    group = doc["concurrency"]["group"]
    assert "github.event_name == 'schedule' && 'nightly'" in group, group
    changes = (WF / "changes.yml").read_text(encoding="utf-8")
    arm = re.search(r"^\s+([\w|]+)\)\s*\n\s+echo \"src=true\"", changes, re.M)
    assert arm and "schedule" in arm.group(1).split("|"), (
        "changes.yml must send a scheduled run to src=true"
    )


# The ci.yml jobs a docs-only diff can break, so they run on one (gh-1801
# item 3): lint (mdformat reads the docs), coverage (the full suite on one
# leg; tests read the live tree), and the strict docs build. Plus the jobs
# that are not checks of the diff at all.
_DOCS_CAN_BREAK = {"lint", "coverage", "docs"}
_NOT_A_CHECK = {"changes", "ci-passed", "trigger-mirror"}


def test_a_docs_only_diff_skips_only_what_docs_cannot_break():
    """Every other ci.yml job skips on an explicit code=false -- and only on
    an explicit one, so an unset output (any early exit in changes.yml)
    runs it."""
    jobs = _jobs("ci.yml")
    assert "docs" in jobs and "docs" in _needs(jobs["ci-passed"])
    run = " ".join(s.get("run", "") for s in jobs["docs"]["steps"])
    assert "make docs-check" in run
    rest = sorted(set(jobs) - _DOCS_CAN_BREAK - _NOT_A_CHECK)
    assert len(rest) >= 8, f"the walk must find the jobs: {rest}"
    ungated = [
        k
        for k in rest
        if "needs.changes.outputs.code != 'false'"
        not in str(jobs[k].get("if"))
    ]
    assert ungated == [], (
        f"{ungated} run on a docs-only diff; gate them on "
        "`needs.changes.outputs.code != 'false'`, or name them in "
        "_DOCS_CAN_BREAK with the reason"
    )
    for k in _DOCS_CAN_BREAK - {"docs"}:
        assert "code" not in str(jobs[k].get("if")), (
            f"{k} must run on a docs-only diff"
        )


DOCS = WF.parent.parent / "docs"


def test_no_merge_queue_wiring_or_docs():
    """jm has no merge queue; nothing may run for one or describe one.

    The queue was switched off in July 2026 (the last ``merge_group`` run
    was PR #438), and the ``main`` ruleset has no ``merge_queue`` rule:
    ``gh api repos/just-buildit/just-makeit/rules/branches/main``. The
    ``merge_group`` trigger, a ``changes.yml`` case arm for it, and a
    contributor guide telling people to "add the PR to the merge queue"
    outlived it by three months. A PR merges by auto-merge on ``CI passed``.
    Turning a queue back on is a decision; it removes this test with it.
    """
    workflows = sorted(WF.glob("*.yml"))
    docs = sorted(DOCS.rglob("*.md"))
    assert workflows and docs, "the walk below must have something to find"
    hits = [
        wf.name
        for wf in workflows
        if "merge_group" in _on(wf.name)
        or re.search(r"\bmerge_group\b", wf.read_text(encoding="utf-8"))
    ]
    hits += [
        str(d.relative_to(DOCS))
        for d in docs
        if re.search(r"merge[ -]queue", d.read_text(encoding="utf-8"), re.I)
    ]
    assert hits == [], f"merge queue wiring or docs, with no queue: {hits}"


def test_ci_builds_the_docker_image_on_every_source_change():
    """A PR editing only docker.yml must run it (gh-1643's second hole).

    ci.yml's PR trigger is unfiltered, so the one filter is changes.yml,
    which runs everything but a version bump.
    """
    assert not set(_on("ci.yml")["pull_request"] or {}) & {
        "paths",
        "paths-ignore",
    }, "a path filter on ci.yml's PR trigger is a second answer to changes.yml"
    jobs = _jobs("ci.yml")
    callers = [
        k
        for k, j in jobs.items()
        if j.get("uses") == "./.github/workflows/docker.yml"
    ]
    assert len(callers) == 1, "ci.yml must call docker.yml once"
    job = jobs[callers[0]]
    # A source change, or a push whose tree its PR tested (gh-1801): the
    # matrix skips that push, but the image must still publish from main.
    # A docs-only diff (code=false) cannot change the image (gh-1801 item 3).
    assert job.get("if") == (
        "(needs.changes.outputs.src == 'true' &&\n"
        " needs.changes.outputs.code != 'false') ||\n"
        "needs.changes.outputs.tested == 'true'"
    ), job
    assert callers[0] in _needs(jobs["ci-passed"])
    # Built and smoke-tested on a PR; pushed from main.
    assert job["with"]["publish"] == "${{ github.event_name == 'push' }}"


def test_docker_publishes_on_its_input_alone():
    """Every step that logs in or pushes reads `inputs.publish`, nothing else.

    A called workflow's ``github.event_name`` is its caller's, so an event
    check here would decide publishing for ci.yml's PR runs by the
    caller's event, and once already silently skipped the push on
    release.yml's dispatch path.
    """
    jobs = _jobs("docker.yml")
    pushes = re.compile(r"docker push|imagetools create")
    bad = []
    for name, job in jobs.items():
        for step in job.get("steps", []):
            if not (
                "login-action" in step.get("uses", "")
                or pushes.search(step.get("run") or "")
            ):
                continue
            if "inputs.publish" not in (step.get("if") or job.get("if", "")):
                bad.append(f"{name}: {step.get('name') or step['uses']}")
    assert bad == [], f"publishes without reading inputs.publish: {bad}"
    text = (WF / "docker.yml").read_text(encoding="utf-8")
    assert "github.event_name" not in text.split("\njobs:")[1]
    assert (
        _on("docker.yml")["workflow_call"]["inputs"]["publish"]["default"]
        is False
    ), "a new caller must not publish by default"


_NEG_GREP = re.compile(r"^\s*!\s+grep\s+(.*)$")


def negated_greps_of_unproven_files(script: str) -> "list[str]":
    """The ``! grep ... <path>`` lines in *script* with no ``test -f <path>``
    above them.

    >>> negated_greps_of_unproven_files('! grep "x" a/b.h')
    ['! grep "x" a/b.h']
    >>> negated_greps_of_unproven_files('test -f a/b.h\\n! grep "x" a/b.h')
    []
    >>> negated_greps_of_unproven_files('echo hi | ! grep -q x')
    []
    """
    proven: "set[str]" = set()
    bad = []
    for line in script.splitlines():
        m = re.match(r"^\s*test -f (\S+)\s*$", line)
        if m:
            proven.add(m.group(1).strip("\"'"))
            continue
        g = _NEG_GREP.match(line)
        if not g:
            continue
        try:
            args = shlex.split(g.group(1))
        except ValueError:
            continue
        operands = [a for a in args if not a.startswith("-")]
        # The pattern is the first operand; anything after it is a file.
        for path in operands[1:]:
            if path not in proven:
                bad.append(line.strip())
                break
    return bad


def test_no_step_negates_a_grep_of_a_file_it_has_not_proven():
    bad = []
    for wf in sorted(WF.glob("*.yml")):
        for name, job in _jobs(wf.name).items():
            for step in job.get("steps", []) or []:
                for line in negated_greps_of_unproven_files(
                    step.get("run") or ""
                ):
                    bad.append(f"{wf.name}:{name}: {line}")
    assert bad == [], (
        "`! grep X path` passes when path does not exist (grep exits 2); "
        "precede it with `test -f path`:\n" + "\n".join(bad)
    )


_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")


def inert_negations(script: str) -> "list[str]":
    """The standalone ``! cmd`` lines of *script*, outside heredoc bodies.

    GitHub runs a step with ``bash -e``, and bash exempts a ``!`` pipeline
    from errexit: ``! grep X f`` exits 1 when X is found, and the step goes
    on. Such a line fails the step only by being its last command, which the
    next line appended silently undoes -- the ``--mutable`` NCO check was
    inert that way, and wrong too, matching a getter that is const by
    design (gh-1591 2b). Spell a negative check so it can fail:
    ``if grep -q X f; then exit 1; fi``.

    >>> inert_negations('! grep x a.h\\ngrep y a.h')
    ['! grep x a.h']
    >>> inert_negations('if grep -q x a.h; then exit 1; fi')
    []
    >>> inert_negations("python3 - <<'PY'\\n! not shell\\nPY")
    []
    """
    bad = []
    until = None
    for line in script.splitlines():
        if until is not None:
            if line.strip() == until:
                until = None
            continue
        m = _HEREDOC.search(line)
        if m:
            until = m.group(1)
        if re.match(r"^\s*!\s+\S", line):
            bad.append(line.strip())
    return bad


def test_no_step_has_a_negated_command_errexit_ignores():
    bad = []
    for wf in sorted(WF.glob("*.yml")):
        for name, job in _jobs(wf.name).items():
            for step in job.get("steps", []) or []:
                for line in inert_negations(step.get("run") or ""):
                    bad.append(f"{wf.name}:{name}: {line}")
    assert bad == [], (
        "bash -e does not stop on a failed `! cmd`, so this line cannot "
        "fail its step; use `if cmd; then exit 1; fi`:\n" + "\n".join(bad)
    )
