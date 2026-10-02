- **A benchmark that records through its own helper is no longer reported
    as `SILENT`** (gh-1691). `jm status` / `jm apply` called a benchmark
    silent when no line of it opened with `jm_bench_add(`, so a project
    recording through a helper that wraps it -- doppler's
    `dp_bench_record(&_bench, ...)` -- was told every such benchmark
    "measures nothing" while it wrote 4-7 entries. The source scan now looks
    only at the `jm_bench_t` accumulator handed to `jm_bench_write_json`:
    a benchmark is silent when that accumulator is declared in the file and
    touched by nothing else. Passing it to any call, in the file or a
    header, keeps the scan quiet, since jm cannot see where that code ends.
    And `jm bench`, which has the JSON, now names a binary that ran and
    recorded nothing as `silent`, whatever its source looks like.
