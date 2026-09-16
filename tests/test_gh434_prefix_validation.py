"""gh-434 — a config-only prefix must be refused, with a reason.

A doppler source build tree ships `doppler-config.cmake` but, pre doppler#380,
no `doppler-targets.cmake`: `install(EXPORT)` only materialises the targets
file at install time. Such a directory looks like a doppler prefix and hard-
fails at cmake CONFIGURE — *"include could not find requested file:
.../doppler-targets.cmake"* — which reads as a broken example rather than an
unfinished install.

**This moved rather than went away.** The check was written for the candidate
SCAN, which no longer exists: the example downloads doppler's latest release
and does not look at the machine (see `_find_doppler_prefix`). The hazard did
not move with it — a person can hand exactly this directory to
`--doppler-prefix`, and there the failure is worse, because they chose the
path deliberately and the cmake error never mentions it. So the rule now lives
in `why_prefix_unusable`, on the explicit path.

That also made these tests honest. They used to carry a module-level skip when
a system-wide doppler existed, because a real install outranked the fixtures —
the tests could not run on the machines most likely to have the bug.
`why_prefix_unusable` is a pure function of a path, so nothing shadows it and
nothing is skipped.
"""

import importlib.util
import sys
from pathlib import Path

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


def _prefix(
    root: Path, rel: str, *, targets: bool, version: str | None = None
):
    """A doppler-shaped prefix; *targets* controls the gh-434 condition."""
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "doppler-config.cmake").write_text("# config\n", encoding="utf-8")
    if targets:
        (d / "doppler-targets.cmake").write_text(
            "# targets\n", encoding="utf-8"
        )
    if version is not None:
        pc = root / "lib" / "pkgconfig" / "doppler.pc"
        pc.parent.mkdir(parents=True, exist_ok=True)
        pc.write_text(f"Version: {version}\n", encoding="utf-8")
    return root


def test_a_config_only_build_tree_is_refused(tmp_path):
    m = _load_module()
    p = _prefix(tmp_path / "bld", ".", targets=False)
    why = m.why_prefix_unusable(p)
    assert why is not None
    assert "doppler-targets.cmake" in why


def test_the_refusal_says_what_to_do_about_it(tmp_path):
    """A refusal that does not name the fix just relocates the confusion."""
    m = _load_module()
    why = m.why_prefix_unusable(_prefix(tmp_path / "bld", ".", targets=False))
    assert "cmake --install" in why


def test_a_complete_build_tree_is_accepted(tmp_path):
    m = _load_module()
    p = _prefix(tmp_path / "bld", ".", targets=True)
    assert m.why_prefix_unusable(p) is None


def test_an_installed_prefix_is_accepted(tmp_path):
    m = _load_module()
    p = _prefix(tmp_path / "inst", "lib/cmake/doppler", targets=True)
    assert m.why_prefix_unusable(p) is None


def test_a_lib64_prefix_is_accepted(tmp_path):
    """cmake searches both; so must this."""
    m = _load_module()
    p = _prefix(tmp_path / "inst64", "lib64/cmake/doppler", targets=True)
    assert m.why_prefix_unusable(p) is None


def test_a_directory_with_no_doppler_at_all_is_refused(tmp_path):
    m = _load_module()
    (tmp_path / "empty").mkdir()
    why = m.why_prefix_unusable(tmp_path / "empty")
    assert why is not None and "doppler-config.cmake" in why
