- **A writable property's setter can refuse a value** (gh-2182). A property
    may declare `error` and `error_message`, spelled as a method's pair
    (`jm property --writable --error EXC --error-message TEXT`). Its C
    setter then returns `int`, and a non-zero return raises `EXC` with the
    message and the return code, so `acc.alpha = -0.5` says the core refused
    it. Before, the setter discarded whatever `<stem>_set_<prop>` returned:
    the assignment succeeded and the property went on reading its old
    value. Where jm declares the setter it declares it `int`, with an `int`
    stub; the `.pyi` and `help()` document the exception in a `Raises`
    section; `jm apply` and `jm script` carry both keys. `error` is refused,
    naming the fix, wherever there is no C setter call to test: a read-only
    property, `field = true`, and a property named for a state field (whose
    accessor jm declares `void`). A property declaring no `error` renders
    exactly as before.
