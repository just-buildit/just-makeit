"""gh-767 step (2) — apply repairs the one binding class it may.

gh-761 fixed the arity jm *emits* for ``*_max_out``: it comes from the C
prototype, so a state-only ``x_max_out(state)`` gets ``METH_NOARGS`` and a
length-bearing ``x_max_out(state, n)`` gets ``METH_VARARGS``. That did nothing
for a project's existing fragments, which are frozen at whatever jm emitted
when they were created — so the `.pyi` moved to ``max_out(n)`` while the
binding kept ``METH_NOARGS``, and calling it raised ``TypeError``.

Repairing it is safe for exactly this class, on the same licence
``_is_reclaimable_glue`` takes: a ``*_max_out`` wrapper has **no authoring
path**. The implementation lives in the sacred ``_core.c``; the wrapper is
marshalling jm writes and nobody edits. Every other drifted binding stays a
warning, because its body is the user's — including ``execute``, which
doppler#616 confirmed is hand-tuned on two objects.

The three pieces move together or not at all. Moving the METH flag without
the body is the one outcome *worse* than the bug: the call stops raising and
starts silently ignoring its argument, returning the state-only answer to a
length-bearing question.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit import _docsync as D  # noqa: E402

# A fragment as an older jm emitted it: state-only wrapper, METH_NOARGS row.
FROZEN = """\
#include "delay/delay_core.h"

static PyObject *
DelayObj_ptr_max_out(DelayObject *self, PyObject *Py_UNUSED(ignored))
{
    return PyLong_FromSize_t(delay_ptr_max_out(self->handle));
}

static PyObject *
DelayObj_execute(DelayObject *self, PyObject *args)
{
    /* HAND-TUNED: float64 -> float32 cast, bespoke by design. */
    return NULL;
}

static PyMethodDef DelayObj_methods[] = {
  {"ptr_max_out", (PyCFunction)DelayObj_ptr_max_out, METH_NOARGS,
   "ptr_max_out() -> int\\n"},
  {"execute", (PyCFunction)DelayObj_execute, METH_VARARGS,
   "execute(x) -> ndarray\\n"},
  {NULL, NULL, 0, NULL}
};
"""

# What today's jm renders: length-bearing prototype, so METH_VARARGS.
FRESH = """\
#include "delay/delay_core.h"

static PyObject *
DelayObj_ptr_max_out(DelayObject *self, PyObject *args)
{
    Py_ssize_t n = 0;
    if (!PyArg_ParseTuple(args, "n", &n))
        return NULL;
    return PyLong_FromSize_t(delay_ptr_max_out(self->handle, (size_t)n));
}

static PyObject *
DelayObj_execute(DelayObject *self, PyObject *args, PyObject *kwds)
{
    return NULL;
}

static PyMethodDef DelayObj_methods[] = {
  {"ptr_max_out", (PyCFunction)DelayObj_ptr_max_out, METH_VARARGS,
   "ptr_max_out(n) -> int\\n"},
  {"execute", (PyCFunction)(void *)DelayObj_execute,
   METH_VARARGS | METH_KEYWORDS, "execute(x, out=None) -> ndarray\\n"},
  {NULL, NULL, 0, NULL}
};
"""


class TestTheRepair:
    def test_the_max_out_row_takes_the_new_flags(self):
        out, fixed = D.refresh_glue_bindings(FROZEN, FRESH)
        assert fixed == ["ptr_max_out"]
        row = out.split('"ptr_max_out"')[1].split("},")[0]
        assert "METH_VARARGS" in row and "METH_NOARGS" not in row

    def test_the_wrapper_body_moves_with_the_flags(self):
        """The half-fix — flags without the body — is worse than no fix:
        the call stops raising TypeError and silently ignores its argument."""
        out, _ = D.refresh_glue_bindings(FROZEN, FRESH)
        assert 'PyArg_ParseTuple(args, "n", &n)' in out
        # The old state-only wrapper is gone, signature included.
        assert "Py_UNUSED(ignored)" not in out
        assert "delay_ptr_max_out(self->handle, (size_t)n)" in out

    def test_the_docstring_moves_too(self):
        """Otherwise `help()` advertises `ptr_max_out()` on a binding that
        now requires an argument. transplant_docs cannot fix this alone — a
        changed synopsis reads to it as hand-written prose."""
        out, _ = D.refresh_glue_bindings(FROZEN, FRESH)
        assert "ptr_max_out(n) -> int" in out

    def test_it_is_idempotent(self):
        once, fixed_1 = D.refresh_glue_bindings(FROZEN, FRESH)
        twice, fixed_2 = D.refresh_glue_bindings(once, FRESH)
        assert fixed_2 == []
        assert twice == once


class TestWhatItRefusesToTouch:
    def test_a_hand_tuned_method_is_left_alone(self):
        """`execute` drifted too — METH_VARARGS to METH_VARARGS|METH_KEYWORDS
        — and must not be repaired. doppler#616: two objects carry a
        bespoke float64->float32 cast in exactly this wrapper."""
        out, fixed = D.refresh_glue_bindings(FROZEN, FRESH)
        assert "execute" not in fixed
        assert "HAND-TUNED: float64 -> float32 cast" in out
        row = out.split('"execute"')[1].split("},")[0]
        assert "METH_KEYWORDS" not in row

    def test_a_member_only_in_the_reference_is_not_invented(self):
        """Adding a genuinely new binding is transplant_missing_bindings'
        job; this function only repairs what is already there."""
        stripped = FROZEN.replace(
            '  {"ptr_max_out", (PyCFunction)DelayObj_ptr_max_out, '
            'METH_NOARGS,\n   "ptr_max_out() -> int\\n"},\n',
            "",
        )
        assert '"ptr_max_out"' not in stripped
        out, fixed = D.refresh_glue_bindings(stripped, FRESH)
        assert fixed == []
        assert out == stripped

    def test_an_already_correct_binding_is_a_no_op(self):
        out, fixed = D.refresh_glue_bindings(FRESH, FRESH)
        assert fixed == []
        assert out == FRESH


class TestTheKwargsWarning:
    """doppler#616 named this class: a refresh that loses no member at all
    can still rewrite ``kwlist[]``, so ``Obj(bank=…)`` becomes a TypeError
    and ``Obj(a, b)`` binds its positionals to different parameters."""

    OLD = """\
static int
DelayObj_init(DelayObject *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"rate", "bank", NULL};
    return 0;
}
"""
    NEW_DROPPED = """\
static int
DelayObj_init(DelayObject *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"rate", NULL};
    return 0;
}
"""
    NEW_REORDERED = """\
static int
DelayObj_init(DelayObject *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"bank", "rate", NULL};
    return 0;
}
"""

    def test_a_dropped_kwarg_is_reported(self, capsys):
        added, removed, reordered = D.warn_init_kwargs_drift(
            "f.c", self.OLD, self.NEW_DROPPED
        )
        assert removed == ("bank",) and added == () and not reordered
        assert "no longer accepted: bank" in capsys.readouterr().err

    def test_a_reorder_is_reported_even_though_no_name_changed(self, capsys):
        """The failure a member-level audit cannot see: same names, new
        positional binding."""
        added, removed, reordered = D.warn_init_kwargs_drift(
            "f.c", self.OLD, self.NEW_REORDERED
        )
        assert reordered and not added and not removed
        assert "new positional order" in capsys.readouterr().err

    def test_agreement_is_silent(self, capsys):
        assert D.warn_init_kwargs_drift("f.c", self.OLD, self.OLD) == (
            (),
            (),
            False,
        )
        assert capsys.readouterr().err == ""

    def test_it_is_reported_not_repaired(self):
        """Preserving the old kwlist under a freshly rendered body would
        leave the name array and the `&var` list out of step, so
        PyArg_ParseTupleAndKeywords binds each keyword to the neighbouring
        variable — it compiles, it runs, and it is wrong."""
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            D.warn_init_kwargs_drift("f.c", self.OLD, self.NEW_DROPPED)
        assert "will not preserve it on its own" in buf.getvalue()
