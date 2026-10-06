- **An ordinary object registers a hand-written method:
    `[[<obj>.extra_methods]]`** (gh-1997). A CPython function in the
    object's `_extra.c` (`<comp>_ext_extra.c`, or `<cname>_ext_<comp>_extra.c`
    in a module) had no way into the object's method table: `manual_stub`
    emits only a `.pyi` placeholder, and once a module fragment is jm's
    (`fragment = "generated"`) its table is rendered whole from the manifest,
    so a row typed into it was lost on the flip and `jm adopt` refused it.
    A row now declares it with gh-1190's composer keys, `name`, `fn`,
    `flags`, `args`, `returns` and `doc`: jm writes the `PyMethodDef` entry
    after the generated ones, a prototype above the table (in a module, in
    the aggregator before the fragment, so a sacred fragment's own row
    compiles too), the `#include` of the file and the `.pyi` member, through
    the one emitter the composer now shares. Write the function with the
    signature its `flags` imply, `self` as a `PyObject *`. A `manual_stub`
    method of the same name may stay: the row's stub replaces its
    placeholder, and a stub written by hand is kept. `apply` refuses a row
    whose name jm already generates (`reset`, a property, `__enter__`, ...)
    or a `[[<obj>.methods]]` entry declares, two rows of one name, one `fn`
    with two `flags`, and an `fn` the object's `_core.h` declares; `jm method`
    refuses a name a row holds. A view does not inherit the rows, and
    `jm script` names them in a NOTE.
