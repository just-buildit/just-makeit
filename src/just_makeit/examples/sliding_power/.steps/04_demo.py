"""Power estimator demo.

Run from the project root after `pip install -e .`:
    python3 .steps/04_demo.py
"""

import math
import numpy as np
from my_power import PowerEst

est = PowerEst()

# --- sine wave: expected power = 0.5 (amplitude 1, RMS = 1/sqrt(2)) --------
for n in range(128):
    y = est.step(math.sin(2 * math.pi * n / 16) + 0j)
print(f"sine   power (expect ~0.500): {y.real:.4f}")

# --- white noise: expected power ≈ variance --------------------------------
rng = np.random.default_rng(42)
noise = (rng.standard_normal(128) + 1j * rng.standard_normal(128)) / math.sqrt(
    2
)
for x in noise:
    y = est.step(complex(x))
print(f"noise  power (expect ~1.000): {y.real:.4f}")

# --- silence: power decays to zero after 64 samples -----------------------
for _ in range(64):
    y = est.step(0j)
print(f"silence power (expect 0.000): {y.real:.4f}")

# --- steps() on a block ---------------------------------------------------
est.reset()
block = np.array(
    [math.sin(2 * math.pi * n / 16) for n in range(128)], dtype=np.complex64
)
out = est.steps(block)
print(f"steps() final power (expect ~0.500): {out[-1].real:.4f}")
