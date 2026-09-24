"""gh-1377: the doppler-backed examples fetch doppler on every CI host.

`nco_tone` (and `kitchen_sink`, through it) downloads doppler's release
build. When no build could be fetched the build step was SKIPPED inside a
test that still reported PASSED, so two wrong rows in the host table went
unseen: macOS was asked for as ``darwin-arm64`` (doppler publishes
``macos-arm64``) and Windows had no row. These pin the table to the hosts
jm's CI runs on, and pin the switch that makes a skip visible there.
"""

import importlib.util
import platform
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NCO_TONE = ROOT / "src" / "just_makeit" / "examples" / "nco_tone" / "test.py"
WORKFLOWS = ROOT / ".github" / "workflows"


def _load():
    spec = importlib.util.spec_from_file_location("_nco_tone_1377", NCO_TONE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# (sys.platform, platform.machine()) as each CI runner reports it, and the
# asset doppler publishes for it -- read off doppler v0.55.0's release.
@pytest.mark.parametrize(
    ("plat", "machine", "asset"),
    [
        ("linux", "x86_64", ("linux-x86_64", ".tar.gz")),  # ubuntu-latest
        ("linux", "aarch64", ("linux-aarch64", ".tar.gz")),  # ubuntu-arm
        ("darwin", "arm64", ("macos-arm64", ".tar.gz")),  # macos-latest
        ("win32", "AMD64", ("windows-x86_64", ".zip")),  # windows-latest
    ],
)
def test_every_ci_host_maps_to_a_published_asset(
    monkeypatch, plat, machine, asset
):
    mod = _load()
    monkeypatch.setattr(mod.sys, "platform", plat)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    assert mod._platform_tag() == asset


def test_an_unpublished_host_has_no_asset(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod.sys, "platform", "freebsd14")
    assert mod._platform_tag() is None


def test_unavailable_skips_by_default(monkeypatch, capsys):
    mod = _load()
    monkeypatch.delenv(mod._REQUIRE_ENV, raising=False)
    assert mod._unavailable("no doppler here") is None
    assert "no doppler here" in capsys.readouterr().out


def test_unavailable_fails_when_required(monkeypatch):
    mod = _load()
    monkeypatch.setenv(mod._REQUIRE_ENV, "1")
    with pytest.raises(AssertionError, match="no doppler here"):
        mod._unavailable("no doppler here")


def test_a_failed_fetch_reaches_the_switch(monkeypatch):
    """The download's None goes through the switch, not a bare return."""
    mod = _load()
    monkeypatch.setenv(mod._REQUIRE_ENV, "1")
    monkeypatch.setattr(mod, "latest_release", lambda: None)
    monkeypatch.setattr(mod, "_download_doppler", lambda version: None)
    with pytest.raises(AssertionError, match=mod._REQUIRE_ENV):
        mod._find_doppler_prefix()


_RUNS_EXAMPLES = re.compile(
    r"make test-examples|just-makeit example \"\$example\""
)
# A job is a two-space-indented key under `jobs:`, up to the next one.
_JOB = re.compile(r"(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)")


def _jobs_running_examples():
    """(workflow, job, body) for each job whose steps run the examples."""
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        text = wf.read_text(encoding="utf-8")
        body = text[text.index("\njobs:") :]
        for m in _JOB.finditer(body):
            # A comment ABOUT `make test-examples` is not a step running it.
            code = re.sub(r"(?m)^\s*#.*$", "", m.group(2))
            if _RUNS_EXAMPLES.search(code):
                yield wf.name, m.group(1), m.group(2)


def test_every_job_running_the_examples_requires_doppler():
    """A job running the examples without the switch skips silently."""
    jobs = list(_jobs_running_examples())
    assert jobs, "found no job running the examples; the scan is unarmed"
    missing = [
        f"{wf}:{job}"
        for wf, job, body in jobs
        if not re.search(r'^\s+JM_REQUIRE_DOPPLER: "1"', body, re.M)
    ]
    assert not missing, (
        "these jobs run the examples without JM_REQUIRE_DOPPLER, so an "
        f"unfetchable doppler would skip their build and pass: {missing}"
    )
