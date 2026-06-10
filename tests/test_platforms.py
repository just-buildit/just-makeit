"""Windows CMake boilerplate is opt-in per project (gh-213).

jm emits the MinGW runtime-DLL ``if(WIN32 …)`` block into every component /
module ``CMakeLists.txt``. For a project that does not target Windows that is
untested boilerplate the drift gate freezes in place. It is now gated on
``[project] platforms`` (default ``["linux", "macos"]``): off by default, and
``jm status --check`` treats its absence as correct.
"""

from just_makeit import _config as C
from just_makeit import _status
from just_makeit._apply import run as jm_apply
from just_makeit._module import run as jm_module
from just_makeit._new import run as jm_new
from just_makeit._object import run as jm_object

_WINDOWS = ["linux", "macos", "windows"]


def _scaffold(root, platforms=None):
    jm_new("p", root, platforms=platforms)
    jm_object(root, "eng", module=None, state_vars=[("g", "double", "1.0")])
    jm_module(root, "mod")
    jm_object(root, "fir", module="mod", state_vars=[("g", "double", "1.0")])
    return root


def _comp_cmake(root):
    return (root / "native/src/eng/CMakeLists.txt").read_text()


def _mod_cmake(root):
    return (root / "native/src/mod/CMakeLists.txt").read_text()


class TestConfig:
    def test_default_platforms(self, tmp_path):
        cfg = {"project": {}}
        assert C.project_platforms(cfg) == ["linux", "macos"]
        assert C.is_windows_target(cfg) is False

    def test_windows_target(self):
        cfg = {"project": {"platforms": _WINDOWS}}
        assert C.is_windows_target(cfg) is True


class TestDefaultOff:
    def test_no_windows_blocks(self, tmp_path):
        root = _scaffold(tmp_path / "p")
        assert "if(WIN32" not in _comp_cmake(root)
        assert "if(WIN32" not in _mod_cmake(root)
        # No platforms key written for the default.
        assert "platforms" not in (root / "just-makeit.toml").read_text()

    def test_status_clean(self, tmp_path):
        root = _scaffold(tmp_path / "p")
        assert _status.run(root, check=True) == 0


class TestWindowsOptIn:
    def test_blocks_emitted(self, tmp_path):
        root = _scaffold(tmp_path / "p", platforms=_WINDOWS)
        assert "if(WIN32 AND CMAKE_C_COMPILER_ID" in _comp_cmake(root)
        assert "-static-libgcc" in _comp_cmake(root)
        assert "libwinpthread-1.dll" in _mod_cmake(root)
        # The opt-in is persisted as a real TOML array.
        manifest = (root / "just-makeit.toml").read_text()
        assert 'platforms = ["linux", "macos", "windows"]' in manifest
        assert C.is_windows_target(C.load(root)) is True

    def test_status_clean(self, tmp_path):
        root = _scaffold(tmp_path / "p", platforms=_WINDOWS)
        assert _status.run(root, check=True) == 0


class TestMigration:
    def test_apply_strips_blocks_when_windows_dropped(self, tmp_path):
        # A project built with Windows on (like doppler's frozen blocks) drops
        # them on the next apply once `windows` leaves [project] platforms —
        # and status is clean afterwards.
        root = _scaffold(tmp_path / "p", platforms=_WINDOWS)
        assert "if(WIN32" in _comp_cmake(root)

        manifest = root / "just-makeit.toml"
        lines = [
            ln
            for ln in manifest.read_text().splitlines()
            if not ln.startswith("platforms = ")
        ]
        manifest.write_text("\n".join(lines) + "\n")

        jm_apply(root)
        assert "if(WIN32" not in _comp_cmake(root)
        assert "if(WIN32" not in _mod_cmake(root)
        assert _status.run(root, check=True) == 0
