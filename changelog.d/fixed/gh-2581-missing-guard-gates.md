- **`jm status --check` fails when a binding fragment lacks a safety guard
    jm now emits** (gh-2581). A module object's `<mod>_ext_<obj>.c` is
    sacred, so a guard added to a wrapper after the fragment was rendered
    never arrives. `apply` named it in an advisory warning, and
    `status --check` exited 0. doppler carried two capture fragments without the
    output-size checks that way. Each marker `apply` reads in a member's
    body is now classified once, as safety or advisory, in one table both
    commands read. A missing safety guard is reported as `GUARD`: the
    fragment, the member, each missing guard and what goes wrong without
    it. It is in `--json` as `missing_guards`, fails `--check`, and cannot
    be allowed. Safety guards are the output-size and returned-count
    checks, declared refusals (`error_sentinel`, `error_negative`,
    `status_return`, `status_errors` rows, `strict`), the `out=` contiguity
    test, the integer range test, the inflight count and teardown wait of
    a GIL-free call, retained and required capsules, a borrow release, a
    deferred free, the byte-array converter and `str_hint`, and a records
    method's capacity and consumed-input loop. A calling convention, a
    result's Python shape and `nogil` itself stay advisory. `apply`'s
    warning is now marked `!` when it names a guard. **On upgrade this can
    turn a downstream drift gate red: each finding is a guard the binding
    does not have.** Re-render the fragment (`jm adopt`, or delete it and
    re-run `jm apply`), or write the guard in jm's own form, which `GUARD`
    shows; an equivalent spelled another way is not recognised yet
    (gh-2589).
