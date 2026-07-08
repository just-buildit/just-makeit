"""
gh-421 — a ``variable_output`` method whose sole param is a *scalar* (not an
array) — e.g. ``Delay.push_ptr(x)``, where ``x`` is the value being pushed,
not a count — had its buffer-growth fallback cast the scalar's raw value as
if it were a size: ``size_t _need = (size_t)x;``. A pushed value has no
"count" semantics; the fallback must instead call the method's own
``<name>_max_out()``, per the standard variable_output triplet.
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._new import run as new_run
from just_makeit._method import run as method_run


def _ext(project, name="push_ptr", arg_type="void"):
    method_run(
        project,
        "delay",
        name,
        None,
        arg_type,
        "double _Complex",
        True,
        [],
        params=[("x", "double _Complex")],
    )
    return (project / "native" / "src" / "delay" / "delay_ext.c").read_text(
        encoding="utf-8"
    )


class TestScalarOnlyParamUsesMaxOut:
    def test_no_raw_value_cast_to_size_t(self, tmp_path):
        project = tmp_path / "dsp"
        new_run("dsp", project, ["delay"], [("n", "size_t", "0")])
        ext = _ext(project)
        assert "(size_t)x" not in ext

    def test_growth_fallback_uses_max_out(self, tmp_path):
        project = tmp_path / "dsp"
        new_run("dsp", project, ["delay"], [("n", "size_t", "0")])
        ext = _ext(project)
        assert "size_t _need = delay_push_ptr_max_out(self->handle);" in ext

    def test_no_out_kwarg_for_scalar_only_param(self, tmp_path):
        # gh-412: a params method (even a single scalar) gets no `out=`
        # buffer feature -- confirms the out= _min_cap validation branch
        # (a separate use of the same fallback) isn't even reachable here,
        # so this shape only exercises the internal growth-fallback path.
        project = tmp_path / "dsp"
        new_run("dsp", project, ["delay"], [("n", "size_t", "0")])
        ext = _ext(project)
        assert '"push_ptr_max_out"' not in ext
        assert "_omax" not in ext
