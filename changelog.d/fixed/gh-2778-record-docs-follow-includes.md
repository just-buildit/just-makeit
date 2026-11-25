- **A module function's record fields are documented from the header
    that declares the struct (gh-2778).** A `single` record field with no
    `doc` falls back to its struct's member doc, but for a
    `[[module.X.functions]]` row that fallback read only the module's own
    header. A struct declared in another project header, which the module
    header includes, left every field undocumented in the `.pyi` and at
    runtime, whatever Doxygen style its members used. The module header's
    quoted project includes are now followed, transitively, as an object
    header's already were (gh-724): both read struct member docs through
    one lookup. A leading `/** */`, a trailing `/**< */` and a trailing
    `///<` are all read, and a manifest `doc` still wins.
