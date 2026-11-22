- **A Segment's flat read of a `get_fn` source field resolves at the
    segment's `fs` (gh-2717).** A `flat_sources` Segment's flat getter
    returned the inner source's attribute, and the inner source holds the
    source type's default `fs`, since the segment's `fs` keyword is the
    segment's. So doppler's `Segment(type="ofdm", fs=40e6,   bandwidth=20e6).window` read the unset sentinel where `Synth(...)` with
    the same keywords read 4. The flat getter of a field naming `get_fn` now
    calls the resolver with the segment's own `fs`, as the source's getter
    calls it with its own. A segment with no `fs` field reads at the
    source's rate, as before.
