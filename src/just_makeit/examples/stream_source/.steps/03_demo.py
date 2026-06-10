"""Drive the generated stream() / __iter__ on the ramp source."""

import numpy as np

from stream_source_demo import Ramp

# stream(block, count=k): a source never drains, so `count` bounds it to k
# blocks of `block` samples. Source blocks are freshly allocated each call, so
# collecting them with list() is safe.
ramp = Ramp(value=0.0, step_inc=1.0)
blocks = list(ramp.stream(4, count=3))
print("3 blocks of 4:", [b.tolist() for b in blocks])
assert [b.shape for b in blocks] == [(4,), (4,), (4,)]
assert np.array_equal(np.concatenate(blocks), np.arange(12, dtype=np.float32))

# on_block(b) fires after each block is consumed (post-yield) — the seam for
# pacing, progress, or tee-to-sink. Here it just records each block's sum.
ramp = Ramp(value=0.0, step_inc=1.0)
sums: list[float] = []
for _ in ramp.stream(
    4, count=2, on_block=lambda b: sums.append(float(b.sum()))
):
    pass  # consume the block; the hook runs right after
print("on_block sums:", sums)
assert sums == [6.0, 22.0]  # [0+1+2+3], [4+5+6+7]

# iter(ramp) streams with stream_block_default (256). A source is infinite, so
# break out yourself — here we just take the first block.
ramp = Ramp(value=0.0, step_inc=1.0)
first = next(iter(ramp))
print("__iter__ default block shape:", first.shape)
assert first.shape == (256,) and first[0] == 0.0 and first[1] == 1.0

print("stream_source demo: OK")
