- **`jm upgrade` respells the C your manifest holds, and a `JM_DEFINE_STEPS`
    stem** (gh-1653). Moving onto a `c_prefix` left three kinds of author C
    spelling the old names, and each broke the build the next time jm
    rendered from the manifest: an `*_impl` body or a `type` naming a
    sibling's derived type (`lo_state_t *`), an `*_impl_file`'s `::fn` in a
    file the upgrade had just renamed it in (`apply` could no longer find the
    body), and `JM_DEFINE_STEPS (fir, ...)`, whose bare stem is not itself a
    derived name, together with the `fir_step_batch` it pastes. Each is now
    respelled in place with the same map and code-only matcher as the C
    files. The file stem, author-named keys (`fn`, `create_fn`), comments
    and macros are untouched, and `apply`'s existing-tree refusal names the
    same strings. `replace = {}` tables are not yet covered (gh-1656).
