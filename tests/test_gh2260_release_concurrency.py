"""A duplicate release run waits, then ends green without publishing (gh-2260).

One tag push started TWO ``release.yml`` runs for v0.104.0, two seconds
apart on the same SHA. Both passed ``resolve`` and ``verify-ci`` and were
queued at ``build``, racing to publish: the loser would have failed on
PyPI's "file exists", its ``github-release`` would have failed or edited
the winner's, and ``release-watch`` -- which follows one run -- could have
reported a release that shipped as failed. It was cancelled by hand.

Two halves, both read from the workflow rather than listed here:

- **A per-tag concurrency group that never cancels.** A second run for the
  same tag queues behind the first instead of racing it. Cancelling in
  progress is refused: it could kill a run halfway through publishing.
- **An already-on-PyPI check before anything builds.** Once the duplicate
  is let through, the version is already out; a step reading PyPI's
  per-version endpoint sets ``published``, and every job but the one
  running it is skipped on it, directly or through its ``needs``. The run
  concludes green, so ``release-watch`` goes on to verify the artifacts the
  first run shipped. ``publish`` carries the condition itself as well: it
  is the one job a missed skip would make irreversible.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WF = (
    Path(__file__).resolve().parent.parent
    / ".github"
    / "workflows"
    / "release.yml"
)

# The endpoint scripts/release-watch.sh verifies a release against.
_PER_VERSION = re.compile(r"pypi\.org/pypi/\S+/\S+/json")


def _doc() -> dict:
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _needs(job: dict) -> "list[str]":
    n = job.get("needs") or []
    return [n] if isinstance(n, str) else list(n)


def _upstream(jobs: dict, name: str) -> "set[str]":
    """Every job ``name`` waits on, transitively."""
    seen: "set[str]" = set()
    todo = _needs(jobs[name])
    while todo:
        n = todo.pop()
        if n not in seen:
            seen.add(n)
            todo.extend(_needs(jobs[n]))
    return seen


def _checker(jobs: dict) -> "tuple[str, str]":
    """The job and step id that ask PyPI whether this version is out."""
    found = [
        (name, step["id"])
        for name, job in jobs.items()
        for step in job.get("steps", [])
        if _PER_VERSION.search(step.get("run", ""))
        and "published=true" in step.get("run", "")
    ]
    assert len(found) == 1, (
        "release.yml must ask PyPI's per-version endpoint, in exactly one "
        f"step, whether this version is already out; found {found}"
    )
    return found[0]


def _gated(job: dict, checker: str) -> bool:
    cond = str(job.get("if", ""))
    return f"needs.{checker}.outputs.published" in cond and "!=" in cond


def test_release_runs_queue_per_tag_and_never_cancel():
    conc = _doc().get("concurrency")
    assert isinstance(conc, dict), (
        "release.yml has no workflow-level `concurrency:`, so two runs for "
        "one tag race to publish (gh-2260)"
    )
    group = str(conc.get("group", ""))
    assert re.search(r"github\.ref(_name)?\b", group), (
        f"the release concurrency group {group!r} is not keyed on the tag, "
        "so it does not serialise two runs for the same release"
    )
    assert conc.get("cancel-in-progress") is False, (
        "release.yml must say `cancel-in-progress: false`: cancelling could "
        "kill a run halfway through publishing"
    )


def test_already_published_check_runs_before_build_and_gates_publish():
    jobs = _doc()["jobs"]
    checker, step = _checker(jobs)
    out = str(jobs[checker].get("outputs", {}).get("published", ""))
    assert f"steps.{step}.outputs.published" in out, (
        f"job {checker!r} must expose its PyPI answer as output "
        f"`published` (from step {step!r})"
    )
    assert "build" in jobs and checker in _upstream(jobs, "build"), (
        f"the already-on-PyPI check ({checker!r}) must run before `build`"
    )
    assert _gated(jobs["publish"], checker), (
        "`publish` must itself be skipped when the version is already on "
        f"PyPI: `if: needs.{checker}.outputs.published != 'true'`"
    )
    # Nothing that BUILDS OR PUBLISHES runs on a duplicate: `publish` and
    # everything it waits on carry the condition, or wait on a job that does
    # (a skipped need skips it). gh-2310 narrowed this from "nothing but the
    # check runs": what comes AFTER `publish` (github-release, docker, and
    # the read-only probe that feeds docker) now runs for a published
    # version, idempotently, because skipping it ended a re-run green with
    # the release or the image missing -- see test_gh2310_release_rerun.py.
    gated = {n for n, j in jobs.items() if _gated(j, checker)}
    before_publish = (_upstream(jobs, "publish") | {"publish"}) - {checker}
    loose = sorted(
        n
        for n in before_publish
        if n not in gated and not (_upstream(jobs, n) & gated)
    )
    assert loose == [], (
        f"these jobs build or publish and still run when the version is "
        f"already on PyPI: {loose}"
    )
