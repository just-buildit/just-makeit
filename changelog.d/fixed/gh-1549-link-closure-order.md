- **An object's link lines carry its whole `depends_on` closure, whatever
    order its components are declared in** (gh-1549). `apply` walked the
    closure over the part of the manifest it had replayed so far. So when
    `b` depended on `a`, and `a` was declared after `b` (in a later module,
    or later in the same one), `a`'s own dependencies were missing from
    `b_core`, `test_b_core` and `bench_b_core`, and the C test failed to link
    with `undefined reference`. `status --check` passed, because it sees the
    same replay. Reordering the declarations used to be the workaround.
