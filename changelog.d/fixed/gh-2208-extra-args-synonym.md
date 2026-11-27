- **A method's `extra_args` reaches every face, not only the replay**
    (gh-2208). `extra_args` is the manifest's synonym for `params`, and
    only `apply`'s replay translated it. Documenting the method in the
    sacred header (or declaring a state-only `*_max_out`) sends `apply`
    into a second render that read `params` alone, so the binding called
    the kernel without the argument and the project stopped compiling; the
    `.pyi` dropped it, `jm status --check` agreed, and `jm script`
    reconstructed the method without it. `load` now reads `extra_args` as
    `params` once, for every reader, and a save writes the row back as the
    author spelled it. A row declaring both `params` and `extra_args` is
    refused, naming it: each reader used to pick one, not all the same one.
