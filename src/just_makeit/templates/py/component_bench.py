"""Benchmark for <<Component>>.

Run standalone:  python src/<<package>>/benchmarks/bench_<<component>>.py
Or via make:     make bench
"""
import time
import numpy as np

from <<package>> import <<Component>>

REPS      = 1_000
<<bench_block_consts>>


def _bench(label: str, fn, *args, reps: int = REPS) -> float:
    for _ in range(max(1, reps // 10)):  # warmup
        fn(*args)
    t0 = time.perf_counter()
    for _ in range(reps):
        fn(*args)
    return (time.perf_counter() - t0) / reps


def main() -> None:
    obj = <<Component>>(<<py_create_args>>)
    print("<<component>>")
<<bench_step_py>>
<<bench_steps_py>>

if __name__ == "__main__":
    main()
