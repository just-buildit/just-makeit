- **`jm upgrade` respells a C `default`, and refuses an author-named key it
    cannot** (gh-1671). A state field's or scalar parameter's `default` is
    C spliced into the create/reset body or a C local, so
    `default = "sizeof(lo_state_t)"` went stale under a new `c_prefix`; it
    is now respelled and named by `apply`'s refusal, while an enum entry's
    `default` (and an enum spec `type`) -- a choice string -- is left
    alone. An init-param's `default_raw` is respelled too. A key that names
    a function you wrote (`create_fn`, `close_fn`, `fn`, ...) is still
    never respelled, but when it names a symbol the prefix renames --
    a handle's `create_fn = "lo_create"` over component `lo` -- `apply`
    and `jm upgrade` now refuse before writing, naming the file, the key
    and the new spelling.
