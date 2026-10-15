- **A composer module documents each member once, on both faces**
    (gh-2283). A source, segment, timeline or composer type's runtime
    `help()` was one fixed sentence, while its `.pyi` listed every field
    as `Parameters`; the factories and the composer's own methods carried
    a signature line at runtime and a different sentence in the stub. Each
    docstring is now defined once, and the binding's text is derived from
    the stub's. A source or segment type's summary is its struct's header
    `@brief`. A factory lists the source type's `Parameters` (less the
    discriminant it presets), and names what its discriminant value means
    when the `[[enum]]` declares `enumerators`.
