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
