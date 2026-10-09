- **`jm bench`'s timeout costs only the slow benchmark, not its whole file**
    (gh-2135). A benchmark file past its budget lost every result in it, the
    fast benchmarks too, because pytest-benchmark writes its JSON once per run. A
    file that times out is now run again one benchmark at a time, each under the
    same budget. The fast ones keep their results, and the slow one is named in
    `timed_out` as `pytest <file>::<benchmark>`. A file that cannot be listed is
    named as `pytest <file>`. A file that does not time out is still one run, so the
    common path costs nothing. A file that does costs one budget for the run, then
    one for each of its k slow benchmarks: 1 + k budgets. The whole-tree listing is
    budgeted too, and a file whose import outlasts the budget costs three budgets
    before it is named.
