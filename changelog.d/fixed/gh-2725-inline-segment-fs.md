- **An inline-built composer Segment builds its source at the segment's
    `fs`** (gh-2725). `Seg(type=..., fs=40e6, ...)` took `fs` as the
    segment's own and never handed it to the source it built, so
    `seg.sources[0].fs` was the source type's default and a read through
    that source, such as a `get_fn` field, resolved at a rate no segment
    had. The source is now built at the segment's `fs`, given or defaulted,
    as the composer's read-back already did. They are separate objects
    afterwards: setting `seg.fs` moves the segment only, and a
    `Segment.sum` source keeps its own `fs`.
