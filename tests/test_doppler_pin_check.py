"""The nco_tone doppler pin, and the advisory that reports when it lags.

`_DOPPLER_VERSION` is what a local run downloads; `nco_tone_ci.yml` downloads
doppler's *latest* release instead, deliberately. The two paths agree only
while the pin tracks the latest release.

**Why the currency report is advisory rather than a gate**, pinned here so the
reasoning is not lost the next time someone wants to give it teeth: the pin
drifts because a different repository published, not because of anything in
the change being linted. Measured 2026-08-30 — doppler shipped five releases
in thirty days — so a hard gate would redden this repo roughly weekly and block
PRs with nothing to do with doppler. That is the `standard-check` shape, whose
cost this repo already knows.

What actually protects the example is `test_example[nco_tone]` building
against the pin. The report is a sighting, not a guard, and the tests below
assert exactly that: it must never return non-zero, in any of its three states.

A local doppler install SHADOWS the pin (`_find_doppler_prefix` prefers it), so
on a developer box the pin may not be exercised at all — which is how a stale
one ships unnoticed. That is a known hole, not something these tests close.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import check_doppler_pin as C  # noqa: E402

PIN_FILE = (
    Path(__file__).parent.parent
    / "src"
    / "just_makeit"
    / "examples"
    / "nco_tone"
    / "test.py"
)


def test_it_reads_the_real_pin():
    """Against the real file, so a rename or a reformat is caught here rather
    than by the check quietly reporting nothing forever."""
    pin = C.read_pin()
    assert pin is not None, "_DOPPLER_VERSION not found in the example"
    assert pin[0].isdigit(), pin
    assert f'_DOPPLER_VERSION = "{pin}"' in PIN_FILE.read_text(
        encoding="utf-8"
    )


def test_the_pin_is_at_or_above_the_floor_the_example_needs():
    """doppler v0.39.0 added the trailing capacity argument to
    `nco_steps_u32` that the example's step() body passes. Below that the
    example cannot compile, whatever the currency report says — this is the
    invariant with teeth, and it needs no network."""
    assert C._key(C.read_pin()) >= C._key("0.39.0")


def test_behind_is_reported_but_does_not_fail(capsys, monkeypatch):
    monkeypatch.setattr(C, "read_pin", lambda *a, **k: "0.30.0")
    monkeypatch.setattr(C, "latest_release", lambda *a, **k: "0.45.0")
    assert C.main() == 0, "the currency report must stay advisory"
    out = capsys.readouterr().out
    assert "BEHIND" in out
    assert "0.30.0" in out and "0.45.0" in out
    assert "advisory" in out
    assert "_DOPPLER_VERSION" in out, "must name what to change"


def test_current_is_reported(capsys, monkeypatch):
    monkeypatch.setattr(C, "read_pin", lambda *a, **k: "0.45.0")
    monkeypatch.setattr(C, "latest_release", lambda *a, **k: "0.45.0")
    assert C.main() == 0
    assert "current" in capsys.readouterr().out


def test_ahead_of_latest_is_not_reported_as_behind(capsys, monkeypatch):
    """A pin bumped to an unpublished tag, or a yanked release, must not read
    as drift — `>=`, not `==`."""
    monkeypatch.setattr(C, "read_pin", lambda *a, **k: "0.46.0")
    monkeypatch.setattr(C, "latest_release", lambda *a, **k: "0.45.0")
    assert C.main() == 0
    assert "BEHIND" not in capsys.readouterr().out


def test_unreachable_network_says_so_rather_than_passing_silently(
    capsys, monkeypatch
):
    """Offline is not evidence the pin is current. It must not fail — being
    offline is not the PR's fault — but it must not print a clean-looking
    nothing either, which is the shape that makes a check useless."""
    monkeypatch.setattr(C, "read_pin", lambda *a, **k: "0.45.0")
    monkeypatch.setattr(C, "latest_release", lambda *a, **k: None)
    assert C.main() == 0
    out = capsys.readouterr().out
    assert "unknown" in out and "0.45.0" in out


def test_a_missing_pin_constant_is_announced(capsys, monkeypatch):
    """If the constant is renamed the check loses its subject. Saying so beats
    reporting a confident nothing."""
    monkeypatch.setattr(C, "read_pin", lambda *a, **k: None)
    assert C.main() == 0
    assert "no _DOPPLER_VERSION" in capsys.readouterr().out


def test_version_keys_compare_numerically():
    assert C._key("0.9.0") < C._key("0.10.0"), "string compare would invert"
    assert C._key("0.45.0") == C._key("0.45.0")
    assert C._key("1.0.0") > C._key("0.99.9")
