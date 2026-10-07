- **`jm add` asks before it writes anything, and keeps the state rows it is
    appending to** (gh-1889, gh-1890). The manifest was saved first and the
    question came after, so answering N, or running with no terminal, exited
    0 with the new `[[<obj>.state]]` row on disk and nothing rebuilt:
    `jm status` reported STALE, and the `jm apply` it advises rewrote
    `create()` against a `_core.c` that was never regenerated, which stopped
    the project compiling. The question now comes first, and declining exits
    1 with every file as it was. The state list was also rebuilt from each
    row's name, type and default alone, so one `jm add` deleted every
    `opaque` field and every `doc`, `no_ctor`, `controllable` and `str_hint`
    on the rows it kept. The rows already there are now kept as written and
    the new ones appended, and a new name that repeats an opaque field's, or
    another new one, is refused before anything is written.
