"""gh-434 — _find_doppler_prefix must not accept a config-only prefix.

A doppler source build tree ships doppler-config.cmake but (pre
doppler#380) no doppler-targets.cmake: install(EXPORT) only materialises
the targets file at install time. The candidate scan accepted any
directory containing the config, so such a tree was returned as a usable
prefix and the example hard-failed at cmake configure ("include could
not find requested file: .../doppler-targets.cmake") instead of falling
through to the auto-downloaded prebuilt release.

The fix requires doppler-targets.cmake to exist next to the config
before a candidate is accepted.
"""

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


# A real doppler install in a system prefix would satisfy the candidate
# scan before the home-relative paths this test controls.
_SYSTEM_HAS_DOPPLER = any(
    (Path(p) / rel / "doppler-config.cmake").exists()
    for p in ("/usr/local", "/usr")
    for rel in (".", "lib/cmake/doppler", "lib64/cmake/doppler")
)

pytestmark = pytest.mark.skipif(
    _SYSTEM_HAS_DOPPLER,
    reason="system-wide doppler install shadows the test prefixes",
)


def _fake_home(tmp_path, monkeypatch, mod):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    # The download fallback must not fire a real network fetch; a sentinel
    # also lets tests assert the fall-through happened.
    monkeypatch.setattr(mod, "_download_doppler", lambda: "DOWNLOAD")


def test_config_only_build_tree_is_rejected(tmp_path, monkeypatch):
    mod = _load_module()
    _fake_home(tmp_path, monkeypatch, mod)
    build = tmp_path / "doppler" / "build"
    build.mkdir(parents=True)
    (build / "doppler-config.cmake").write_text("# config\n")
    # No doppler-targets.cmake: the false-positive prefix must be skipped
    # and the scan must fall through to the download.
    assert mod._find_doppler_prefix() == "DOWNLOAD"


def test_complete_build_tree_is_accepted(tmp_path, monkeypatch):
    mod = _load_module()
    _fake_home(tmp_path, monkeypatch, mod)
    build = tmp_path / "doppler" / "build"
    build.mkdir(parents=True)
    (build / "doppler-config.cmake").write_text("# config\n")
    (build / "doppler-targets.cmake").write_text("# targets\n")
    assert mod._find_doppler_prefix() == str(build)


def test_installed_prefix_is_accepted(tmp_path, monkeypatch):
    mod = _load_module()
    _fake_home(tmp_path, monkeypatch, mod)
    cfgdir = tmp_path / ".local" / "doppler" / "lib" / "cmake" / "doppler"
    cfgdir.mkdir(parents=True)
    (cfgdir / "doppler-config.cmake").write_text("# config\n")
    (cfgdir / "doppler-targets.cmake").write_text("# targets\n")
    assert mod._find_doppler_prefix() == str(tmp_path / ".local" / "doppler")


def test_installed_prefix_outranks_incomplete_build_tree(
    tmp_path, monkeypatch
):
    mod = _load_module()
    _fake_home(tmp_path, monkeypatch, mod)
    # Incomplete source build tree (the last candidate) ...
    build = tmp_path / "doppler" / "build"
    build.mkdir(parents=True)
    (build / "doppler-config.cmake").write_text("# config\n")
    # ... and a complete rootless install (an earlier candidate).
    cfgdir = tmp_path / ".local" / "doppler" / "lib" / "cmake" / "doppler"
    cfgdir.mkdir(parents=True)
    (cfgdir / "doppler-config.cmake").write_text("# config\n")
    (cfgdir / "doppler-targets.cmake").write_text("# targets\n")
    assert mod._find_doppler_prefix() == str(tmp_path / ".local" / "doppler")
