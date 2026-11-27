#!/usr/bin/env python3
"""Report a red nightly CI run as ONE open issue (gh-2773).

ci.yml's nightly cron runs the whole matrix on ``main``, every leg
resolving jm's runtime dependencies fresh (``make test`` runs pytest under
``uv run --no-project``, which never reads ``uv.lock``). That is the run
that sees a dependency release break jm with no PR in flight, and it used
to go red where nobody had to look: the nightly gates no merge and no
release.

ci.yml's ``nightly-red`` job runs this, as ``make ci-nightly-red``, after
``CI passed`` has failed on a ``schedule`` run. It deduplicates on the
label :data:`LABEL`:

- an OPEN issue carries the label: comment on it (the oldest, if several),
  naming the run and its failed jobs;
- none does: make sure the label exists, and open one titled
  :data:`TITLE` carrying it.

It never closes, reopens or edits an issue. A person closes it once
``main`` is green; the next red nightly then opens a fresh one.

Any event but ``schedule`` reports nothing and calls no ``gh`` at all, so a
PR's red run, or a dispatch, cannot file a nightly issue whatever the
workflow's ``if:`` says.

Environment
-----------
The variables GitHub Actions sets in every step, so the workflow passes
nothing but ``GH_TOKEN``:

GITHUB_EVENT_NAME
    Only ``schedule`` reports.
GITHUB_REPOSITORY
    ``owner/repo``, handed to ``gh`` as ``GH_REPO``.
GITHUB_SERVER_URL, GITHUB_RUN_ID
    The run being reported: its jobs are listed through the API, and its
    URL goes in the text.
GITHUB_SHA
    The commit the run tested.
GH
    The ``gh`` binary (default ``gh``). The tests point it at a fake, as
    ``ci-tree-tested.sh``'s do.

Examples
--------
Outside a nightly it says so and touches nothing::

    $ GITHUB_EVENT_NAME=push python3 scripts/nightly-red.py
    nightly-red: event 'push' is not the nightly; nothing to report
"""

from __future__ import annotations

import os
import subprocess
import sys

#: The label every nightly-red issue carries; the dedup key.
LABEL = "nightly-red"
#: The title of the issue opened when no labelled one is open.
TITLE = "Nightly CI is red"
#: Job conclusions that are not red. A job still running (this one) has
#: no conclusion yet, which the API reports as null.
NOT_RED = {"success", "skipped", "neutral", None}


def _gh(*args: str, stdin: str | None = None) -> str:
    """Run ``gh`` with *args* and return its stdout; any failure is fatal.

    A reporter that swallowed a ``gh`` error would turn the job green over
    an unreported red nightly, the exact silence it exists to end.
    """
    gh = os.environ.get("GH", "gh")
    env = {**os.environ, "GH_REPO": os.environ["GITHUB_REPOSITORY"]}
    r = subprocess.run(
        [gh, *args], input=stdin, capture_output=True, text=True, env=env
    )
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"nightly-red: `gh {args[0]}` failed")
    return r.stdout


def failed_jobs(run_id: str) -> list[str]:
    """Names of the jobs of run *run_id* that ended red, in API order."""
    out = _gh(
        "api",
        "--paginate",
        f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/jobs",
        "--jq",
        '.jobs[] | [.conclusion // "", .name] | @tsv',
    )
    names = []
    for line in out.splitlines():
        conclusion, _, name = line.partition("\t")
        if (conclusion or None) not in NOT_RED:
            names.append(f"{name} ({conclusion})")
    return names


def open_issue() -> str | None:
    """The oldest open issue carrying :data:`LABEL`, or None."""
    out = _gh(
        "issue",
        "list",
        "--label",
        LABEL,
        "--state",
        "open",
        "--json",
        "number",
        "--jq",
        ".[].number",
    )
    numbers = sorted(int(n) for n in out.split())
    return str(numbers[0]) if numbers else None


def body(run_url: str, sha: str, jobs: list[str]) -> str:
    """The text of the new issue or the comment: the run and what failed."""
    listed = "\n".join(f"- {j}" for j in jobs) or "- (none reported)"
    return (
        f"The nightly CI run {run_url} on `{sha}` ended red.\n\n"
        f"Jobs that did not succeed:\n\n{listed}\n\n"
        "The nightly resolves jm's dependencies fresh, so a red run with no "
        "change on main is often a dependency release. Close this issue "
        "once main is green again; the next red nightly opens a new one.\n"
    )


def main() -> int:
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event != "schedule":
        print(
            f"nightly-red: event '{event}' is not the nightly; "
            "nothing to report"
        )
        return 0
    run_id = os.environ["GITHUB_RUN_ID"]
    run_url = "/".join(
        (
            os.environ["GITHUB_SERVER_URL"],
            os.environ["GITHUB_REPOSITORY"],
            "actions/runs",
            run_id,
        )
    )
    text = body(run_url, os.environ["GITHUB_SHA"], failed_jobs(run_id))
    number = open_issue()
    if number:
        _gh("issue", "comment", number, "--body-file", "-", stdin=text)
        print(f"nightly-red: commented on #{number}")
        return 0
    # `issue create --label` refuses a label the repository lacks, and a
    # first red nightly must not die on that. `--force` makes this an
    # upsert, so an existing label is left as it is in all but its text.
    _gh(
        "label",
        "create",
        LABEL,
        "--description",
        "A scheduled CI run on main went red",
        "--color",
        "B60205",
        "--force",
    )
    url = _gh(
        "issue",
        "create",
        "--title",
        TITLE,
        "--label",
        LABEL,
        "--body-file",
        "-",
        stdin=text,
    )
    print(f"nightly-red: opened {url.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
