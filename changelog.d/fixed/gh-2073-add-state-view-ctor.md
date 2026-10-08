- **`jm add --state` and `jm remove state` on an object with a view rebuild
    the view's binding too, so the module compiles and `jm status --check`
    passes** (gh-2073). A view without its own `init_params` takes its
    parent's constructor keywords, and the rebuild these commands run
    re-declares the view's `create_fn` at the new arity. It deleted and
    re-rendered the object's own binding fragment (gh-965) but not the
    view's, so the view's `__init__` kept the old `kwlist` and called its
    `create_fn` with the old arguments: `status` reported KWARGS drift that
    `jm apply` could not fix and the build failed with "too few arguments".
    Removing the object's last state field failed the build too, with
    nothing reported: the view still passed the field and still bound its
    accessors. `jm regenerate --discard` now rebuilds every binding fragment
    over the object's core, the object's and each view's; a view's
    hand-written `_extra.c` is left alone.
