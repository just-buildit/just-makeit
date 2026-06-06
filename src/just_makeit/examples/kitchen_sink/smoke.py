"""End-to-end smoke test for the built kitchen_sink `dsp` extension.

Run with PYTHONPATH=<proj>/src. Exercises every object flavor through the
generated Python bindings.
"""

import os

import numpy as np

import kitchen_sink.dsp as dsp


def main() -> None:
    # scalar step + writable property
    g = dsp.Gain(2.0)
    y = g.steps(np.array([1.0, 2.0, 3.0], dtype=np.float32))
    assert np.allclose(y, [2.0, 4.0, 6.0]), y
    g.gain = 3.0
    assert g.gain == 3.0
    # --batch method (1:1-rate block transform)
    yb = g.process_batch(np.array([1.0, 2.0, 3.0], dtype=np.float32))
    assert np.allclose(yb, [3.0, 6.0, 9.0]), yb

    # generator (void -> complex64), mutable
    lfo = dsp.Lfo(0, 2**30)
    o = lfo.steps(4)
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

    # module-level function
    assert dsp.lerp(0.0, 10.0, 0.5) == 5.0

    # symbol reexported from the no_generate dsp_fn sibling
    assert dsp.db10(100.0) == 20.0

    # the real-doppler-linked tone (only when doppler was available)
    if os.environ.get("KITCHEN_SINK_DOPPLER"):
        import kitchen_sink as ks

        tone = ks.Tone(norm_freq=0.25)  # quarter-circle steps: 1, j, -1, -j
        got = [tone.step() for _ in range(4)]
        want = [1 + 0j, 0 + 1j, -1 + 0j, 0 - 1j]
        assert all(abs(g - w) < 1e-6 for g, w in zip(got, want)), got
        print("kitchen_sink smoke: all flavors + real doppler link OK")
    else:
        print("kitchen_sink smoke: all object flavors OK (doppler skipped)")


if __name__ == "__main__":
    main()
