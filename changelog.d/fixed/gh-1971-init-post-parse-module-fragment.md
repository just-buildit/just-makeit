- **`init_post_parse` reaches the fragment of an object in a module**
    (gh-1971). The key is the manifest's way to write a dynamic default (a C
    snippet injected after `PyArg_ParseTupleAndKeywords`) and was accepted
    and then ignored for any object with per-object `<mod>_ext_<obj>.c`
    fragments, with no diagnostic. Two places dropped it: the fragment's
    context never passed it on, and the replay's temp manifest, which the
    module render reads, had no way to persist it (the gh-1172 shape).
    `jm adopt` made this dangerous: a fragment carrying the snippet by hand
    showed as a `differs` unit, and `--accept` deleted the default the
    manifest line was written to carry. `status --check` could not see it,
    since it renders through the same path. A standalone object rendered by
    delete-and-`apply` was also seen to lose the key; that path is not
    changed here.
