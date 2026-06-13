"""Tests for dtype-dispatch array init-param (gh-224, dtype shape).

An ``[[comp.init_params]]`` array entry with ``real_type`` + ``real_create_fn``
declares a *polymorphic constructor*: when the array arrives as ``real_type``'s
numpy dtype, ``real_create_fn`` is called; otherwise the default
``<comp>_create`` is called with the entry's declared (complex) dtype. This is
the "which ctor?" dtype shape from gh-224 — e.g. doppler's FIR, where float32
taps select ``fir_create_real`` and complex64 taps select ``fir_create`` — and
it is generatable today via the existing per-param fields (no separate
``init_variants`` table needed). The optional-arg shape is covered by
``test_init_optional_array.py``.

The 8-tuple init-param layout is
``(name, type, default, default_raw, real_type, real_create_fn, optional,
create_fn)`` (see ``_config.init_params``).
"""


def _ctx(params, component="fir", Component="Fir", array_args=()):
    from just_makeit._context import _build_no_state_init_ctx

    return _build_no_state_init_ctx(
        component, Component, params, array_args=array_args
    )


class TestDtypeDispatch:
    # taps default to complex64; float32 taps select fir_create_real.
    PARAMS = [
        (
            "taps",
            "float _Complex[]",
            "",
            "",
            "float[]",
            "fir_create_real",
            False,
            "",
        ),
    ]

    def test_probes_real_dtype(self):
        block = _ctx(self.PARAMS)["array_args_parse_block"]
        assert "PyArray_TYPE(_taps_probe) == NPY_FLOAT" in block

    def test_real_branch_calls_real_create_fn(self):
        block = _ctx(self.PARAMS)["array_args_parse_block"]
        # float32 path: real create fn, non-const float* data.
        assert (
            "fir_create_real((const float *)PyArray_DATA(taps_arr), taps_len)"
            in block
        )

    def test_default_branch_calls_default_create(self):
        block = _ctx(self.PARAMS)["array_args_parse_block"]
        # complex64 path: default create with the declared complex dtype.
        assert "NPY_COMPLEX64" in block
        assert (
            "fir_create((const float complex *)PyArray_DATA(taps_arr), "
            "taps_len)" in block
        )

    def test_dispatch_is_a_branch_real_before_default(self):
        block = _ctx(self.PARAMS)["array_args_parse_block"]
        assert "if (_taps_real)" in block
        assert block.index("fir_create_real(") < block.index(
            "self->handle = fir_create("
        )

    def test_create_line_empty(self):
        # The constructor is emitted inside the dispatch block, so the default
        # single-line create() slot stays empty (mirrors the optional shape).
        assert _ctx(self.PARAMS)["create_line"] == ""

    def test_pyi_signature_uses_arraylike(self):
        ctx = _ctx(self.PARAMS)
        assert "taps: npt.ArrayLike" in ctx["init_params_pyi"]
