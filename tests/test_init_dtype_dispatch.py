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
        component,
        Component,
        params,
        array_args=array_args,
        csym=component,
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
        line = _ctx(self.PARAMS)["create_line"]
        # float32 path: real create fn, non-const float* data.
        assert (
            "fir_create_real((const float *)PyArray_DATA(taps_arr), taps_len)"
            in line
        )

    def test_default_branch_calls_default_create(self):
        ctx = _ctx(self.PARAMS)
        # complex64 path: default create with the declared complex dtype.
        assert "NPY_COMPLEX64" in ctx["array_args_parse_block"]
        assert (
            "fir_create((const float _Complex *)PyArray_DATA(taps_arr), "
            "taps_len)" in ctx["create_line"]
        )

    def test_dispatch_is_a_branch_real_before_default(self):
        line = _ctx(self.PARAMS)["create_line"]
        assert "if (_taps_real)" in line
        assert line.index("fir_create_real(") < line.index(
            "self->handle = fir_create("
        )

    def test_a_failed_probe_clears_its_error(self):
        """gh-1826: the probe only chooses the constructor. Its failure is
        cleared so the acquisition after it -- which reports the argument's
        own error -- does not run with an exception already set."""
        block = _ctx(self.PARAMS)["array_args_parse_block"]
        assert "if (_taps_probe) {" in block
        assert "} else {\n            PyErr_Clear();\n        }" in block

    def test_the_block_acquires_and_never_constructs(self):
        """gh-1825: the call is `create_line`'s, after every array's locals;
        the dispatch block only acquires the array and records its dtype."""
        ctx = _ctx(self.PARAMS)
        assert "self->handle" not in ctx["array_args_parse_block"]
        assert "Py_DECREF(taps_arr);" in ctx["array_args_decref"]

    def test_pyi_signature_uses_arraylike(self):
        ctx = _ctx(self.PARAMS)
        assert "taps: npt.ArrayLike" in ctx["init_params_pyi"]


def _p(name, ct, real_type="", real_fn="", optional=False, create_fn=""):
    return (name, ct, "", "", real_type, real_fn, optional, create_fn)


class TestDispatchDoesNotCompose:
    """gh-1825: each dispatch picks the constructor per call, so a second
    one -- of either kind, in either order -- or a 2-D one is refused at
    render rather than emitted as C that overwrites or does not compile."""

    def _refused(self, params, *needles):
        import pytest

        with pytest.raises(ValueError) as e:
            _ctx(params)
        for n in needles:
            assert n in str(e.value), str(e.value)

    def test_two_dtype_dispatches(self):
        self._refused(
            [
                _p("a", "float _Complex[]", "float[]", "fir_a"),
                _p("b", "float _Complex[]", "float[]", "fir_b"),
            ],
            "'a' and 'b' each select a constructor",
        )

    def test_dispatch_then_optional(self):
        self._refused(
            [
                _p("a", "float _Complex[]", "float[]", "fir_a"),
                _p("b", "float[]", optional=True, create_fn="fir_b"),
            ],
            "'a' and 'b' each select a constructor",
        )

    def test_optional_then_dispatch(self):
        self._refused(
            [
                _p("b", "float[]", optional=True, create_fn="fir_b"),
                _p("a", "float _Complex[]", "float[]", "fir_a"),
            ],
            "'b' and 'a' each select a constructor",
        )

    def test_a_2d_dispatch(self):
        self._refused(
            [_p("a", "float _Complex[][]", "float[][]", "fir_a")],
            "only supported for a 1-D array",
        )


class TestAlternateConstructorsAreScaffolded:
    """gh-1827: every constructor the binding calls besides create() is
    declared and stubbed, from the same parts as its call."""

    def test_real_create_fn(self):
        ctx = _ctx(TestDtypeDispatch.PARAMS)
        proto = (
            "fir_state_t *fir_create_real(const float *taps, size_t taps_len)"
        )
        assert proto + ";" in ctx["alt_create_decls"]
        assert (
            "fir_create_real(const float *taps, size_t taps_len)\n{"
            in (ctx["alt_create_impls"])
        )

    def test_optional_create_fn_matches_its_call(self):
        ctx = _ctx(
            [
                _p("taps", "float[][]", optional=True, create_fn="fir_poly"),
                ("n", "int", "4", ""),
            ]
        )
        assert (
            "fir_state_t *fir_poly(size_t taps_dim0, size_t taps_dim1,"
            " const float *taps, int n);" in ctx["alt_create_decls"]
        )
        assert (
            "fir_poly(taps_dim0, taps_dim1,"
            " (const float *)PyArray_DATA(taps_arr), n)"
            in ctx["array_args_parse_block"]
        )

    def test_no_dispatch_no_alternates(self):
        ctx = _ctx([("n", "int", "4", "")])
        assert ctx["alt_create_decls"] == ctx["alt_create_impls"] == ""
