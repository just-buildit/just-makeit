import numpy as np

from composites.ring import Ring

r = Ring(capacity=4)
assert r.used == 0 and r.fill_fraction == 0.0

# array-in method -> count accepted (drops past capacity)
assert r.push(np.array([1, 2, 3, 4, 5], dtype=np.float32)) == 4
assert r.fill_fraction == 1.0

# int-in -> independent numpy array, FIFO oldest-first
assert r.pop(2).tolist() == [1.0, 2.0]
assert r.used == 2

# writable scalar property (push scales by gain)
r.gain = 2.0
assert r.gain == 2.0

# context manager + idempotent close()
with Ring(capacity=8) as rr:
    rr.push(np.arange(3, dtype=np.float32))
    assert rr.used == 3
# after __exit__ the handle is closed; access raises rather than crashing
try:
    _ = rr.used
except RuntimeError:
    pass
