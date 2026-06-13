"""Integration tests for `just-makeit app`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._app import run as app_run
from just_makeit._object import run as object_run
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


@pytest.fixture()
def no_step_project(tmp_path):
    # A non-generatable shape: no step() means jm app falls back to a stub.
    root = tmp_path / "proj"
    new_run("proj", root, ["sink"], [("count", "uint32_t", "0")], no_step=True)
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

    def test_cli_is_generated_not_stub(self, project):
        # engine is a scalar step() object → the CLI body is generated, not a
        # <<IMPLEMENT>> stub.
        app_run(project, target="console", name="dsp_tool", object_="engine")
        text = (project / "src" / "proj" / "cli.py").read_text()
        assert "<<IMPLEMENT" not in text
        assert "obj.step(x)" in text

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


class TestAppGenerationVsFallback:
    """Scalar step() objects get a generated argv parser + I/O loop; --argc-argv
    is superseded. Non-generatable shapes (e.g. no_step) fall back to a stub,
    where --argc-argv still toggles the argv skeleton vs (void) suppression."""

    def test_scalar_object_supersedes_argc_argv(self, project):
        # engine is generatable → a full parser is emitted regardless of
        # --argc-argv; no IMPLEMENT stub, no (void)argc suppression.
        app_run(
            project,
            target="c",
            name="dsp_tool",
            object_="engine",
            argc_argv=True,
        )
        text = (project / "native" / "src" / "app" / "dsp_tool.c").read_text()
        assert "<<IMPLEMENT" not in text
        assert "(void)argc" not in text
        assert "engine_step(state, x)" in text
        assert '"--gain"' in text

    def test_no_step_default_suppresses_argc_argv(self, no_step_project):
        app_run(no_step_project, target="c", name="t", object_="sink")
        text = (no_step_project / "native" / "src" / "app" / "t.c").read_text()
        assert "(void)argc" in text
        assert "if (argc > 1)" not in text

    def test_no_step_argc_argv_emits_skeleton(self, no_step_project):
        app_run(
            no_step_project,
            target="c",
            name="t",
            object_="sink",
            argc_argv=True,
        )
        text = (no_step_project / "native" / "src" / "app" / "t.c").read_text()
        assert "if (argc > 1)" in text
        assert "IMPLEMENT" in text
        assert "(void)argc" not in text


class TestAppTargetCollision:
    """gh-184 part 3: an app whose --name matches an existing CMake target
    (a module ext target, or a component's ext target) must use a distinct
    exe target id (<name>_app) with OUTPUT_NAME <name>, so CMake doesn't error
    with "another target with the same name already exists" while the built
    binary stays <name>."""

    def _module_project(self, tmp_path):
        root = tmp_path / "proj"
        new_run("proj", root, modules=["wfmgen"])
        object_run(
            root,
            "synth",
            "wfmgen",
            arg_type="void",
            return_type="float _Complex",
        )
        return root

    def test_collision_with_module_target(self, tmp_path):
        root = self._module_project(tmp_path)
        app_run(root, target="c", name="wfmgen", object_="synth")
        cmake = (root / "CMakeLists.txt").read_text()
        # Distinct exe target, binary preserved via OUTPUT_NAME.
        assert "add_executable(wfmgen_app native/src/app/wfmgen.c)" in cmake
        assert (
            "set_target_properties(wfmgen_app PROPERTIES OUTPUT_NAME wfmgen)"
            in cmake
        )
        assert "target_link_libraries(wfmgen_app" in cmake
        assert "install(TARGETS wfmgen_app" in cmake
        # The app never claims the bare name (which the module ext target,
        # declared in native/src/wfmgen/CMakeLists.txt, already owns).
        assert "add_executable(wfmgen " not in cmake

    def test_collision_with_component_target(self, project):
        # `project` has component "engine"; an app named "engine" collides
        # with engine's own Python ext target.
        app_run(project, target="c", name="engine", object_="engine")
        cmake = (project / "CMakeLists.txt").read_text()
        assert "add_executable(engine_app native/src/app/engine.c)" in cmake
        assert (
            "set_target_properties(engine_app PROPERTIES OUTPUT_NAME engine)"
            in cmake
        )

    def test_no_collision_omits_output_name(self, project):
        app_run(project, target="c", name="dsp_tool", object_="engine")
        cmake = (project / "CMakeLists.txt").read_text()
        assert "add_executable(dsp_tool native/src/app/dsp_tool.c)" in cmake
        # No app-specific OUTPUT_NAME (the top-level lib has its own, unrelated).
        assert "OUTPUT_NAME dsp_tool" not in cmake

    def test_collision_idempotent(self, tmp_path):
        root = self._module_project(tmp_path)
        app_run(root, target="c", name="wfmgen", object_="synth")
        app_run(root, target="c", name="wfmgen", object_="synth")
        cmake = (root / "CMakeLists.txt").read_text()
        # Re-run reuses the same suffixed id — no <name>_app_app, single block.
        assert cmake.count("add_executable(wfmgen_app") == 1
        assert "wfmgen_app_app" not in cmake
