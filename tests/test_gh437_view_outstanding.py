"""gh-437: the default zero-copy view must survive same-size calls.

Follow-up to gh-219 (deferred free on grow). The generated variable_output
binding returned a view of the internal grow-on-demand buffer but retired
the buffer only when it grew — a same-size (or smaller) next call reused
the buffer in place, silently overwriting any outstanding view from the
previous call (a caller accumulating returned chunks got the last call's
data in every early chunk).

The fix: the binding keeps a weakref to the last returned view and, when
that view is still alive on the next call, retires the buffer and
allocates fresh exactly like a grow — zero-copy is preserved for the
drain-immediately pattern (the weakref is dead, the buffer is reused in
place), and accumulate-chunks callers get independent buffers.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._method import run as method_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402


@pytest.fixture()
def project(tmp_path):
    dest = tmp_path / "dsp"
    new_run("dsp", dest, ["nco"], [("freq", "double", "0.0")])
    return dest


class TestViewOutstandingRetire:
    def _ext(self, project):
        method_run(
            project,
            "nco",
            "execute_cf32",
            None,
            "void",
            "float _Complex",
            True,
            [],
        )
        return (project / "native" / "src" / "nco" / "nco_ext.c").read_text(
            encoding="utf-8"
        )

    def test_view_ref_field(self, project):
        ext = self._ext(project)
        assert "PyObject *_execute_cf32_view_ref;" in ext

    def test_liveness_probe_gates_reuse(self, project):
        ext = self._ext(project)
        # the probe runs before the alloc decision...
        assert "int _view_live = 0;" in ext
        # ...with the 3.13+/legacy weakref API split...
        assert "PyWeakref_GetRef(self->_execute_cf32_view_ref" in ext
        assert "PyWeakref_GetObject(self->_execute_cf32_view_ref" in ext
        # ...and a live view forces the retire+malloc path even when the
        # capacity check alone would allow in-place reuse.
        assert "|| _view_live)" in ext

    def test_returned_view_is_registered(self, project):
        ext = self._ext(project)
        assert (
            "self->_execute_cf32_view_ref = PyWeakref_NewRef(arr, NULL);"
            in ext
        )
        # the old weakref is dropped before the new one is stored
        assert "Py_XDECREF(self->_execute_cf32_view_ref);" in ext

    def test_dealloc_drops_weakref(self, project):
        ext = self._ext(project)
        dealloc = ext[ext.index("_dealloc") :]
        assert "Py_XDECREF(self->_execute_cf32_view_ref);" in dealloc

    def test_out_path_untracked(self, project):
        """The explicit out= branch fills the CALLER's buffer — no view
        tracking applies there (their array, their lifetime)."""
        ext = self._ext(project)
        out_branch = ext[
            ext.index("out_obj && out_obj != Py_None") : ext.index(
                "size_t _need"
            )
        ]
        assert "_view_ref" not in out_branch
