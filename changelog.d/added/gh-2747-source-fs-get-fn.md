- **A composer source's `fs` can read through the project's C, and a
    segment field's `get_fn` sees the segment's sources (gh-2747).**
    doppler's `ofdm` source reads an `fs` at a face's default as its
    channel bandwidth, but `Synth(type="ofdm").fs` and
    `Segment(type="ofdm").fs` read the default. A `fs_get_fn = "<fn>"` on
    `[module.X.source]`, declared in `<cname>_bridge.h` as
    `double fn(const <struct> *, double fs)`, is the read of the source
    type's `fs`; the setter, the constructor, the stored member and the
    record keep the stored value. A segment field's `get_fn` is now handed
    the segment's sources in its `sources_member` / `count_member`, read by
    the one conversion the Composer builds its segments with.
