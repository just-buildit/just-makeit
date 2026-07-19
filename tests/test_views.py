"""Tests for `[[<obj>.views]]` — a second class over one C core (gh-504)."""

from __future__ import annotations

import pytest

from just_makeit import _config as C

# `tomllib` is stdlib only on 3.11+; reuse the loader _config already resolved
# (the `tomli` backport on 3.9/3.10) rather than importing `tomllib` directly,
# which would collection-error the whole module on those legs.
tomllib = C.tomllib
from just_makeit import _context as Ctx
from just_makeit import _stubs as S
from just_makeit import _view
from just_makeit._method import run as method_run
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._property import run as property_run
from just_makeit._status import run as status_run


# ── Config layer ────────────────────────────────────────────────────────────


def _cfg_with_view():
    return {
        "project": {"name": "demo", "version": "0.1.0"},
        "acq": {
            "arg_type": "float _Complex",
            "return_type": "size_t",
            "state": [{"name": "rate", "type": "double", "default": "1.0"}],
            "properties": [
                {"name": "symbol_rate", "type": "double"},
                {"name": "dwell", "type": "size_t"},
            ],
            "views": [
                {
                    "class_name": "BurstAcquisition",
                    "create_fn": "acq_create_burst",
                    "init_params": [
                        {"name": "reps", "type": "int", "default": "1"}
                    ],
                    "exclude_properties": ["symbol_rate"],
                }
            ],
        },
    }


class TestConfigRoundTrip:
    def test_dump_reload_preserves_view(self):
        text = C._dump(_cfg_with_view())
        cfg = tomllib.loads(text)
        views = C.views(cfg, "acq")
        assert len(views) == 1
        v = views[0]
        assert v["class_name"] == "BurstAcquisition"
        assert v["create_fn"] == "acq_create_burst"
        assert v["exclude_properties"] == ["symbol_rate"]

    def test_view_init_params_own(self):
        cfg = _cfg_with_view()
        v = cfg["acq"]["views"][0]
        tups = C.view_init_params(cfg, "acq", v)
        assert tups[0][0] == "reps" and tups[0][2] == "1"

    def test_view_init_params_fallback_to_parent(self):
        cfg = _cfg_with_view()
        # A view with no own init_params inherits the parent's constructor shape.
        v = {"class_name": "X", "create_fn": "acq_create_x"}
        assert C.view_init_params(cfg, "acq", v) == C.init_params(cfg, "acq")

    def test_view_exclude_properties(self):
        v = _cfg_with_view()["acq"]["views"][0]
        assert C.view_exclude_properties(v) == {"symbol_rate"}

    def test_no_views_emits_nothing(self):
        cfg = _cfg_with_view()
        cfg["acq"].pop("views")
        assert "views" not in C._dump(cfg)


# ── create_fn threading through make_state_ctx ──────────────────────────────


class TestCreateFnThreading:
    _sv = [("rate", "double", "1.0")]

    def test_default_unchanged(self):
        ctx = Ctx.make_state_ctx("acq", "Acq", self._sv)
        assert "acq_create(" in ctx["create_line"]

    def test_override_reaches_create_line(self):
        ctx = Ctx.make_state_ctx(
            "acq", "BurstAcquisition", self._sv, create_fn="acq_create_burst"
        )
        assert "acq_create_burst(" in ctx["create_line"]
        assert "acq_create(" not in ctx["create_line"]

    def test_override_no_state_path(self):
        ctx = Ctx.make_state_ctx(
            "acq", "Burst", [], no_state=True, create_fn="acq_create_burst"
        )
        assert "acq_create_burst(" in ctx["create_line"]


# ── .pyi second class block ─────────────────────────────────────────────────


class TestStubViewBlock:
    def _pyi(self):
        return S.make_module_pyi(_module_cfg_with_view(), "dsp", root=None)

    def test_both_class_blocks(self):
        pyi = self._pyi()
        assert "class Acq:" in pyi
        assert "class BurstAcquisition:" in pyi

    def test_excluded_property_absent_from_view(self):
        pyi = self._pyi()
        view_block = pyi[pyi.index("class BurstAcquisition") :]
        assert "symbol_rate" not in view_block

    def test_excluded_property_present_on_parent(self):
        pyi = self._pyi()
        parent = pyi[
            pyi.index("class Acq") : pyi.index("class BurstAcquisition")
        ]
        assert "symbol_rate" in parent

    def test_view_init_uses_own_params(self):
        pyi = self._pyi()
        view_block = pyi[pyi.index("class BurstAcquisition") :]
        # __init__ takes the view's `reps`, not the parent's `rate`.
        init = next(ln for ln in view_block.splitlines() if "__init__" in ln)
        assert "reps: int" in init
        assert "rate" not in init

    def test_no_synthetic_key_leaks(self):
        assert "__view_" not in self._pyi()


def _module_cfg_with_view():
    return {
        "project": {"name": "demo", "version": "0.1.0"},
        "module": {"dsp": {"objects": ["acq"]}},
        "acq": {
            "arg_type": "float _Complex",
            "return_type": "float _Complex",
            "state": [{"name": "rate", "type": "double", "default": "1.0"}],
            "properties": [{"name": "symbol_rate", "type": "double"}],
            "views": [
                {
                    "class_name": "BurstAcquisition",
                    "create_fn": "acq_create_burst",
                    "init_params": [{"name": "reps", "type": "int"}],
                    "exclude_properties": ["symbol_rate"],
                }
            ],
        },
    }


# ── End-to-end scaffold (no compiler needed) ────────────────────────────────


@pytest.fixture()
def view_project(tmp_path):
    """A module project with one object + one view over it."""
    dest = tmp_path / "demo"
    new_run("demo", dest, [], [], build_system="cmake")
    module_run(dest, "dsp")
    object_run(
        dest,
        "acq",
        module="dsp",
        state_vars=[("rate", "double", "1.0")],
        arg_type="float _Complex",
        return_type="float _Complex",
    )
    property_run(dest, "acq", "symbol_rate", "dsp", "double", False, False)
    _view.run(
        dest,
        "acq",
        "BurstAcquisition",
        "dsp",
        "acq_create_burst",
        init_params=[("reps", "int", "1")],
        exclude_properties=["symbol_rate"],
    )
    return dest


class TestScaffold:
    def test_view_fragment_file_created(self, view_project):
        frag = (
            view_project
            / "native"
            / "src"
            / "dsp"
            / "dsp_ext_burstacquisition.c"
        )
        assert frag.exists()
        # Distinct type over the shared acq_state_t, distinct C symbols.
        text = frag.read_text(encoding="utf-8")
        assert "BurstAcquisitionObject" in text
        assert "acq_state_t *handle;" in text
        assert "self->handle = acq_create_burst(" in text

    def test_parent_fragment_not_overwritten(self, view_project):
        acq = view_project / "native" / "src" / "dsp" / "dsp_ext_acq.c"
        assert acq.exists()
        assert "AcqObject" in acq.read_text(encoding="utf-8")

    def test_aggregator_registers_both_types(self, view_project):
        agg = (
            view_project / "native" / "src" / "dsp" / "dsp_ext.c"
        ).read_text(encoding="utf-8")
        assert '#include "dsp_ext_acq.c"' in agg
        assert '#include "dsp_ext_burstacquisition.c"' in agg
        assert 'PyModule_AddObject(m, "Acq"' in agg
        assert 'PyModule_AddObject(m, "BurstAcquisition"' in agg

    def test_create_fn_scaffolded_into_core(self, view_project):
        h = (view_project / "native" / "inc" / "acq" / "acq_core.h").read_text(
            encoding="utf-8"
        )
        c = (view_project / "native" / "src" / "acq" / "acq_core.c").read_text(
            encoding="utf-8"
        )
        assert "acq_state_t *acq_create_burst(int reps);" in h
        assert "acq_create_burst(int reps)" in c

    def test_pyi_has_both_classes(self, view_project):
        pyi = (view_project / "src" / "demo" / "dsp" / "dsp.pyi").read_text(
            encoding="utf-8"
        )
        assert "class Acq:" in pyi
        assert "class BurstAcquisition:" in pyi

    def test_init_reexports_view(self, view_project):
        init = (
            view_project / "src" / "demo" / "dsp" / "__init__.py"
        ).read_text(encoding="utf-8")
        assert "BurstAcquisition" in init

    def test_status_check_is_clean(self, view_project):
        # The drift gate: a freshly scaffolded view project must round-trip
        # through apply's throwaway reconstruction with zero drift.
        assert status_run(view_project, check=True) == 0


# ── Generator validation ────────────────────────────────────────────────────


class TestValidation:
    def test_rejects_create_fn_equal_to_parent(self, tmp_path):
        dest = tmp_path / "demo"
        new_run("demo", dest, [], [], build_system="cmake")
        module_run(dest, "dsp")
        object_run(
            dest, "acq", module="dsp", state_vars=[("r", "double", "1.0")]
        )
        with pytest.raises(SystemExit):
            _view.run(dest, "acq", "V", "dsp", "acq_create")

    def test_rejects_unknown_exclude_property(self, tmp_path):
        dest = tmp_path / "demo"
        new_run("demo", dest, [], [], build_system="cmake")
        module_run(dest, "dsp")
        object_run(
            dest, "acq", module="dsp", state_vars=[("r", "double", "1.0")]
        )
        with pytest.raises(SystemExit):
            _view.run(
                dest,
                "acq",
                "V",
                "dsp",
                "acq_make",
                exclude_properties=["nonexistent"],
            )

    def test_rejects_duplicate_class_name(self, tmp_path):
        dest = tmp_path / "demo"
        new_run("demo", dest, [], [], build_system="cmake")
        module_run(dest, "dsp")
        object_run(
            dest, "acq", module="dsp", state_vars=[("r", "double", "1.0")]
        )
        # 'Acq' is the parent's own class name.
        with pytest.raises(SystemExit):
            _view.run(dest, "acq", "Acq", "dsp", "acq_make")

    def test_requires_module(self, tmp_path):
        dest = tmp_path / "demo"
        new_run("demo", dest, [], [], build_system="cmake")
        module_run(dest, "dsp")
        object_run(
            dest, "acq", module="dsp", state_vars=[("r", "double", "1.0")]
        )
        with pytest.raises(SystemExit):
            _view.run(dest, "acq", "V", None, "acq_make")

    def test_rejects_array_dispatch_parent(self, tmp_path):
        # A parent using dtype-dispatch init-params embeds <comp>_create in the
        # arg-parse block and blanks create_line, so create_fn can't be honoured
        # — v1 rejects it up front rather than silently ignoring the view's ctor.
        dest = tmp_path / "demo"
        new_run("demo", dest, [], [], build_system="cmake")
        module_run(dest, "dsp")
        object_run(
            dest,
            "acq",
            module="dsp",
            no_state=True,
            init_params=[
                # optional-array dispatch: (name, type, ..., optional, create_fn)
                (
                    "h",
                    "float _Complex[]",
                    "",
                    "",
                    "",
                    "",
                    True,
                    "acq_make_h",
                    False,
                )
            ],
        )
        with pytest.raises(SystemExit):
            _view.run(dest, "acq", "V", "dsp", "acq_make")


# ── CLI parser + jm script round-trip ───────────────────────────────────────


class TestCliAndScript:
    def _project_with_view_via_cli(self, dest, monkeypatch):
        from just_makeit import _cli_view

        new_run("demo", dest, [], [], build_system="cmake")
        module_run(dest, "dsp")
        object_run(
            dest, "acq", module="dsp", state_vars=[("rate", "double", "1.0")]
        )
        # Drive the actual CLI parser (Path.cwd()-relative), not _view.run.
        monkeypatch.chdir(dest)
        _cli_view.run(
            [
                "acq",
                "BurstAcquisition",
                "--module",
                "dsp",
                "--create-fn",
                "acq_create_burst",
                "--init-param",
                "reps:int:1",
            ]
        )

    def test_cli_view_creates_the_view(self, tmp_path, monkeypatch):
        dest = tmp_path / "demo"
        self._project_with_view_via_cli(dest, monkeypatch)
        cfg = C.load(dest)
        views = C.views(cfg, "acq")
        assert len(views) == 1
        assert views[0]["class_name"] == "BurstAcquisition"
        assert views[0]["create_fn"] == "acq_create_burst"

    def test_script_round_trips_the_view(self, tmp_path, monkeypatch, capsys):
        from just_makeit import _script

        dest = tmp_path / "demo"
        self._project_with_view_via_cli(dest, monkeypatch)
        capsys.readouterr()  # drop scaffold output
        _script.run(dest)
        out = capsys.readouterr().out
        assert "just-makeit view acq BurstAcquisition" in out
        assert "--create-fn acq_create_burst" in out
        assert "--init-param reps:int:1" in out

    @pytest.mark.parametrize(
        "args",
        [
            [],  # no positionals
            ["acq"],  # missing view class name
            ["acq", "V", "--module"],  # flag missing value
            ["acq", "V", "--create-fn"],
            ["acq", "V", "--exclude-property"],
            ["acq", "V", "--exclude-method"],
            ["acq", "V", "--doc"],
            ["acq", "V", "--bogus"],  # unknown flag
        ],
    )
    def test_cli_view_arg_errors_exit(self, args):
        from just_makeit import _cli_view

        with pytest.raises(SystemExit):
            _cli_view.run(args)


# ── Per-view method subsets (exclude_methods) ───────────────────────────────


@pytest.fixture()
def view_project_with_excluded_method(tmp_path):
    """A module object with a declared method + a view that excludes it."""
    dest = tmp_path / "demo"
    new_run("demo", dest, [], [], build_system="cmake")
    module_run(dest, "dsp")
    object_run(
        dest,
        "acc",
        module="dsp",
        state_vars=[("sum", "double", "0.0")],
        arg_type="double",
        return_type="double",
        mutable=True,
    )
    method_run(dest, "acc", "reset_hard", "dsp", "void", "void", False, [])
    _view.run(
        dest,
        "acc",
        "SeededAcc",
        "dsp",
        "acc_create_seeded",
        init_params=[("seed", "double", "0.0")],
        exclude_methods=["reset_hard"],
    )
    return dest


class TestExcludeMethods:
    def test_config_round_trip(self):
        cfg = {
            "project": {"name": "demo", "version": "0.1.0"},
            "acc": {
                "arg_type": "double",
                "return_type": "double",
                "state": [{"name": "sum", "type": "double", "default": "0.0"}],
                "methods": [{"name": "tune", "arg_type": "void"}],
                "views": [
                    {
                        "class_name": "SeededAcc",
                        "create_fn": "acc_make",
                        "exclude_methods": ["tune"],
                    }
                ],
            },
        }
        cfg2 = tomllib.loads(C._dump(cfg))
        v = C.views(cfg2, "acc")[0]
        assert C.view_exclude_methods(v) == {"tune"}

    def test_excluded_method_dropped_from_view_fragment(
        self, view_project_with_excluded_method
    ):
        d = view_project_with_excluded_method / "native" / "src" / "dsp"
        parent = (d / "dsp_ext_acc.c").read_text(encoding="utf-8")
        view = (d / "dsp_ext_seededacc.c").read_text(encoding="utf-8")
        assert "reset_hard" in parent  # kept on the parent
        assert "reset_hard" not in view  # dropped from the view

    def test_shared_c_function_still_declared(
        self, view_project_with_excluded_method
    ):
        # The excluded method's C function stays in the sacred core (the parent
        # still calls it) — excluding only drops the view's Python wrapper.
        h = (
            view_project_with_excluded_method
            / "native"
            / "inc"
            / "acc"
            / "acc_core.h"
        ).read_text(encoding="utf-8")
        assert "acc_reset_hard" in h

    def test_excluded_method_absent_from_view_pyi(
        self, view_project_with_excluded_method
    ):
        pyi = (
            view_project_with_excluded_method
            / "src"
            / "demo"
            / "dsp"
            / "dsp.pyi"
        ).read_text(encoding="utf-8")
        parent = pyi[pyi.index("class Acc") : pyi.index("class SeededAcc")]
        view = pyi[pyi.index("class SeededAcc") :]
        assert "reset_hard" in parent
        assert "reset_hard" not in view

    def test_status_check_clean(self, view_project_with_excluded_method):
        assert status_run(view_project_with_excluded_method, check=True) == 0

    def test_rejects_unknown_exclude_method(self, tmp_path):
        dest = tmp_path / "demo"
        new_run("demo", dest, [], [], build_system="cmake")
        module_run(dest, "dsp")
        object_run(
            dest, "acc", module="dsp", state_vars=[("s", "double", "0.0")]
        )
        with pytest.raises(SystemExit):
            _view.run(
                dest,
                "acc",
                "V",
                "dsp",
                "acc_make",
                exclude_methods=["nonexistent"],
            )

    def test_script_emits_exclude_method(
        self, view_project_with_excluded_method, capsys
    ):
        from just_makeit import _script

        capsys.readouterr()
        _script.run(view_project_with_excluded_method)
        out = capsys.readouterr().out
        assert "--exclude-method reset_hard" in out
