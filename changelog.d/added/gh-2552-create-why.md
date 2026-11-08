- **An object's or a view's constructor can name its refusal** (gh-2552).
    `create_error` / `create_error_message` raised one message for every way
    a `create()` could refuse. `create_why = true` on `[<obj>]` or on a
    `[[<obj>.views]]` entry is gh-1706's `why` on the constructor: the C
    `create()` (or `create_fn`) takes a trailing `const char **why` after
    every init param, the binding passes the address of a local initialised
    to `NULL`, and a refusal raises `create_error` with the sentence the
    constructor wrote, or `create_error_message` when it wrote none. The
    parameter is C only -- not in the Python signature or the `.pyi` -- and
    the sacred header's prototype, the `_core.c` stub, a `header_only`
    inline create, the C test and the bench all carry it; a generated C app
    prints the sentence. A view states its
    own (its `create_fn` is a different function) and still inherits the
    parent's `create_error`. Refused at load: a `create_why` with no
    `create_error` in force, one that is not `true`/`false`, and one beside
    an init param named `why`, grouped, inherited by a view, or its own; `jm remove error` is refused
    while a `create_why` raises through the translation. The `const char **why` parameter and the
    `&_why` argument are now one constant each, shared by module functions,
    composers, objects and views.
    `jm status`'s `CTOR` check (gh-1076) now compares every view's
    `create_fn` against the header too, and names the constructor it
    compared: a drifted view prototype broke the build with `status --check`
    exiting 0.
