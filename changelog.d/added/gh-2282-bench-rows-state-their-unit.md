- **Every C benchmark row states what it counts** (gh-2282).
    `jm_bench_add_unit(&_bench, name, times, rounds, iters, unit)` records a
    row of `iters` units per round. The unit is a word: `jm_bench.h` defines
    the ones jm's generated benchmarks use (`JM_BENCH_SAMPLE`,
    `JM_BENCH_CALL`, `JM_BENCH_FRAME`), and a project passes its own
    (`"bin"`, `"tap"`) without editing the vendored header. A unit the JSON
    could not carry as written stops the run, naming the row. The
    five-argument `jm_bench_add` still compiles and records calls, which is
    what `iters` was documented to count. The JSON keeps every field it had
    and adds `extra_info.unit` and `rate_unit` (`Sa/s` for a sample,
    `<unit>s/s` otherwise, from one `jm_bench_rate_label`), `MSa_s` on a
    sample row, and `stats.<stat>_per_unit` beside each per-round `min`,
    `max`, `mean`, `stddev` and `median`. Before, `extra_info` was a literal
    `{}`, the header called `ops` samples per second while documenting
    `iters` as calls, and a reader taking `mean` for a time per call was off
    by a factor of `iters`. `jm bench`'s table now has a `Per` column and
    shows each row's throughput in the unit its JSON states (`MSa/s`,
    `Mcalls/s`). It shows a pytest-benchmark row per call, and a row from an
    older `jm_bench.h` per round in `ops/s`, where it used to label every
    row `Sa/s`. **Adopting the header changes what an unchanged call
    means:** a five-argument `jm_bench_add` row reads per round in `ops/s`
    against an older `jm_bench.h` and per call against this one, so move
    every row that counts samples (or bins, taps) to `jm_bench_add_unit` in
    the same change as the re-vendor. Generated benchmarks name the unit
    they time: `step()`, `steps()` and a method over an input are samples, a
    method with no input is calls. A no-input records method, timed one call
    a round, had claimed `BENCH_N` of them and reported a rate `BENCH_N`
    times too high. A benchmark jm generates now calls `jm_bench_add_unit`,
    so an existing project adopts the new `jm_bench.h` (`jm status` reports
    it OUTDATED) before it builds one.
