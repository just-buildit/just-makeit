- **A composer `bytes` field can name `get_fn`, so a member that describes
    its bits reads as them (gh-2718).** doppler's bit fields are a literal
    (`bits` set) or generated (`bits` NULL, `len` the length a PN or Gold
    generator renders), and the getter read a generated one as `None`, with
    `get_fn` refused on a `bytes` field. A `bytes` field's `get_fn` is
    `size_t fn(const <struct> *, double fs, uint8_t *out, size_t max)`,
    declared in `<cname>_bridge.h`: it returns the length and writes at most
    `max` elements, so `out = NULL, max = 0` sizes the buffer. The getter
    reads through it, a `flat_sources` Segment's read of the field renders
    at the segment's `fs` (as gh-2717's scalar reads do), and `to_json`
    writes a generated member as the bits it renders, where it left the
    field out.
