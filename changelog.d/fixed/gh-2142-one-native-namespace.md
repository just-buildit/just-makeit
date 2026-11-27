- **An object named for another module's cname is refused** (gh-2142).
    `jm module X` refused a name a component already held, but nothing
    asked the other order: `jm object a --module b` beside a module `a`
    exited 0 under `layout = "nested"` and left a manifest `apply` and
    `status` could not replay, and in the flat layout the same command, a
    standalone `jm object filt` beside a module `filt`, or a hand-written
    manifest holding either, stopped only at the occupied-directory check
    with a message about files rather than names. Manifest load and save
    now refuse two modules, two components, or a component and a module
    whose C symbol stem is one -- other than a module and its own
    collocated object -- so `jm object`, `jm module`, `apply` and
    `status --check` all refuse with one `error:` line naming both and
    the stem, before anything is written. The stem is `_csym.stem`'s, so
    under `c_prefix = "p"` the names `filt` and `p_filt` clash too, in
    any order of `jm object` and `jm module`; before, only `apply` and
    `status` caught that, as `P_FILT_CORE_H` derived twice.
- **Recovering a project that already holds such a clash**, which every
    command now refuses at load, `jm remove` included. When the two names
    differ (`filt` and `p_filt`), comment out `c_prefix` in
    `just-makeit.toml`, run `just-makeit remove object p_filt -f` (or
    `remove module`), and restore the line. When they are one name (a
    nested `a` in module `b` beside module `a`), delete the object's
    `[a]` table or `objects/a.toml`, its entry in `[module.b] objects`,
    and the files jm wrote for it (`native/src/b/a/`,
    `native/inc/<pkg>/b/a/`, `native/tests/b/a/`, `native/benchmarks/b/a/`,
    `native/src/b/b/b_ext_a.c`, `src/<pkg>/b/tests/test_a.py`), then run
    `just-makeit apply`. Either way, re-add the object under a new name.
