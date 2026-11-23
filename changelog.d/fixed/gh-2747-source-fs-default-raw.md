- **A composer source's `fs` takes its object's `default_raw`, and a
    foreign `Segment.sources` element is refused (gh-2747).** The source
    type's `fs` default comes from the `fs` init param of `source.object`
    (gh-2678), which was refused when it gave `default_raw` (a header
    constant) instead of `default`; it is now that init param's default, as
    for any other. And `Composer(...)` read every element of a segment's
    `sources` list as a source object without checking it, so an element
    a caller appended was undefined behavior; it is now a `TypeError`
    naming the element.
