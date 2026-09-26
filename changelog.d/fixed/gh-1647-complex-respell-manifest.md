- **`jm upgrade`'s `_Complex` respell reaches the bodies in your manifest**
    (gh-1647). An object's `impl` / `create_impl` / `reset_impl` /
    `destroy_impl` body is C that jm renders into the header, and the
    upgrade respelled only the header: the next `jm apply` put
    `float complex` back ("the manifest is the source of truth --
    overwriting the header"), and every later upgrade re-reported the same
    file. The bodies are now respelled in place, code only, through the same
    walker the `c_prefix` respell uses, so upgrade and apply converge.
