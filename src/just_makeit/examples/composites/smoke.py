"""End-to-end smoke test for the built composites `ring` handle extension.

Run with PYTHONPATH=<proj>/src. Exercises the whole generated `Ring` surface:
the constructor, an array-in method, an int-in -> array-out method, the
decoded-getter properties, the writable scalar property, and the
context-manager / closed-guard RAII protocol.
"""

import numpy as np

from composites.ring import Ring


def main() -> None:
    # constructor: create_fn = ringbuf_open(capacity)
    r = Ring(capacity=4)

    # decoded-getter properties off the live ringbuf_stats() struct
    assert r.used == 0
    assert r.fill_fraction == 0.0  # derived expr: used / capacity

    # array-in method -> count accepted (drops past capacity 4)
    accepted = r.push(np.array([1, 2, 3, 4, 5, 6], dtype=np.float32))
    assert accepted == 4, accepted
    assert r.used == 4
    assert r.fill_fraction == 1.0

    # int-in -> independent numpy-owned array, FIFO oldest-first
    out = r.pop(2)
    assert out.dtype == np.float32
    assert out.tolist() == [1.0, 2.0], out
    assert r.used == 2
    assert r.fill_fraction == 0.5

    # writable scalar property: default, set, round-trip — and it takes effect
    # (push scales by gain in the backing C).
    assert r.gain == 1.0
    r.gain = 10.0
    assert r.gain == 10.0
    r2 = Ring(capacity=4)
    r2.gain = 10.0
    r2.push(np.array([1, 2, 3], dtype=np.float32))
    assert r2.pop(3).tolist() == [10.0, 20.0, 30.0]

    # context manager + idempotent close(): after the block the handle is
    # closed, so property access raises rather than touching freed memory.
    with Ring(capacity=8) as rr:
        rr.push(np.arange(3, dtype=np.float32))
        assert rr.used == 3
    try:
        _ = rr.used
    except RuntimeError:
        pass
    else:
        raise AssertionError("closed-guard did not fire after __exit__")

    print("composites smoke: Ring handle surface OK")


if __name__ == "__main__":
    main()
