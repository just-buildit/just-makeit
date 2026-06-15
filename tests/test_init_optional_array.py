"""Tests for optional array init-param with alternate create dispatch (#25).

An ``[[comp.init_params]]`` entry with ``optional = true`` on an array type
declares an optional kwarg that triggers a secondary C constructor when
supplied by the caller.  When omitted, the default ``<comp>_create`` is called
with only the scalar params.
"""

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


def _ctx(params, component="resamp", Component="Resamp", array_args=()):
    from just_makeit._context import _build_no_state_init_ctx

    return _build_no_state_init_ctx(
        component, Component, params, array_args=array_args
    )


def _parse_init_param(spec):
    from just_makeit._cli_parse import parse_init_param_flag

    # remaining[0] is the flag; remaining[1] is the spec value — i=0 mimics CLI.
    token, _ = parse_init_param_flag(["--init-param", spec], 0)
    return token


# ── _build_no_state_init_ctx: 2-D optional array ─────────────────────────────


class TestOptionalArray2D:
    PARAMS = [
        ("bank", "float[][]", "", "", "", "", True, "Resamp_create_custom"),
        ("rate", "double", "0.0", "", "", "", False, ""),
    ]

    def test_format_has_optional_O(self):
        ctx = _ctx(self.PARAMS)
        assert ctx["init_parse_fmt"] == "|Od"

    def test_bank_obj_local_declared(self):
        ctx = _ctx(self.PARAMS)
        assert "PyObject *bank_obj = NULL;" in ctx["init_locals"]

    def test_none_check_block_present(self):
        ctx = _ctx(self.PARAMS)
        assert (
            "if (bank_obj && bank_obj != Py_None)"
            in ctx["array_args_parse_block"]
        )

    def test_ndim_validation_present(self):
        ctx = _ctx(self.PARAMS)
        assert "PyArray_NDIM(bank_arr) != 2" in ctx["array_args_parse_block"]

    def test_alt_create_fn_called_when_bank_given(self):
        ctx = _ctx(self.PARAMS)
        assert "Resamp_create_custom(" in ctx["array_args_parse_block"]

    def test_default_create_called_in_else(self):
        ctx = _ctx(self.PARAMS)
        assert "resamp_create(rate)" in ctx["array_args_parse_block"]

    def test_create_line_empty(self):
        ctx = _ctx(self.PARAMS)
        assert ctx["create_line"] == ""

    def test_dim_args_before_data_ptr(self):
        block = _ctx(self.PARAMS)["array_args_parse_block"]
        dim0_pos = block.index("bank_dim0")
        ptr_pos = block.index("PyArray_DATA(bank_arr)")
        assert dim0_pos < ptr_pos

    def test_decref_inside_block(self):
        ctx = _ctx(self.PARAMS)
        assert "Py_DECREF(bank_arr)" in ctx["array_args_parse_block"]

    def test_array_args_decref_not_duplicated(self):
        ctx = _ctx(self.PARAMS)
        # Decref happens inside the if/else block, not in the outer decref list.
        assert "bank_arr" not in ctx["array_args_decref"]

    def test_pyi_optional_signature(self):
        ctx = _ctx(self.PARAMS)
        assert "bank: npt.ArrayLike | None = None" in ctx["init_params_pyi"]
        assert "rate: np.float64 = 0.0" in ctx["init_params_pyi"]

    def test_py_create_args_omits_optional_array(self):
        ctx = _ctx(self.PARAMS)
        # Test helper creates with scalar defaults only; optional array omitted.
        assert "bank" not in ctx["py_create_args"]
        assert "0.0" in ctx["py_create_args"]

    def test_kwlist_contains_bank(self):
        ctx = _ctx(self.PARAMS)
        assert '"bank"' in ctx["init_kwlist"]
        assert '"rate"' in ctx["init_kwlist"]

    def test_create_params_has_only_scalars(self):
        # Default create signature excludes the optional array.
        ctx = _ctx(self.PARAMS)
        assert "bank" not in ctx["create_params"]
        assert "double rate" in ctx["create_params"]

    def test_scalar_rate_passed_to_both_branches(self):
        block = _ctx(self.PARAMS)["array_args_parse_block"]
        # Both if-branch (alt create) and else-branch (default create) pass rate.
        assert block.count("rate") >= 2


# ── _build_no_state_init_ctx: 1-D optional array ─────────────────────────────


class TestOptionalArray1D:
    PARAMS = [
        ("taps", "float[]", "", "", "", "", True, "Filt_create_custom"),
        ("gain", "double", "1.0", "", "", "", False, ""),
    ]

    def test_format_has_optional_O(self):
        ctx = _ctx(self.PARAMS, component="filt", Component="Filt")
        assert ctx["init_parse_fmt"] == "|Od"

    def test_no_ndim_check_for_1d(self):
        ctx = _ctx(self.PARAMS, component="filt", Component="Filt")
        assert "PyArray_NDIM" not in ctx["array_args_parse_block"]

    def test_len_arg_before_data_ptr(self):
        block = _ctx(self.PARAMS, component="filt", Component="Filt")[
            "array_args_parse_block"
        ]
        len_pos = block.index("taps_len")
        ptr_pos = block.index("PyArray_DATA(taps_arr)")
        assert len_pos < ptr_pos

    def test_alt_create_fn_called(self):
        block = _ctx(self.PARAMS, component="filt", Component="Filt")[
            "array_args_parse_block"
        ]
        assert "Filt_create_custom(" in block

    def test_create_line_empty(self):
        ctx = _ctx(self.PARAMS, component="filt", Component="Filt")
        assert ctx["create_line"] == ""


# ── TOML round-trip ───────────────────────────────────────────────────────────


class TestOptionalArrayToml:
    def test_optional_true_written(self):
        from just_makeit._config import (
            from_new,
            add_component,
            _dump,
        )

        cfg = from_new("proj")
        add_component(
            cfg,
            "resamp",
            [],
            no_state_=True,
            init_params_=[
                (
                    "bank",
                    "float[][]",
                    "",
                    "",
                    "",
                    "",
                    True,
                    "Resamp_create_custom",
                ),
                ("rate", "double", "0.0"),
            ],
        )
        text = _dump(cfg)
        assert "optional = true" in text
        assert 'create_fn = "Resamp_create_custom"' in text

    def test_optional_false_not_written(self):
        from just_makeit._config import from_new, add_component, _dump

        cfg = from_new("proj")
        add_component(
            cfg,
            "resamp",
            [],
            no_state_=True,
            init_params_=[("rate", "double", "0.0")],
        )
        assert "optional" not in _dump(cfg)

    def test_roundtrip_reads_back_8tuple(self):
        import tempfile
        from pathlib import Path
        from just_makeit._config import (
            from_new,
            add_component,
            save,
            load,
            init_params,
        )

        cfg = from_new("proj")
        add_component(
            cfg,
            "resamp",
            [],
            no_state_=True,
            init_params_=[
                (
                    "bank",
                    "float[][]",
                    "",
                    "",
                    "",
                    "",
                    True,
                    "Resamp_create_custom",
                ),
                ("rate", "double", "0.0"),
            ],
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            save(root, cfg)
            loaded = load(root)
            result = init_params(loaded, "resamp")
        assert result[0] == (
            "bank",
            "float[][]",
            "",
            "",
            "",
            "",
            True,
            "Resamp_create_custom",
            False,
        )
        assert result[1][:3] == ("rate", "double", "0.0")
        assert result[1][6] is False
        assert result[1][8] is False

    def test_normal_param_reads_back_false_optional(self):
        from just_makeit._config import from_new, add_component, init_params

        cfg = from_new("proj")
        add_component(
            cfg,
            "gen",
            [],
            no_state_=True,
            init_params_=[("n", "int", "16")],
        )
        result = init_params(cfg, "gen")
        assert result == [("n", "int", "16", "", "", "", False, "", False)]


# ── CLI parser ────────────────────────────────────────────────────────────────


class TestParseInitParamCLI:
    def test_optional_2d_returns_8tuple(self):
        tok = _parse_init_param("bank:float[][]:optional:Resamp_create_custom")
        assert tok == (
            "bank",
            "float[][]",
            "",
            "",
            "",
            "",
            True,
            "Resamp_create_custom",
            False,
        )

    def test_optional_1d_returns_8tuple(self):
        tok = _parse_init_param("taps:float[]:optional:Filt_create_custom")
        assert tok == (
            "taps",
            "float[]",
            "",
            "",
            "",
            "",
            True,
            "Filt_create_custom",
            False,
        )

    def test_optional_without_create_fn(self):
        tok = _parse_init_param("taps:float[]:optional")
        assert tok == ("taps", "float[]", "", "", "", "", True, "", False)

    def test_scalar_returns_8tuple_false_optional(self):
        tok = _parse_init_param("rate:double:0.0")
        assert tok == ("rate", "double", "0.0", "", "", "", False, "", False)

    def test_scalar_no_default_returns_8tuple(self):
        tok = _parse_init_param("n:int")
        assert tok[0] == "n"
        assert tok[1] == "int"
        assert tok[6] is False
        assert tok[7] == ""

    def test_optional_on_scalar_exits(self):
        from just_makeit._cli_parse import parse_init_param_flag

        with pytest.raises(SystemExit):
            parse_init_param_flag(["--init-param", "rate:double:optional"], 0)

    def test_case_insensitive_optional(self):
        tok = _parse_init_param("bank:float[][]:OPTIONAL:Resamp_create_custom")
        assert tok[6] is True
