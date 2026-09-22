import unittest
import numpy as np
from stale.dsp import Fir

# ---------------------------------------------------------------------------
# pytest compatibility shim — tests run under both pytest and unittest discover
# ---------------------------------------------------------------------------
try:
    import pytest as _pytest

    _approx = _pytest.approx
    _raises = _pytest.raises
except ImportError:
    import contextlib, math

    class _Approx:
        def __init__(self, expected, rel=1e-6):
            self._exp = expected
            self._tol = rel * (abs(expected) if expected else 1e-12)

        def __eq__(self, other):
            import cmath
            return cmath.isclose(complex(other), complex(self._exp),
                                 rel_tol=1e-6, abs_tol=1e-12)

        def __repr__(self):
            return f"approx({self._exp!r})"

    @contextlib.contextmanager
    def _raises(exc_type, match=None):
        import re
        try:
            yield
        except exc_type as e:
            if match and not re.search(match, str(e)):
                raise AssertionError(
                    f"Exception message {str(e)!r} did not match {match!r}"
                ) from e
        else:
            raise AssertionError(f"{exc_type.__name__} was not raised")

    _approx = _Approx
# ---------------------------------------------------------------------------


class TestFir(unittest.TestCase):
    def test_create(self):
        obj = Fir(1.0)
        self.assertIsNotNone(obj)

    def test_step_runs(self):
        obj = Fir(1.0)
        y = obj.step(1.0)
        assert isinstance(y, float)

    def test_steps_shape_dtype(self):
        obj = Fir(1.0)
        x = np.ones(64, dtype=np.float32)
        y = obj.steps(x)
        self.assertEqual(y.shape, (64,))
        self.assertEqual(y.dtype, np.float32)

    def test_steps_out_param(self):
        x   = np.ones(64, dtype=np.float32)
        buf = np.zeros(64, dtype=np.float32)
        obj1 = Fir(1.0)
        ret = obj1.steps(x, buf)
        self.assertIs(ret, buf)
        obj2 = Fir(1.0)
        np.testing.assert_array_equal(ret, obj2.steps(x))

    def test_getter_setter(self):
        obj = Fir(1.0)
        assert obj.get_scale() == _approx(1.0)
        obj.set_scale(2.0)
        assert obj.get_scale() == _approx(2.0)

    def test_reset(self):
        obj = Fir(1.0)
        obj.set_scale(2.0)
        obj.reset()
        assert obj.get_scale() == _approx(1.0)

    def test_context_manager(self):
        with Fir(1.0) as obj:
            y = obj.step(1.0)
        assert isinstance(y, float)

    def test_destroy(self):
        obj = Fir(1.0)
        obj.destroy()
        with _raises(RuntimeError, match="destroyed"):
            obj.step(1.0)
