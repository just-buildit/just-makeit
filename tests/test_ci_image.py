"""gh-1796: jm's CI toolchain image is a snapshot, moved only by a commit.

Before it, every Linux leg of ``ci.yml`` ran ``apt-get`` against a live
mirror, so the toolchain a PR was tested with was whatever that mirror served
that minute, and a stalled one hung a leg until ``timeout-minutes`` cancelled
it (gh-1792). ``docker/Dockerfile.ci`` pins the base and uv by digest and every
apt package by a dated snapshot; ``scripts/ci_image.py`` moves those pins and
checks that ``.github/ci-image`` was built from them.

GATE: every FROM and COPY --from in docker/Dockerfile.ci is pinned by digest,
      and every apt-get in it is bounded by a timeout; `make ci-image-refresh`
      moves each pin by replacing the value it captured, refusing an anchor
      that does not match exactly as often as declared; the image's inputs
      hash moves with the Dockerfile and bootstrap.toml's dev.apt group and
      with nothing else there; ci-image.yml builds, smokes and checks through
      the make targets, and re-pins through a PR rather than a push to main.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ci_image as C  # noqa: E402

DOCKERFILE = C.DOCKERFILE.read_text(encoding="utf-8")
WORKFLOW = ROOT / ".github" / "workflows" / "ci-image.yml"


def _instructions(text: str) -> "list[str]":
    """The Dockerfile's instructions, continuation lines joined, no comments."""
    joined = re.sub(r"\\\n", " ", text)
    return [
        ln.strip()
        for ln in joined.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def test_every_image_the_dockerfile_reads_is_pinned_by_digest():
    refs = []
    for ins in _instructions(DOCKERFILE):
        if ins.startswith("FROM "):
            refs.append(ins.split()[1])
        m = re.match(r"COPY\s+--from=(\S+)", ins)
        if m and ("/" in m.group(1) or ":" in m.group(1)):
            refs.append(m.group(1))
    assert len(refs) >= 3, f"the walk must find the images: {refs}"
    loose = [r for r in refs if not re.search(r"@sha256:[0-9a-f]{64}$", r)]
    assert loose == [], (
        f"{loose} would build from whatever the tag points at today; pin "
        "by digest (`make ci-image-refresh` moves the pins)"
    )


def test_every_apt_get_in_the_dockerfile_is_bounded():
    # Per invocation, not per RUN: a RUN chaining two apt-gets with one
    # timeout between them leaves the other free to stall.
    calls = []
    for ins in _instructions(DOCKERFILE):
        if ins.startswith("RUN "):
            calls += re.split(r"&&|;", ins)
    calls = [c for c in calls if re.search(r"\bapt-get\b", c)]
    assert len(calls) >= 3, f"the walk must find the apt calls: {calls}"
    # The final stage passes its options through a bash array; that array
    # must itself carry the timeouts.
    arrays = re.findall(r"O=\(([^)]*)\)", re.sub(r"\\\n", " ", DOCKERFILE))
    bounded = any("Timeout=" in a for a in arrays)
    unbounded = [
        c.strip()
        for c in calls
        if "Timeout=" not in c and not ('"${O[@]}"' in c and bounded)
    ]
    assert unbounded == [], (
        f"an apt-get with retries but no timeout hangs on a stalled "
        f"connection instead of retrying (gh-1792): {unbounded}"
    )


def _fake(n: str) -> str:
    return "sha256:" + n * 64


def test_refresh_moves_exactly_the_three_pins():
    out = C.refresh(DOCKERFILE, _fake("c"), "20990101T000000Z", _fake("d"))
    assert out.count(_fake("c")) == 2
    assert "ARG APT_SNAPSHOT=20990101T000000Z\n" in out
    assert out.count(_fake("d")) == 1
    # Nothing but the captured values moved: undo them and the file is back.
    back = out
    for name, pat, _ in C.ANCHORS:
        old = pat.search(DOCKERFILE).group(1)
        new = pat.search(out).group(1)
        back = back.replace(new, old)
    assert back == DOCKERFILE


@pytest.mark.parametrize("name", [a[0] for a in C.ANCHORS])
def test_refresh_refuses_an_anchor_it_cannot_find(name):
    pat = dict((a[0], a[1]) for a in C.ANCHORS)[name]
    # A reformat that moves the value off the anchored line.
    broken = pat.sub(
        lambda m: m.group(0).replace(m.group(1), "\\\n  x"), DOCKERFILE
    )
    with pytest.raises(ValueError, match=f"the {name} pin matched"):
        C.refresh(broken, _fake("c"), "20990101T000000Z", _fake("d"))


def test_inputs_move_with_the_dockerfile_and_the_dev_group_only(tmp_path):
    df = tmp_path / "Dockerfile.ci"
    df.write_text(DOCKERFILE, encoding="utf-8")
    boot = tmp_path / "bootstrap.toml"
    src = C.BOOTSTRAP.read_text(encoding="utf-8")
    boot.write_text(src, encoding="utf-8")
    base = C.inputs(df, boot)

    boot.write_text(
        src.replace(
            '"cmake", "pkg-config"]', '"cmake", "pkg-config", "ninja"]'
        ),
        encoding="utf-8",
    )
    assert "ninja" in boot.read_text(encoding="utf-8"), "brew edit must land"
    assert C.inputs(df, boot) == base, "a brew-only change needs no re-pin"

    boot.write_text(
        src.replace('"build-essential",', '"build-essential", "ninja-build",'),
        encoding="utf-8",
    )
    assert C.packages(boot)[1] == "ninja-build", "dev.apt edit must land"
    assert C.inputs(df, boot) != base

    boot.write_text(src, encoding="utf-8")
    df.write_text(DOCKERFILE + "\n", encoding="utf-8")
    assert C.inputs(df, boot) != base


def _steps(job: str) -> "list[dict]":
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"][job]["steps"]


def test_the_workflow_runs_the_make_targets():
    runs = {
        job: " ".join(s.get("run", "") for s in _steps(job))
        for job in ("refresh", "build", "publish")
    }
    assert "make ci-image-refresh" in runs["refresh"]
    assert "make ci-image-build" in runs["build"]
    assert "make ci-image-smoke" in runs["build"]
    assert "make ci-image-check" in runs["publish"]
    # A second copy of the build or the check would drift from the target.
    assert "docker buildx build" not in " ".join(runs.values())


def test_the_workflow_re_pins_through_a_pr_never_onto_main():
    script = next(
        s["run"] for s in _steps("publish") if "git push" in s.get("run", "")
    )
    assert 'git push origin "HEAD:$branch"' in script
    assert 'branch="chore/ci-image-$TAG"' in script
    assert "gh pr create" in script
    # GITHUB_TOKEN pushes start no workflow, so the PR's CI is dispatched.
    assert 'gh workflow run ci.yml --ref "$branch"' in script
