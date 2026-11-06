- **A file that includes `jm_bench.h` or `jm_test.h` and calls nothing
    builds under `-Wall -Wextra -Werror`** (gh-2497). `jm_bench.h` defined
    `jm_bench_write_json`, `jm_dcmp` and `jm_quantile` as plain `static`,
    so a second benchmark source sharing one binary failed
    `-Wunused-function`; doppler hit it splitting a bench's rows across two
    files. They are `static inline` now, like the rest of the header, and
    `jm_test.h`'s `jm_checks` / `jm_fails` counters are marked maybe-unused
    for the same reason. Every header a scaffolded project carries is now
    compiled alone, under gcc and clang, by the test suite.
