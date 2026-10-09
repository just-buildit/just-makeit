- **A numpy section in the `doc` of a hand-written method, or of a property,
    no longer fails `jm status --check`** (gh-2059). `status` reported any
    manifest `doc` with a `Parameters` / `----------` heading as `DOC !`,
    on the grounds that jm generates the numpy sections itself and the
    author's section would duplicate them. For several tables that was
    false. An `[[<obj>.extra_methods]]` or `[[module.X.extra_methods]]` row's
    `doc` is the whole stub body and the whole runtime `__doc__`, with
    nothing generated beside it. The remedy the finding printed, Doxygen in
    `_core.h`, could not apply either, because a hand-written method has no
    core declaration. So a hand-written method could not carry a real numpy
    docstring with a doctest. A sectioned `doc` was rendered in every
    position, and the same was true for an object's and a view's
    properties, a capsule's methods and properties, a handle's getters, and
    a composer's serializers and computed properties. Those tables are now
    named once, in `_docstring.DOC_STANDS_ALONE`, and their `doc` is never
    reported. A `doc` beside a generated section is still reported: an
    object's class doc and methods, parameter and field descriptions.
