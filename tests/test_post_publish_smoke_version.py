"""The post-publish smoke tests the version the release published.

`artifact.yml` also runs on `workflow_run` after Release completes, and
installs the just-published version from PyPI. It read that version from
`github.event.workflow_run.head_branch`: the tag, for a tag push. But the
recovery path when a tag's own run fails is a dispatch of `release.yml`
with `-f tag=` -- tags cannot be deleted or moved -- and a dispatch runs on
`main`. v0.109.0 was published that way, and every post-publish leg ran
`pip install just-makeit==main`.

The tag now travels in the release run's title: `release.yml` sets
`run-name: Release <tag>` for both triggers, and every PyPI install step
strips that same prefix from `workflow_run.display_title`. Read as YAML, so
the prefix is taken from `run-name` itself rather than restated here: change
one side and this fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip(
    "yaml", reason="pyyaml is needed to read the workflow wiring"
)

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
ARTIFACT = WORKFLOWS / "artifact.yml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _title_prefix() -> str:
    """The literal text `run-name` puts before the tag."""
    run_name = _load(RELEASE).get("run-name", "")
    assert "inputs.tag || github.ref_name" in run_name, (
        "release.yml's run-name must carry the tag for a tag push AND a "
        f"dispatch (`inputs.tag || github.ref_name`); it is {run_name!r}"
    )
    return run_name.split("${{", 1)[0]


def _pypi_install_steps() -> "list[tuple[str, dict]]":
    steps = []
    for name, job in _load(ARTIFACT)["jobs"].items():
        for step in job.get("steps", []):
            if "just-makeit==" in step.get("run", ""):
                steps.append((f"{name}: {step.get('name')}", step))
    return steps


def test_there_is_a_pypi_install_to_check():
    """Not vacuous: the smoke and its Windows form each install from PyPI."""
    assert len(_pypi_install_steps()) >= 2


@pytest.mark.parametrize(
    "label,step", _pypi_install_steps(), ids=lambda v: str(v)[:60]
)
def test_the_version_comes_from_the_release_title(label, step):
    prefix = _title_prefix()
    env = step.get("env", {})
    assert env.get("RELEASE_TITLE") == (
        "${{ github.event.workflow_run.display_title }}"
    ), f"{label}: RELEASE_TITLE must be the release run's display_title"
    assert f"${{RELEASE_TITLE#{prefix}}}" in step["run"], (
        f"{label}: must strip release.yml's run-name prefix {prefix!r}"
    )


def test_no_step_reads_head_branch_as_a_version():
    """`head_branch` is `main` for a dispatched release."""
    text = ARTIFACT.read_text(encoding="utf-8")
    assert "workflow_run.head_branch" not in text
