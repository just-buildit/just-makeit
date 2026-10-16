- **An object can decline its Python benchmark** (gh-2316).
    `no_python_bench = true` on an object says it has no
    `bench_<obj>.py`: `jm apply` no longer scaffolds one, and
    `jm status --check` no longer reports a deleted one MISSING. Before
    it, deleting the file was undone by the next `apply`. A file still on
    disk under the key is reported as DECLINED by `status --check`, and
    `apply` warns about it; jm deletes nothing. The C
    `bench_<obj>_core.c` is unaffected; a method's `bench = false` is the
    knob there.
