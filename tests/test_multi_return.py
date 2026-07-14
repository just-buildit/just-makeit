"""Multi-return packing is already expressible (gh-224 sibling, gh-223).

The two "multi-return" shapes gh-223 cites are generatable today — no new
``return_type = "tuple(...)"`` / ``list_of`` syntax is needed:

- **tuple of arrays** (e.g. NCO ``steps_u32_ovf(n) -> (uint32 phase[],
  uint8 overflow[])``): a ``variable_output`` method with ``multi_output``
  listing the extra output element types. The binding packs all outputs with
  ``PyTuple_Pack``.
- **list of fixed-shape records** (e.g. a detector ``push`` returning a list of
  ``(lag, peak_mag, noise_est, test_stat)`` tuples): a method with
  ``result_fields`` + ``max_results_param``. The binding fills a bounded C
  buffer and returns a ``PyList`` of per-event ``Py_BuildValue`` tuples.

These lock the generated form for the two exact shapes; the underlying features
are exercised more broadly in ``test_method.py`` (multi_output) and
``test_cli_method.py`` / ``test_stubs.py`` (result_fields).
"""


def _methods_ctx(methods, component="c", Component="C"):
    from just_makeit._context import make_methods_ctx

    return make_methods_ctx(component, Component, methods)


class TestTupleOfArrays:
    """NCO overflow shape: variable_output + multi_output -> tuple of arrays."""

    METHOD = {
        "name": "steps_u32_ovf",
        "arg_type": "void",
        "return_type": "uint32_t",
        "variable_output": True,
        "multi_output": ["uint8_t"],
        "max_results_param": "n",
    }

    def test_packs_two_arrays_into_a_tuple(self):
        body = _methods_ctx([self.METHOD], "nco", "Nco")["extra_methods_c"]
        assert "PyTuple_Pack(2" in body

    def test_secondary_output_gets_its_own_array(self):
        body = _methods_ctx([self.METHOD], "nco", "Nco")["extra_methods_c"]
        # the second output (uint8 overflow flags) is a second NumPy array
        # (arr0 = phase, arr1 = overflow) packed into the returned tuple
        assert "arr1" in body
        assert "PyArray_SimpleNewFromData" in body


class TestListOfRecords:
    """Detector shape: result_fields -> list of fixed-shape record tuples.

    ``return_type`` names the user's C record struct (the element type of the
    bounded results buffer the C kernel fills)."""

    METHOD = {
        "name": "push",
        "arg_type": "float _Complex",
        "return_type": "detector_event_t",
        "result_fields": [
            {"name": "lag", "type": "uint32_t"},
            {"name": "peak_mag", "type": "float"},
            {"name": "noise_est", "type": "float"},
            {"name": "test_stat", "type": "float"},
        ],
        "max_results_param": "max_events",
    }

    def test_returns_a_python_list(self):
        body = _methods_ctx([self.METHOD], "detector", "Detector")[
            "extra_methods_c"
        ]
        assert "PyList_New" in body
        assert "PyList_SET_ITEM" in body

    def test_each_item_is_a_built_record_tuple(self):
        body = _methods_ctx([self.METHOD], "detector", "Detector")[
            "extra_methods_c"
        ]
        # one Py_BuildValue per event, reading the struct's fields
        assert "Py_BuildValue" in body
        assert "results[i].lag" in body
        assert "results[i].test_stat" in body

    def test_bounded_struct_buffer(self):
        body = _methods_ctx([self.METHOD], "detector", "Detector")[
            "extra_methods_c"
        ]
        # the C kernel fills a caller-bounded buffer of the record struct
        assert "detector_event_t results[" in body


class TestListOfRecordsHeaderDeclMatchesBody:
    """gh-244 regression: the surgically-injected _core.h declaration for a
    result_fields method must match the _core.c body's signature exactly.
    They're built by two different code paths — ``_method.py``'s
    ``_build_method_prototype`` (header, injected via
    ``_inject_decls_into_core_h``) vs. ``_methods_c_stub_result_fields``
    (body) — and previously only the body-builder knew about
    ``result_fields``, so the header got a plain scalar-return declaration
    with generic ``x``/``x_len`` param names instead, a hard compile error
    (conflicting types). This is the default case (no ``max_results_param``
    — jm auto-appends a bare ``size_t max_results``); ``max_results_param``
    set (an existing named param) already worked and is covered above."""

    def test_prototype_has_result_fields_signature(self):
        from just_makeit._method import _build_method_prototype

        proto = _build_method_prototype(
            "comp",
            "find_peaks",
            "float[]",
            "peaks_result_t",
            variable_output=False,
            multi_output=[],
            params=[],
            result_fields=[
                {"name": "index", "type": "size_t"},
                {"name": "magnitude", "type": "float"},
            ],
        )
        assert proto == (
            "size_t comp_find_peaks(comp_state_t *state, const float *in,"
            " size_t n_in, peaks_result_t *result, size_t max_results);"
        )

    def test_prototype_matches_body_stub_signature(self):
        from just_makeit._method import (
            _build_method_prototype,
            _methods_c_stub_result_fields,
        )

        result_fields = [
            {"name": "index", "type": "size_t"},
            {"name": "magnitude", "type": "float"},
        ]
        proto = _build_method_prototype(
            "comp",
            "find_peaks",
            "float[]",
            "peaks_result_t",
            variable_output=False,
            multi_output=[],
            params=[],
            result_fields=result_fields,
        ).rstrip(";")
        stub = _methods_c_stub_result_fields(
            "comp", "find_peaks", "float[]", "peaks_result_t"
        )
        # The declaration's "fn(...)" text must appear verbatim as the
        # definition's signature line in the body stub.
        sig = proto.split(" ", 1)[1]
        assert sig in stub

    def test_single_record_prototype_uses_shared_param_names(self):
        # single=True (one record by value, no results[]/max_results) must
        # also use in/n_in — matching _methods_c_stub_result_single, not the
        # generic fallback's x/x_len.
        from just_makeit._method import _build_method_prototype

        proto = _build_method_prototype(
            "comp",
            "find_peak",
            "float[]",
            "peaks_result_t",
            variable_output=False,
            multi_output=[],
            params=[],
            result_fields=[{"name": "index", "type": "size_t"}],
            single=True,
        )
        assert proto == (
            "peaks_result_t comp_find_peak(comp_state_t *state,"
            " const float *in, size_t n_in);"
        )


class TestBenchBlockResultFields:
    """gh-244 regression: the bench harness's call site also needs to know
    about result_fields — it previously fell through to the generic
    array-arg branch, calling the C function with 3 args instead of the 5
    (state, in, n_in, result, max_results) its actual signature takes."""

    METHOD = {
        "name": "find_peaks",
        "arg_type": "float[]",
        "return_type": "peaks_result_t",
        "result_fields": [
            {"name": "index", "type": "size_t"},
            {"name": "magnitude", "type": "float"},
        ],
    }

    def test_call_includes_results_buffer_and_cap(self):
        from just_makeit._context._methods import _bench_method_block

        block = _bench_method_block("comp", self.METHOD)
        assert (
            "comp_find_peaks(obj, find_peaks_in, BENCH_N,"
            " find_peaks_results, 64)" in block
        )

    def test_results_buffer_declared(self):
        from just_makeit._context._methods import _bench_method_block

        block = _bench_method_block("comp", self.METHOD)
        assert "peaks_result_t find_peaks_results[64];" in block

    def test_respects_custom_max_results(self):
        from just_makeit._context._methods import _bench_method_block

        m = dict(self.METHOD, max_results=16)
        block = _bench_method_block("comp", m)
        assert "find_peaks_results[16];" in block
        assert ", find_peaks_results, 16)" in block


class TestFunctionResultFieldsWrapper:
    """gh-244 regression: jm function's Python-glue wrapper generator
    (_py_wrapper_for_function) only handled result_fields when
    max_results_param named an existing param — the common case (jm
    auto-appends a bare trailing max_results, same as jm method) fell
    through to the generic path and silently dropped the result_fields
    handling, producing an under-arity call."""

    def test_default_case_passes_literal_cap(self):
        from just_makeit._render import _py_wrapper_for_function

        wrapper = _py_wrapper_for_function(
            "find_peaks",
            [{"name": "x", "type": "float[]"}],
            "peaks_result_t",
            result_fields=[
                {"name": "index", "type": "size_t"},
                {"name": "magnitude", "type": "float"},
            ],
        )
        assert "size_t _max = 64;" in wrapper
        assert "find_peaks(x, x_len, _results, _max)" in wrapper

    def test_named_param_case_unchanged(self):
        # max_results_param set: the cap is already one of the parsed args
        # (in call_args), so it is not passed again.
        from just_makeit._render import _py_wrapper_for_function

        wrapper = _py_wrapper_for_function(
            "push",
            [
                {"name": "x", "type": "float _Complex"},
                {"name": "max_events", "type": "size_t"},
            ],
            "detector_event_t",
            result_fields=[{"name": "lag", "type": "uint32_t"}],
            max_results_param="max_events",
        )
        assert "size_t _max = (size_t)max_events;" in wrapper
        assert "push(x, max_events, _results)" in wrapper


class TestCliFunctionResultFieldReturnType:
    """gh-244: jm function's --return-type validator ran inline, before
    --result-field could be seen later in argv, so it always rejected a
    result_fields function's struct-name return type — jm method's
    equivalent validator is deferred post-loop for exactly this reason."""

    def test_struct_return_type_accepted_with_result_field(
        self, tmp_path, monkeypatch
    ):
        import sys

        sys.path.insert(0, str(tmp_path))
        from just_makeit._new import run as new_run
        from just_makeit._cli_function import run as cli_run

        root = tmp_path / "proj"
        new_run("proj", root, modules=["dsp"])
        monkeypatch.chdir(root)
        # Must not exit(1) / print the scalar-allowlist error.
        cli_run(
            [
                "find_peaks",
                "--module",
                "dsp",
                "--param",
                "x:float[]",
                "--return-type",
                "peaks_result_t",
                "--result-field",
                "index:size_t",
            ]
        )
        h = (root / "native/inc/dsp/dsp_core.h").read_text(encoding="utf-8")
        assert "peaks_result_t *result" in h
