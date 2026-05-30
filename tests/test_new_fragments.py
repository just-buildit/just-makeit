"""Integration tests for `jm new --fragments` (opt-in fragment layout)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _config as C
from just_makeit._new import run as new_run
from just_makeit._module import run as module_run
from just_makeit._object import run as object_run
from just_makeit._function import run as function_run


class TestNewFragments:
    def test_seeds_include_globs(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, fragments=True)
        manifest = C.load_manifest(root)
        assert manifest["include"] == ["objects/*.toml", "modules/*.toml"]
        # No object/module sections inline.
        assert set(manifest) <= {"project", "include"}

    def test_object_routes_to_fragment(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, fragments=True)
        object_run(
            root, "eng", module=None, state_vars=[("g", "double", "1.0")]
        )
        assert (root / "objects" / "eng.toml").exists()
        # Central manifest still carries no [eng] section.
        assert "eng" not in C.load_manifest(root)
        assert "eng" in C.components(C.load(root))

    def test_module_and_members_route_to_fragment(self, tmp_path):
        root = tmp_path / "dsp"
        new_run("dsp", root, fragments=True)
        module_run(root, "io")
        object_run(root, "fir", module="io", state_vars=[("n", "int", "4")])
        function_run(
            root,
            "scale",
            "io",
            params=[("x", "float", False)],
            return_type="float",
        )
        io = root / "modules" / "io.toml"
        assert io.exists()
        text = io.read_text(encoding="utf-8")
        assert "[module.io]" in text and "fir" in text and "scale" in text
        # Manifest never gains a [module.X] section.
        assert "module" not in C.load_manifest(root)
        cfg = C.load(root)
        assert C.module_objects(cfg, "io") == ["fir"]
        assert [f["name"] for f in C.module_functions(cfg, "io")] == ["scale"]

    def test_default_is_still_central(self, tmp_path):
        # Without --fragments the layout is unchanged (single manifest).
        root = tmp_path / "plain"
        new_run("plain", root)
        object_run(
            root, "eng", module=None, state_vars=[("g", "double", "1.0")]
        )
        manifest = C.load_manifest(root)
        assert "include" not in manifest
        assert "eng" in manifest  # inline section, not a fragment
        assert not (root / "objects").exists()


class TestCliFragmentsFlag:
    def test_flag_forwarded(self):
        from unittest.mock import patch
        from just_makeit import _cli_new

        with patch("just_makeit._new.run") as mock_run:
            _cli_new.run(["proj", "--fragments"])
            assert mock_run.call_args.kwargs["fragments"] is True

    def test_default_false(self):
        from unittest.mock import patch
        from just_makeit import _cli_new

        with patch("just_makeit._new.run") as mock_run:
            _cli_new.run(["proj"])
            assert mock_run.call_args.kwargs["fragments"] is False
