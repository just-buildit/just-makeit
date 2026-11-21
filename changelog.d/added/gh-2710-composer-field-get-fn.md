- **A composer field's Python read goes through C: `get_fn` (gh-2710).** A
    source or segment field's getter read its struct member, so a stored
    sentinel -- doppler's `window = WFM_WINDOW_UNSET`, meaning "the
    standard's count, `2 * fs / bandwidth`" -- read back as
    `18446744073709551615` where the generator used 4. `get_fn = "<fn>"` on
    a `[[module.X.source.fields]]` or `[[module.X.segment.fields]]` row
    reads it through `<type> fn(const <struct> *, double fs)`, handed the
    source's `fs` or the segment's own. jm declares the prototype in
    `<cname>_bridge.h` beside the other seams. Only the read changes: the
    setter, the constructor keyword, `to_json`, `from_json` and the stored
    member keep the stored value. A `bytes`, `complex` or owned-pointer
    field has no scalar read, and the key is refused there.
