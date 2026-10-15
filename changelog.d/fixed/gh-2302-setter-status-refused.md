- **A setter whose status the binding would discard is refused**
    (gh-2302). A writable property with no `error` gets a binding that
    calls its C setter as a bare statement. When the header already
    declared that setter returning `int`, the status was dropped, so an
    assignment C refused succeeded in Python. On a module object nothing
    said so; on a standalone one `apply` rewrote the declaration to
    `void`, out of step with its definition. `jm apply` and
    `jm status --check` now refuse it before writing anything, naming the
    property and the declaration. Declare `error` (gh-2182), or make the
    setter `void`. A project with such setters must do one or the other
    before this release will apply.
