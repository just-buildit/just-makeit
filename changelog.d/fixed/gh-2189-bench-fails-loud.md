- **`jm bench` fails when a benchmark run fails** (gh-2189). A
    `bench_<comp>_core`'s exit status was never read, and a missing JSON was
    skipped without a word, so a benchmark that crashed, or one that refused to
    write a short set, was simply absent from a run that exited 0. A non-zero
    exit, a death by signal, and an exit 0 with no JSON or with a JSON that does
    not parse are each now reported as `failed bench_<comp>_core (<why>)`, with
    the binary's stderr quoted beneath. A target that built but whose binary
    cannot be found is reported the same way, where it was skipped. The Python
    side had the same hole: pytest's exit status was never read, so a benchmark
    that raised dropped out of the snapshot. A `pytest` run that exits other
    than 0 or 5 (nothing collected) is now `failed pytest <file> (exit N)`. As
    with a timeout, the other benchmarks still run and are saved, the snapshot
    lists the failure under `"failed"`, and `jm bench` then exits 1 naming it.
    `--check` fails on it too, and its JSON document lists it under `"failed"`.
    A JSON a failed run did write is not read.
