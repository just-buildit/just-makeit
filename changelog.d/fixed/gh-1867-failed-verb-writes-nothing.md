- **A command that changes the project and then fails leaves the project as
    it was** (gh-1867, gh-2040). `jm regenerate` deleted every file the
    component owned and only then ran `apply`, so a refusal in that `apply`
    lost the component, your edits in `_core.c` included. The refusal could
    be about another component's manifest row. `jm method` and
    `jm property` saved `_core.c` and the manifest and only then rendered
    the binding, so a render that refused left both changed and the binding
    not, and `jm status` then reported drift you did not make. Now every
    command that changes the project records the tree before it runs. If it
    does not succeed (a refusal, an exit, a crash, Ctrl-C), jm puts back
    every file it had written or deleted and says how many. That widens
    the undo `jm apply <fragment.toml>` already had for the fragment it
    composes (gh-1660). It also covers the writes `apply`'s reconcile made
    before its own stub checks refused (gh-1676), which the message already
    said were not written. `jm regenerate` also refuses an `impl_file` that
    names a file inside the component before deleting anything. Before, it
    deleted that file and then reported it "not found".
