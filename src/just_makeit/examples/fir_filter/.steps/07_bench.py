import timeit
import numpy as np
from my_fir import FirFilter

BLOCK = 100_000
RUNS = 500

f = FirFilter(gain=1.0)
h = np.array([0.25, 0.5, 0.25] + [0.0] * 13, dtype=np.float32)
f.set_coeffs(h)
signal = (np.random.randn(BLOCK) + 1j * np.random.randn(BLOCK)).astype(np.complex64)

elapsed = min(timeit.repeat(lambda: f.steps(signal), number=RUNS, repeat=5))
print(f"{RUNS * BLOCK / elapsed / 1e6:.1f} M complex samples/sec")
