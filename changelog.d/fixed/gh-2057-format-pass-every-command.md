- **A project that opts into `c_style` is formatted after every command
    that changes it, so `jm record` no longer leaves its glue STALE**
    (gh-2057). The post-command pass ran after a hand-kept list of the
    commands that emitted C, and the list had lost `jm record`, which
    re-renders the binding when it re-declares an element a member speaks
    (gh-2055): on a `c_style` project the re-rendered glue stayed in jm's
    own style while `jm apply` wrote the project's, and `jm status --check`
    reported it STALE on a tree nobody had touched. `jm app`, `jm config`,
    `jm ci` and `jm migrate-to-fragments` were missing from the list as
    well. It is now read from one classification of every command, which
    also feeds a test that holds every command that changes a project to
    the tree `jm apply` writes.
