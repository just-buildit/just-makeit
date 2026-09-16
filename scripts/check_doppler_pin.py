#!/usr/bin/env python3
"""Report when the nco_tone example's doppler pin lags doppler's latest release.

`_DOPPLER_VERSION` is what a local run downloads; `nco_tone_ci.yml` downloads
doppler's *latest* release instead, deliberately — the example is worth more
exercising current doppler than frozen against an old one. The two paths
therefore agree only while the pin tracks the latest release, and a stale pin
means a local run builds against an API the example no longer uses.

**Advisory on purpose. This never fails the build.**

The pin drifts because a *different* repository published, not because of
anything in the change being linted. Measured 2026-08-30: doppler shipped five
releases in thirty days, so a hard gate here would redden just-makeit roughly
weekly and block PRs that have nothing to do with doppler. This repo has been
bitten by exactly that shape before — `standard-check` turns every vendoring
repo red the moment canonical moves.

So this prints and exits 0. It is a *sighting*, and the thing that actually
protects the example is `test_example[nco_tone]` building against the pin.

Unreachable network is also not a failure here, for the same reason: being
offline is not evidence the pin is stale. Say so and exit 0 — the one thing
this must never do is print nothing and look like a pass.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PIN_FILE = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "just_makeit"
    / "examples"
    / "nco_tone"
    / "test.py"
)
LATEST_URL = "https://api.github.com/repos/doppler-dsp/doppler/releases/latest"
_PIN_RE = re.compile(r'^_DOPPLER_VERSION\s*=\s*"([^"]+)"', re.MULTILINE)
TIMEOUT_S = 10


def _example():
    """The nco_tone example module, loaded by path.

    It is example payload rather than importable library code, so it is loaded
    the way the example runner loads it.
    """
    import importlib.util as u

    spec = u.spec_from_file_location("_nco_tone_example", PIN_FILE)
    mod = u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_pin(path: Path = PIN_FILE) -> str | None:
    """The pinned version, or None when the constant is gone or renamed."""
    if not path.is_file():
        return None
    m = _PIN_RE.search(path.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def latest_release(url: str = LATEST_URL) -> str | None:
    """doppler's latest release tag without the leading ``v``, or None.

    **Delegates to the example**, which is the one implementation. This script
    already reads `_DOPPLER_VERSION` out of that file; the resolver flows the
    same direction. It lives there rather than here because the example must
    run standalone — it ships in the package and `scripts/` does not, so a copy
    here would be the second implementation of a primitive, and those drift.
    """
    return _example().latest_release(url)


def _key(v: str) -> tuple:
    """A comparable version key; unparseable parts sort as 0."""
    return tuple(int(p) if p.isdigit() else 0 for p in v.split("."))


def main() -> int:
    pin = read_pin()
    if pin is None:
        # Not a failure, but it IS the check losing its subject, which is
        # worth more than silence: a renamed constant would otherwise make
        # this print a clean-looking nothing forever.
        print(
            "doppler-pin-check: no _DOPPLER_VERSION found in "
            f"{PIN_FILE.name} — the pin moved or was renamed; "
            "update scripts/check_doppler_pin.py"
        )
        return 0

    latest = latest_release()
    if latest is None:
        print(
            f"doppler-pin-check: pin {pin}; could not reach the GitHub "
            "releases API, so currency is unknown (offline or rate-limited)"
        )
        return 0

    if _key(pin) >= _key(latest):
        print(f"doppler-pin-check: pin {pin} is current (latest v{latest})")
        return 0

    print(
        f"doppler-pin-check: pin {pin}, latest v{latest} — BEHIND.\n"
        "  CI builds the example against doppler's latest, so the two paths "
        "now differ.\n"
        "  Bump _DOPPLER_VERSION in "
        "src/just_makeit/examples/nco_tone/test.py when convenient.\n"
        "  (advisory — this does not fail the build)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
