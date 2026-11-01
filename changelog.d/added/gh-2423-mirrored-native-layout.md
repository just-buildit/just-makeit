- **`[project] layout = "mirrored"` puts each component's C test and C
    benchmark in a directory of its own** (gh-2423). jm wrote every
    `test_<c>_core.c`, `test_<c>_symbols.c` and `bench_<c>_core.c` flat in
    `native/tests/` and `native/benchmarks/`, found only by file-name prefix,
    while the header beside them lived at `native/inc/<pkg>/<c>/`. With the
    key, they go to `native/tests/<c>/` and `native/benchmarks/<c>/`: the
    directory the component's header has, asked of one primitive
    (`_incpath.native_rel`) by every writer, the build templates, `remove`,
    `status`'s hollow-target scan, the ownership rules and `upgrade`. The
    harness headers stay at the tree roots and the C test's include path
    gains `native/tests`. Setting the key on an existing project and running
    `jm apply` moves the files with your edits (moving them yourself first
    works the same), respells any build-file path to a moved one, and
    removing the key moves them back; an unknown value is refused. `flat`
    stays the default and renders byte-identically to before.
