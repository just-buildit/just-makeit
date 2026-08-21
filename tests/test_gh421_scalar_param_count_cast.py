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

    def test_out_kwarg_for_scalar_only_param(self, tmp_path):
        """gh-1079: this shape now DOES get the `out=` buffer.

        It used to be the assertion's opposite, with a gh-412 note saying the
        `_min_cap` validation branch "isn't even reachable here". It is now:
        the buffer is validated against `<m>_max_out(state)`, which is the
        same expression the internal allocation uses for this shape — so the
        caller's buffer is checked against exactly what the binding would
        have allocated itself.

        Safe only because gh-1085 refuses a zero bound up front. Without
        that, `max_out()` returning 0 ("unknown", which jm documents as
        legal) made `_min_cap` zero and any buffer acceptable.
        """
        project = tmp_path / "dsp"
        new_run("dsp", project, ["delay"], [("n", "size_t", "0")])
        ext = _ext(project)
        assert '"push_ptr_max_out"' in ext
        assert "_omax" in ext
        # The growth-fallback path this class is really about is unchanged.
        assert "delay_push_ptr_max_out(self->handle)" in ext
