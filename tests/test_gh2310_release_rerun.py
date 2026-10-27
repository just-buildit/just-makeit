"""gh-2310: 'Re-run all jobs' after a post-publish failure must not end green.

A release publishes to PyPI and then fails at ``github-release`` or
``docker``. 'Re-run failed jobs' is fine. 'Re-run all jobs' re-runs
``resolve``, which sees the version on PyPI and sets ``published=true``;
``publish`` is skipped on it, and the skip cascaded through ``needs`` to
``github-release`` and ``docker``. The run concluded success with the GitHub
Release or the image missing. Observed, not read: a dispatch of the workflow
for the already-released ``v0.111.2`` ran only ``resolve`` and ended green
(run 40444738724, posted on the issue).

The class is "a skip that means *nothing to do* is indistinguishable from a
skip that means *not done*". The fix makes the post-publish jobs decide for
themselves: they run when ``publish`` succeeded OR was skipped only because
the version is already on PyPI, and each is idempotent. This file holds that
three ways, all read from ``release.yml`` rather than restated:

1. a small evaluator for the GitHub Actions expression subset the workflow
   uses, run over the REAL job graph for each scenario -- modelled, but
   anchored to the observation above;
2. the hardening the issue asks for: a job whose ``if`` uses a status
   function (``always()``, ``failure()``, ``cancelled()``) escapes the
   'a skipped need skips its dependant' rule that gh-2260's test assumes, so
   it must carry the ``published`` gate itself;
3. the idempotence each post-publish job relies on.

The runs for one tag are serialised by the workflow's concurrency group,
``release-<tag>`` with ``cancel-in-progress: false``. GitHub holds at most
one RUNNING and one PENDING run per group: a later run waits behind the
first, and a third replaces the pending second. A run therefore starts only
after the previous one finished, so its presence checks (does the release
exist, is the image published) see what that run shipped, and two runs never
reach ``gh release create`` together.
"""

from __future__ import annotations

import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "release.yml"
DOCKER = ROOT / ".github" / "workflows" / "docker.yml"

STATUS_FUNCS = ("always()", "failure()", "cancelled()", "success()")


def _jobs() -> dict:
    return yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]


def _needs(job: dict) -> "list[str]":
    n = job.get("needs") or []
    return [n] if isinstance(n, str) else list(n)


# -- a GitHub Actions expression evaluator, for the subset release.yml uses ---

_TOKEN = re.compile(
    r"\s*(?:(?P<op>&&|\|\||==|!=|!|\(|\))|"
    r"(?P<str>'[^']*')|(?P<name>[A-Za-z_][\w.\-]*))"
)


def _tokens(text: str) -> "list[tuple[str, str]]":
    out, at = [], 0
    text = text.strip()
    while at < len(text):
        m = _TOKEN.match(text, at)
        assert m, f"cannot read {text[at:]!r} in {text!r}"
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        at = m.end()
    return out


class _Eval:
    """Evaluate one ``if:`` against ``needs`` results and outputs."""

    def __init__(self, text: str, needs: dict):
        self.t = _tokens(text)
        self.i = 0
        self.needs = needs  # {job: (result, outputs)}

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def parse(self):
        v = self.or_()
        assert self.i == len(self.t), f"trailing tokens in {self.t}"
        return v

    def or_(self):
        v = self.and_()
        while self.peek() == ("op", "||"):
            self.take()
            r = self.and_()
            v = bool(v) or bool(r)
        return v

    def and_(self):
        v = self.not_()
        while self.peek() == ("op", "&&"):
            self.take()
            r = self.not_()
            v = bool(v) and bool(r)
        return v

    def not_(self):
        if self.peek() == ("op", "!"):
            self.take()
            return not self.not_()
        return self.cmp()

    def cmp(self):
        left = self.primary()
        if self.peek() in (("op", "=="), ("op", "!=")):
            op = self.take()[1]
            right = self.primary()
            return (left == right) if op == "==" else (left != right)
        return left

    def primary(self):
        kind, val = self.take()
        if (kind, val) == ("op", "("):
            v = self.or_()
            assert self.take() == ("op", ")")
            return v
        if kind == "str":
            return val[1:-1]
        assert kind == "name", (kind, val)
        if self.peek() == ("op", "("):
            self.take()
            assert self.take() == ("op", ")")
            return self.func(val)
        return self.path(val)

    def func(self, name: str):
        results = [r for r, _ in self.needs.values()]
        if name == "always":
            return True
        if name == "cancelled":
            return False  # no scenario cancels a run
        if name == "failure":
            return any(r == "failure" for r in results)
        if name == "success":
            return all(r == "success" for r in results)
        raise AssertionError(f"unmodelled function {name}()")

    def path(self, dotted: str):
        parts = dotted.split(".")
        assert parts[0] == "needs", f"unmodelled context {dotted}"
        result, outputs = self.needs[parts[1]]
        if parts[2] == "result":
            return result
        assert parts[2] == "outputs", dotted
        return outputs.get(parts[3], "")


def _condition(job: dict) -> "str | None":
    raw = job.get("if")
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    m = re.fullmatch(r"\$\{\{\s*(.*?)\s*\}\}", text)
    return m.group(1) if m else text


def _runs(job: dict, needs: dict) -> bool:
    """Whether GitHub would start *job* given its needs' results."""
    cond = _condition(job)
    implicit = all(r == "success" for r, _ in needs.values())
    if cond is None:
        return implicit
    if not any(f in cond for f in STATUS_FUNCS):
        # No status function: `success()` is prepended implicitly.
        return implicit and bool(_Eval(cond, needs).parse())
    return bool(_Eval(cond, needs).parse())


def _simulate(
    published: str,
    image_exists: str,
    fail: "tuple[str, ...]" = (),
    jobs: "dict | None" = None,
) -> "dict[str, str]":
    """Each job's result for a run whose ``resolve`` answered *published*."""
    jobs = _jobs() if jobs is None else jobs
    state: "dict[str, tuple[str, dict]]" = {}
    pending = dict(jobs)
    outputs_of = {
        "resolve": {"published": published},
        "image-state": {"exists": image_exists},
    }
    while pending:
        progressed = False
        for name, job in list(pending.items()):
            deps = _needs(job)
            if any(d not in state for d in deps):
                continue
            needs = {d: state[d] for d in deps}
            if _runs(job, needs):
                res = "failure" if name in fail else "success"
            else:
                res = "skipped"
            state[name] = (
                res,
                outputs_of.get(name, {}) if res == "success" else {},
            )
            del pending[name]
            progressed = True
        assert progressed, f"a dependency cycle among {sorted(pending)}"
    return {n: r for n, (r, _) in state.items()}


def _ran(res: "dict[str, str]") -> "set[str]":
    return {n for n, r in res.items() if r != "skipped"}


POST = ("github-release", "docker")
PRE = ("verify-ci", "build", "smoke", "artifact-smoke", "publish")


def _before_the_fix() -> dict:
    """The job graph as release.yml had it when #2310 was observed: the two
    post-publish jobs carry no `if:` of their own, and there is no probe."""
    jobs = {k: dict(v) for k, v in _jobs().items() if k != "image-state"}
    jobs["github-release"].pop("if")
    jobs["docker"].pop("if")
    jobs["docker"]["needs"] = ["resolve", "github-release"]
    return jobs


def test_the_evaluator_reproduces_the_observed_run():
    """The anchor. Run 40444738724 dispatched the workflow for the
    already-released v0.111.2 and GitHub's job table was: `resolve`
    success; every other job skipped; the run green. The evaluator, run on
    the pre-fix graph for `published=true`, must say the same -- else it
    models something other than GitHub and the scenarios below prove
    nothing."""
    res = _simulate("true", "true", jobs=_before_the_fix())
    assert res == {
        "resolve": "success",
        "verify-ci": "skipped",
        "build": "skipped",
        "smoke": "skipped",
        "artifact-smoke": "skipped",
        "publish": "skipped",
        "github-release": "skipped",
        "docker": "skipped",
    }, res


def test_the_first_run_runs_everything_but_the_probe():
    res = _simulate(published="false", image_exists="false")
    assert _ran(res) == set(_jobs()) - {"image-state"}, res
    assert res["image-state"] == "skipped", res
    assert all(r == "success" for n, r in res.items() if n != "image-state")


def test_a_first_run_builds_even_when_main_ci_already_published_the_image():
    """Measured on v0.111.2: main's push CI published `sha-<release commit>`
    38 minutes before this workflow's docker job started. A probe taken late
    in a first run would find it and skip the release's own build, and one
    taken early would not; which path a first run takes must not depend on
    how long `verify-ci` waited. After a `publish` that succeeded here, docker
    runs, whatever the registry says -- the probe is not even asked."""
    res = _simulate(published="false", image_exists="true")
    assert res["docker"] == "success", res
    assert res["image-state"] == "skipped", res


@pytest.mark.parametrize(
    "image",
    ["false", "true"],
    ids=["release_missing_or_image_missing", "everything_present"],
)
def test_a_published_version_builds_and_publishes_nothing(image):
    """The gh-2260 half: a duplicate or a re-run never builds or publishes."""
    res = _simulate(published="true", image_exists=image)
    for job in PRE:
        assert res[job] == "skipped", (job, res)


def test_rerun_all_after_a_post_publish_failure_runs_both_post_jobs():
    """THE gh-2310 case: published to PyPI, then github-release or docker
    failed; 'Re-run all jobs' sees `published=true` and an image that is not
    there. Both jobs must run -- each is idempotent, so running is cheap --
    or the run ends green with the release or the image missing."""
    res = _simulate(published="true", image_exists="false")
    assert res["github-release"] == "success", res
    assert res["docker"] == "success", res


def test_a_duplicate_run_with_everything_shipped_stays_cheap():
    """The image exists: docker is skipped. github-release runs but is a
    no-op (its step finds the release and leaves it), so the run is green
    and `release-watch` goes on to verify what the first run shipped."""
    res = _simulate(published="true", image_exists="true")
    assert res["github-release"] == "success", res
    assert res["docker"] == "skipped", res


def test_a_failed_publish_still_skips_the_post_jobs():
    """`!cancelled()` stops the skip cascading; it must not run these after
    a publish that FAILED, nor after a smoke that failed before it."""
    res = _simulate(published="false", image_exists="false", fail=("publish",))
    assert res["github-release"] == "skipped", res
    assert res["docker"] == "skipped", res
    res = _simulate(published="false", image_exists="false", fail=("smoke",))
    for job in ("publish", *POST):
        assert res[job] == "skipped", (job, res)


def test_a_failed_release_still_skips_docker():
    res = _simulate(
        published="true", image_exists="false", fail=("github-release",)
    )
    assert res["docker"] == "skipped", res


# -- the hardening ------------------------------------------------------------


def test_a_job_that_escapes_the_skip_cascade_carries_the_published_gate():
    """gh-2260's test assumes 'a skipped need skips its dependant'. That
    holds only while no `if:` uses `always()`, `failure()` or `cancelled()`.
    Assert it: such a job must say, in its own condition, what a skipped
    `publish` means -- it names `published`."""
    escaping = {
        name: _condition(job)
        for name, job in _jobs().items()
        if _condition(job)
        and re.search(r"\b(always|failure|cancelled)\(\)", _condition(job))
    }
    assert set(escaping) == set(POST), escaping  # armed: the two it is for
    for name, cond in escaping.items():
        assert "needs.resolve.outputs.published" in cond or any(
            f"needs.{up}.result" in cond for up in _needs(_jobs()[name])
        ), f"{name} escapes the skip cascade with no published gate: {cond}"
    # github-release is the one that reads `published` directly.
    assert (
        "needs.resolve.outputs.published == 'true'"
        in escaping["github-release"]
    )


def test_the_detector_sees_an_unguarded_always():
    """The arming check: a future `if: always()` job must read as escaping."""
    assert re.search(r"\b(always|failure|cancelled)\(\)", "always()")
    job = {"needs": ["publish"], "if": "always()"}
    needs = {"publish": ("skipped", {})}
    assert _runs(job, needs) is True  # it WOULD run on a skipped publish


# -- the idempotence the post jobs rely on ---------------------------------


def _step(job: dict, name: str) -> dict:
    (step,) = [s for s in job["steps"] if s.get("name") == name]
    return step


def test_github_release_leaves_an_existing_release_alone():
    run = _step(_jobs()["github-release"], "Create GitHub Release")["run"]
    assert "gh release view" in run, run
    assert run.index("gh release view") < run.index("gh release create"), run


def test_the_image_probe_asks_for_the_tag_docker_writes():
    """`image-state` and `docker.yml` name the image the same way, or the
    probe answers 'absent' for an image that exists (a wasted rebuild) -- or
    worse, 'present' for one that does not."""
    steps = {s.get("id"): s for s in _jobs()["image-state"]["steps"]}
    run = steps["probe"]["run"]
    ref = re.search(r'ref="([^"]+)"', run).group(1)
    assert ref.endswith("/jm-examples-linux:sha-${SHA}"), ref
    docker = DOCKER.read_text(encoding="utf-8")
    # docker.yml pushes `sha-<commit>-<arch>` and combines them into
    # `sha-<commit>`: the tag the probe asks for.
    assert "jm-examples-linux" in docker
    assert 'sha-${SHA}-${ARCH}"' in docker
    assert '"${BASE}:sha-${SHA}"' in docker
    # The probe fails toward building.
    assert 'echo "exists=false"' in run


def test_the_runs_for_one_tag_are_serialised_and_never_cancelled():
    conc = yaml.safe_load(WF.read_text(encoding="utf-8"))["concurrency"]
    assert conc["cancel-in-progress"] is False, conc
    assert "inputs.tag || github.ref_name" in conc["group"], conc


# -- :latest never moves backwards -------------------------------------------
#
# `docker.yml`'s manifest step OVERWRITES the tags it is given, and the
# post-publish jobs now run again for a version already on PyPI. The image
# probe answers 'absent' on any doubt, so a re-run or a dispatch for an OLDER
# tag whose probe hiccups would rebuild that old release and point `:latest`
# at it. Only the newest release may move `:latest`; an older tag gets its
# `sha-` tag and nothing else. These run the workflows' own shell, with `gh`
# and `docker` stubbed, rather than reading it.


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _step_run(path: Path, job: str, step: str) -> str:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    (hit,) = [
        s
        for s in doc["jobs"][job]["steps"]
        if s.get("id") == step or s.get("name") == step
    ]
    return hit["run"]


def _bash(script: str, env: dict, tmp: Path, stubs: "dict[str, str]"):
    bin_dir = tmp / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, body in {"sleep": "exit 0\n", **stubs}.items():
        _stub(bin_dir, name, body)
    full = {"PATH": f"{bin_dir}:/usr/bin:/bin", **env}
    return subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        env=full,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _newest(tmp: Path, tag: str, refs: "list[str] | None"):
    """Run resolve's `newest` step for *tag*; `refs=None` makes `gh` fail."""
    out = tmp / "out"
    if refs is None:
        gh = "exit 1\n"
    else:
        listing = "".join(f"refs/tags/{r}\\n" for r in refs)
        gh = f"printf '{listing}'\n"
    run = _bash(
        _step_run(WF, "resolve", "newest"),
        {
            "TAG": tag,
            "REPO": "o/r",
            "GH_TOKEN": "x",
            "GITHUB_OUTPUT": str(out),
        },
        tmp,
        {"gh": gh},
    )
    return run, (out.read_text(encoding="utf-8") if out.exists() else "")


TAGS = ["v0.9.0", "v0.10.0", "v0.111.2", "v0.112.0rc1", "nightly"]


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v0.111.2", "true"),
        ("v0.10.0", "false"),
        # A string sort puts v0.9.0 above v0.10.0 and v0.111.2: the version
        # sort is the point.
        ("v0.9.0", "false"),
        # Not a plain vX.Y.Z tag: never "the newest".
        ("v0.112.0rc1", "false"),
    ],
)
def test_only_the_highest_release_tag_is_newest(tmp_path, tag, expected):
    run, out = _newest(tmp_path, tag, TAGS)
    assert run.returncode == 0, (run.stdout, run.stderr)
    assert out.strip() == f"newest={expected}", (tag, out)


def test_an_unreadable_tag_list_fails_instead_of_guessing(tmp_path):
    """A guess could move `:latest` backwards (or leave it stale for the
    release that should have moved it). Red and re-runnable is the answer."""
    run, out = _newest(tmp_path, "v0.111.2", None)
    assert run.returncode != 0, (run.stdout, run.stderr)
    assert out == "", out
    assert "not guessing" in run.stdout + run.stderr


def _manifest(tmp: Path, latest: str, sha: str = "abc123") -> "list[str]":
    """The `-t` tags the manifest step hands `imagetools create`."""
    rec = tmp / "rec"
    run = _bash(
        _step_run(DOCKER, "manifest", "Create and push combined manifest"),
        {"OWNER": "o", "SHA": sha, "LATEST": latest},
        tmp,
        {"docker": f'printf "%s\\n" "$@" > {rec}\n'},
    )
    assert run.returncode == 0, (run.stdout, run.stderr)
    args = rec.read_text(encoding="utf-8").split()
    return [args[i + 1] for i, a in enumerate(args) if a == "-t"]


def test_the_manifest_moves_latest_only_when_told_to(tmp_path):
    assert _manifest(tmp_path / "a", "true") == [
        "ghcr.io/o/jm-examples-linux:sha-abc123",
        "ghcr.io/o/jm-examples-linux:latest",
    ]


def test_the_manifest_leaves_latest_alone_otherwise(tmp_path):
    tags = _manifest(tmp_path / "b", "false")
    assert tags == ["ghcr.io/o/jm-examples-linux:sha-abc123"], tags
    # ...and an empty or odd value is "no", not "yes".
    assert _manifest(tmp_path / "c", "")[-1].endswith(":sha-abc123")


def test_the_input_defaults_to_moving_latest():
    """ci.yml's push to main calls docker.yml without it, and main's tip is
    by definition the newest thing there is."""
    doc = yaml.safe_load(DOCKER.read_text(encoding="utf-8"))
    on = doc.get("on") or doc.get(True)
    spec = on["workflow_call"]["inputs"]["latest"]
    assert spec["type"] == "boolean" and spec["default"] is True, spec


def test_release_passes_whether_its_tag_is_the_newest():
    docker = _jobs()["docker"]
    expr = str(docker["with"]["latest"])
    assert "needs.resolve.outputs.newest == 'true'" in expr, expr


def test_an_older_tag_whose_probe_doubts_leaves_latest_untouched(tmp_path):
    """The scenario: v0.10.0 is re-run (or dispatched) while v0.111.2 is the
    newest. The image probe cannot read the registry and answers 'absent', so
    docker RUNS. The `latest` it is handed must be false, and the manifest
    step, given that, must not write `:latest`."""
    res = _simulate(published="true", image_exists="false")
    assert res["docker"] == "success", res  # the doubt path does run docker
    expr = re.fullmatch(
        r"\$\{\{\s*(.*?)\s*\}\}", str(_jobs()["docker"]["with"]["latest"])
    ).group(1)
    needs = {"resolve": ("success", {"newest": "false"})}
    latest = str(_Eval(expr, needs).parse()).lower()
    assert latest == "false", latest
    assert _manifest(tmp_path, latest) == [
        "ghcr.io/o/jm-examples-linux:sha-abc123"
    ]
    # ...and for the newest release it does move.
    needs = {"resolve": ("success", {"newest": "true"})}
    assert str(_Eval(expr, needs).parse()).lower() == "true"


# -- the GitHub "Latest release" badge never moves backwards ------------------
#
# `gh release create --latest` marks the release it creates as GitHub's
# "Latest". `github-release` now creates a MISSING release on a re-run or a
# dispatch, which for an older tag would move the badge backwards: the same
# regression as `:latest`, with the same answer from the same `newest`.


def _create_release(tmp: Path, newest: str, exists: bool = False):
    """Run the `Create GitHub Release` step; return `gh release create`'s argv
    (None if it was not called)."""
    rec = tmp / "create_args"
    stub = (
        'if [ "$1 $2" = "release view" ]; then exit '
        f"{0 if exists else 1}; fi\n"
        'if [ "$1 $2" = "release create" ]; then '
        f'printf "%s\\n" "$@" > {rec}; exit 0; fi\n'
        "exit 2\n"
    )
    run = _bash(
        _step_run(WF, "github-release", "Create GitHub Release"),
        {"RELEASE_TAG": "v0.10.0", "NEWEST": newest, "GH_TOKEN": "x"},
        tmp,
        {"gh": stub},
    )
    assert run.returncode == 0, (run.stdout, run.stderr)
    return rec.read_text(encoding="utf-8").split() if rec.exists() else None


def test_only_the_newest_release_takes_the_latest_badge(tmp_path):
    assert "--latest=true" in _create_release(tmp_path / "a", "true")


@pytest.mark.parametrize("newest", ["false", ""])
def test_an_older_release_does_not_take_it_back(tmp_path, newest):
    args = _create_release(tmp_path / "b", newest)
    assert "--latest=false" in args and "--latest=true" not in args, args


def test_an_existing_release_is_not_touched(tmp_path):
    assert _create_release(tmp_path / "c", "true", exists=True) is None


def test_github_release_is_handed_the_same_newest():
    env = _step(_jobs()["github-release"], "Create GitHub Release")["env"]
    assert env["NEWEST"] == "${{ needs.resolve.outputs.newest }}", env


# -- the image is named for the commit that was built -------------------------
#
# docker.yml checks out `inputs.ref` (the tag) but used to NAME the image for
# `github.sha`, the CALLER's commit. On a tag push or a re-run they coincide;
# on a DISPATCH from main for any tag that is not main's head they do not, and
# the old tag's code was pushed as `sha-<main's head>`, overwriting the image
# CI built for main while the `image-state` probe, asking for the RESOLVED
# tag's sha, never found it.

MAIN_HEAD = "m" * 40
TAG_COMMIT = "t" * 40


def test_every_image_name_comes_from_one_value():
    """Registration-free over docker.yml: `github.sha` appears only in the
    two env lines that default the `sha` input, never in a tag line."""
    text = DOCKER.read_text(encoding="utf-8")
    uses = [ln.strip() for ln in text.splitlines() if "github.sha" in ln]
    code = [ln for ln in uses if not ln.startswith("#")]
    assert code == ["SHA: ${{ inputs.sha || github.sha }}"] * 2, code


def test_release_names_the_image_for_the_resolved_tags_commit():
    assert (
        _jobs()["docker"]["with"]["sha"] == "${{ needs.resolve.outputs.sha }}"
    )
    (probe,) = [
        x for x in _jobs()["image-state"]["steps"] if x.get("id") == "probe"
    ]
    assert probe["env"]["SHA"] == "${{ needs.resolve.outputs.sha }}", probe


def _arch_push(tmp: Path, sha: str) -> "list[str]":
    rec = tmp / "rec"
    run = _bash(
        _step_run(DOCKER, "linux", "Tag and push arch-specific image to GHCR"),
        {"OWNER": "o", "SHA": sha, "ARCH": "amd64"},
        tmp,
        {"docker": f'printf "%s\\n" "$@" >> {rec}\n'},
    )
    assert run.returncode == 0, (run.stdout, run.stderr)
    return rec.read_text(encoding="utf-8").split()


def test_a_dispatch_from_main_names_the_image_for_the_tag_not_for_main(
    tmp_path,
):
    """The scenario: dispatched from main (github.sha = main's head) for an
    older tag. `inputs.sha || github.sha` is the resolved tag's commit, and
    every tag written carries it -- never main's head."""
    value = "inputs.sha || github.sha"
    for job, step in (
        ("linux", "Tag and push arch-specific image to GHCR"),
        ("manifest", "Create and push combined manifest"),
    ):
        doc = yaml.safe_load(DOCKER.read_text(encoding="utf-8"))
        (s,) = [x for x in doc["jobs"][job]["steps"] if x.get("name") == step]
        assert s["env"]["SHA"] == "${{ " + value + " }}", s["env"]
    inputs = {"sha": TAG_COMMIT}
    github = {"sha": MAIN_HEAD}
    resolved = inputs["sha"] or github["sha"]
    assert resolved == TAG_COMMIT
    pushed = _arch_push(tmp_path / "p", resolved)
    assert any(a.endswith(f":sha-{TAG_COMMIT}-amd64") for a in pushed), pushed
    assert MAIN_HEAD not in " ".join(pushed), pushed
    combined = _manifest(tmp_path / "m", "false", sha=resolved)
    assert combined == [f"ghcr.io/o/jm-examples-linux:sha-{TAG_COMMIT}"]
    assert all(MAIN_HEAD not in t for t in combined), combined


def test_ci_still_names_the_image_for_its_own_commit():
    """ci.yml passes no `sha`: the default is the caller's own commit."""
    doc = yaml.safe_load(DOCKER.read_text(encoding="utf-8"))
    on = doc.get("on") or doc.get(True)
    spec = on["workflow_call"]["inputs"]["sha"]
    assert spec["default"] == "" and spec["type"] == "string", spec
