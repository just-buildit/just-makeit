- **`jm bench` finds, prints and keeps its Python results** (gh-1915,
    gh-1833, gh-1841). Three fixes to the same path:
    - pytest collected only `test_*.py`, so the `bench_*.py` a generated project
        writes was never run and `jm bench` reported `Python benchmarks: none found` with exit 0. Collection now uses doppler's `python_files`, the
        set a bench file needs.
    - `jm bench --check --json` wrote the build's progress, cmake's output and
        each benchmark's banner to stdout ahead of the JSON document, so the
        document did not parse. Everything else now goes to stderr for that run.
    - pytest-benchmark writes its JSON once per session, so one timeout over
        the whole tree discarded every Python result. Each benchmark file is its
        own run under its own budget. A file past it is named in the snapshot's
        `timed_out` marker, as a C binary is.
- The timeit template's `make bench` pointer, which no target ran, is removed.
