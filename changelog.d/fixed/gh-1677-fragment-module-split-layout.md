- **`jm apply <fragment>` with `module = "X"` routes into the module wherever
    it is declared** (gh-1677). The check read the central manifest alone, so
    on a split-layout project -- `jm new`'s default, where `[module.X]` lives
    in `modules/X.toml` -- it refused a module the project has
    (`Defined modules: ['(none)']`). And the wiring was a text edit of that
    one file that never matched a dotted id's `[module."dsp.filters"]`, so on
    a single-manifest project the object was silently left unwired and built
    as a standalone extension, with exit 0. Both now go through the reader
    and writer every other command uses: the merged manifest is checked, and
    the object is added to `[module.X].objects` in the file that declares
    it. A refusal after composing restores that file too.
