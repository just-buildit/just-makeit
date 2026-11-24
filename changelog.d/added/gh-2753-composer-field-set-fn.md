- **A composer field's Python setter writes through C: `set_fn`
    (gh-2753).** A source or segment field's setter stored the value in its
    own member, so a field that is half of a pair could not keep the pair
    whole: doppler's `s.code_rate = "3/4"` on a default ofdm source stored
    half a pair the renderer refuses. `set_fn = "<fn>"` on a
    `[[module.X.source.fields]]` or `[[module.X.segment.fields]]` row names
    `const char *fn(<struct> *, <type> value)`, declared in
    `<cname>_bridge.h` beside the other seams. The setter converts and
    checks the value first, then calls `fn` instead of storing: `NULL`
    means written (siblings included), a sentence is raised as `ValueError`
    and the struct is left as it was. The constructor, `from_json` and the
    record do not call it. A `bytes`, `complex`, owned-pointer or ranged
    field is refused when the manifest loads.
