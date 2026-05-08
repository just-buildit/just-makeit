"""Demonstrate the pure FIR filter — caller owns the params."""
import numpy as np
from my_fir_pure import FirPure

# Create params — heap-allocated, reference-counted via Python GC
fir = FirPure()

# Load sinc coefficients (16-tap low-pass, cutoff ≈ 0.1 * fs)
import math
n = 16
coeffs = np.array(
    [math.sin(math.pi * 0.1 * (k - n // 2)) / (math.pi * (k - n // 2))
     if k != n // 2 else 0.1
     for k in range(n)],
    dtype=np.float32,
)
coeffs /= coeffs.sum()
fir.set_coeffs(coeffs)

# Process a 1 kHz tone at fs=8 kHz (should pass; above cutoff ≈ 800 Hz gets attenuated)
t = np.arange(1024) / 8000.0
signal = np.exp(1j * 2 * math.pi * 200.0 * t).astype(np.complex64)
out = fir.steps(signal)
print(f"in  power: {np.mean(np.abs(signal)**2):.3f}")
print(f"out power: {np.mean(np.abs(out)**2):.3f}")

# Two independent channels sharing the same algorithm — no cross-contamination
fir2 = FirPure()
fir2.set_coeffs(coeffs)
out2 = fir2.steps(signal * 2)
print(f"\nchannel 2 power (2× input): {np.mean(np.abs(out2)**2):.3f}")

# Context-manager usage (explicit resource release)
with FirPure() as f:
    f.set_coeffs(coeffs)
    y = f(signal[0])
    print(f"\ncontext-manager single sample: {y}")
