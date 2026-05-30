"""Integration tests for `just-makeit migrate-to-fragments`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._function import run as function_run
from just_makeit._migrate import run as migrate_run


def _sig(root: Path) -> dict:
    """A layout-independent fingerprint of the loaded config."""
    cfg = C.load(root)
    return {
        "components": sorted(C.components(cfg)),
        "modules": sorted(C.modules(cfg)),
        "io_objects": sorted(C.module_objects(cfg, "io")),
        "io_functions": sorted(
            f["name"] for f in C.module_functions(cfg, "io")
        ),
    }


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "dsp"
    new_run("dsp", root, modules=["io"])
    object_run(
        root, "eng", module=None, state_vars=[("gain", "double", "1.0")]
    )
    object_run(root, "fir", module="io", state_vars=[("n", "int", "4")])
    function_run(
        root,
        "scale",
        "io",
        params=[("x", "float", False)],
        return_type="float",
    )
    return root


class TestMigrate:
    def test_moves_objects_and_modules(self, project):
        migrate_run(project)
        assert (project / "objects" / "eng.toml").exists()
        assert (project / "objects" / "fir.toml").exists()
        assert (project / "modules" / "io.toml").exists()
        manifest = C.load_manifest(project)
        # Manifest keeps only [project] + include.
        assert set(manifest) <= {"project", "include"}
        assert manifest["include"] == ["objects/*.toml", "modules/*.toml"]

    def test_load_round_trips(self, project):
        before = _sig(project)
        migrate_run(project)
        assert _sig(project) == before

    def test_idempotent(self, project, capsys):
        migrate_run(project)
        first = (project / "modules" / "io.toml").read_text(encoding="utf-8")
        migrate_run(project)
        assert "nothing to do" in capsys.readouterr().out
        assert (project / "modules" / "io.toml").read_text(
            encoding="utf-8"
        ) == first

    def test_module_config_lives_in_fragment(self, project):
        migrate_run(project)
        io = (project / "modules" / "io.toml").read_text(encoding="utf-8")
        assert "[module.io]" in io
        assert "fir" in io  # objects = ["fir"]
        assert "scale" in io  # the module function

    def test_mutation_preserves_layout(self, project):
        migrate_run(project)
        # Add a standalone object — must NOT re-inline [module.io] into
        # the manifest (the regression the save-path fix closes).
        object_run(
            project, "extra", module=None, state_vars=[("k", "int", "1")]
        )
        manifest = C.load_manifest(project)
        assert "module" not in manifest
        assert (project / "objects" / "extra.toml").exists()
        # Everything still loads.
        assert "extra" in C.components(C.load(project))
        assert C.module_objects(C.load(project), "io") == ["fir"]


class TestModuleFragmentLoading:
    def test_conflicting_module_declaration_errors(self, tmp_path):
        # Two fragments declaring the same module's non-functions config
        # is a hard error (a module belongs in exactly one place).
        root = tmp_path / "dsp"
        new_run("dsp", root)
        manifest = root / C.FILENAME
        manifest.write_text(
            'include = ["modules/*.toml"]\n\n'
            + manifest.read_text(encoding="utf-8").replace(
                'include = ["modules/*.toml"]\n\n', ""
            ),
            encoding="utf-8",
        )
        mods = root / "modules"
        mods.mkdir()
        (mods / "a.toml").write_text(
            '[module.io]\nobjects = ["x"]\n', encoding="utf-8"
        )
        (mods / "b.toml").write_text(
            '[module.io]\nobjects = ["y"]\n', encoding="utf-8"
        )
        with pytest.raises(ValueError, match="conflicts"):
            C.load(root)
