- **A new project's `_core` stubs build clean under `-Wall -Wextra`**
    (gh-1857). A scaffolded constructor or `reset()` left the parameters its
    placeholder body does not read unsuppressed, so a project building its C
    with `-Werror` failed before a line of it was written: `state` in an
    empty `reset()`, every parameter of a `--no-state` or `init_params`
    constructor, an `--array-arg` beside the fields `create()` assigns, the
    dtype-dispatch and optional-array constructors, and a `jm view`
    constructor. Each stub now says `(void)name;` for exactly the parameters
    it leaves to the author, as method stubs always did, and the author's
    body replaces the line with the placeholder. Under `--header-only` the
    `create()` body is on its own line again rather than glued to
    `return obj;`. Existing `_core` files are the author's and are not
    rewritten. The warning sweep drops its last `-Wno-error` flag.
