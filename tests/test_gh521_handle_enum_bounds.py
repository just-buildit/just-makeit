"""gh-521: a handle decoded-getter must range-check before indexing the enum.

The getter emitted a bare ``PyUnicode_FromString(_enum_<e>[<acc>])``. A handle
module wraps an external resource, so its enum-valued fields are typically
decoded from data the process does not control — gh-514's motivating case was a
Midas BLUE header's format mode designator, where unsupported modes provably
occur in real files. Indexing blind therefore read past the table on any code
outside it: at exactly ``len`` that is the NULL terminator, giving
``PyUnicode_FromString(NULL)``, and beyond it arbitrary memory.

In a built extension that **segfaulted** the interpreter (exit 139) — isolated
to the getter, since constructing the same object without reading the property
exited cleanly. It now raises a ValueError the caller can act on, which is the
same distinction gh-514 was about.

The object-property decode (gh-519) is the peer of this one and carries the
identical check; the two must not drift.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._handle import render_getsets

ENUMS = [{"name": "ftype", "values": ["raw", "wav", "blue"]}]


def _cfg(fields):
    return {
        "enum": ENUMS,
        "module": {
            "rdr": {
                "kind": "handle",
                "backing": "rdr",
                "type_name": "Rdr",
                "getters": [
                    {"fn": "rdr_info", "out": "rdr_info_t", "fields": fields}
                ],
            }
        },
    }


def _funcs(fields):
    return render_getsets(_cfg(fields), "rdr")[0]


# ── the fix ─────────────────────────────────────────────────────────────────


def test_enum_field_is_range_checked_before_indexing():
    """The headline: the guard exists, names the value, and precedes the
    subscript that would otherwise read past the table."""
    src = _funcs([{"name": "file_type", "type": "int", "enum": "ftype"}])
    assert "long _v = (long)(tmp.file_type);" in src
    # ftype has 3 values, so the only valid indices are 0..2.
    assert "if (_v < 0 || _v >= 3) {" in src
    assert "PyErr_Format(PyExc_ValueError," in src
    assert "out-of-range ftype value" in src
    assert "return PyUnicode_FromString(_enum_ftype[_v]);" in src
    assert src.index("if (_v < 0 || _v >= 3)") < src.index("_enum_ftype[_v]")


def test_the_null_terminator_index_is_rejected():
    """Index == len is the table's NULL terminator, i.e. the case that gave
    PyUnicode_FromString(NULL). The bound is exclusive, so it is excluded."""
    src = _funcs([{"name": "ft", "type": "int", "enum": "ftype"}])
    assert "_v >= 3" in src  # not `> 3`, which would admit the terminator


def test_from_alias_is_honoured_in_the_check():
    """A struct member read under a different name checks the same accessor
    it indexes with."""
    src = _funcs(
        [{"name": "kind", "from": "raw_kind", "type": "int", "enum": "ftype"}]
    )
    assert "long _v = (long)(tmp.raw_kind);" in src
    assert "out-of-range ftype value" in src


def test_scalar_out_getter_checks_tmp_directly():
    """A scalar `out` getter's accessor is bare `tmp`, not `tmp.<field>`."""
    cfg = _cfg([{"name": "ft", "type": "int", "enum": "ftype"}])
    cfg["module"]["rdr"]["getters"][0]["out"] = "int"
    src = render_getsets(cfg, "rdr")[0]
    assert "long _v = (long)(tmp);" in src


# ── everything else is untouched ────────────────────────────────────────────


def test_plain_field_keeps_the_one_line_return():
    """Only the enum transform changed; a plain field renders as before."""
    src = _funcs([{"name": "n", "type": "int"}])
    assert "    return PyLong_FromLong((long)tmp.n);" in src
    assert "_v" not in src


def test_scale_field_keeps_the_one_line_return():
    src = _funcs([{"name": "g", "type": "double", "scale": "0.5"}])
    assert "return PyFloat_FromDouble" in src
    assert "_v <" not in src


def test_expr_field_is_not_range_checked():
    """An `expr` field's value is the expression's result, not an index into
    the table, so the check does not apply to it."""
    src = _funcs(
        [
            {
                "name": "ft",
                "type": "int",
                "enum": "ftype",
                "expr": "tmp.a + tmp.b",
            }
        ]
    )
    assert "_v <" not in src


def test_unresolvable_enum_keeps_the_unchecked_form():
    """A field naming an enum absent from the SSOT has no known length; emit
    the historical form rather than a check that rejects every value."""
    cfg = _cfg([{"name": "ft", "type": "int", "enum": "nope"}])
    src = render_getsets(cfg, "rdr")[0]
    assert "return PyUnicode_FromString(_enum_nope[tmp.ft]);" in src
    assert "_v <" not in src
