- **One `coerce_str_fn` helper per function, no dead grammar, and composer
    comments within 79 columns** (gh-1736). A composer emitted one
    byte-identical `_coerce_<field>` helper per field naming a
    `coerce_str_fn`; the helper is now `_coerce_<fn>`, one per distinct
    function, called by every field that names it. jm's own `0`/`1` / `0x`
    str grammar is emitted in `_attach_bytes` only while some `bit_pattern`
    field still reads it -- when every one names a host reader, the shared
    attach takes `bytes` and int sequences only. And the generated comments
    that interpolate a name (the bridge header's `coerce_str_fn` field list,
    the `_bridge.h` / `_ext.c` / `_cli.c` banners, the segments-rebuild
    comment) are wrapped to 79 columns; doppler's ran to 104.
