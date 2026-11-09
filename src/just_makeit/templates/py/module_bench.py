"""Benchmark for <<Component>>.

<<bench_run>>
Or via make:     make bench
"""

import time

import numpy as np

<<class_import>>

REPS = 1_000
<<bench_block_consts>>


def _bench(label: str, fn, *args, reps: int = REPS) -> float:
    for _ in range(max(1, reps // 10)):  # warmup
        fn(*args)
    t0 = time.perf_counter()
    for _ in range(reps):
        fn(*args)
    return (time.perf_counter() - t0) / reps


def main() -> None:
<<py_new_obj_4>>
    print("<<component>>")
<<bench_step_py>>
<<bench_steps_py>>

if __name__ == "__main__":
    main()
