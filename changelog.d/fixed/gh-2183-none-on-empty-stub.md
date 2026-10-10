- **A `none_on_empty` method's stub says it can return `None`** (gh-2183). A
    `variable_output` method with `none_on_empty = true` returns `None` when
    the kernel writes nothing, on its allocating route and its `out=` route,
    but both `.pyi` producers annotated the bare array, so a type checker
    rejected `if x.value() is None`. Only the borrow route asked the key. One
    predicate now decides it, the one the binding's `Py_RETURN_NONE` is
    emitted from: every face (the standalone and module `.pyi`, and the
    runtime synopsis) annotates `<array> | None`, a tuple result included,
    and both `Returns` sections say when the result is `None`. A borrow's
    `Returns` now says when too, and its runtime synopsis reads
    `-> ndarray | None`. Projects without `none_on_empty` regenerate
    unchanged.
