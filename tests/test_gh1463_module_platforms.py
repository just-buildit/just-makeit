"""gh-1463: ``[module.X] platforms`` -- a handle module built only somewhere.

doppler's ``wfm_sink`` embeds a POSIX-only core, so on Windows the target its
generated CMake links (``$<TARGET_OBJECTS:stream_core_obj>``) does not exist
and the GENERATE step fails, and the package re-exporting it breaks on import.
The key is the author saying where the backing exists, and both faces follow:

- the module's ``CMakeLists.txt`` creates its target only there;
- the owning package imports it only there, so elsewhere the NAME is absent
  and the package still imports.

Absent, nothing changes -- asserted byte-for-byte, because every existing
manifest takes that path.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C  # noqa: E402
from just_makeit import _keys  # noqa: E402
from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _ring(platforms: list[str] | None) -> dict:
    """A toy handle whose backing, in the real case, is platform-specific."""
    mod = {
        "kind": "handle",
        "backing": "ringbuf",
        "type_name": "Ring",
        "package": "sig",
        "create_fn": "ringbuf_open",
        "close_fn": "ringbuf_close",
        "create_args": [{"name": "capacity", "type": "size_t"}],
        "extra_link_libs": ["$<TARGET_OBJECTS:posix_only_obj>"],
    }
    if platforms is not None:
        mod["platforms"] = platforms
    return mod


def _project(dest: Path, platforms: list[str] | None) -> None:
    """Package module ``sig`` (one object) re-exporting the handle's type."""
    _silent(new_run, "dsp", dest)
    _silent(module_run, dest, "sig")
    _silent(
        object_run,
        dest,
        "mix",
        module="sig",
        state_vars=[("gain", "float", "1.0f")],
        arg_type="float",
        return_type="float",
    )
    cfg = C.load(dest)
    cfg["module"]["ringbuf"] = _ring(platforms)
    cfg["module"]["sig"]["reexports"] = {"ringbuf": ["Ring"]}
    C.save(dest, cfg)
    _silent(apply_run, dest)


def _cmake(dest: Path) -> str:
    return (dest / "native/src/ringbuf/CMakeLists.txt").read_text()


def _init(dest: Path) -> str:
    return (dest / "src/dsp/sig/__init__.py").read_text(encoding="utf-8")


def _tree(dest: Path) -> dict[Path, bytes]:
    return {
        p: p.read_bytes()
        for p in dest.rglob("*")
        if p.is_file() and "build" not in p.parts
    }


# ── CMake face ───────────────────────────────────────────────────────────────


def test_cmake_target_only_on_the_declared_platforms(tmp_path):
    _project(tmp_path, ["linux", "macos"])
    assert _cmake(tmp_path).startswith(
        'if(BUILD_PYTHON AND (CMAKE_SYSTEM_NAME STREQUAL "Linux" OR APPLE))\n'
    )


def test_absent_key_renders_exactly_as_before(tmp_path):
    _project(tmp_path, None)
    assert _cmake(tmp_path).startswith("if(BUILD_PYTHON)\n")
    text = _init(tmp_path)
    assert "from .ringbuf import Ring  # noqa: E402" in text
    assert "platforms" not in text


# ── Python face ──────────────────────────────────────────────────────────────


def test_reexport_is_guarded_and_absent_from_the_literal_all(tmp_path):
    _project(tmp_path, ["linux"])
    text = _init(tmp_path)
    assert (
        'if __import__("sys").platform in ("linux",):'
        "  # [module.ringbuf] platforms\n"
        "    from .ringbuf import Ring  # noqa: E402\n"
        '    __all__ += ["Ring"]'
    ) in text
    # exactly one import of it, and never unguarded
    assert text.count("from .ringbuf import") == 1
    assert "\nfrom .ringbuf import" not in text
    literal_all = next(
        ln for ln in text.splitlines() if ln.startswith("__all__ = [")
    )
    assert '"Ring"' not in literal_all and '"Mix"' in literal_all


@pytest.mark.parametrize(
    ("platform", "present"),
    [("linux", True), ("darwin", False), ("win32", False)],
)
def test_the_guard_decides_by_sys_platform(
    tmp_path, monkeypatch, platform, present
):
    """Run the rendered guard. The extension modules do not exist here, so
    each import is stubbed -- what is under test is which names the
    package ends up binding and exporting on each platform."""
    _project(tmp_path, ["linux"])
    text = _init(tmp_path)
    monkeypatch.setattr(sys, "platform", platform)
    pkg = type(sys)("dsp.sig")
    pkg.__package__ = "dsp.sig"
    for leaf, name in (("sig", "Mix"), ("ringbuf", "Ring")):
        stub = type(sys)(f"dsp.sig.{leaf}")
        setattr(stub, name, object())
        monkeypatch.setitem(sys.modules, f"dsp.sig.{leaf}", stub)
    monkeypatch.setitem(sys.modules, "dsp", type(sys)("dsp"))
    monkeypatch.setitem(sys.modules, "dsp.sig", pkg)
    exec(compile(text, "__init__.py", "exec"), pkg.__dict__)
    assert ("Ring" in pkg.__all__) is present
    assert hasattr(pkg, "Ring") is present
    assert "Mix" in pkg.__all__


def test_setting_the_key_replaces_the_unguarded_line(tmp_path):
    """The migration a real project takes: applied once without the key,
    then with it. The old line goes; it is not left beside the block."""
    _project(tmp_path, None)
    cfg = C.load(tmp_path)
    cfg["module"]["ringbuf"]["platforms"] = ["linux"]
    C.save(tmp_path, cfg)
    _silent(apply_run, tmp_path)
    text = _init(tmp_path)
    assert text.count("from .ringbuf import") == 1
    assert "    from .ringbuf import Ring" in text


def test_changing_the_platforms_rewrites_the_block_in_place(tmp_path):
    _project(tmp_path, ["linux"])
    cfg = C.load(tmp_path)
    cfg["module"]["ringbuf"]["platforms"] = ["linux", "macos"]
    C.save(tmp_path, cfg)
    _silent(apply_run, tmp_path)
    text = _init(tmp_path)
    assert text.count("# [module.ringbuf] platforms") == 1
    assert '("linux", "darwin",)' in text


# ── convergence: what `jm status --check` relies on ─────────────────────────


def test_apply_is_idempotent(tmp_path):
    _project(tmp_path, ["linux", "macos"])
    before = _tree(tmp_path)
    _silent(apply_run, tmp_path)
    assert _tree(tmp_path) == before


def test_manifest_only_rebuild_is_identical(tmp_path):
    """Replay from the manifest alone reproduces both guarded faces."""
    _project(tmp_path, ["linux"])
    want = {
        rel: (tmp_path / rel).read_bytes()
        for rel in (
            "native/src/ringbuf/CMakeLists.txt",
            "src/dsp/sig/__init__.py",
        )
    }
    other = tmp_path.parent / "replay"
    _project(other, ["linux"])
    for rel, data in want.items():
        assert (other / rel).read_bytes() == data, rel


def test_key_survives_a_manifest_round_trip(tmp_path):
    _project(tmp_path, ["macos"])
    C.save(tmp_path, C.load(tmp_path))
    assert C.load(tmp_path)["module"]["ringbuf"]["platforms"] == ["macos"]


# ── the key's contract ───────────────────────────────────────────────────────


def test_an_unknown_platform_is_refused():
    cfg = {"module": {"ringbuf": {"platforms": ["posix"]}}}
    with pytest.raises(ValueError, match="'posix' is not one of"):
        C.module_platforms(cfg, "ringbuf")


def test_an_empty_list_is_refused_not_read_as_everywhere():
    with pytest.raises(ValueError, match="non-empty list"):
        C.module_platforms({"module": {"r": {"platforms": []}}}, "r")


def test_only_the_handle_kind_accepts_the_key():
    """Honoured by the handle renderer alone, so any other kind must report
    it instead of silently building everywhere (gh-1114)."""
    assert "platforms" in _keys.HANDLE_MODULE_KEYS
    assert "platforms" not in _keys.CAPSULE_MODULE_KEYS
    assert "platforms" not in _keys.COMPOSER_MODULE_KEYS
