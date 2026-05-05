import unittest
import numpy as np
from gain import Gain

# ---------------------------------------------------------------------------
# pytest compatibility shim — tests run under both pytest and unittest discover
# ---------------------------------------------------------------------------
try:
    import pytest as _pytest

    _approx = _pytest.approx
    _raises = _pytest.raises
except ImportError:
    import contextlib
    import math

    class _Approx:
        def __init__(self, expected, rel=1e-6):
            self._exp = expected
            self._tol = rel * (abs(expected) if expected else 1e-12)

        def __eq__(self, other):
            return math.isclose(other, self._exp, rel_tol=1e-6, abs_tol=1e-12)

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


class TestGain(unittest.TestCase):
    def test_create(self):
        obj = Gain(1.0)
        self.assertIsNotNone(obj)

    def test_step_passthrough(self):
        obj = Gain(1.0)
        y = obj.step(3.0 + 4.0j)
        assert abs(y - (3.0 + 4.0j)) < 1e-6

    def test_steps_shape_dtype(self):
        obj = Gain(1.0)
        x = np.ones(64, dtype=np.complex64)
        y = obj.steps(x)
        self.assertEqual(y.shape, (64,))
        self.assertEqual(y.dtype, np.complex64)

    def test_steps_passthrough(self):
        obj = Gain(1.0)
        x = np.ones(8, dtype=np.complex64)
        y = obj.steps(x)
        np.testing.assert_allclose(y.real, 1.0, rtol=1e-5)
        np.testing.assert_allclose(y.imag, 0.0, atol=1e-6)

    def test_getter_setter(self):
        obj = Gain(1.0)
        assert obj.get_gain() == _approx(1.0)
        obj.set_gain(2.0)
        assert obj.get_gain() == _approx(2.0)

    def test_reset(self):
        obj = Gain(1.0)
        obj.reset()
        assert obj.get_gain() == _approx(0.0)

    def test_context_manager(self):
        with Gain(1.0) as obj:
            y = obj.step(1.0 + 1.0j)
        assert abs(y - (1.0 + 1.0j)) < 1e-6

    def test_destroy(self):
        obj = Gain(1.0)
        obj.destroy()
        with _raises(RuntimeError, match="destroyed"):
            obj.step(1.0 + 0.0j)
