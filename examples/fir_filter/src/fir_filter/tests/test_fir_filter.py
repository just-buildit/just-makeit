import unittest
import numpy as np
from fir_filter import FirFilter

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


class TestFirFilter(unittest.TestCase):
    def test_create(self):
        obj = FirFilter(1.0)
        self.assertIsNotNone(obj)

    def test_step_identity(self):
        """coeffs=[1,0,...] → delay-free passthrough."""
        obj = FirFilter(1.0)
        h = np.zeros(16, dtype=np.float32)
        h[0] = 1.0
        obj.set_coeffs(h)
        y0 = obj.step(3.0 + 4.0j)
        assert abs(y0 - (3.0 + 4.0j)) < 1e-5
        y1 = obj.step(0.0 + 0.0j)
        assert abs(y1) < 1e-5

    def test_steps_shape_dtype(self):
        obj = FirFilter(1.0)
        h = np.zeros(16, dtype=np.float32)
        h[0] = 1.0
        obj.set_coeffs(h)
        x = np.ones(64, dtype=np.complex64)
        y = obj.steps(x)
        self.assertEqual(y.shape, (64,))
        self.assertEqual(y.dtype, np.complex64)

    def test_steps_impulse_response(self):
        """Impulse response of a 3-tap averager matches the taps."""
        obj = FirFilter(1.0)
        h = np.array([0.25, 0.5, 0.25] + [0.0] * 13, dtype=np.float32)
        obj.set_coeffs(h)
        impulse = np.zeros(16, dtype=np.complex64)
        impulse[0] = 1.0
        y = obj.steps(impulse)
        np.testing.assert_allclose(y[:3].real, h[:3], atol=1e-6)
        np.testing.assert_allclose(y[:3].imag, [0.0, 0.0, 0.0], atol=1e-6)
        np.testing.assert_allclose(y[3:].real, 0.0, atol=1e-6)

    def test_getter_setter(self):
        obj = FirFilter(1.0)
        assert obj.get_gain() == _approx(1.0)
        obj.set_gain(2.0)
        assert obj.get_gain() == _approx(2.0)
        _arr = np.zeros(16, dtype=np.float32)
        _arr[0] = 1
        obj.set_coeffs(_arr)
        _got = obj.get_coeffs()
        assert _got[0] == _approx(1)
        _view = obj.get_coeffs_view()
        assert not _view.flags['WRITEABLE']
        assert _view[0] == _approx(1)
        _arr = np.zeros(16, dtype=np.complex64)
        _arr[0] = 1
        obj.set_delay(_arr)
        _got = obj.get_delay()
        assert _got[0] == _approx(1)
        _view = obj.get_delay_view()
        assert not _view.flags['WRITEABLE']
        assert _view[0] == _approx(1)

    def test_reset(self):
        obj = FirFilter(1.0)
        obj.set_gain(2.0)
        obj.set_coeffs(np.ones(16, dtype=np.float32))
        obj.set_delay(np.ones(16, dtype=np.complex64))
        obj.reset()
        assert obj.get_gain() == _approx(1.0)
        assert obj.get_coeffs()[0] == _approx(0)
        assert obj.get_delay()[0] == _approx(0)

    def test_context_manager(self):
        h = np.zeros(16, dtype=np.float32)
        h[0] = 1.0
        with FirFilter(1.0) as obj:
            obj.set_coeffs(h)
            y = obj.step(1.0 + 1.0j)
        assert abs(y - (1.0 + 1.0j)) < 1e-5

    def test_destroy(self):
        obj = FirFilter(1.0)
        obj.destroy()
        with _raises(RuntimeError, match="destroyed"):
            obj.step(1.0 + 0.0j)
