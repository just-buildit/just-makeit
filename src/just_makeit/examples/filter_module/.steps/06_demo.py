"""Demo: Fir (complex) and Biquad (real) from the filter module."""

import math
import sys

sys.path.insert(0, "src")

import numpy as np
from my_filters.filter import Biquad, Fir

# ── FIR: 16-tap complex low-pass (windowed sinc, cutoff = 0.1 * fs) ─────────
N = 16
h = np.array(
    [
        math.sin(math.pi * 0.1 * (k - N // 2)) / (math.pi * (k - N // 2))
        if k != N // 2
        else 0.1
        for k in range(N)
    ],
    dtype=np.float32,
)
h /= h.sum()

fir = Fir(gain=1.0)
fir.set_coeffs(h)

impulse = np.zeros(N, dtype=np.complex64)
impulse[0] = 1.0
ir = fir.steps(impulse)
print("FIR impulse response (first 4):", ir[:4].real.round(4))

# ── Biquad: real low-pass at cutoff = 0.1 * fs, Q = 0.707 ───────────────────
fc, Q = 0.1, 0.707
w0 = 2 * math.pi * fc
alpha = math.sin(w0) / (2 * Q)
c = math.cos(w0)
a0 = 1 + alpha

bq = Biquad(
    b0=(1 - c) / 2 / a0,
    b1=(1 - c) / a0,
    b2=(1 - c) / 2 / a0,
    a1=-2 * c / a0,
    a2=(1 - alpha) / a0,
)

t = np.arange(512, dtype=np.float32) / 512
lo = np.cos(2 * math.pi * 0.05 * t)  # 0.05*fs — passband
hi = np.cos(2 * math.pi * 0.40 * t)  # 0.40*fs — stopband

out_lo = bq.steps(lo)
bq.reset()
out_hi = bq.steps(hi)

print(f"Biquad passband power:  {np.mean(out_lo**2):.3f}  (expect ≈ 0.5)")
print(f"Biquad stopband power:  {np.mean(out_hi**2):.5f} (expect << 0.5)")

# ── Both types from one import ───────────────────────────────────────────────
print("\nBoth types live in the same module:")
print(f"  {Fir}    — complex I/Q")
print(f"  {Biquad} — real float")
