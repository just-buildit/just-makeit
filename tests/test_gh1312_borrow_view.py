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

# gh-1591: this file's hand-written C and expectations spell jm's bare
# derived names, so its projects opt out of the prefix `jm new` now
# defaults to; the default is gated by tests/test_gh1591_*.py.

from __future__ import annotations
from _jminc import INC_ROOT  # noqa: E402

import contextlib
import io
import os
import shutil
import subprocess
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._context._methods import (  # noqa: E402
    make_properties_ctx,
)
from just_makeit._context._parse import borrow_view_c  # noqa: E402
from just_makeit import _borrow  # noqa: E402
from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._module import run as module_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._object import run as object_run  # noqa: E402


def _silent(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _no_toolchain():
    if not shutil.which("cmake"):
        return "cmake not found"
    if not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
        return "no C compiler found"
    return None


_SKIP = _no_toolchain()

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


def _wrapper_of(ext: str, first_line: str) -> str:
    """The whole C wrapper starting at *first_line*, to its closing brace.

    A fixed-width slice truncates the emitted body and reports a missing pin
    that is right there -- the same defect as matching one line's layout
    instead of the property it stands for.
    """
    i = ext.index(first_line)
    j = ext.index("\n}\n", i)
    return ext[i : j + 3]


def _every_borrow_shape(dest: Path) -> Path:
    """A project carrying every shape that pins an owner into a view.

    An array state gives `get_<name>_view()`; a `variable_output` method with
    `out=` gives the caller-pinned return. The `buf_field` property is
    rendered directly below, because it needs an opaque struct field that
    `jm object` has no flag for.
    """
    _silent(new_run, "p", dest, c_prefix=None)
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
            csym="ring",
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
            csym="ring",
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
            csym="ring",
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

    #: Every module that asks the question. `_stubs.py` was missed on the
    #: first pass -- it spells the predicate with its OWN local names
    #: (`m_var and m.get("record_dtype")`), so a scan keyed to one module's
    #: variable names did not see it. CLAUDE.md says "four places"; there
    #: are five, and the fifth is one of the two .pyi generators.
    _ASKERS = ("_method.py", "_context/_methods.py", "_stubs.py")

    def _sources(self):
        for rel in self._ASKERS:
            yield rel, (self._SRC / rel).read_text()

    def test_no_module_spells_the_predicate_inline(self):
        """`record_dtype` conjoined with a variable-output flag, anywhere
        outside `_record`.

        Keyed on the two TERMS rather than on one module's local variable
        names, which is what hid the fifth site: `_stubs.py` spells it
        `m_var and m.get("record_dtype")`, so a scan looking for the literal
        `variable_output and record_dtype` walked straight past it.

        `record_dtype and i == 0` is a different question (which output is
        the record) and is not matched -- the second term has to be a
        variable-output flag.
        """
        var_flag = re.compile(r"\b\w*var(?:iable_output)?\w*\b")
        offenders = []
        for rel, text in self._sources():
            for n, line in enumerate(text.splitlines(), 1):
                code = line.split("#", 1)[0]
                if "record_dtype" not in code or "is_record_array" in code:
                    continue
                if not re.search(r"\band\b", code):
                    continue
                if var_flag.search(code.replace("record_dtype", "")):
                    offenders.append(f"{rel}:{n}: {line.strip()}")
        assert not offenders, "\n".join(offenders)

    def test_both_modules_actually_call_it(self):
        """An empty scan passes the test above for free -- these are the
        modules that asked the question, so they must still ask it."""
        for rel, text in self._sources():
            assert "is_record_array(" in text, rel

    def test_the_predicate_answers_the_shapes(self):
        from just_makeit import _record

        # variable_output owns the buffer...
        assert _record.is_record_array(True, "dp_tlm_rec_t", False)
        # ...and gh-1310: so does a borrow, with the same element type.
        assert _record.is_record_array(False, "dp_tlm_rec_t", True)
        # Neither owner, or no record type, is not a record array.
        assert not _record.is_record_array(True, "", False)
        assert not _record.is_record_array(False, "dp_tlm_rec_t", False)
        assert not _record.is_record_array(False, "", True)

    def test_the_borrow_argument_is_required(self):
        """gh-1310: a call site that has not heard of `borrow` must FAIL,
        not quietly answer `False` and render the `list[tuple]` shape for a
        borrowed record -- the drift this predicate was extracted to end,
        arriving through the fix for it."""
        from just_makeit import _record

        with pytest.raises(TypeError):
            _record.is_record_array(True, "dp_tlm_rec_t")


class TestTheBorrowShape:
    """`borrow = true`: the kernel LENDS a pointer into the state's memory.

    Every other array-returning shape jm generates hands back memory somebody
    allocated for the call -- NumPy's, or the caller's `out=`. A borrow is the
    other arrangement, and jm had no method form of it: the two existing
    borrowing shapes are both accessors.
    """

    @staticmethod
    def _project(root, **kw):
        _silent(new_run, "p", root, c_prefix=None)
        _silent(
            object_run, root, "ring", None, state_vars=[("cap", "size_t", "8")]
        )
        _silent(
            method_run,
            root,
            "ring",
            "wait",
            None,  # module
            "void",  # arg_type
            "float _Complex",  # return_type
            False,  # variable_output
            [],  # multi_output
            params=[("n", "size_t")],
            borrow=True,
            **kw,
        )
        return root

    def test_the_prototype_returns_a_pointer(self, tmp_path):
        """The kernel lends; it does not fill an out-param."""
        root = self._project(tmp_path / "p")
        h = (root / INC_ROOT / "ring/ring_core.h").read_text()
        assert "float _Complex *ring_wait(ring_state_t *state, size_t n);" in h

    def test_the_stub_returns_a_pointer_too(self, tmp_path):
        """A scalar `return` for a pointer-returning function does not
        compile, and "no foot-guns" means the untouched scaffold builds."""
        root = self._project(tmp_path / "p")
        core = (root / "native/src/ring/ring_core.c").read_text()
        body = core[core.index("ring_wait") :][:400]
        assert "return NULL;" in body, body

    def test_the_binding_pins_and_is_checked(self, tmp_path):
        root = self._project(tmp_path / "p")
        ext = (root / "native/src/ring/ring_ext.c").read_text()
        wrapper = _wrapper_of(ext, "Ring_wait(RingObject")
        assert (
            borrow_view_c(
                "_p", "(n)", "NPY_COMPLEX64", writeable=False, arr="_view"
            )
            in wrapper
        ), wrapper

    def test_a_null_return_raises(self, tmp_path):
        """NULL is the author's only failure signal -- there is no count to
        report -- so it always raises, declared `error` or not."""
        root = self._project(tmp_path / "p")
        ext = (root / "native/src/ring/ring_ext.c").read_text()
        wrapper = _wrapper_of(ext, "Ring_wait(RingObject")
        assert "if (!_p)" in wrapper
        assert "PyErr_SetString(PyExc_ValueError" in wrapper, wrapper
        # ...and NOT the return-code raise, which reads an `_rc` this shape
        # has not got and would not compile.
        assert "_rc" not in wrapper, wrapper

    def test_it_is_read_only_by_default(self, tmp_path):
        root = self._project(tmp_path / "p")
        ext = (root / "native/src/ring/ring_ext.c").read_text()
        wrapper = _wrapper_of(ext, "Ring_wait(RingObject")
        assert "NPY_ARRAY_WRITEABLE" in wrapper

    def test_borrow_writeable_opts_out(self, tmp_path):
        root = self._project(tmp_path / "p", borrow_writeable=True)
        ext = (root / "native/src/ring/ring_ext.c").read_text()
        wrapper = _wrapper_of(ext, "Ring_wait(RingObject")
        assert "NPY_ARRAY_WRITEABLE" not in wrapper, wrapper


class TestBothFacesAgree:
    """The four-face problem is the reason this shape is generated at all.

    doppler hand-writes stub, header, core and ext with no gate over any of
    them, and the three widths have already diverged. A generated borrow is
    only worth having if its faces cannot.
    """

    @staticmethod
    def _project(root):
        _silent(new_run, "p", root, c_prefix=None)
        _silent(
            object_run, root, "ring", None, state_vars=[("cap", "size_t", "8")]
        )
        _silent(module_run, root, "m")
        _silent(
            object_run,
            root,
            "mring",
            module="m",
            state_vars=[("cap", "size_t", "8")],
        )
        for obj, mod in (("ring", None), ("mring", "m")):
            _silent(
                method_run,
                root,
                obj,
                "wait",
                mod,  # module
                "void",  # arg_type
                "float _Complex",  # return_type
                False,  # variable_output
                [],  # multi_output
                params=[("n", "size_t")],
                borrow=True,
            )
        return root

    def test_both_pyi_writers_say_ndarray(self, tmp_path):
        """A borrow returns a VIEW, not one element. Both writers had to be
        taught, and they are peers that have disagreed before."""
        root = self._project(tmp_path / "p")
        standalone = (root / "src/p/ring.pyi").read_text()
        module = (root / "src/p/m/m.pyi").read_text()
        want = "def wait(self, n: int) -> NDArray[np.complex64]:"
        assert want in standalone, standalone
        assert want in module, module

    def test_the_runtime_synopsis_agrees(self, tmp_path):
        """`-> complex` above a body handing back an array is a doc face
        describing a different function than the one it is attached to."""
        root = self._project(tmp_path / "p")
        ext = (root / "native/src/ring/ring_ext.c").read_text()
        assert '"wait(n) -> ndarray' in ext, ext[:200]

    def test_the_generated_doctest_is_not_a_scalar(self, tmp_path):
        """It used to assert `obj.wait(0)` -> `0j`: a doctest that is wrong,
        not merely misleading."""
        root = self._project(tmp_path / "p")
        ext = (root / "native/src/ring/ring_ext.c").read_text()
        demo = ext[ext.index('"wait(n) -> ndarray') :][:900]
        assert ">>> y = obj.wait(" in demo, demo
        assert '"    0j' not in demo, demo


class TestTheRefusals:
    """`_borrow.why_not` answers in prose, so a bad declaration is caught at
    the door rather than as a C compile error several files away."""

    def test_borrow_and_variable_output_are_different_answers(self):
        why = _borrow.why_not(
            {"name": "wait", "borrow": True, "variable_output": True}
        )
        assert "different answers to who owns the result" in why

    def test_out_type_is_refused(self):
        why = _borrow.why_not(
            {"name": "wait", "borrow": True, "out_type": "float"}
        )
        assert "no `out_type`" in why

    def test_a_count_is_required(self):
        why = _borrow.why_not({"name": "wait", "borrow": True})
        assert "needs a param carrying the element count" in why

    def test_an_ambiguous_count_is_never_guessed(self):
        why = _borrow.why_not(
            {
                "name": "wait",
                "borrow": True,
                "params": [{"name": "a"}, {"name": "b"}],
            }
        )
        assert "cannot tell which of 2 params" in why
        assert "borrow_count" in why

    def test_a_count_naming_no_param_is_refused(self):
        why = _borrow.why_not(
            {
                "name": "wait",
                "borrow": True,
                "borrow_count": "nope",
                "params": [{"name": "n"}],
            }
        )
        assert "not one of its params" in why

    def test_a_good_declaration_is_accepted(self):
        assert (
            _borrow.why_not(
                {"name": "wait", "borrow": True, "params": [{"name": "n"}]}
            )
            == ""
        )


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
class TestItActuallyBorrows:
    """Built and run, because reading it is what missed the last two defects.

    The generated stub returned a VALUE for a pointer-returning function --
    it did not compile -- and three of the four faces said `-> complex` for a
    method handing back an array. Neither is visible in a string comparison;
    both are obvious the moment a compiler and an interpreter see them.
    """

    KERNEL = """    if (n > 8) return NULL;
    for (size_t i = 0; i < n; i++)
        state->buf[i] = (float)i + 0.0f * I;
    return state->buf;"""

    @classmethod
    def _built(cls, root: Path, implement: bool):
        _silent(new_run, "p", root, c_prefix=None)
        _silent(
            object_run, root, "ring", None, state_vars=[("cap", "size_t", "8")]
        )
        _silent(
            method_run,
            root,
            "ring",
            "wait",
            None,
            "void",
            "float _Complex",
            False,
            [],
            params=[("n", "size_t")],
            borrow=True,
        )
        if implement:
            h = root / INC_ROOT / "ring/ring_core.h"
            h.write_text(
                h.read_text().replace(
                    "    size_t cap;",
                    "    size_t cap;\n    float _Complex buf[8];",
                    1,
                )
            )
            c = root / "native/src/ring/ring_core.c"
            text = c.read_text()
            i = text.index("ring_wait(ring_state_t *state, size_t n)")
            j = text.index("\n}\n", i)
            body_start = text.index("{", i) + 1
            c.write_text(text[:body_start] + "\n" + cls.KERNEL + text[j:])
        r = subprocess.run(
            ["cmake", "-S", str(root), "-B", str(root / "build")],
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        r = subprocess.run(
            ["cmake", "--build", str(root / "build"), "--target", "ring"],
            capture_output=True,
            text=True,
            timeout=900,
        )
        return r

    def test_the_untouched_scaffold_compiles(self, tmp_path):
        """ "No foot-guns, all green from day one": before this the stub
        emitted a scalar `return` for a `T *` function."""
        r = self._built(tmp_path / "p", implement=False)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

    def test_the_view_borrows_pins_and_refuses_writes(self, tmp_path):
        root = tmp_path / "p"
        r = self._built(root, implement=True)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

        probe = root / "probe.py"
        probe.write_text(
            "import sys, numpy as np\n"
            "from p.ring import Ring\n"
            "r = Ring(cap=8)\n"
            "v = r.wait(4)\n"
            "assert v.dtype == np.complex64, v.dtype\n"
            "assert v.shape == (4,), v.shape\n"
            "assert not v.flags.writeable, 'writeable'\n"
            "assert v.base is r, 'not pinned to the object'\n"
            # it BORROWS: a second call sees the same memory, no copy
            "assert np.shares_memory(v, r.wait(4)), 'copied'\n"
            # the pin is a real reference, so the object outlives the name
            "before = sys.getrefcount(r)\n"
            "w = r.wait(2)\n"
            "assert sys.getrefcount(r) > before, 'pin did not incref'\n"
            "del w\n"
            # NULL -> the declared exception, not a segfault or a None
            "try:\n"
            "    r.wait(99)\n"
            "    raise SystemExit('oversize did not raise')\n"
            "except ValueError:\n"
            "    pass\n"
            "print('OK')\n"
        )
        out = subprocess.run(
            [sys.executable, str(probe)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(root),
            env={**os.environ, "PYTHONPATH": str(root / "src")},
        )
        assert out.returncode == 0, out.stdout + out.stderr
        assert "OK" in out.stdout, out.stdout
