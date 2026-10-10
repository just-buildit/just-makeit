- **A C benchmark's JSON holds every row it recorded, past 32** (gh-2188).
    `jm_bench.h` kept its rows in a fixed array of 32, and `jm_bench_add`
    dropped every row past it without a word: the benchmark printed every
    row, exited 0, and wrote a JSON missing the rest, so `jm bench` and
    `bench-compare` saw fewer rows than were measured. The rows are now a heap
    array that grows, so there is no cap, and `JM_BENCH_MAX_ENTRIES` is gone.
    An allocation the header cannot make now exits non-zero with a message on
    stderr, rather than dropping that row. `jm_bench.h` is create-only: an
    existing project picks the fix up by re-vendoring it (`jm status` reports
    it outdated).
