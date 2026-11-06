- **`jm status` reports a benchmark target `jm bench` never runs**
    (gh-2494). `jm bench` runs only `bench_<s>_core` for each benchmark it
    knows of, so a target over a benchmark source under any other name was
    built on every build and run by nothing, and `jm status --check` said
    clean: its source is compiled, so it was no UNBUILT orphan. doppler
    lost all ten rows of `bench_correlator_1d`, defined in a hand-owned
    `*_extra.cmake`, that way. Now any `add_executable` named `bench_*`, or
    over a file under `native/benchmarks/`, that `jm bench` does not run is
    listed under UNRUN (`unrun_benches` in `--json`) and fails `--check`.
    Only the project's own build files are read: not a hidden directory's,
    nor a nested project's, which its own `jm bench` runs.
    One run deliberately some other way is exempted by the
    `status_allow` entry `<build file>:<target>`, for example
    `native/src/stream/CMakeLists.txt:bench_stream`.
