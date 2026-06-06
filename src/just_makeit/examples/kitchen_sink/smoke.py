"""End-to-end smoke test for the built kitchen_sink `dsp` extension.

Run with PYTHONPATH=<proj>/src. Exercises every object flavor through the
generated Python bindings.
"""

import numpy as np

import kitchen_sink.dsp as dsp


def main() -> None:
    # scalar step + writable property
    g = dsp.Gain(2.0)
    y = g.steps(np.array([1.0, 2.0, 3.0], dtype=np.float32))
    assert np.allclose(y, [2.0, 4.0, 6.0]), y
    g.gain = 3.0
    assert g.gain == 3.0

    # generator (void -> complex64), mutable
    nco = dsp.NCO(0, 2**30)
    o = nco.steps(4)
    assert o.dtype == np.complex64 and len(o) == 4, o

    # consumer (float -> void) + field property
    m = dsp.Meter(0.0)
    m.steps(np.array([0.5, -2.0, 1.0], dtype=np.float32))
    assert m.peak == 2.0, m.peak

    # variable_output + pass_capacity + nogil (decimate by 2)
    r = dsp.Resamp(0.5)
    z = r.execute(np.arange(8, dtype=np.complex64))
    assert np.allclose(z, [0, 2, 4, 6]), z

    # depends_on sibling (mixer uses nco)
    mx = dsp.Mixer()
    w = mx.steps(np.ones(3, dtype=np.complex64))
    assert w.dtype == np.complex64 and len(w) == 3, w

    # vendored cJSON, opaque state, component extra_link_libs
    c = dsp.Config('{"gain": 2.5, "rate": 4}')
    assert c.get_number("gain") == 2.5
    assert c.get_number("rate") == 4.0

    print("kitchen_sink smoke: all object flavors OK")


if __name__ == "__main__":
    main()
