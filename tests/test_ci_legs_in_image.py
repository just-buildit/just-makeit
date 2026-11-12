"""gh-1796, gh-2625: ci.yml's Linux legs run in the pinned CI images.

The images themselves are the org standard's (``HAS_CI_IMAGE``): the
vendored ``docker/ci.Dockerfile`` builds one per ``CI_IMAGE_BASES`` entry,
``.github/ci-images.env`` pins each as ``CI_IMAGE_<key>``, and
``make ci-image-check`` -- run by ``lint`` -- refuses a pin not built from
this tree. What the standard cannot know is which of jm's jobs run in them.
Before gh-1796, every Linux leg ran ``apt-get`` against a live mirror, so
the toolchain a PR was tested with was whatever that mirror served that
minute, and a stalled one hung a leg until ``timeout-minutes`` cancelled it
(gh-1792). Consumer smoke kept doing so until gh-2625, when an Ubuntu DNS
outage failed every one of its Linux legs on every PR, and nothing merged.

The ways that silently undoes itself, each one asserted here:

- the legs stop reading a pin, or read a key that is not there -- an empty
  ``container:`` image runs the leg on the bare runner, which is the macOS
  path, so nothing fails;
- a test or examples leg runs as root, where the ``scratch_dir`` gate's
  read-only directory is deleted anyway and the gate skips;
- a step installs system packages live inside an image, which is exactly
  what the image replaced;
- a step names a path by ``${{ runner.temp }}`` or ``${{ github.workspace }}``,
  which expand to the HOST's paths: inside the image they do not exist, and a
  ``--junitxml`` under one failed every nested pytest the suite runs;
- the CMake floor the consumer smoke tests is not the one
  ``docker/ci-extra.sh`` bakes, or not into the image the leg runs in, so
  the leg would have to download it per run again.

Consumer smoke runs as root, not 1001: its default-prefix step installs to
``/usr/local`` and runs ``ldconfig``, and the image's ``ci`` user has no
sudo. No suite gate runs there to skip.

The jobs that install live ON PURPOSE are not here: ``install-deps-smoke``
and Artifact smoke's bootstrap.toml step, whose install is the subject.

GATE: ci.yml's Linux test, examples and consumer-smoke legs run in an image
      .github/ci-images.env pins (CI_IMAGE_2404, CI_IMAGE_2204), read by a
      job `CI passed` waits on and that fails on a missing key; the test and
      examples legs as uid 1001; no step in those legs installs system
      packages inside an image, or names a host path by expression; the
      consumer-smoke floor CMake is the one docker/ci-extra.sh bakes into
      the image that leg names.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
PIN = ROOT / ".github" / "ci-images.env"
EXTRA = ROOT / "docker" / "ci-extra.sh"
MAKEFILE = ROOT / "Makefile"
IN_IMAGE_1001 = ("test", "examples")
IN_IMAGE = (*IN_IMAGE_1001, "consumer-smoke")


def _jobs() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _pin_step(jobs: dict) -> str:
    (step,) = [s for s in jobs["toolchain"]["steps"] if s.get("id") == "pin"]
    return step["run"]


def _pin_keys(jobs: dict) -> "list[str]":
    """The image keys the toolchain job reads, and so fails without."""
    run = _pin_step(jobs)
    m = re.search(r"for k in ([\d ]+); do", run)
    assert m, f"the pin step reads no CI_IMAGE_<key> list:\n{run}"
    return m.group(1).split()


def _consumer_legs(jobs: dict) -> "list[dict]":
    return jobs["consumer-smoke"]["strategy"]["matrix"]["include"]


def test_the_toolchain_job_reads_keys_the_pin_has():
    jobs = _jobs()
    run = _pin_step(jobs)
    keys = _pin_keys(jobs)
    pinned = re.findall(r"^(\w+)=", PIN.read_text(encoding="utf-8"), re.M)
    for k in keys:
        assert f"CI_IMAGE_{k}" in pinned, (k, pinned)
    # Each key is a base the Makefile builds, so a repin keeps it current.
    m = re.search(r"^CI_IMAGE_BASES\s*=(.*)$", MAKEFILE.read_text(), re.M)
    assert m, "no CI_IMAGE_BASES in the Makefile"
    built = {re.sub(r"\D", "", b.split(":", 1)[1]) for b in m.group(1).split()}
    assert set(keys) <= built, (keys, built)
    assert "ci-images.env" in run
    # An empty output is not an error to Actions: it runs the leg bare.
    assert 'test -n "$ref"' in run, "an empty reference must fail the job"


def test_ci_passed_waits_on_the_toolchain_job():
    assert "toolchain" in _jobs()["ci-passed"]["needs"]


def test_the_test_and_examples_legs_run_in_the_pinned_image_as_1001():
    jobs = _jobs()
    for name in IN_IMAGE_1001:
        job = jobs[name]
        assert "toolchain" in job["needs"], name
        image = job.get("container", {}).get("image", "")
        assert "needs.toolchain.outputs.image" in image, (name, image)
        assert "startsWith(matrix.os, 'ubuntu')" in image, (name, image)
        assert job["container"].get("options") == "--user 1001", (
            f"{name} would run as root, where the scratch_dir gate skips"
        )


def test_every_linux_consumer_leg_runs_in_a_pinned_image():
    """gh-2625: each Linux leg names a pin key the container maps to an image.

    A Linux leg with no ``image`` would run bare, and need a live install;
    a key the expression does not map, or the toolchain job does not read,
    yields an empty image, which also runs bare -- silently.
    """
    jobs = _jobs()
    job = jobs["consumer-smoke"]
    assert "toolchain" in job["needs"]
    expr = job.get("container", {}).get("image", "")
    outputs = jobs["toolchain"]["outputs"]
    keys = _pin_keys(jobs)
    linux = [
        leg for leg in _consumer_legs(jobs) if leg["os"].startswith("ubuntu")
    ]
    assert len(linux) >= 4, "the walk must find the Linux legs"
    for leg in linux:
        k = leg.get("image")
        assert k in keys, f"{leg['name']}: image {k!r} is not a pinned key"
        m = re.search(
            rf"matrix\.image == '{k}' && needs\.toolchain\.outputs\.(\w+)",
            expr,
        )
        assert m, f"{leg['name']}: the container maps no image for {k}"
        assert f"steps.pin.outputs.image_{k}" in outputs[m.group(1)], (
            k,
            outputs,
        )
    for leg in _consumer_legs(jobs):
        if not leg["os"].startswith("ubuntu"):
            assert "image" not in leg, f"{leg['name']} cannot run a container"
    # Root, deliberately: the default-prefix install needs it (see above).
    assert "--user" not in str(job["container"].get("options", ""))


def test_nothing_installs_live_inside_the_image():
    """A live apt in a leg that runs in the image re-opens what it closed.

    Every step of an in-image job that installs system packages must be
    skipped there: by ``!job.container.id``, or by running on macOS only.
    """
    jobs = _jobs()
    live = re.compile(r"apt-get|install-deps|just-runit")
    found, bad = set(), []
    for name in IN_IMAGE:
        for step in jobs[name]["steps"]:
            if live.search(step.get("run", "")):
                found.add(name)
                cond = str(step.get("if", ""))
                if "!job.container.id" not in cond and (
                    "runner.os == 'macOS'" not in cond
                ):
                    bad.append(
                        f"{name}: {step.get('name') or step['run'][:40]}"
                    )
    # Each job keeps its macOS (or bare-runner) install: the walk saw it.
    assert found == set(IN_IMAGE), found
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


def test_the_floor_cmake_is_baked_into_the_image_its_leg_runs_in():
    """gh-2625: the CMake floor leg's version is the one its image bakes.

    ``docker/ci-extra.sh`` installs ``FLOOR_CMAKE`` into ``/opt/cmake-<v>``
    on one base; ``scripts/consumer-smoke.sh`` uses that directory and, in a
    CI image, refuses to download one that is missing. So a leg asking for
    another version, or running in another base's image, fails in CI rather
    than fetching -- this says so before CI does.
    """
    extra = EXTRA.read_text(encoding="utf-8")
    m = re.search(r"^FLOOR_CMAKE=(\S+)$", extra, re.M)
    assert m, "docker/ci-extra.sh declares no FLOOR_CMAKE"
    floor = m.group(1)
    on = re.search(r'"\$\{VERSION_ID:-\}" != (\d+)\.(\d+)', extra)
    assert on, "docker/ci-extra.sh names no base for the floor"
    base_key = on.group(1) + on.group(2)
    legs = [leg for leg in _consumer_legs(_jobs()) if leg.get("cmake")]
    assert legs, "the walk must find the CMake floor leg"
    for leg in legs:
        assert leg["cmake"] == floor, (leg, floor)
        assert leg.get("image") == base_key, (leg, base_key)
