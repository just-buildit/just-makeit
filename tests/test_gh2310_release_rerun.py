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


def test_the_first_run_runs_everything():
    res = _simulate(published="false", image_exists="false")
    assert _ran(res) == set(_jobs()), res
    assert all(r == "success" for r in res.values()), res


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
    assert "jm-examples-linux" in docker and 'sha-${{ github.sha }}"' in docker
    # The probe fails toward building.
    assert 'echo "exists=false"' in run


def test_the_runs_for_one_tag_are_serialised_and_never_cancelled():
    conc = yaml.safe_load(WF.read_text(encoding="utf-8"))["concurrency"]
    assert conc["cancel-in-progress"] is False, conc
    assert "inputs.tag || github.ref_name" in conc["group"], conc
