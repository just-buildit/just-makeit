"""gh-788 gap 1 — the numpy dtype for a C record, built from the compiler.

doppler's `Telemetry.read()` returns one row per 16-byte C record and the
drain is a single `memcpy`, because the dtype *is* the struct layout::

    dtype([("n", "<u8"), ("value", "<f4"), ("probe", "<u2"), ("flags", "<u2")])

jm had no `PyArray_Descr` concept anywhere — `variable_output` returns a plain
typed array of one element type — so the module's primary read path could not
be declared and `telemetry` stayed `no_generate = "true"`.

**Why the offsets are not optional.** numpy's default for a list of
`(name, format)` pairs is *packed*; C inserts padding to satisfy alignment.
Measured:

    C-padded  {uint8; uint64} -> itemsize 16
    numpy packed              -> itemsize  9

The two agree for doppler's record by luck (`uint64, float, uint16, uint16` is
16 bytes either way) and disagree the moment a field ordering needs padding —
at which point every row after the first is read 7 bytes off. Deriving the
layout from `offsetof`/`sizeof` cannot drift from what the compiler actually
did, so the `memcpy` is safe by construction rather than by review.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _record as R  # noqa: E402

FIELDS = [
    R.RecordField("n", "uint64_t", ""),
    R.RecordField("value", "float", ""),
    R.RecordField("probe", "uint16_t", ""),
    R.RecordField("flags", "uint16_t", ""),
]


class TestTheEmittedHelper:
    def _c(self) -> str:
        return R.dtype_c("_tlm_read", "dp_tlm_rec_t", FIELDS)

    def test_offsets_come_from_offsetof(self):
        """Not from numpy's packing rules — that is the whole point."""
        c = self._c()
        for f in FIELDS:
            assert f"offsetof(dp_tlm_rec_t, {f.name})" in c

    def test_the_itemsize_comes_from_sizeof(self):
        assert "sizeof(dp_tlm_rec_t)" in self._c()

    def test_every_field_gets_its_numpy_type(self):
        c = self._c()
        for enum in ("NPY_UINT64", "NPY_FLOAT", "NPY_UINT16"):
            assert enum in c

    def test_it_reuses_the_one_type_table(self):
        """`_NP_ENUM` is what the plain array paths already use. A second
        ctype→numpy mapping is how gh-450's tables drifted."""
        from just_makeit import _types as T

        assert T._NP_ENUM[T._CTYPE_META["uint64_t"]["py_type"]] == "NPY_UINT64"

    def test_the_descr_is_cached(self):
        """A descr is immutable; rebuilding one per call would allocate four
        Python objects on every read of a hot path."""
        c = self._c()
        assert "_tlm_read_dtype = NULL;" in c
        assert "if (_tlm_read_dtype) {" in c

    def test_every_allocation_is_released_on_every_path(self):
        """Four `Py_BuildValue`/`PyList_New` results, one `goto done`, so a
        failure part-way through must not leak. Py_XDECREF, not Py_DECREF —
        the early gotos leave later locals NULL."""
        c = self._c()
        for name in ("names", "formats", "offsets", "spec"):
            assert f"Py_XDECREF({name});" in c
        assert c.count("goto done;") >= 4

    def test_it_declares_before_it_uses(self):
        """The statics must precede the function that reads them, or the
        fragment does not compile — and gh-729/gh-779 are what happens when a
        file-scope declaration and its user come apart."""
        c = self._c()
        assert c.index("_tlm_read_dtype = NULL;") < c.index(
            "_tlm_read_get_dtype(void)"
        )


class TestTheLayoutClaim:
    """The generated C asserts a numpy contract; these pin that the contract
    is what jm thinks it is, on this numpy, rather than assuming it."""

    def test_a_spec_with_offsets_honours_them(self):
        d = np.dtype(
            {
                "names": ["flag", "n"],
                "formats": [np.dtype(np.uint8), np.dtype(np.uint64)],
                "offsets": [0, 8],
                "itemsize": 16,
            }
        )
        assert d.itemsize == 16
        assert d.fields["n"][1] == 8

    def test_and_that_packing_would_have_been_wrong(self):
        """The measurement that makes the offsets load-bearing: without them
        numpy would lay this struct out 7 bytes tighter than C does."""
        packed = np.dtype([("flag", np.uint8), ("n", np.uint64)])
        assert packed.itemsize == 9, (
            "if numpy ever starts padding by default this test should fail "
            "and the offsets rationale should be re-read, not deleted"
        )

    def test_the_doppler_record_round_trips(self):
        d = np.dtype(
            {
                "names": ["n", "value", "probe", "flags"],
                "formats": [
                    np.dtype(np.uint64),
                    np.dtype(np.float32),
                    np.dtype(np.uint16),
                    np.dtype(np.uint16),
                ],
                "offsets": [0, 8, 12, 14],
                "itemsize": 16,
            }
        )
        assert d.itemsize == 16
        buf = np.zeros(3, dtype=d)
        buf["n"] = [1, 2, 3]
        buf["value"] = [1.5, 2.5, 3.5]
        assert buf["n"].tolist() == [1, 2, 3]
        assert buf["value"].tolist() == [1.5, 2.5, 3.5]
