"""One emitter for "borrow a pointer, pin the owner" (gh-1312).

`PyArray_SetBaseObject` **steals** its reference on success and does **not**
steal it when it returns -1. jm emitted it from three places with three
different levels of care:

* an array state's `get_<name>_view()` checked the return and unwound;
* the `buf_field` property ignored it and called `Py_INCREF(self)`
  *afterwards*, so a failure leaked that reference AND returned an array whose
  base was never set -- the dangling view the pin exists to prevent;
* a `variable_output` `out=` return ignored it too, where the reference being
  stolen is the caller's `out_arr` rather than a fresh one.

Two copies of one contract, a fix applied to one. `_parse.borrow_view_c` is
now the single emitter for the pin-`self` shape, and the `out=` site is
checked in place (it hands over a reference it already owns, so it cannot
share the body).

The gate below is **registration-free**: it walks whatever `PyArray_SetBaseObject`
calls the generated tree actually contains and requires every one of them to
be inside an `if (...)`. A fourth emitter is covered without being added to a
list.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._context._methods import (  # noqa: E402
    make_properties_ctx,
)
from just_makeit._context._parse import borrow_view_c  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


_SETBASE = re.compile(r"PyArray_SetBaseObject\s*\(")


def _setbase_calls(text: str) -> list[str]:
    """Every `PyArray_SetBaseObject` call site, as the line it starts on.

    Comments are stripped first: this file's own emitters explain the call in
    prose directly above it, and a scanner that reads its own explanation as
    code reports an offender forever.
    """
    lines = [
        ln.split("/*", 1)[0].split("//", 1)[0] for ln in text.splitlines()
    ]
    return [ln for ln in lines if _SETBASE.search(ln)]


def _is_checked(line: str) -> bool:
    """True when the call is the condition of an `if`, not a bare statement."""
    return line.strip().startswith("if (") or line.strip().startswith("if(")


def _every_borrow_shape(dest: Path) -> Path:
    """A project carrying every shape that pins an owner into a view.

    An array state gives `get_<name>_view()`; a `variable_output` method with
    `out=` gives the caller-pinned return. The `buf_field` property is
    rendered directly below, because it needs an opaque struct field that
    `jm object` has no flag for.
    """
    _silent(new_run, "p", dest)
    _silent(
        object_run,
        dest,
        "o",
        None,
        state_vars=[("taps", "float[8]", "0.0f")],
        arg_type="float _Complex",
        return_type="float _Complex",
        variable_output=True,
    )
    return dest


class TestEverySetBaseObjectIsChecked:
    """The defect class, over whatever the tree actually emits."""

    def test_no_unchecked_call_in_a_generated_tree(self, tmp_path):
        root = _every_borrow_shape(tmp_path / "p")
        offenders = []
        for src in sorted(root.rglob("*.c")):
            for line in _setbase_calls(src.read_text()):
                if not _is_checked(line):
                    offenders.append(
                        f"{src.relative_to(root)}: {line.strip()}"
                    )
        assert not offenders, "\n".join(offenders)

    def test_the_scan_reaches_calls_at_all(self, tmp_path):
        """An empty scan passes the test above for free.

        Absent output is not a pass: the tree must actually contain pinned
        views, or the gate is measuring nothing.
        """
        root = _every_borrow_shape(tmp_path / "p")
        found = [
            line
            for src in root.rglob("*.c")
            for line in _setbase_calls(src.read_text())
        ]
        assert found, "no PyArray_SetBaseObject anywhere -- gate is inert"

    def test_the_buf_field_property_is_checked(self):
        """Rendered directly: `jm object` has no flag for an opaque buffer
        field, and this is the site that was wrong."""
        ctx = make_properties_ctx(
            "ring",
            "Ring",
            [
                {
                    "name": "data",
                    "type": "float _Complex[]",
                    "buf_field": "buf",
                    "len_field": "n",
                }
            ],
            set(),
            {},
            [],
            {},
        )
        body = next(
            v
            for v in ctx.values()
            if isinstance(v, str) and "getprop_data" in v
        )
        calls = _setbase_calls(body)
        assert calls, "the buf_field getter pins nothing"
        assert all(_is_checked(c) for c in calls), body


class TestTheTwoPinShapesShareOneEmitter:
    """Both pin-`self` sites render `borrow_view_c`'s body verbatim."""

    def test_the_state_view_is_the_shared_body(self, tmp_path):
        root = _every_borrow_shape(tmp_path / "p")
        ext = (root / "native/src/o/o_ext.c").read_text()
        expected = borrow_view_c(
            "o_get_taps_view(self->handle)",
            "8",
            "NPY_FLOAT",
            writeable=False,
        )
        assert expected in ext, ext[:400]

    def test_the_buf_field_property_is_the_shared_body(self):
        ctx = make_properties_ctx(
            "ring",
            "Ring",
            [
                {
                    "name": "data",
                    "type": "float _Complex[]",
                    "buf_field": "buf",
                    "len_field": "n",
                }
            ],
            set(),
            {},
            [],
            {},
        )
        body = next(
            v
            for v in ctx.values()
            if isinstance(v, str) and "getprop_data" in v
        )
        expected = borrow_view_c(
            "self->handle->buf",
            "self->handle->n",
            "NPY_COMPLEX64",
            writeable=True,
        )
        assert expected in body, body


class TestTheWriteabilityAsymmetryIsDeliberate:
    """The two shapes disagree, and the disagreement is load-bearing.

    An array state's view is read-only; a `buf_field` property is not.
    Normalising them while factoring would have been a silent behaviour
    change for anyone writing through the property today, so `writeable` is a
    required argument with no default and this test pins both answers.
    """

    def test_the_state_view_is_read_only(self, tmp_path):
        root = _every_borrow_shape(tmp_path / "p")
        ext = (root / "native/src/o/o_ext.c").read_text()
        view = ext[ext.index("O_get_taps_view") :][:600]
        assert "NPY_ARRAY_WRITEABLE" in view, view

    def test_the_buf_field_property_is_writeable(self):
        ctx = make_properties_ctx(
            "ring",
            "Ring",
            [
                {
                    "name": "data",
                    "type": "float _Complex[]",
                    "buf_field": "buf",
                    "len_field": "n",
                }
            ],
            set(),
            {},
            [],
            {},
        )
        body = next(
            v
            for v in ctx.values()
            if isinstance(v, str) and "getprop_data" in v
        )
        assert "NPY_ARRAY_WRITEABLE" not in body, body


class TestTheEmitterItself:
    def test_writeable_false_clears_the_flag(self):
        assert "NPY_ARRAY_WRITEABLE" in borrow_view_c(
            "p", "n", "NPY_FLOAT", writeable=False
        )

    def test_writeable_true_does_not(self):
        assert "NPY_ARRAY_WRITEABLE" not in borrow_view_c(
            "p", "n", "NPY_FLOAT", writeable=True
        )

    def test_the_incref_precedes_the_steal(self):
        """`SetBaseObject` steals on success, so the reference must exist
        before the call and be given back on the error path."""
        body = borrow_view_c("p", "n", "NPY_FLOAT", writeable=True)
        assert body.index("Py_INCREF(self)") < body.index(
            "PyArray_SetBaseObject"
        )
        tail = body[body.index("PyArray_SetBaseObject") :]
        assert "Py_DECREF(self)" in tail, tail

    def test_it_is_used_and_not_reimplemented(self):
        """A third hand-rolled copy is the thing this retires."""
        import just_makeit._context._methods as M
        import just_makeit._context._state as S

        for mod in (M, S):
            src = Path(mod.__file__).read_text()
            # The pin-self body must not be spelled out again beside the
            # shared call. `out_arr` is the caller-pinned shape and keeps its
            # own, checked, in-place call.
            assert "(PyObject *)self) < 0" not in src, mod.__name__


class TestTheRecordArrayPredicateHasOneHome:
    """`record_dtype` + `variable_output` means "an ARRAY of records".

    That predicate was spelled inline in four places -- `_method`'s prototype
    builder and its stub dispatch, and `_context/_methods`' declaration and
    return-annotation chains. Four copies of one question is what drifts, and
    gh-788 records that it did: two chains disagreed and the generated
    declaration described a kernel the binding never called.

    Extracted as `_record.is_record_array`, the peer of `_record.is_record`.
    Behaviour is unchanged by that move; what changes is that the borrow
    shape can widen it in ONE place instead of four.
    """

    _SRC = Path(__file__).parent.parent / "src" / "just_makeit"

    def _sources(self):
        for rel in ("_method.py", "_context/_methods.py"):
            yield rel, (self._SRC / rel).read_text()

    def test_no_module_spells_the_predicate_inline(self):
        offenders = []
        for rel, text in self._sources():
            for n, line in enumerate(text.splitlines(), 1):
                code = line.split("#", 1)[0]
                if "variable_output and record_dtype" in code:
                    offenders.append(f"{rel}:{n}: {line.strip()}")
        assert not offenders, "\n".join(offenders)

    def test_both_modules_actually_call_it(self):
        """An empty scan passes the test above for free -- these are the
        modules that asked the question, so they must still ask it."""
        for rel, text in self._sources():
            assert "is_record_array(" in text, rel

    def test_the_predicate_answers_the_three_shapes(self):
        from just_makeit import _record

        assert _record.is_record_array(True, "dp_tlm_rec_t")
        assert not _record.is_record_array(True, "")
        assert not _record.is_record_array(False, "dp_tlm_rec_t")
