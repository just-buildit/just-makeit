"""Integration tests for `just-makeit apply`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._module import run as module_run
from just_makeit._method import run as method_run
from just_makeit._property import run as property_run
from just_makeit._apply import run as apply_run

_IGNORE = {"compile_commands.json"}


def _scaffold(root: Path) -> None:
    """Build a project with a standalone object and a module object."""
    new_run("proj", root, ["widget"], [("gain", "float", "0.0f")])
    object_run(root, "gadget", None, state_vars=[("g", "float", "1.0f")])
    module_run(root, "dsp")
    object_run(root, "nco", "dsp", state_vars=[("freq", "float", "0.0f")])
    method_run(
        root,
        "nco",
        "tune",
        "dsp",
        "void",
        "float _Complex",
        False,
        [],
        params=[("freq", "double")],
    )
    property_run(root, "nco", "locked", "dsp", "uint8_t", True, field=True)


def _tree(root: Path) -> dict[str, bytes]:
    """Map every file (relative path -> bytes), skipping build artifacts."""
    out: dict[str, bytes] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if "build" in rel.parts or rel.name in _IGNORE:
            continue
        out[str(rel)] = p.read_bytes()
    return out


def _strip_to_manifest(root: Path) -> None:
    """Delete everything except just-makeit.toml."""
    import shutil

    for p in root.iterdir():
        if p.name == "just-makeit.toml":
            continue
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()


class TestApplyMaterialize:
    def test_recreates_every_file(self, tmp_path):
        """apply on a manifest-only directory rebuilds the whole project."""
        ref = tmp_path / "ref"
        _scaffold(ref)
        expected = _tree(ref)

        proj = tmp_path / "proj"
        _scaffold(proj)
        _strip_to_manifest(proj)
        assert list(proj.iterdir()) == [proj / "just-makeit.toml"]

        apply_run(proj)
        assert _tree(proj) == expected

    def test_manifest_untouched(self, tmp_path):
        proj = tmp_path / "proj"
        _scaffold(proj)
        before = (proj / "just-makeit.toml").read_bytes()
        _strip_to_manifest(proj)
        apply_run(proj)
        assert (proj / "just-makeit.toml").read_bytes() == before

    def test_idempotent(self, tmp_path):
        """A second apply on a complete project changes nothing."""
        proj = tmp_path / "proj"
        _scaffold(proj)
        before = _tree(proj)
        apply_run(proj)
        assert _tree(proj) == before


class TestApplyAddOnly:
    def test_existing_files_not_overwritten(self, tmp_path):
        """apply never clobbers a file that already exists."""
        proj = tmp_path / "proj"
        _scaffold(proj)
        core_c = proj / "native" / "src" / "widget" / "widget_core.c"
        marker = core_c.read_text(encoding="utf-8") + "\n/* HAND EDIT */\n"
        core_c.write_text(marker, encoding="utf-8")

        apply_run(proj)
        assert core_c.read_text(encoding="utf-8") == marker


class TestApplyErrors:
    def test_no_manifest_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            apply_run(tmp_path)

    def test_empty_manifest_exits(self, tmp_path):
        proj = tmp_path / "proj"
        new_run("proj", proj)
        with pytest.raises(SystemExit):
            apply_run(proj)
