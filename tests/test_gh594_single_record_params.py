"""gh-594: a record method's `params` never reached its C signature.

Reported as "`single = true` + an array param generates malformed C":

    float _Complex[] rx = 0;                    /* not valid C */
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "d|K", ...))
    ber_align_t _r = meter_align(self->handle, rx, t0);

Three symptoms from one omission -- the array type emitted verbatim as a
declaration, parsed as a scalar double, and passed with no length.

The reported table said `single = true` with SCALAR-only params was correct.
It is not, in a fresh project: doppler hand-maintains the C header, so its
copy happened to match. Generated from scratch, the header declared
`ber_align_t meter_align(meter_state_t *state);` while the binding called it
with every param -- "too many arguments to function". A NON-single record
method dropped params on both sides at once: self-consistent, compiles, and
silently ignores every declared param.

So the real defect is one rule missing from four places:

1. `_method._build_method_prototype`   -- record branch built its signature
   from `arg_type` alone.
2. `_method._methods_c_stub_result_single` / `_result_fields` -- the stub
   bodies that must match that prototype.
3. `_context._methods` decl_lines      -- the peer prototype builder, which
   additionally ignored `single` entirely.
4. `_context._methods` single binding  -- a private scalar-only param loop
   (`_CTYPE_META.get(t, {}).get("fmt", "d")`) instead of the shared
   `_build_params_parse` every other method shape already used.

Sites 1-3 now expand params through one helper (`_types.c_param_parts`) and
site 4 goes through the shared parse builder, so an array param gets the
pointer + `_len` treatment the non-single path always gave it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import (
    _build_method_prototype,
    _methods_c_stub_result_fields,
    _methods_c_stub_result_single,
)
from just_makeit._module import run as module_run
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run
from just_makeit._method import run as method_run
from just_makeit._types import c_param_parts, c_param_suppress

ARRAY_PARAMS = [("rx", "float _Complex[]"), ("t0", "size_t")]
RESULT_FIELDS = [
    {"name": "lag", "type": "int"},
    {"name": "phase", "type": "double"},
]


class TestParamExpansionHelper:
    """One rule for turning a declared param into C parameters."""

    def test_array_param_expands_to_pointer_and_length(self):
        assert c_param_parts(ARRAY_PARAMS) == [
            "const float _Complex *rx",
            "size_t rx_len",
            "size_t t0",
        ]

    def test_accepts_dicts_as_well_as_tuples(self):
        dicts = [
            {"name": "rx", "type": "float _Complex[]"},
            {"name": "t0", "type": "size_t"},
        ]
        assert c_param_parts(dicts) == c_param_parts(ARRAY_PARAMS)

    def test_suppression_covers_the_synthesised_length(self):
        # A stub that forgets (void)rx_len; warns the moment it compiles.
        assert c_param_suppress(ARRAY_PARAMS) == [
            "(void)rx;",
            "(void)rx_len;",
            "(void)t0;",
        ]


class TestPrototype:
    """The C declaration must carry every declared param."""

    def test_single_record_with_array_param(self):
        proto = _build_method_prototype(
            "meter",
            "align",
            "void",
            "ber_align_t",
            variable_output=False,
            multi_output=[],
            params=ARRAY_PARAMS,
            result_fields=RESULT_FIELDS,
            single=True,
        )
        assert proto == (
            "ber_align_t meter_align(meter_state_t *state,"
            " const float _Complex *rx, size_t rx_len, size_t t0);"
        )

    def test_single_record_with_scalar_params_only(self):
        """The row the issue called correct -- it was not, from scratch."""
        proto = _build_method_prototype(
            "meter",
            "align",
            "void",
            "ber_align_t",
            variable_output=False,
            multi_output=[],
            params=[("t0", "size_t"), ("pfa", "double")],
            result_fields=RESULT_FIELDS,
            single=True,
        )
        assert proto == (
            "ber_align_t meter_align(meter_state_t *state,"
            " size_t t0, double pfa);"
        )

    def test_non_single_record_also_carries_params(self):
        proto = _build_method_prototype(
            "meter",
            "scan",
            "void",
            "ber_hit_t",
            variable_output=False,
            multi_output=[],
            params=ARRAY_PARAMS,
            result_fields=RESULT_FIELDS,
            single=False,
        )
        assert proto == (
            "size_t meter_scan(meter_state_t *state,"
            " const float _Complex *rx, size_t rx_len, size_t t0,"
            " ber_hit_t *result, size_t max_results);"
        )

    def test_primary_arg_and_params_coexist(self):
        proto = _build_method_prototype(
            "tm",
            "analyze",
            "float _Complex[]",
            "tone_metrics_t",
            variable_output=False,
            multi_output=[],
            params=[("lo", "double")],
            result_fields=RESULT_FIELDS,
            single=True,
        )
        assert proto == (
            "tone_metrics_t tm_analyze(tm_state_t *state,"
            " const float _Complex *in, size_t n_in, double lo);"
        )


class TestStubMatchesPrototype:
    """The stub IS the definition the prototype declares -- they must agree."""

    def _sig_of(self, proto):
        return proto.rstrip(";").split(" ", 1)[1]

    def test_single_stub_signature_matches(self):
        proto = _build_method_prototype(
            "meter",
            "align",
            "void",
            "ber_align_t",
            variable_output=False,
            multi_output=[],
            params=ARRAY_PARAMS,
            result_fields=RESULT_FIELDS,
            single=True,
        )
        stub = _methods_c_stub_result_single(
            "meter", "align", "void", "ber_align_t", params=ARRAY_PARAMS
        )
        assert self._sig_of(proto) in stub
        # every param silenced, including the synthesised length
        assert "(void)rx; (void)rx_len; (void)t0;" in stub

    def test_result_fields_stub_signature_matches(self):
        proto = _build_method_prototype(
            "meter",
            "scan",
            "void",
            "ber_hit_t",
            variable_output=False,
            multi_output=[],
            params=ARRAY_PARAMS,
            result_fields=RESULT_FIELDS,
            single=False,
        )
        stub = _methods_c_stub_result_fields(
            "meter", "scan", "void", "ber_hit_t", 64, params=ARRAY_PARAMS
        )
        assert self._sig_of(proto) in stub


class TestDeclPeerAgrees:
    """`make_methods_ctx` builds the SAME record prototype as `_method`.

    This is jm's most-repeated failure mode: two builders for one artifact,
    one fixed and one left behind. Here the copies are
    `_method._build_method_prototype` (which writes the header via
    `_inject_decls_into_core_h`) and `make_methods_ctx`'s `method_decls`
    (which fills the `component_core.h` template slot).

    On the current tree the injected decl wins in every file-writing path, so
    `method_decls` is shadowed for record methods and a defect in it is
    invisible end-to-end -- which is exactly why it had drifted furthest: it
    ignored `params` AND `single`, so it would have declared
    `size_t meter_align(state, ber_align_t *result, size_t max_results)` for a
    method that returns one record by value. Asserted directly here so the two
    cannot diverge again while one of them is unobservable.
    """

    def _decls_for(self, method):
        from just_makeit._context._methods import make_methods_ctx

        return make_methods_ctx("meter", "Meter", [method])["method_decls"]

    def _method_entry(self, **over):
        entry = {
            "name": "align",
            "arg_type": "void",
            "return_type": "ber_align_t",
            "params": [
                {"name": "rx", "type": "float _Complex[]"},
                {"name": "t0", "type": "size_t"},
            ],
            "result_fields": RESULT_FIELDS,
            "single": True,
        }
        entry.update(over)
        return entry

    def test_single_record_decl_matches_the_prototype_builder(self):
        decls = self._decls_for(self._method_entry())
        expected = _build_method_prototype(
            "meter",
            "align",
            "void",
            "ber_align_t",
            variable_output=False,
            multi_output=[],
            params=ARRAY_PARAMS,
            result_fields=RESULT_FIELDS,
            single=True,
        )
        assert expected in decls

    def test_non_single_record_decl_matches_too(self):
        decls = self._decls_for(self._method_entry(single=False))
        expected = _build_method_prototype(
            "meter",
            "align",
            "void",
            "ber_align_t",
            variable_output=False,
            multi_output=[],
            params=ARRAY_PARAMS,
            result_fields=RESULT_FIELDS,
            single=False,
        )
        assert expected in decls

    def test_single_record_does_not_declare_a_results_buffer(self):
        # The pre-fix shape: `single` was ignored here entirely.
        decls = self._decls_for(self._method_entry())
        assert "max_results" not in decls
        assert "ber_align_t *result" not in decls


def _scaffold(dest, params):
    """The issue's shape: a module object with a single-record method."""
    new_run("p", dest)
    module_run(dest, "ber")
    object_run(dest, "meter", module="ber", arg_type="void", no_step=True)
    method_run(
        dest,
        "meter",
        "align",
        "ber",
        "void",
        "ber_align_t",
        False,
        [],
        params=params,
        result_fields=RESULT_FIELDS,
        single=True,
        record_name="BerAlign",
    )
    return dest


class TestGeneratedBinding:
    """End-to-end, against the manifest from the issue."""

    @pytest.fixture
    def ext(self, tmp_path):
        dest = _scaffold(tmp_path / "p", ARRAY_PARAMS)
        return (dest / "native/src/ber/ber_ext_meter.c").read_text(
            encoding="utf-8"
        )

    def test_no_malformed_array_declaration(self, ext):
        """The headline symptom: `float _Complex[] rx = 0;`."""
        assert "float _Complex[] rx" not in ext
        # Case-folded so it stays armed against either spelling. As a
        # lowercase-only check it went vacuous the moment jm started
        # emitting `_Complex` (gh-1246): nothing could match it again.
        assert "complex[]" not in ext.lower()

    def test_array_param_is_parsed_as_an_object_not_a_double(self, ext):
        # was: "d|K" -- the array param parsed as a scalar double.
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "OK"' in ext
        assert "PyObject *rx_obj = NULL;" in ext

    def test_defaulted_param_stays_optional(self, tmp_path):
        """gh-240's `|` must survive the move to the shared builder."""
        dest = _scaffold(
            tmp_path / "p",
            [("rx", "float _Complex[]"), ("t0", "size_t", "0")],
        )
        ext = (dest / "native/src/ber/ber_ext_meter.c").read_text("utf-8")
        assert 'PyArg_ParseTupleAndKeywords(args, kwds, "O|K"' in ext

    def test_array_param_is_converted_and_sized(self, ext):
        assert "PyArray_FROM_OTF(" in ext
        assert (
            "const float _Complex *rx = (const float _Complex *)PyArray_DATA(rx_arr);"
            in ext
        )
        assert "size_t rx_len = (size_t)PyArray_SIZE(rx_arr);" in ext

    def test_call_passes_pointer_and_length(self, ext):
        # was: meter_align(self->handle, rx, t0) -- one argument short.
        assert "meter_align(self->handle, rx, rx_len, t0)" in ext

    def test_acquired_array_is_released_on_every_path(self, ext):
        # after the call, and on the structseq-creation failure path
        assert "Py_DECREF(rx_arr);" in ext
        assert (
            "if (!Meter_align_type) { Py_DECREF(rx_arr); return NULL; }" in ext
        )

    def test_prototype_and_binding_agree(self, tmp_path):
        dest = _scaffold(tmp_path / "p", ARRAY_PARAMS)
        header = (dest / "native/inc/meter/meter_core.h").read_text("utf-8")
        assert (
            "ber_align_t meter_align(meter_state_t *state,"
            " const float _Complex *rx, size_t rx_len, size_t t0);" in header
        )

    def test_scalar_only_params_also_agree(self, tmp_path):
        """The issue's 'correct' row -- a compile error before this fix."""
        dest = _scaffold(tmp_path / "p", [("t0", "size_t"), ("pfa", "double")])
        header = (dest / "native/inc/meter/meter_core.h").read_text("utf-8")
        ext = (dest / "native/src/ber/ber_ext_meter.c").read_text("utf-8")
        assert (
            "ber_align_t meter_align(meter_state_t *state,"
            " size_t t0, double pfa);" in header
        )
        assert "meter_align(self->handle, t0, pfa)" in ext
