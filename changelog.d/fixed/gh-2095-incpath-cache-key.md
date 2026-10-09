- **The cached project manifest is reloaded when jm writes a fragment, not
    only when `just-makeit.toml` changes** (gh-2095). jm caches the merged
    manifest it reads per include spelled and per symbol stemmed, and the
    cache was keyed on the central manifest alone, while the merge also
    reads `objects/*.toml` and `modules/*.toml`. In one process, a command
    that wrote only a fragment (`jm object` or `jm method` on a split
    layout) left every later reader on the manifest from before the write:
    the new component was not a component, and the new method was not
    there for the doc reader. Commands run one per process, so this bit
    the in-process test harness rather than the CLI. The key now also
    counts the TOML files jm has written, so a lookup still costs one
    `stat` and a dict hit. Inside a deferred save the cache serves the
    pending write, as the loader does.
