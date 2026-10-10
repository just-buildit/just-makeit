- **A C benchmark's JSON holds every row it recorded, past 32** (gh-2188).
    `jm_bench.h` kept its rows in a fixed array of 32, and `jm_bench_add`
    dropped every row past it without a word: the benchmark printed every
    row, exited 0, and wrote a JSON missing the rest, so `jm bench` and
    `bench-compare` saw fewer rows than were measured. The rows are now a heap
    array that grows, so there is no cap, and `JM_BENCH_MAX_ENTRIES` is gone.
    A row the header cannot hold (an allocation that fails) or one with no
    time in it (fewer than 1 round, whose statistics were read out of an
    empty array) now stops the run with a message on stderr and a non-zero
    exit, rather than being dropped or written as garbage.
    `jm_bench_write_json` now frees the rows once the file is written, so a
    benchmark built with `-fsanitize=address` exits with nothing leaked. It
    takes a non-const `jm_bench_t *`, and the bench is empty afterwards.
    `jm_bench_free` does the same for a benchmark that returns without
    writing. `jm_bench.h` is create-only: an existing project picks the fix
    up by re-vendoring it (`jm status` reports it outdated).
