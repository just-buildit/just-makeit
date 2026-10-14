- **Every C benchmark row states what it counts** (gh-2282).
    `jm_bench_add_unit(&_bench, name, times, rounds, iters, unit)` records a
    row of `iters` samples, calls or frames per round (`JM_BENCH_SAMPLE`,
    `JM_BENCH_CALL`, `JM_BENCH_FRAME`), from one `JM_BENCH_UNITS` table in
    `jm_bench.h`. The five-argument `jm_bench_add` still compiles and records
    calls, which is what `iters` was documented to count. The JSON keeps every
    field it had and adds `extra_info.unit` and `rate_unit`, `MSa_s` on a
    sample row, and `stats.<stat>_per_unit` beside each per-round `min`,
    `max`, `mean`, `stddev` and `median`. Before, `extra_info` was a literal
    `{}`, the header called `ops` samples per second while documenting
    `iters` as calls, and a reader taking `mean` for a time per call was off
    by a factor of `iters`. `jm bench`'s table now has a `Per` column and
    shows each row's throughput in its own unit (`MSa/s`, `Mcalls/s`). It
    shows a pytest-benchmark row per call, and a row from an older
    `jm_bench.h` per round in `ops/s`, where it used to label every row
    `Sa/s`. Generated benchmarks name the unit they time: `step()`,
    `steps()` and a method over an input are samples, a method with no input
    is calls. A no-input records method, timed one call a round, had claimed
    `BENCH_N` of them and reported a rate `BENCH_N` times too high. A
    benchmark jm generates now calls `jm_bench_add_unit`, so an existing
    project adopts the new `jm_bench.h` (`jm status` reports it OUTDATED)
    before it builds one.
