"""Integration tests for `just-makeit regenerate`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._regenerate import run as regen_run


def _plant(core_c: Path, marker: str = "USER_EDIT") -> None:
    """Drop a sentinel into the steps() body of a _core.c."""
    import re

    text = core_c.read_text(encoding="utf-8")
    core_c.write_text(
        re.sub(
            r"(_steps\([^)]*\)\s*\{)",
            r"\1\n    /* " + marker + " */",
            text,
            count=1,
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "demo"
    new_run("demo", root, ["eng"], [("gain", "double", "1.0")])
    return root


class TestRegenerate:
    def test_discards_core_c_edits_and_rebuilds(self, project):
        core_c = project / "native" / "src" / "eng" / "eng_core.c"
        _plant(core_c, "USER_EDIT_TO_DISCARD")
        assert "USER_EDIT_TO_DISCARD" in core_c.read_text(encoding="utf-8")
        regen_run(project, "eng", force=True)
        # Fresh scaffold — the planted edit is gone, file exists again.
        assert core_c.exists()
        assert "USER_EDIT_TO_DISCARD" not in core_c.read_text(encoding="utf-8")

    def test_glue_files_recreated(self, project):
        ext_c = project / "native" / "src" / "eng" / "eng_ext.c"
        pyi = project / "src" / "demo" / "eng.pyi"
        regen_run(project, "eng", force=True)
        assert ext_c.exists()
        assert pyi.exists()

    def test_manifest_untouched(self, project):
        before = (project / "just-makeit.toml").read_text(encoding="utf-8")
        regen_run(project, "eng", force=True)
        after = (project / "just-makeit.toml").read_text(encoding="utf-8")
        assert before == after

    def test_abort_leaves_files_intact(self, project, monkeypatch):
        core_c = project / "native" / "src" / "eng" / "eng_core.c"
        _plant(core_c, "KEEP_ME")
        # Decline the confirmation prompt.
        monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
        regen_run(project, "eng", force=False)
        # Nothing deleted; the user's edit survives.
        assert "KEEP_ME" in core_c.read_text(encoding="utf-8")

    def test_unknown_component_exits(self, project):
        with pytest.raises(SystemExit):
            regen_run(project, "nope", force=True)

    def test_module_object(self, tmp_path):
        root = tmp_path / "proj"
        new_run("proj", root, modules=["dsp"])
        object_run(root, "nco", "dsp", state_vars=[("f", "float", "0.0f")])
        core_c = root / "native" / "src" / "nco" / "nco_core.c"
        _plant(core_c, "MODULE_EDIT")
        regen_run(root, "nco", force=True)
        assert core_c.exists()
        assert "MODULE_EDIT" not in core_c.read_text(encoding="utf-8")

    def test_missing_files_just_applies(self, project):
        # Delete the component's source dir, then regenerate recreates it.
        import shutil

        shutil.rmtree(project / "native" / "src" / "eng")
        regen_run(project, "eng", force=True)
        assert (project / "native" / "src" / "eng" / "eng_core.c").exists()
