- **`jm bench` takes a run budget, and a timeout costs one benchmark, not
    the run** (gh-1687). Every benchmark run had a hard-coded 600 s, and a
    `bench_<comp>_core` that took longer -- normal on a Cortex-A53-class
    board, where the same binary runs 10-20x slower than on a desktop core --
    raised a traceback that discarded every result already collected, so
    `benchmarks/history/` came out empty. The budget is now
    `[project.bench] timeout = <seconds>` in `just-makeit.toml`, overridden
    for one run by `jm bench --timeout S`; `0` is no limit, and unset keeps
    600 s. A run past it is killed and reported as `timeout`, the other
    benchmarks still run and are saved (the snapshot lists what timed out),
    and `jm bench` then exits 1 naming it; under `--check` it fails the gate.
    The builds `jm bench` drives are no longer timed at all: a cold build on
    the same board took 603 s, and slow is not failed.
