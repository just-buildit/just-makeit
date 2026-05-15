import pytest
import numpy as np
from dsp_algo import DspAlgo


def test_create():
    obj = DspAlgo(1.0)
    assert obj is not None


def test_step_runs():
    obj = DspAlgo(1.0)
    y = obj.step(1.0 + 0.0j)
    assert isinstance(y, complex)


def test_steps_shape_dtype():
    obj = DspAlgo(1.0)
    x = np.ones(64, dtype=np.complex64)
    y = obj.steps(x)
    assert y.shape == (64,)
    assert y.dtype == np.complex64


def test_steps_out_param():
    x = np.ones(64, dtype=np.complex64)
    buf = np.zeros(64, dtype=np.complex64)
    obj1 = DspAlgo(1.0)
    ret = obj1.steps(x, buf)
    assert ret is buf
    obj2 = DspAlgo(1.0)
    np.testing.assert_array_equal(ret, obj2.steps(x))


def test_getter_setter():
    obj = DspAlgo(1.0)
    assert obj.get_gain() == pytest.approx(1.0)
    obj.set_gain(2.0)
    assert obj.get_gain() == pytest.approx(2.0)


def test_reset():
    obj = DspAlgo(1.0)
    obj.set_gain(2.0)
    obj.reset()
    assert obj.get_gain() == pytest.approx(1.0)


def test_context_manager():
    with DspAlgo(1.0) as obj:
        y = obj.step(1.0 + 0.0j)
    assert isinstance(y, complex)


def test_destroy():
    obj = DspAlgo(1.0)
    obj.destroy()
    with pytest.raises(RuntimeError, match="destroyed"):
        obj.step(1.0 + 0.0j)
