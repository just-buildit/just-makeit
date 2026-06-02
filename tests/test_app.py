"""Integration tests for `just-makeit app`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._app import run as app_run
from just_makeit._config import load, app_config


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    new_run(
        "proj",
        root,
        ["engine"],
        [("gain", "float", "1.0f"), ("rate", "double", "44100.0")],
    )
    return root


@pytest.fixture()
def no_state_project(tmp_path):
    root = tmp_path / "proj"
    new_run("proj", root, ["gen"], [])
    return root


class TestAppTargetC:
    def test_main_c_created(self, project):
        app_run(project, target="c", name="dsp_tool", object_="engine")
        assert (project / "native" / "src" / "app" / "dsp_tool.c").exists()

    def test_main_c_includes_component(self, project):
        app_run(project, target="c", name="dsp_tool", object_="engine")
        text = (project / "native" / "src" / "app" / "dsp_tool.c").read_text()
        assert "engine/engine_core.h" in text

    def test_main_c_calls_create_destroy(self, project):
        app_run(project, target="c", name="dsp_tool", object_="engine")
        text = (project / "native" / "src" / "app" / "dsp_tool.c").read_text()
        assert "engine_create" in text
        assert "engine_destroy" in text

    def test_cmake_executable_added(self, project):
        app_run(project, target="c", name="dsp_tool", object_="engine")
        cmake = (project / "CMakeLists.txt").read_text()
        assert "add_executable(dsp_tool" in cmake
        assert "engine_core" in cmake

    def test_cmake_idempotent(self, project):
        app_run(project, target="c", name="dsp_tool", object_="engine")
        app_run(project, target="c", name="dsp_tool", object_="engine")
        cmake = (project / "CMakeLists.txt").read_text()
        assert cmake.count("add_executable(dsp_tool") == 1

    def test_toml_app_section_persisted(self, project):
        app_run(project, target="c", name="dsp_tool", object_="engine")
        cfg = load(project)
        assert app_config(cfg)["target"] == "c"
        assert app_config(cfg)["name"] == "dsp_tool"
        assert app_config(cfg)["object"] == "engine"

    def test_no_state_vars_no_ctor_args(self, no_state_project):
        app_run(no_state_project, target="c", name="tool", object_="gen")
        text = (
            no_state_project / "native" / "src" / "app" / "tool.c"
        ).read_text()
        assert "gen_create" in text


class TestAppTargetConsole:
    def test_cli_py_created(self, project):
        app_run(project, target="console", name="dsp_tool", object_="engine")
        assert (project / "src" / "proj" / "cli.py").exists()

    def test_cli_imports_component(self, project):
        app_run(project, target="console", name="dsp_tool", object_="engine")
        text = (project / "src" / "proj" / "cli.py").read_text()
        assert "from . import Engine" in text

    def test_cli_has_argparse_flags_for_state(self, project):
        app_run(project, target="console", name="dsp_tool", object_="engine")
        text = (project / "src" / "proj" / "cli.py").read_text()
        assert "--gain" in text
        assert "--rate" in text

    def test_cli_create_call_uses_args(self, project):
        app_run(project, target="console", name="dsp_tool", object_="engine")
        text = (project / "src" / "proj" / "cli.py").read_text()
        assert "gain=args.gain" in text
        assert "rate=args.rate" in text

    def test_cli_has_implement_placeholder(self, project):
        app_run(project, target="console", name="dsp_tool", object_="engine")
        text = (project / "src" / "proj" / "cli.py").read_text()
        assert "<<IMPLEMENT" in text

    def test_toml_app_section_persisted(self, project):
        app_run(project, target="console", name="dsp_tool", object_="engine")
        cfg = load(project)
        assert app_config(cfg)["target"] == "console"


class TestAppTargetPep723:
    def test_script_created_in_root(self, project):
        app_run(project, target="pep723", name="dsp_tool", object_="engine")
        assert (project / "dsp_tool.py").exists()

    def test_script_has_pep723_block(self, project):
        app_run(project, target="pep723", name="dsp_tool", object_="engine")
        text = (project / "dsp_tool.py").read_text()
        assert "# /// script" in text
        assert "dependencies" in text
        assert "proj" in text

    def test_script_imports_component(self, project):
        app_run(project, target="pep723", name="dsp_tool", object_="engine")
        text = (project / "dsp_tool.py").read_text()
        assert "from proj import Engine" in text

    def test_script_has_argparse_flags(self, project):
        app_run(project, target="pep723", name="dsp_tool", object_="engine")
        text = (project / "dsp_tool.py").read_text()
        assert "--gain" in text
        assert "--rate" in text

    def test_toml_app_section_persisted(self, project):
        app_run(project, target="pep723", name="dsp_tool", object_="engine")
        cfg = load(project)
        assert app_config(cfg)["target"] == "pep723"


class TestAppDefaults:
    def test_default_name_is_project_name(self, project):
        app_run(project, target="c")
        cfg = load(project)
        assert app_config(cfg)["name"] == "proj"

    def test_default_object_is_first_component(self, project):
        app_run(project, target="c")
        cfg = load(project)
        assert app_config(cfg)["object"] == "engine"

    def test_unknown_target_exits(self, project):
        with pytest.raises(SystemExit):
            app_run(project, target="unknown")

    def test_unknown_object_exits(self, project):
        with pytest.raises(SystemExit):
            app_run(project, target="c", object_="ghost")

    def test_no_components_exits(self, tmp_path):
        root = tmp_path / "empty"
        new_run("empty", root)
        with pytest.raises(SystemExit):
            app_run(root, target="c")

    def test_components_excludes_app(self, project):
        """app section must not appear in components() list."""
        from just_makeit._config import components

        app_run(project, target="c", name="tool", object_="engine")
        cfg = load(project)
        assert "app" not in components(cfg)

    def test_rerun_different_target_updates_toml(self, project):
        app_run(project, target="c", name="tool", object_="engine")
        app_run(project, target="pep723", name="tool", object_="engine")
        cfg = load(project)
        assert app_config(cfg)["target"] == "pep723"


class TestAppArgcArgv:
    """--argc-argv replaces the default (void)argc/(void)argv suppression with
    an if (argc > 1) block containing an IMPLEMENT placeholder."""

    def test_argc_argv_generates_if_block(self, project):
        app_run(
            project,
            target="c",
            name="dsp_tool",
            object_="engine",
            argc_argv=True,
        )
        text = (project / "native" / "src" / "app" / "dsp_tool.c").read_text()
        assert "if (argc > 1)" in text

    def test_argc_argv_has_implement_placeholder(self, project):
        app_run(
            project,
            target="c",
            name="dsp_tool",
            object_="engine",
            argc_argv=True,
        )
        text = (project / "native" / "src" / "app" / "dsp_tool.c").read_text()
        assert "IMPLEMENT" in text

    def test_argc_argv_no_void_suppress(self, project):
        app_run(
            project,
            target="c",
            name="dsp_tool",
            object_="engine",
            argc_argv=True,
        )
        text = (project / "native" / "src" / "app" / "dsp_tool.c").read_text()
        assert "(void)argc" not in text

    def test_default_suppresses_argc_argv(self, project):
        app_run(project, target="c", name="dsp_tool", object_="engine")
        text = (project / "native" / "src" / "app" / "dsp_tool.c").read_text()
        assert "(void)argc" in text
        assert "if (argc > 1)" not in text
