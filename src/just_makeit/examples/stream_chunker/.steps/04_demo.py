"""Integration test: feed irregular bursts into Chunker, verify chunk output.

Run from the project root after building:
    python3 .steps/04_demo.py

Constraint: with chunk_size=64 and an internal buf[256], the output buffer
pre-allocated by --variable-output holds 256 samples (4 complete chunks).
Callers must not push more samples in one call than the output buffer can hold:
  max safe push = floor(256 / chunk_size) * chunk_size - current_n_buf
For chunk_size=64 and worst-case n_buf=63: max push ≈ 192 samples.
"""

import sys
import pathlib

# Add the built extension to sys.path
build_dir = pathlib.Path("build")
for p in build_dir.rglob("my_chunker*.so"):
    sys.path.insert(0, str(p.parent))
    break
sys.path.insert(0, str(pathlib.Path("src")))

import numpy as np
from my_chunker import Chunker

CHUNK = 64

c = Chunker(chunk_size=CHUNK)

# Irregular bursts that collectively push 281 samples.
# Max single burst = 180; worst-case output = 3 chunks = 192 samples < 256.
bursts = [7, 50, 1, 40, 180, 3]  # sum = 281
# Expected: floor(281 / 64) = 4 complete chunks (256 samples), 25 buffered.

collected = []  # copies of complete-chunk views
total_in = 0

for size in bursts:
    block = np.ones(size, dtype=np.complex64) * complex(total_in, 0)
    view = c.push(block)
    # view is a zero-copy slice of the object's internal output buffer.
    # It becomes stale on the next push() call — copy immediately.
    if len(view):
        assert len(view) % CHUNK == 0, (
            f"output length {len(view)} is not a multiple of chunk_size {CHUNK}"
        )
        collected.append(view.copy())
    total_in += size

total_out = sum(len(v) for v in collected)
assert total_out == 4 * CHUNK, (
    f"expected {4 * CHUNK} output samples, got {total_out}"
)

# Verify that the first burst (7 samples) produced no output
assert len(collected[0]) >= CHUNK, "first non-empty view should be ≥ one chunk"

# reset() clears the accumulator; next push starts fresh
c.reset()
view = c.push(np.zeros(CHUNK, dtype=np.complex64))
assert len(view) == CHUNK, "after reset: one full chunk in → one chunk out"

print("stream_chunker demo: PASSED")
print(f"  fed {total_in} samples in {len(bursts)} irregular bursts")
print(
    f"  received {total_out} output samples ({total_out // CHUNK} complete "
    f"{CHUNK}-sample chunks)"
)
print(f"  {total_in - total_out} samples remain buffered (flushed on reset)")
print(
    f"  {len(collected)} non-empty push() calls (some bursts produced 0 output)"
)
