"""Drive the generated stream() / __iter__ on the finite drainer."""

import numpy as np

from stream_blockwise_demo import Drainer

# stream(8) over total=20 yields blocks of 8, 8, 4, then stops on the empty
# (drained) block. Every block is an independent NumPy-owned array, so they
# can be collected directly -- no copy needed.
d = Drainer(total=20, pos=0)
collected = list(d.stream(8))
print("drained in blocks:", [b.shape[0] for b in collected])
assert [b.shape for b in collected] == [(8,), (8,), (4,)]
assert collected[0].dtype == np.complex64
assert np.array_equal(
    np.concatenate(collected).real, np.arange(20, dtype=np.float32)
)

# count caps the iteration no matter how much source is left.
d = Drainer(total=20, pos=0)
first = next(d.stream(8, count=1))
print("count=1 -> one block of", first.shape[0])
assert first.shape == (8,)

# on_block(b) fires after each consumed block (post-yield).
d = Drainer(total=20, pos=0)
sizes: list[int] = []
for _ in d.stream(8, on_block=lambda b: sizes.append(len(b))):
    pass
print("on_block sizes:", sizes)
assert sizes == [8, 8, 4]

# iter(drainer) uses the default block (1024 >= the 20 remaining), so the whole
# source comes back as one block, then drains.
d = Drainer(total=20, pos=0)
blocks = [b.copy() for b in d]
print("__iter__ blocks:", [b.shape[0] for b in blocks])
assert len(blocks) == 1 and blocks[0].shape == (20,)

print("stream_blockwise demo: OK")
