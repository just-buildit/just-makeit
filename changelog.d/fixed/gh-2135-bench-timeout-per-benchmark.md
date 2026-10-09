- **`jm bench`'s timeout costs only the slow benchmark, not its whole file**
    (gh-2135). A benchmark file past its budget lost every result in it, the
    fast benchmarks too, because pytest-benchmark writes its JSON once per run. A
    file that times out is now run again one benchmark at a time, each under the
    same budget. The fast ones keep their results, and the slow one is named in
    `timed_out` as `pytest <file>::<benchmark>`. A file that does not time out is
    still one run, so the common path costs nothing; a file that does costs up to
    a second budget.
