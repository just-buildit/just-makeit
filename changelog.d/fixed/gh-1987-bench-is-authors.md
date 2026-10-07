- **`jm method` and `jm remove` leave your C benchmark alone** (gh-1987).
    `native/benchmarks/bench_<obj>_core.c` is yours: `apply` never rewrites
    it, and neither do `jm property`, `jm warning` or `jm error`. But on a
    standalone object `jm method`, and `jm remove` of any member (method,
    property, warning, error), re-rendered it from scratch, discarding
    whatever you had written there. They no longer touch it, so a new method
    is not timed until you add it, and a removed one is not taken out. When
    the benchmark still calls the method `jm remove method` took away, a
    second note says so beside "delete it by hand", since deleting the body
    is what makes that benchmark stop linking. On a standalone object, the
    benchmark `jm apply` writes when the file is missing still times every
    method the manifest declares.
