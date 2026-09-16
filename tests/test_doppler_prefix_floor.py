"""A doppler below the example's floor is refused, not compiled.

doppler v0.39.0 added the trailing capacity argument the example's `step()`
passes; anything older configures fine and then fails to COMPILE as::

    error: too many arguments to function 'nco_steps_u32'

which reads as a bug in the example rather than a fact about the install. That
cost real time twice — 2026-07-30 and 2026-08-30.

**This is gh-434's rule one stage later**, and it moved to the same place.
gh-434 refused a prefix carrying `doppler-config.cmake` with no
`doppler-targets.cmake` because it hard-fails at cmake *configure*; a
below-floor prefix is the same false positive caught at *compile*. Both now
live in `why_prefix_unusable`.

The original framing — "a local install SHADOWS the pin, and that is the
convenience it exists for" — is gone with the scan. The example fetches
doppler's latest release and never inspects the machine, so a below-floor
doppler can only arrive by someone naming it with `--doppler-prefix`. That is
where the check belongs: they chose the path, so the refusal should name it.
"""

# `str | None` below is PEP 604; jm's matrix starts at 3.9, where that is
# a runtime TypeError in a signature without this import.
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_NCO_TONE = (
    Path(__file__).parent.parent
    / "src"
    / "just_makeit"
    / "examples"
    / "nco_tone"
    / "test.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("nco_tone_test", _NCO_TONE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _install(root: Path, version: str | None):
    """A complete, installed-shape doppler prefix at *version*."""
    d = root / "lib" / "cmake" / "doppler"
    d.mkdir(parents=True, exist_ok=True)
    (d / "doppler-config.cmake").write_text("# config\n", encoding="utf-8")
    (d / "doppler-targets.cmake").write_text("# targets\n", encoding="utf-8")
    if version is not None:
        pc = root / "lib" / "pkgconfig" / "doppler.pc"
        pc.parent.mkdir(parents=True, exist_ok=True)
        pc.write_text(f"Version: {version}\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("version", ["0.13.2", "0.33.3", "0.38.1"])
def test_a_below_floor_prefix_is_refused(tmp_path, version):
    m = _load_module()
    why = m.why_prefix_unusable(_install(tmp_path / version, version))
    assert why is not None
    assert version in why and m._DOPPLER_FLOOR in why


def test_the_refusal_explains_why_that_version_cannot_work(tmp_path):
    """Naming the floor without the reason invites someone to lower it."""
    m = _load_module()
    why = m.why_prefix_unusable(_install(tmp_path / "old", "0.38.1"))
    assert "nco_steps_u32" in why


@pytest.mark.parametrize("version", ["0.39.0", "0.45.0", "0.49.0", "1.0.0"])
def test_an_at_or_above_floor_prefix_is_accepted(tmp_path, version):
    m = _load_module()
    assert m.why_prefix_unusable(_install(tmp_path / version, version)) is None


def test_an_unmeasurable_prefix_is_accepted_not_refused(tmp_path):
    """None means 'cannot judge'. Refusing a prefix merely because its version
    could not be read would reject working installs that ship no `.pc`."""
    m = _load_module()
    assert m.why_prefix_unusable(_install(tmp_path / "nopc", None)) is None
