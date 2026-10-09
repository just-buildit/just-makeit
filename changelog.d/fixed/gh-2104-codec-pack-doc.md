- **A codec-pack method's `doc` reaches `help()`, and a multi-line `doc`
    keeps its indent in the stub** (gh-2104). A `[[<obj>.methods]]` row
    with `codec` + `sink_fn` pasted its `doc` raw into the `.pyi`, so every
    line after the first sat in column 0, and its `PyMethodDef` doc was the
    fixed line `<name>(...) -- add a codec-typed value.` whatever the
    manifest said. Both faces now carry the `doc` as written, through the
    helpers every other method uses; with no `doc` the fixed line stays. A
    `single` record's stub had the same raw paste: its type doc
    (`record_doc`) and each `result_fields` doc, in `Attributes` and on the
    field's property. Those are laid out the same way now.
