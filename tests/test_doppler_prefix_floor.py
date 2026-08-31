"""A discovered doppler below the example's floor is rejected, not compiled.

`_find_doppler_prefix` searches `/usr/local`, `/usr`, `~/.local/doppler`,
`~/.local` and `~/doppler/build` *before* falling back to the pinned
auto-download, so any local install **shadows** the pin. That is the
convenience it exists for, and it has cost real time twice — 2026-07-30 and
2026-08-30 — both times as::

    error: too many arguments to function 'nco_steps_u32'

which reads as a bug in the example rather than a fact about the install.
doppler v0.39.0 added the trailing capacity argument the example's `step()`
passes; anything older configures fine and then fails to compile.

**This is gh-434's rule, one stage later.** That change rejected a discovered
prefix carrying `doppler-config.cmake` without `doppler-targets.cmake`,
because it "hard-fails at cmake configure instead of falling through to the
auto-download". A below-floor install is the same false positive, caught at
compile instead of configure, and gets the same answer: skip it and use the
pin.

Two deliberate limits:

* **Unreadable version means accept.** A source build tree may ship the cmake
  config without a `.pc`, and rejecting a prefix that merely could not be
  measured would break doppler's own developers. "Cannot judge" is not
  "unusable".
* **This says nothing about currency.** A local install newer than the pin is
  still used, and still shadows it. That is the pin's job, reported advisorily
  by `make lint` — see `tests/test_doppler_pin_check.py` for why it does not
  gate.

The choice is announced either way, because "which doppler did this build
against" is the first question a failure raises, and a discovered install
answered it nowhere — which is how one shadowed the pin for six releases.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLE = (
    Path(__file__).parent.parent
    / "src"
    / "just_makeit"
    / "examples"
    / "nco_tone"
    / "test.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_nco_tone_ex", EXAMPLE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_nco_tone_ex"] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _install(prefix: Path, version: str | None, libdir: str = "lib") -> Path:
    """A directory indistinguishable from a real doppler install."""
    cm = prefix / libdir / "cmake" / "doppler"
    cm.mkdir(parents=True)
    for f in ("doppler-config.cmake", "doppler-targets.cmake"):
        (cm / f).write_text("", encoding="utf-8")
    if version is not None:
        pc = prefix / libdir / "pkgconfig"
        pc.mkdir(parents=True, exist_ok=True)
        (pc / "doppler.pc").write_text(
            f"Name: doppler\nVersion: {version}\n", encoding="utf-8"
        )
    return prefix


def _search(tmp_path: Path, monkeypatch, version: str | None, libdir="lib"):
    """Run the real search with a fake HOME holding one install."""
    _install(tmp_path / ".local" / "doppler", version, libdir)
    monkeypatch.setattr(M.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(M, "_download_doppler", lambda *a, **k: "<downloaded>")
    return M._find_doppler_prefix()


def test_the_floor_is_encoded_not_only_described():
    """It lived in a comment while the code compared nothing, which is what
    let a below-floor install reach the compiler twice."""
    assert M._DOPPLER_FLOOR == "0.39.0"
    assert M._version_key(M._DOPPLER_VERSION) >= M._version_key(
        M._DOPPLER_FLOOR
    ), "the pin must not be below the floor it enforces"


@pytest.mark.parametrize("version", ["0.33.3", "0.13.2", "0.38.1"])
def test_a_below_floor_install_is_skipped_for_the_pin(
    tmp_path: Path, monkeypatch, capsys, version
):
    """0.33.3 and 0.13.2 are the two that actually shadowed the pin here."""
    assert _search(tmp_path, monkeypatch, version) == "<downloaded>"
    out = capsys.readouterr().out
    assert "skipping" in out and version in out
    assert M._DOPPLER_FLOOR in out, "must say what the floor is"


@pytest.mark.parametrize("version", ["0.39.0", "0.45.0", "1.0.0"])
def test_an_at_or_above_floor_install_is_used(
    tmp_path: Path, monkeypatch, capsys, version
):
    got = _search(tmp_path, monkeypatch, version)
    assert got == str(tmp_path / ".local" / "doppler")
    assert version in capsys.readouterr().out, "must announce which doppler"


def test_an_unmeasurable_install_is_accepted_not_rejected(
    tmp_path: Path, monkeypatch, capsys
):
    """A source build tree may have no `.pc`. Rejecting what cannot be
    measured would break doppler's own developers, so "unknown" is accepted
    and labelled rather than treated as unusable."""
    got = _search(tmp_path, monkeypatch, None)
    assert got == str(tmp_path / ".local" / "doppler")
    assert "version unknown" in capsys.readouterr().out


def test_the_version_is_read_from_lib64_too(tmp_path: Path, monkeypatch):
    """Some distributions install into lib64; a prefix there must not read as
    unmeasurable and silently bypass the floor."""
    assert _search(tmp_path, monkeypatch, "0.33.3", libdir="lib64") == (
        "<downloaded>"
    )


def test_no_local_install_downloads_the_pin(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setattr(M.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(M, "_download_doppler", lambda *a, **k: "<downloaded>")
    assert M._find_doppler_prefix() == "<downloaded>"
    assert M._DOPPLER_VERSION in capsys.readouterr().out


def test_version_keys_compare_numerically():
    assert M._version_key("0.9.0") < M._version_key("0.10.0")
    assert M._version_key("0.39.0") == M._version_key("0.39.0")
