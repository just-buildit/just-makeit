"""Round-trip demo: cf32 -> q15 file -> cf32, verify fidelity."""
import os
import sys
import tempfile
import numpy as np

# cwd is the project root when called from test.py; cmake builds the .so into src/.
sys.path.insert(0, "src")

from iqfile.conv import Cf32ToQ15, Q15ToCf32

N = 4096
rng = np.random.default_rng(42)

# ── Generate test signal (normalised to [-0.9, 0.9] to avoid clipping) ────
signal = (rng.standard_normal(N) + 1j * rng.standard_normal(N)).astype(np.complex64)
signal *= 0.9 / np.max(np.abs(signal))

# ── Write cf32 -> q15 ─────────────────────────────────────────────────────
writer = Cf32ToQ15()
packed = writer.steps(signal)                   # int32 array, shape (N,)
q15    = packed.view(np.int16)                  # int16 view, shape (2N,)

with tempfile.NamedTemporaryFile(suffix=".q15", delete=False) as f:
    q15_path = f.name
    q15.tofile(f)

print(f"wrote    {N} complex samples -> {q15_path}  ({os.path.getsize(q15_path)} bytes)")
print(f"written: {writer.samples_written} samples")

# ── Read q15 -> cf32 ──────────────────────────────────────────────────────
fd = os.open(q15_path, os.O_RDONLY)
reader = Q15ToCf32(fd=fd)
recovered = reader.steps(N)
os.close(fd)

print(f"read:    {reader.samples_read} samples,  eof={reader.eof}")

# ── Verify round-trip fidelity ────────────────────────────────────────────
scale = 32767.0
quantisation_noise_floor = 1.0 / scale          # ≈ -90 dB
err = np.max(np.abs(signal - recovered))

print(f"max err: {err:.6f}  (floor ~{quantisation_noise_floor:.6f})")
assert err < 2.0 / scale, f"round-trip error too large: {err}"
print("PASSED")

os.unlink(q15_path)
