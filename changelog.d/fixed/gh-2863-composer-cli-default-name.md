- **A composer CLI with no `name` builds (gh-2863).** `[module.X.cli]`'s
    program was named after the module by default, which is the module's
    own extension target, so cmake refused to configure every CLI declared
    without a `name` (CMP0002). The default is now `<cname>_cli`, the stem
    of the `<cname>_cli.c` it builds, the CLI is counted among the targets
    jm emits, and a `name` jm already emits as a target is refused naming
    it.
