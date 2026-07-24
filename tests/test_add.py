"""Integration tests for `just-makeit add`."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._add import run as add_run
from just_makeit._config import load, state_vars


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "comp"
    new_run("comp", dest, ["comp"], [("gain", "double", "1.0")])
    return dest


class TestAddStateVar:
    def test_add_single_var_header(self, project):
        add_run(project, None, [("order", "int", "4")], force=True)
        core = (project / "native" / "inc" / "comp" / "comp_core.h").read_text(
            encoding="utf-8"
        )
        assert "double gain;" in core
        assert "int order;" in core

    def test_add_single_var_core_c(self, project):
        add_run(project, None, [("order", "int", "4")], force=True)
        c = (project / "native" / "src" / "comp" / "comp_core.c").read_text(
            encoding="utf-8"
        )
        assert "comp_get_gain" in c
        assert "comp_get_order" in c

    def test_add_single_var_ext_c(self, project):
        add_run(project, None, [("order", "int", "4")], force=True)
        ext = (project / "native" / "src" / "comp" / "comp_ext.c").read_text(
            encoding="utf-8"
        )
        assert '"gain"' in ext
        assert '"order"' in ext

    def test_add_single_var_pyi(self, project):
        add_run(project, None, [("order", "int", "4")], force=True)
        pyi = (project / "src" / "comp" / "comp.pyi").read_text(
            encoding="utf-8"
        )
        assert "gain: float = 1.0" in pyi
        assert "order: int = 4" in pyi

    def test_add_single_var_pytest(self, project):
        add_run(project, None, [("order", "int", "4")], force=True)
        test = (project / "src" / "comp" / "tests" / "test_comp.py").read_text(
            encoding="utf-8"
        )
        assert "get_order" in test

    def test_add_single_var_ctest(self, project):
        add_run(project, None, [("order", "int", "4")], force=True)
        ct = (project / "native" / "tests" / "test_comp_core.c").read_text(
            encoding="utf-8"
        )
        assert "comp_get_order" in ct

    def test_add_multiple_vars_at_once(self, project):
        add_run(
            project,
            None,
            [("bandwidth", "double", "200.0"), ("poles", "int", "2")],
            force=True,
        )
        core = (project / "native" / "inc" / "comp" / "comp_core.h").read_text(
            encoding="utf-8"
        )
        assert "double bandwidth;" in core
        assert "int poles;" in core

    def test_no_unreplaced_placeholders(self, project):
        add_run(project, None, [("order", "int", "4")], force=True)
        for path in project.rglob("*"):
            if path.is_file() and path.suffix in (
                ".py",
                ".pyi",
                ".c",
                ".h",
                ".toml",
                ".txt",
            ):
                text = path.read_text(encoding="utf-8")
                assert "<<" not in text, f"Unreplaced placeholder in {path}"


class TestAddPreservesInitParams:
    """gh-87 class of bug, in jm add: a component with both --state and
    --init-param has an init-param-driven ctor. jm add regenerated the
    state context without init_params, silently rebuilding a state-driven
    ctor and dropping the init params from the generated C while they
    stayed in the manifest -- a guaranteed build break on the next mutation.
    """

    @pytest.fixture()
    def init_param_project(self, tmp_path):
        from just_makeit._object import run as object_run

        dest = tmp_path / "p"
        new_run("p", dest)
        object_run(
            dest,
            "obj",
            None,
            [("x", "float", "0")],
            init_params=[("n", "int", "4")],
        )
        return dest

    def test_ctor_keeps_init_param_after_add(self, init_param_project):
        add_run(init_param_project, "obj", [("y", "float", "1")], force=True)
        core = (
            init_param_project / "native" / "inc" / "obj" / "obj_core.h"
        ).read_text(encoding="utf-8")
        # Constructor stays init-param-driven: obj_create(int n), NOT
        # obj_create(float x, float y).
        assert "obj_create(int n)" in core
        assert "obj_create(float" not in core
        # The new internal state field is still added.
        assert "float y;" in core


class TestAddUpdatesConfig:
    def test_config_has_new_var(self, project):
        add_run(project, None, [("order", "int", "4")], force=True)
        cfg = load(project)
        names = [n for n, _, __ in state_vars(cfg, "comp")]
        assert "gain" in names
        assert "order" in names

    def test_config_preserves_original_var(self, project):
        add_run(project, None, [("order", "int", "4")], force=True)
        cfg = load(project)
        vars_ = {n: (t, d) for n, t, d in state_vars(cfg, "comp")}
        assert vars_["gain"] == ("double", "1.0")

    def test_config_state_count(self, project):
        add_run(
            project,
            None,
            [("a", "double", "0.0"), ("b", "int", "0")],
            force=True,
        )
        cfg = load(project)
        assert len(state_vars(cfg, "comp")) == 3  # gain + a + b


class TestAddValidation:
    def test_no_config_exits_1(self, tmp_path):
        with pytest.raises(SystemExit):
            add_run(tmp_path, None, [("x", "double", "0.0")])

    def test_duplicate_name_exits_1(self, project):
        with pytest.raises(SystemExit):
            add_run(project, None, [("gain", "double", "2.0")])

    def test_explicit_component_works(self, project):
        add_run(project, "comp", [("order", "int", "4")], force=True)
        cfg = load(project)
        names = [n for n, _, __ in state_vars(cfg, "comp")]
        assert "order" in names

    def test_wrong_component_exits(self, project):
        with pytest.raises(SystemExit):
            add_run(project, "nonexistent", [("x", "double", "0.0")])


class TestAddManifestGuarantee:
    """The old _backup/restore-on-write-failure mechanism is gone: state is
    now structural and rebuilt from the manifest via the regenerate path, so
    there are no in-place splices into sacred source to roll back. What still
    holds is the manifest-level guarantee: an invalid add (duplicate name) is
    rejected before the manifest is touched, leaving it unchanged.

    (test_backup_restores_on_write_failure and the old
    test_config_not_written_on_failure were deleted: they only probed the
    removed _backup internals via a forced .pyi write failure, which no
    longer exists.)
    """

    def test_hand_edited_create_gets_fresh_body_not_stale_splice(
        self, project
    ):
        """gh-267 regression: `jm regenerate`'s default hand-written-body
        preservation must NOT fire here. `comp_create`'s pre-add signature
        (one param) is stale the moment a field is added (two params) — if
        the old body were spliced back under the new signature it would
        either fail to compile or leave the new field uninitialized."""
        core_c = project / "native" / "src" / "comp" / "comp_core.c"
        text = core_c.read_text(encoding="utf-8")
        core_c.write_text(
            text.replace(
                "obj->gain = gain;",
                "obj->gain = gain; /* HAND_EDIT_STALE_SIG */",
            ),
            encoding="utf-8",
        )
        add_run(project, None, [("order", "int", "4")], force=True)
        text = core_c.read_text(encoding="utf-8")
        assert "HAND_EDIT_STALE_SIG" not in text
        assert "obj->order = order;" in text

    def test_duplicate_add_leaves_manifest_unchanged(self, project):
        cfg_before = load(project)
        before = state_vars(cfg_before, "comp")

        with pytest.raises(SystemExit):
            add_run(project, None, [("gain", "double", "2.0")], force=True)

        cfg_after = load(project)
        assert state_vars(cfg_after, "comp") == before
