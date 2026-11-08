- **A `record_dtype` method's `Returns` names the record's columns**
    (gh-2539). Its result is one structured ndarray, which every stub
    annotates `NDArray[Any]`, and both `.pyi` faces described it as
    `Output.`; only the runtime `__doc__` listed the columns, in a `Fields`
    section numpydoc does not know. Every face -- the standalone and
    module-aggregated `.pyi` and the runtime doc of an object and a module
    object -- now lists each column in the `Returns` entry, `name : type`
    with its doc, one per line, as a `single` record's class lists its
    `Attributes`. A header `@return` still leads the entry and a manifest
    `doc` still owns the summary; the emitted C is unchanged but for the doc
    string.
