- **A module function returns one record by value: `single` and
    `record_name` on `[[module.X.functions]]` (gh-2755).** The keys were
    refused as method keys, and `result_fields` alone gave a list of
    tuples, a capacity param and a `-> Any` stub. With `single = true` the
    C function returns the `return_type` struct by value and Python gets
    ONE named `PyStructSequence`, built by the emitters a single-record
    method uses. The module creates and publishes the type at init, the
    package re-exports it, and the `.pyi` declares the record class and
    annotates the function with it. `record_module` and `record_doc` work
    as on a method, and a field's doc falls back to the struct's own `///<`
    member doc in the module header. `jm function` takes `--single`,
    `--record-name`, `--record-module` and `--record-doc`. Refused at load,
    naming the key: `single` with no `result_fields`, a record key with no
    `single`, and a key a by-value record never reads (`check_return`,
    `max_results`, `out_type` and the other output keys).
