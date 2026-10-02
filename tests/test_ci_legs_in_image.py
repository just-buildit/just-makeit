"""gh-1796: ci.yml's Linux test and examples legs run in the pinned CI image.

The image itself is the org standard's (``HAS_CI_IMAGE``): the vendored
``docker/ci.Dockerfile`` builds it, ``.github/ci-images.env`` pins it, and
``make ci-image-check`` -- run by ``lint`` -- refuses a pin not built from
this tree. What the standard cannot know is which of jm's jobs run in it.
Before this, every Linux leg ran ``apt-get`` against a live mirror, so the
toolchain a PR was tested with was whatever that mirror served that minute,
and a stalled one hung a leg until ``timeout-minutes`` cancelled it
(gh-1792).

Three ways that silently undoes itself, each one asserted here:

- the legs stop reading the pin, or read a key that is not there -- an empty
  ``container:`` image runs the leg on the bare runner, which is the macOS
  path, so nothing fails;
- a leg runs as root, where the ``scratch_dir`` gate's read-only directory is
  deleted anyway and the gate skips;
- a step installs system packages live inside the image, which is exactly
  what the image replaced;
- a step names a path by ``${{ runner.temp }}`` or ``${{ github.workspace }}``,
  which expand to the HOST's paths: inside the image they do not exist, and a
  ``--junitxml`` under one failed every nested pytest the suite runs.

GATE: ci.yml's Linux test and examples legs run, as uid 1001, in the image
      .github/ci-images.env pins as CI_IMAGE_2404, read by a job `CI passed`
      waits on and that fails on a missing key; no step in those legs
      installs system packages inside the image, or names a host path by
      expression.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
PIN = ROOT / ".github" / "ci-images.env"
IN_IMAGE = ("test", "examples")


def _jobs() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _pin_step(jobs: dict) -> str:
    (step,) = [s for s in jobs["toolchain"]["steps"] if s.get("id") == "pin"]
    return step["run"]


def test_the_toolchain_job_reads_a_key_the_pin_has():
    run = _pin_step(_jobs())
    m = re.search(r"\^(CI_IMAGE_\w+)=", run)
    assert m, f"the pin step reads no CI_IMAGE_ key:\n{run}"
    keys = re.findall(r"^(\w+)=", PIN.read_text(encoding="utf-8"), re.M)
    assert m.group(1) in keys, (m.group(1), keys)
    assert "ci-images.env" in run
    # An empty output is not an error to Actions: it runs the leg bare.
    assert 'test -n "$ref"' in run, "an empty reference must fail the job"


def test_ci_passed_waits_on_the_toolchain_job():
    assert "toolchain" in _jobs()["ci-passed"]["needs"]


def test_the_linux_legs_run_in_the_pinned_image_as_1001():
    jobs = _jobs()
    for name in IN_IMAGE:
        job = jobs[name]
        assert "toolchain" in job["needs"], name
        image = job.get("container", {}).get("image", "")
        assert "needs.toolchain.outputs.image" in image, (name, image)
        assert "startsWith(matrix.os, 'ubuntu')" in image, (name, image)
        assert job["container"].get("options") == "--user 1001", (
            f"{name} would run as root, where the scratch_dir gate skips"
        )


def test_nothing_installs_live_inside_the_image():
    """A live apt in a leg that runs in the image re-opens what it closed.

    Every step of an in-image job that installs system packages must be
    skipped there: by ``!job.container.id``, or by running on macOS only.
    """
    jobs = _jobs()
    live = re.compile(r"apt-get|install-deps|just-runit")
    found, bad = 0, []
    for name in IN_IMAGE:
        for step in jobs[name]["steps"]:
            if live.search(step.get("run", "")):
                found += 1
                cond = str(step.get("if", ""))
                if "!job.container.id" not in cond and (
                    "runner.os == 'macOS'" not in cond
                ):
                    bad.append(
                        f"{name}: {step.get('name') or step['run'][:40]}"
                    )
    assert found >= 3, "the walk must find the install steps"
    assert bad == [], f"live installs inside the CI image: {bad}"


def test_no_step_names_a_host_path_by_expression():
    """``${{ runner.temp }}`` inside a container job is the host's path."""
    host = re.compile(r"\$\{\{\s*(runner\.temp|github\.workspace)\s*\}\}")
    jobs = _jobs()
    bad = []
    for name in IN_IMAGE:
        for step in jobs[name]["steps"]:
            # The step's own comment-free YAML: what Actions expands.
            if host.search(yaml.safe_dump(step)):
                bad.append(
                    f"{name}: {step.get('name') or step.get('run', '')[:40]}"
                )
    assert bad == [], f"host paths inside the CI image: {bad}"
