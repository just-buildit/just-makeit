"""The artifact smoke suite must be able to stop a release.

gh-851. `artifact.yml` ran only on `workflow_run` after Release completed —
**post-publish, and not a required check**. It could not block anything by
construction, nothing reported its failure, and it sat red across 34 runs and
three releases while every dashboard looked green. A gate whose result nobody
reads is worse than an absent one, because its presence gets cited as coverage.

Exactly one of its 31 steps needs real PyPI — the install. The other 30
exercise the installed tool and behave identically whether it came from PyPI or
from the wheel the release just built, so they now run *before* publish, where
they can still stop it.

**Why this file exists.** A workflow change cannot be run locally, and the
thing being fixed is precisely a check that looked wired and was not. So the
wiring is asserted here: if someone removes the pre-publish gate, or breaks the
condition that keeps the job from silently skipping, `make test` fails rather
than the next release publishing unguarded.

Read as YAML rather than grepped: the question is what the workflow *means*
(does `publish` depend on this job), and a substring search cannot tell a
`needs:` entry from the same word in a comment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip(
    "yaml", reason="pyyaml is needed to read the workflow wiring"
)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
ARTIFACT = WORKFLOWS / "artifact.yml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    """The `on:` block. PyYAML parses a bare `on` key as the boolean True."""
    return doc[True] if True in doc else doc["on"]


def test_publish_depends_on_the_artifact_smoke():
    """The whole point: a failing smoke suite must prevent publishing.

    Before gh-851 this suite ran after the wheel was already on PyPI, so its
    only possible answer was "the thing you shipped is broken".
    """
    jobs = _load(RELEASE)["jobs"]
    assert "artifact-smoke" in jobs, (
        "release.yml no longer runs the artifact smoke suite before publish; "
        "it can then only report on an artifact that is already public"
    )
    needs = jobs["publish"]["needs"]
    needs = [needs] if isinstance(needs, str) else needs
    assert "artifact-smoke" in needs, (
        f"publish does not depend on artifact-smoke (needs={needs}), so the "
        f"suite runs and cannot stop the release"
    )


def test_the_artifact_smoke_is_reusable_and_takes_a_wheel():
    """It must be callable with a locally built wheel, not only from PyPI."""
    triggers = _triggers(_load(ARTIFACT))
    assert "workflow_call" in triggers, (
        "artifact.yml is not reusable, so release.yml cannot run it against "
        "the wheel it is about to publish"
    )
    inputs = triggers["workflow_call"]["inputs"]
    assert "wheel-artifact" in inputs, sorted(inputs)


def test_the_job_does_not_silently_skip_when_called():
    """The trap this fix could most easily fall into.

    Inside a reusable workflow `github.event_name` is the CALLER's event — a
    tag push here, never `workflow_call`. The original guard admitted only
    `workflow_dispatch` and a successful `workflow_run`, so called from
    release.yml the job would evaluate false, skip every step, and report
    success. `publish` would then depend on a green job that ran nothing,
    which is the same defect as before wearing a passing badge.
    """
    condition = _load(ARTIFACT)["jobs"]["smoke"]["if"]
    assert "wheel-artifact" in condition, (
        "the job's `if:` does not admit the pre-publish invocation, so it "
        f"skips and reports success without running:\n{condition}"
    )


def test_exactly_one_step_resolves_from_pypi_and_it_is_guarded():
    """The claim the whole design rests on, checked rather than asserted.

    "Only one of 31 steps needs PyPI" is why the rest can move before publish.
    If a second PyPI-dependent step appears, that premise is gone and the
    pre-publish run is testing something different from what ships.
    """
    steps = _load(ARTIFACT)["jobs"]["smoke"]["steps"]
    pypi = [
        s
        for s in steps
        if "pip install" in str(s.get("run", ""))
        and "just-makeit==" in str(s.get("run", ""))
    ]
    assert len(pypi) == 1, (
        f"{len(pypi)} steps resolve just-makeit from PyPI; the pre-publish "
        f"run cannot cover more than one, so the premise that everything else "
        f"is source-agnostic no longer holds: "
        f"{[s.get('name') for s in pypi]}"
    )
    guard = str(pypi[0].get("if", ""))
    assert "wheel-artifact" in guard, (
        f"the PyPI install is unguarded, so a pre-publish run would try to "
        f"resolve a version that does not exist yet: if={guard!r}"
    )


def test_the_post_publish_trigger_is_kept():
    """One thing only a published artifact can answer, so it stays.

    Moving everything pre-publish would drop the check that PyPI actually
    serves an installable wheel at that version — a real failure mode
    (a broken upload, a yanked release) that a local wheel cannot detect.
    """
    triggers = _triggers(_load(ARTIFACT))
    assert "workflow_run" in triggers, (
        "the post-publish trigger was removed; nothing then verifies that "
        "PyPI serves an installable artifact at the released version"
    )
