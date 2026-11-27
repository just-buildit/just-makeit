- **A composer source names a whole-struct validator: `check_fn`
    (gh-2856).** The constructor, `from_json` and the record assigned
    source fields directly and called no project code, so doppler's
    `Synth(type="ofdm", constellation="bpsk")` built half a rate pair that
    only the first `steps()` refused. `[module.X.source] check_fn = "<fn>"`
    names `const char *fn(const <source.struct> *)`, declared in
    `<cname>_bridge.h` beside the other seams: `NULL` is whole, a sentence
    is raised as `ValueError`. Every face that writes fields without a
    field's `set_fn` calls it through one emitter: the source constructor
    (so a factory and an inline `Segment`), each property setter, the
    generic JSON record, a delegated `from_json_fn` / `from_file_fn` (each
    source of the state it built) and the c-face CLI (exit 2). A setter
    writes to a copy and commits only when the check passes, so a refused
    assignment leaves the source as it was; a field with `set_fn` calls
    only `set_fn`. A `check_fn` that is not a string, or that sits on any
    composer table but `[module.X.source]`, is refused when the manifest
    loads.
