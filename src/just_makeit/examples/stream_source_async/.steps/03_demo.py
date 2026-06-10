"""Drive the generated async stream() / __aiter__ on the ramp source."""

import asyncio

import numpy as np

from stream_source_async_demo import Ramp


async def main() -> None:
    # async for over stream(block, count=k): same semantics as the sync form,
    # but each producer step runs in the event loop's default executor, so a
    # nogil producer would let other tasks run while the kernel works.
    ramp = Ramp(value=0.0, step_inc=1.0)
    blocks = []
    async for b in ramp.stream(4, count=3):
        blocks.append(b.copy())
    print("3 blocks of 4:", [b.tolist() for b in blocks])
    assert [b.shape for b in blocks] == [(4,), (4,), (4,)]
    assert np.array_equal(
        np.concatenate(blocks), np.arange(12, dtype=np.float32)
    )

    # on_block(b) fires after each block is consumed (post-yield) — the seam
    # for pacing, progress, or tee-to-sink. It stays a plain (sync) callable.
    ramp = Ramp(value=0.0, step_inc=1.0)
    sums: list[float] = []
    async for _ in ramp.stream(
        4, count=2, on_block=lambda b: sums.append(float(b.sum()))
    ):
        pass  # consume the block; the hook runs right after
    print("on_block sums:", sums)
    assert sums == [6.0, 22.0]  # [0+1+2+3], [4+5+6+7]

    # `async for blk in obj` uses stream_block_default (256). A source is
    # infinite, so break out yourself — here we just take the first block.
    ramp = Ramp(value=0.0, step_inc=1.0)
    async for first in ramp:
        print("async for obj, default block:", first.shape)
        assert first.shape == (256,)
        assert first[0] == 0.0 and first[1] == 1.0
        break

    # The sync iterator is still there on the very same object.
    ramp = Ramp(value=0.0, step_inc=1.0)
    assert [b.shape for b in ramp.stream(5, count=2)] == [(5,), (5,)]
    print("sync stream still works")


asyncio.run(main())
print("stream_source_async demo: OK")
