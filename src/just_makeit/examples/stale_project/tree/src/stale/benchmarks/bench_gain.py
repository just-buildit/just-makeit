"""Benchmark for Gain.

Run standalone:  python src/stale/benchmarks/bench_gain.py
Or via make:     make bench
"""
import time
import numpy as np

from stale import Gain

REPS      = 1_000
BLOCK_1K  = 1_024
BLOCK_64K = 65_536


def _bench(label: str, fn, *args, reps: int = REPS) -> float:
    for _ in range(max(1, reps // 10)):  # warmup
        fn(*args)
    t0 = time.perf_counter()
    for _ in range(reps):
        fn(*args)
    return (time.perf_counter() - t0) / reps


def main() -> None:
    obj = Gain(1.0)
    print("gain")
    dt = _bench("step", obj.step, 1.0)
    print(f"  {'step':<22} {dt * 1e9:9.1f} ns/call")

    x1k = np.ones(BLOCK_1K, dtype=np.float32)
    dt = _bench("steps 1k", obj.steps, x1k, reps=max(1, REPS // 10))
    print(f"  {'steps 1k':<22} {dt * 1e6:9.3f} µs  ({BLOCK_1K / dt / 1e6:.1f} MSa/s)")
    x64k = np.ones(BLOCK_64K, dtype=np.float32)
    dt = _bench("steps 64k", obj.steps, x64k, reps=max(1, REPS // 100))
    print(f"  {'steps 64k':<22} {dt * 1e3:9.3f} ms  ({BLOCK_64K / dt / 1e6:.1f} MSa/s)")


if __name__ == "__main__":
    main()
