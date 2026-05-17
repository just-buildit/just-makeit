"""Measure AccF32.steps() and AccCf64.steps() throughput (samples/sec)."""
import sys
import timeit

import numpy as np

sys.path.insert(0, "src")
from my_acc.accumulator import AccCf64, AccF32

BLOCK = 100_000
RUNS = 1_000

f = AccF32()
sig_f32 = np.random.randn(BLOCK).astype(np.float32)
elapsed = min(timeit.repeat(lambda: f.steps(sig_f32), number=RUNS, repeat=5))
print(
    f"AccF32  {BLOCK:>7,} samples  "
    f"{RUNS * BLOCK / elapsed / 1e9:.2f} G samples/sec"
)

c = AccCf64()
sig_c128 = (np.random.randn(BLOCK) + 1j * np.random.randn(BLOCK)).astype(
    np.complex128
)
elapsed = min(timeit.repeat(lambda: c.steps(sig_c128), number=RUNS, repeat=5))
print(
    f"AccCf64 {BLOCK:>7,} samples  "
    f"{RUNS * BLOCK / elapsed / 1e9:.2f} G samples/sec"
)
