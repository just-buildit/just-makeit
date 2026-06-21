"""gh-244 / gh-377: size_t (and other parse_type) init-param defaults.

A parse_type init param parses into a `<parse_type> <name>_raw` intermediate
(e.g. size_t via the `K`-format `unsigned long long`). Its declared `default`
must seed that `_raw` local; a previous bug used only the (rarely-set)
`default_raw`, so an integer default like `n = 8192` silently initialised to
`0` — `Component()` then built with `n=0` (NULL ctor) or a wrong value. Plain
`double`/`float` defaults took a different branch and were unaffected.

gh-377 found the same bug in the ctor_scalars path (state-var-driven ctor,
no explicit [[init_params]] section) — the fix mirrors _emit_scalar's
`dflt or meta["parse_zero"]` guard.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._context._state import make_state_ctx
from just_makeit._new import run as new_run
from just_makeit._object import run as object_run


def _ext(tmp_path):
    dest = tmp_path / "p"
    new_run("p", dest)
    object_run(
        dest,
        "meas",
        None,
        no_state=True,
        arg_type="float _Complex[]",
        return_type="void",
        init_params=[
            ("n", "size_t", "8192"),
            ("fs", "double", "1.0"),
            ("pad", "size_t", "2"),
        ],
    )
    return (dest / "native/src/meas/meas_ext.c").read_text("utf-8")


def test_sizet_init_default_seeds_raw_local(tmp_path):
    ext = _ext(tmp_path)
    assert "unsigned long long n_raw = 8192;" in ext
    assert "unsigned long long pad_raw = 2;" in ext


def test_sizet_default_not_zeroed(tmp_path):
    # The bug: parse_type defaults fell back to parse_zero (0).
    ext = _ext(tmp_path)
    assert "n_raw = 0" not in ext
    assert "pad_raw = 0" not in ext


def test_double_init_default_unaffected(tmp_path):
    # The non-parse_type branch already worked — keep it that way.
    ext = _ext(tmp_path)
    assert "double fs = 1.0;" in ext


# ── gh-377: same bug in the ctor_scalars (state-var-driven) path ─────────────


class TestCtorScalarsParseTypeDefault:
    """size_t state vars drive ctor_scalars when no explicit init_params given.

    The ctor_scalars loop must seed the `_raw` local from the state-var default,
    not unconditionally from the type's parse_zero."""

    def _ctx(self, **kw):
        return make_state_ctx(
            "corr",
            "Corr",
            state_vars=[("dwell", "size_t", "1"), ("nthreads", "int", "1")],
            **kw,
        )

    def test_sizet_state_var_default_applied(self):
        # gh-377: ctor_scalars path must use dflt, not parse_zero.
        ctx = self._ctx()
        assert "unsigned long long dwell_raw = 1;" in ctx["init_locals"]

    def test_int_state_var_unaffected(self):
        ctx = self._ctx()
        assert "int nthreads = 1;" in ctx["init_locals"]

    def test_zero_default_still_zero(self):
        # A default of "0" must still emit parse_zero (0ULL), not an empty init.
        ctx = make_state_ctx(
            "buf",
            "Buf",
            state_vars=[("capacity", "size_t", "0")],
        )
        assert "unsigned long long capacity_raw = 0" in ctx["init_locals"]

    def test_explicit_init_params_still_correct(self):
        # The init_params path must remain unaffected (regression guard).
        ctx = self._ctx(
            init_params=[
                ("dwell", "size_t", "1"),
                ("nthreads", "int", "1"),
            ]
        )
        assert "unsigned long long dwell_raw = 1;" in ctx["init_locals"]
        assert "int nthreads = 1;" in ctx["init_locals"]
