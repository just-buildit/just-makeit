- **An object named for another module's cname is refused** (gh-2142).
    `jm module X` refused a name a component already held, but nothing
    asked the other order: `jm object a --module b` beside a module `a`
    exited 0 under `layout = "nested"` and left a manifest `apply` and
    `status` could not replay, and in the flat layout the same command, a
    standalone `jm object filt` beside a module `filt`, or a hand-written
    manifest holding either, stopped only at the occupied-directory check
    with a message about files rather than names. Manifest load and save
    now refuse any component named for a module's cname other than that
    module's own collocated object, and two modules sharing one cname, so
    `jm object`, `jm module`, `apply` and `status --check` all refuse
    with one `error:` line before anything is written.
