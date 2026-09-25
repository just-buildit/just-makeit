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

GATE: every ci.yml job but the aggregator and what runs after it feeds
      `CI passed`; ci.yml runs the artifact smoke from the wheel `make wheel`
      builds; no workflow step negates a grep of a file it has not proven
      exists.
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
